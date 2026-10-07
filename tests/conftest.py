from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import geopandas as gpd
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString, Point, Polygon
from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings
from app.database import Base, get_db, make_engine
from app.main import create_app

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_data"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        upload_dir=tmp_path / "uploads",
        max_upload_bytes=5 * 1024 * 1024,
        max_uncompressed_bytes=20 * 1024 * 1024,
        max_zip_members=50,
    )


@pytest.fixture
def client(settings) -> TestClient:
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


# ---------- helpers to build test files on the fly ----------

def write_shapefile_zip(gdf: gpd.GeoDataFrame, zip_path: Path, drop: tuple[str, ...] = ()) -> Path:
    """Write `gdf` as a shapefile and zip it, optionally leaving out components (e.g. '.prj')."""
    folder = zip_path.parent / (zip_path.stem + "_src")
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    gdf.to_file(folder / "layer.shp", driver="ESRI Shapefile")
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in sorted(folder.iterdir()):
            if f.suffix.lower() not in drop:
                zf.write(f, f.name)
    return zip_path


@pytest.fixture
def parcels_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"name": ["A", "B"]},
        geometry=[
            Polygon([(78.40, 17.40), (78.41, 17.40), (78.41, 17.41), (78.40, 17.41)]),
            Polygon([(78.42, 17.40), (78.43, 17.40), (78.425, 17.41)]),
        ],
        crs="EPSG:4326",
    )


@pytest.fixture
def lines_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"name": ["L1"]},
        geometry=[LineString([(500000, 1920000), (501000, 1920000), (501000, 1921000)])],
        crs="EPSG:32644",
    )


@pytest.fixture
def points_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"name": ["P"]}, geometry=[Point(78.4, 17.4)], crs="EPSG:4326")


@pytest.fixture
def kml_path() -> Path:
    return SAMPLE_DIR / "survey.kml"
