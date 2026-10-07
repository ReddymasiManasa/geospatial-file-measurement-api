from __future__ import annotations

import pytest

from app.config import Settings, get_settings
from tests.conftest import SAMPLE_DIR, write_shapefile_zip


def upload(client, path, name=None, content_type=None):
    with open(path, "rb") as fh:
        files = {"file": (name or path.name, fh, content_type or "application/octet-stream")}
        return client.post("/api/files/", files=files)


# -------------------------------------------------------------------- upload

def test_upload_kml_returns_file_info(client, kml_path):
    r = upload(client, kml_path)
    assert r.status_code == 201
    body = r.json()
    assert body["filename"] == "survey.kml"
    assert body["file_type"] == "kml"
    assert body["feature_count"] == 5
    assert body["crs"] == "EPSG:4326"
    assert body["status"] == "COMPLETED"
    assert len(body["id"]) == 32


def test_upload_shapefile_zip(client, tmp_path, parcels_gdf):
    z = write_shapefile_zip(parcels_gdf, tmp_path / "parcels.zip")
    r = upload(client, z)
    assert r.status_code == 201
    assert r.json()["file_type"] == "shapefile"
    assert r.json()["feature_count"] == 2


def test_unsupported_extension_is_rejected(client, tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hi")
    r = upload(client, f)
    assert r.status_code == 415
    assert "Unsupported file type" in r.json()["detail"]


def test_missing_file_field_is_validation_error(client):
    assert client.post("/api/files/").status_code == 422


def test_oversized_upload_is_rejected(client, settings, tmp_path):
    client.app.dependency_overrides[get_settings] = lambda: Settings(
        database_url=settings.database_url, upload_dir=settings.upload_dir, max_upload_bytes=100
    )
    r = upload(client, SAMPLE_DIR / "survey.kml")
    assert r.status_code == 413


def test_broken_shapefile_returns_422_and_keeps_a_failed_record(client, tmp_path, parcels_gdf):
    z = write_shapefile_zip(parcels_gdf, tmp_path / "noprj.zip", drop=(".prj",))
    r = upload(client, z)
    assert r.status_code == 422
    file_id = r.json()["file_id"]
    assert "no CRS" in r.json()["detail"]

    info = client.get(f"/api/files/{file_id}/").json()
    assert info["status"] == "FAILED"
    assert "no CRS" in info["error"]

    # measurements are not available for a failed file
    assert client.get(f"/api/files/{file_id}/measurements/").status_code == 409


def test_upload_path_traversal_in_filename_is_harmless(client, settings, kml_path):
    r = upload(client, kml_path, name="../../etc/evil.kml")
    assert r.status_code == 201
    assert r.json()["filename"] == "evil.kml"
    assert not (settings.upload_dir.parent.parent / "etc").exists()


# ---------------------------------------------------------------------- GET

def test_get_file_info(client, kml_path):
    file_id = upload(client, kml_path).json()["id"]
    r = client.get(f"/api/files/{file_id}/")
    assert r.status_code == 200
    assert set(r.json()) >= {"id", "filename", "feature_count", "crs", "status"}
    assert r.json()["id"] == file_id


def test_unknown_file_id_is_404(client):
    for path in ("", "measurements/", "features/"):
        r = client.get(f"/api/files/doesnotexist/{path}")
        assert r.status_code == 404


def test_measurements_for_kml(client, kml_path):
    file_id = upload(client, kml_path).json()["id"]
    body = client.get(f"/api/files/{file_id}/measurements/").json()

    assert body["total"] == 5
    by_name = {r["properties"]["Name"]: r for r in body["results"]}

    assert by_name["Plot A"]["measurement_status"] == "MEASURED"
    assert by_name["Plot A"]["measurements"]["area_sq_m"] == pytest.approx(987_804, rel=0.001)
    assert by_name["Road 1"]["measurements"]["length_m"] == pytest.approx(4523, rel=0.001)
    assert by_name["Gate"]["measurement_status"] == "NOT_APPLICABLE"
    assert by_name["Mixed"]["measurement_status"] == "UNSUPPORTED"
    assert body["summary"]["measured_count"] == 3
    assert body["summary"]["total_area_sq_m"] == pytest.approx(987_804 + 293_993, rel=0.001)


def test_measurements_for_projected_shapefile(client):
    file_id = upload(client, SAMPLE_DIR / "roads_utm44n.zip").json()["id"]
    info = client.get(f"/api/files/{file_id}/").json()
    assert info["crs"] == "EPSG:32644"

    results = client.get(f"/api/files/{file_id}/measurements/").json()["results"]
    lengths = sorted(r["measurements"]["length_m"] for r in results)
    assert lengths[0] == pytest.approx(2000, rel=0.002)  # 1000 m + 1000 m
    assert lengths[1] == pytest.approx(3000, rel=0.002)


def test_points_shapefile_has_nothing_to_measure(client):
    file_id = upload(client, SAMPLE_DIR / "markers_wgs84.zip").json()["id"]
    body = client.get(f"/api/files/{file_id}/measurements/").json()
    assert {r["measurement_status"] for r in body["results"]} == {"NOT_APPLICABLE"}
    assert body["summary"]["measured_count"] == 0


def test_features_endpoint_returns_geometry_crs_and_properties(client, kml_path):
    file_id = upload(client, kml_path).json()["id"]
    body = client.get(f"/api/files/{file_id}/features/").json()
    first = body["results"][0]
    assert set(first) == {"feature_index", "geometry_type", "geometry", "crs", "properties"}
    assert first["geometry_type"] == "Polygon"
    assert first["geometry"]["type"] == "Polygon"
    assert first["crs"] == "EPSG:4326"
    assert first["properties"]["owner"] == "Ravi"


def test_include_geometry_flag(client, kml_path):
    file_id = upload(client, kml_path).json()["id"]
    without = client.get(f"/api/files/{file_id}/measurements/").json()["results"][0]
    with_geom = client.get(
        f"/api/files/{file_id}/measurements/", params={"include_geometry": True}
    ).json()["results"][0]
    assert without["geometry"] is None
    assert with_geom["geometry"]["type"] == "Polygon"


def test_pagination(client, kml_path):
    file_id = upload(client, kml_path).json()["id"]
    page = client.get(f"/api/files/{file_id}/measurements/", params={"limit": 2, "offset": 3}).json()
    assert page["total"] == 5
    assert [r["feature_index"] for r in page["results"]] == [3, 4]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 5000}, {"offset": -1}])
def test_invalid_pagination_params(client, kml_path, params):
    file_id = upload(client, kml_path).json()["id"]
    assert client.get(f"/api/files/{file_id}/measurements/", params=params).status_code == 422


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
