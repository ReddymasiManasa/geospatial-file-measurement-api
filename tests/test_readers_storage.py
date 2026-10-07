from __future__ import annotations

import io
import zipfile

import pytest

from app.exceptions import FileProcessingError, FileTooLargeError
from app.services.readers import read_kml, read_shapefile
from app.services.storage import extract_shapefile, save_upload
from tests.conftest import write_shapefile_zip


def extract(zip_path, dest, **kw):
    return extract_shapefile(
        zip_path, dest, kw.get("max_uncompressed", 10**8), kw.get("max_members", 100)
    )


# ----------------------------------------------------------------- shapefile

def test_read_shapefile_returns_features_crs_and_properties(tmp_path, parcels_gdf):
    z = write_shapefile_zip(parcels_gdf, tmp_path / "p.zip")
    ds = read_shapefile(extract(z, tmp_path / "out"))

    assert ds.file_type == "shapefile"
    assert ds.crs.to_epsg() == 4326
    assert [f.index for f in ds.features] == [0, 1]
    assert ds.features[0].properties == {"name": "A"}
    assert ds.features[0].geometry.geom_type == "Polygon"


def test_shapefile_without_prj_is_rejected_with_clear_message(tmp_path, parcels_gdf):
    z = write_shapefile_zip(parcels_gdf, tmp_path / "p.zip", drop=(".prj",))
    with pytest.raises(FileProcessingError, match="no CRS"):
        read_shapefile(extract(z, tmp_path / "out"))


def test_shapefile_missing_required_component(tmp_path, parcels_gdf):
    z = write_shapefile_zip(parcels_gdf, tmp_path / "p.zip", drop=(".dbf",))
    with pytest.raises(FileProcessingError, match="missing required"):
        extract(z, tmp_path / "out")


def test_archive_without_shapefile(tmp_path):
    z = tmp_path / "x.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("readme.txt", "hello")
    with pytest.raises(FileProcessingError, match="No .shp"):
        extract(z, tmp_path / "out")


def test_archive_with_two_shapefiles_is_rejected(tmp_path, parcels_gdf):
    a = write_shapefile_zip(parcels_gdf, tmp_path / "a.zip")
    z = tmp_path / "two.zip"
    with zipfile.ZipFile(a) as src, zipfile.ZipFile(z, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            dst.writestr(f"one/{name}", data)
            dst.writestr(f"two/{name}", data)
    with pytest.raises(FileProcessingError, match="2 shapefiles"):
        extract(z, tmp_path / "out")


def test_shapefile_inside_nested_folder_and_macos_junk(tmp_path, parcels_gdf):
    a = write_shapefile_zip(parcels_gdf, tmp_path / "a.zip")
    z = tmp_path / "nested.zip"
    with zipfile.ZipFile(a) as src, zipfile.ZipFile(z, "w") as dst:
        for name in src.namelist():
            dst.writestr(f"data/{name}", src.read(name))
            dst.writestr(f"__MACOSX/data/._{name}", b"junk")
    ds = read_shapefile(extract(z, tmp_path / "out"))
    assert len(ds.features) == 2


def test_not_a_zip(tmp_path):
    f = tmp_path / "fake.zip"
    f.write_bytes(b"this is not a zip")
    with pytest.raises(FileProcessingError, match="not a valid .zip"):
        extract(f, tmp_path / "out")


def test_corrupt_shp_content_gives_processing_error_not_crash(tmp_path):
    z = tmp_path / "bad.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for ext in (".shp", ".shx", ".dbf"):
            zf.writestr(f"bad{ext}", b"garbage-bytes-not-a-shapefile")
    with pytest.raises(FileProcessingError):
        read_shapefile(extract(z, tmp_path / "out"))


# ---------------------------------------------------------------- security

def test_zip_slip_entries_cannot_write_outside_destination(tmp_path, parcels_gdf):
    a = write_shapefile_zip(parcels_gdf, tmp_path / "a.zip")
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(a) as src, zipfile.ZipFile(z, "w") as dst:
        for name in src.namelist():
            dst.writestr(f"../../escaped_{name}", src.read(name))
    dest = tmp_path / "safe" / "out"
    shp = extract(z, dest)

    assert shp.parent == dest
    assert not list(tmp_path.glob("escaped_*"))
    assert not list((tmp_path / "safe").glob("escaped_*"))


def test_zip_bomb_is_stopped_by_uncompressed_size_limit(tmp_path):
    z = tmp_path / "bomb.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.shp", b"\0" * 5_000_000)  # compresses to a few KB
        zf.writestr("big.shx", b"\0")
        zf.writestr("big.dbf", b"\0")
    with pytest.raises(FileProcessingError, match="too large once decompressed"):
        extract(z, tmp_path / "out", max_uncompressed=1_000_000)


def test_too_many_zip_members(tmp_path):
    z = tmp_path / "many.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for i in range(20):
            zf.writestr(f"f{i}.txt", "x")
    with pytest.raises(FileProcessingError, match="too many files"):
        extract(z, tmp_path / "out", max_members=10)


def test_save_upload_enforces_size_limit_and_cleans_up(tmp_path):
    dest = tmp_path / "u" / "f.bin"
    with pytest.raises(FileTooLargeError):
        save_upload(io.BytesIO(b"x" * 2048), dest, max_bytes=1024)
    assert not dest.exists()


# ----------------------------------------------------------------------- KML

def test_read_kml_reads_all_folders_and_cleans_properties(kml_path):
    ds = read_kml(kml_path)

    assert ds.file_type == "kml"
    assert ds.crs.to_epsg() == 4326
    assert len(ds.features) == 5
    assert [f.index for f in ds.features] == [0, 1, 2, 3, 4]
    assert {f.properties["layer"] for f in ds.features} == {"Plots", "Roads"}

    plot_a = ds.features[0].properties
    assert plot_a["Name"] == "Plot A"
    assert plot_a["owner"] == "Ravi" and plot_a["crop"] == "rice"  # ExtendedData preserved
    assert "tessellate" not in plot_a and "visibility" not in plot_a  # style noise removed
    assert "NaT" not in plot_a.values()


def test_invalid_kml_gives_processing_error(tmp_path):
    f = tmp_path / "bad.kml"
    f.write_text("<not-kml>")
    with pytest.raises(FileProcessingError):
        read_kml(f)
