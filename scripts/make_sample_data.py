"""Generate small sample files for trying the API (written to ./sample_data).

    python scripts/make_sample_data.py

Creates:
  survey.kml            KML with folders: polygon (with a hole), lines, points, a mixed MultiGeometry
  parcels_wgs84.zip     Shapefile in EPSG:4326 (geographic coordinates)
  roads_utm44n.zip      Shapefile in EPSG:32644 (projected, metres)
  markers_wgs84.zip     Shapefile of points (nothing to measure)
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon

OUT = Path(__file__).resolve().parent.parent / "sample_data"

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Survey</name>
<Folder><name>Plots</name>
  <Placemark><name>Plot A</name><description>North field</description>
    <ExtendedData><Data name="owner"><value>Ravi</value></Data><Data name="crop"><value>rice</value></Data></ExtendedData>
    <Polygon><outerBoundaryIs><LinearRing><coordinates>
      78.400,17.400,0 78.410,17.400,0 78.410,17.410,0 78.400,17.410,0 78.400,17.400,0
    </coordinates></LinearRing></outerBoundaryIs>
    <innerBoundaryIs><LinearRing><coordinates>
      78.403,17.403,0 78.407,17.403,0 78.407,17.407,0 78.403,17.407,0 78.403,17.403,0
    </coordinates></LinearRing></innerBoundaryIs></Polygon></Placemark>
  <Placemark><name>Plot B</name>
    <Polygon><outerBoundaryIs><LinearRing><coordinates>
      78.420,17.400,0 78.425,17.400,0 78.425,17.405,0 78.420,17.405,0 78.420,17.400,0
    </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
</Folder>
<Folder><name>Roads</name>
  <Placemark><name>Road 1</name><LineString><coordinates>78.400,17.400,0 78.420,17.410,0 78.440,17.410,0</coordinates></LineString></Placemark>
  <Placemark><name>Gate</name><Point><coordinates>78.405,17.405,0</coordinates></Point></Placemark>
  <Placemark><name>Mixed</name><MultiGeometry>
    <Point><coordinates>78.41,17.41,0</coordinates></Point>
    <LineString><coordinates>78.41,17.41,0 78.42,17.42,0</coordinates></LineString>
  </MultiGeometry></Placemark>
</Folder>
</Document></kml>
"""


def _zip_shapefile(gdf: gpd.GeoDataFrame, zip_base: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        gdf.to_file(Path(tmp) / "layer.shp", driver="ESRI Shapefile")
        shutil.make_archive(str(zip_base), "zip", tmp)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / "survey.kml").write_text(KML, encoding="utf-8")

    parcels = gpd.GeoDataFrame(
        {"name": ["Parcel 1", "Parcel 2"], "zone": ["residential", "commercial"]},
        geometry=[
            Polygon([(78.40, 17.40), (78.41, 17.40), (78.41, 17.41), (78.40, 17.41)]),
            Polygon([(78.42, 17.40), (78.43, 17.40), (78.425, 17.41)]),
        ],
        crs="EPSG:4326",
    )
    _zip_shapefile(parcels, OUT / "parcels_wgs84")

    roads = gpd.GeoDataFrame(
        {"name": ["Main Road", "Ring Road"]},
        geometry=[
            LineString([(500000, 1920000), (501000, 1920000), (501000, 1921000)]),
            LineString([(500000, 1920000), (500000, 1923000)]),
        ],
        crs="EPSG:32644",
    )
    _zip_shapefile(roads, OUT / "roads_utm44n")

    markers = gpd.GeoDataFrame(
        {"name": ["Marker 1", "Marker 2"]},
        geometry=[Point(78.405, 17.405), Point(78.415, 17.415)],
        crs="EPSG:4326",
    )
    _zip_shapefile(markers, OUT / "markers_wgs84")
    print("Wrote:", *sorted(p.name for p in OUT.iterdir()))


if __name__ == "__main__":
    main()
