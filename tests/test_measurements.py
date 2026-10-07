"""Unit tests for the measurement engine.

Results are validated against an *independent* method: pyproj's ellipsoidal
geodesic calculations on the original lon/lat coordinates.
"""

from __future__ import annotations

import pytest
from pyproj import CRS, Geod, Transformer
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.ops import transform

from app.services.crs import crs_label, utm_epsg_for
from app.services.measurements import (
    ERROR,
    MEASURED,
    NOT_APPLICABLE,
    SKIPPED,
    UNSUPPORTED,
    measure_geometry,
)

WGS84 = CRS.from_epsg(4326)
GEOD = Geod(ellps="WGS84")


def rel_err(actual: float, expected: float) -> float:
    return abs(actual - expected) / expected


def square(lon: float, lat: float, size: float = 0.01) -> Polygon:
    return Polygon([(lon, lat), (lon + size, lat), (lon + size, lat + size), (lon, lat + size)])


# ---------------------------------------------------------------- polygons

@pytest.mark.parametrize("lat", [0.0, 17.4, 45.0, 60.0, -33.9])
def test_polygon_area_matches_geodesic_at_any_latitude(lat):
    poly = square(78.4, lat)
    result = measure_geometry(poly, WGS84)

    expected = abs(GEOD.geometry_area_perimeter(poly)[0])
    assert result.status == MEASURED
    assert rel_err(result.values["area_sq_m"], expected) < 0.001  # within 0.1%
    assert result.values["projected_crs"] == "EPSG:6933"


def test_area_is_not_computed_in_degrees():
    poly = square(78.4, 17.4)
    result = measure_geometry(poly, WGS84)
    assert poly.area == pytest.approx(0.0001)  # the naive, wrong answer (deg^2)
    assert result.values["area_sq_m"] > 1_000_000  # ~1.18 km^2 in reality


def test_hectares_is_consistent_with_square_metres():
    result = measure_geometry(square(10, 50), WGS84)
    assert result.values["area_hectares"] == pytest.approx(result.values["area_sq_m"] / 10_000, rel=1e-6)


def test_polygon_with_hole_subtracts_the_hole():
    outer = [(78.40, 17.40), (78.41, 17.40), (78.41, 17.41), (78.40, 17.41)]
    hole = [(78.403, 17.403), (78.407, 17.403), (78.407, 17.407), (78.403, 17.407)]
    with_hole = Polygon(outer, [hole])
    full = measure_geometry(Polygon(outer), WGS84).values["area_sq_m"]
    holed = measure_geometry(with_hole, WGS84).values["area_sq_m"]
    hole_area = measure_geometry(Polygon(hole), WGS84).values["area_sq_m"]
    assert holed == pytest.approx(full - hole_area, rel=1e-6)


def test_multipolygon_area_is_sum_of_parts():
    a, b = square(78.4, 17.4), square(78.5, 17.5)
    total = measure_geometry(MultiPolygon([a, b]), WGS84).values["area_sq_m"]
    parts = sum(measure_geometry(p, WGS84).values["area_sq_m"] for p in (a, b))
    assert total == pytest.approx(parts, rel=1e-9)


def test_altitude_is_ignored():
    flat = square(78.4, 17.4)
    with_z = Polygon([(x, y, 500.0) for x, y in flat.exterior.coords])
    assert measure_geometry(with_z, WGS84).values["area_sq_m"] == pytest.approx(
        measure_geometry(flat, WGS84).values["area_sq_m"]
    )


def test_invalid_polygon_is_measured_but_flagged():
    bowtie = Polygon([(78.40, 17.40), (78.41, 17.41), (78.41, 17.40), (78.40, 17.41)])
    result = measure_geometry(bowtie, WGS84)
    assert result.status == MEASURED
    assert "invalid" in result.note.lower()


# ------------------------------------------------------------------- lines

def test_line_length_matches_geodesic():
    line = LineString([(78.400, 17.400), (78.420, 17.410), (78.440, 17.410)])
    result = measure_geometry(line, WGS84)
    expected = GEOD.geometry_length(line)
    assert result.status == MEASURED
    assert rel_err(result.values["length_m"], expected) < 0.001
    assert result.values["projected_crs"] == "EPSG:32644"  # UTM 44N for lon 78.4E
    assert result.values["length_km"] == pytest.approx(result.values["length_m"] / 1000, rel=1e-6)


def test_southern_hemisphere_uses_southern_utm_zone():
    line = LineString([(151.20, -33.87), (151.25, -33.90)])
    result = measure_geometry(line, WGS84)
    assert result.values["projected_crs"] == "EPSG:32756"
    assert rel_err(result.values["length_m"], GEOD.geometry_length(line)) < 0.001


def test_multilinestring_length_is_sum_of_parts():
    a = LineString([(78.40, 17.40), (78.41, 17.40)])
    b = LineString([(78.50, 17.50), (78.51, 17.51)])
    total = measure_geometry(MultiLineString([a, b]), WGS84).values["length_m"]
    parts = sum(measure_geometry(g, WGS84).values["length_m"] for g in (a, b))
    assert total == pytest.approx(parts, rel=1e-3)


# ------------------------------------------------------- projected sources

def test_projected_source_crs_gives_ground_metres():
    utm44 = CRS.from_epsg(32644)
    line = LineString([(500000, 1920000), (501000, 1920000), (501000, 1921000)])  # 2000 m grid
    lonlat = transform(Transformer.from_crs(utm44, WGS84, always_xy=True).transform, line)

    result = measure_geometry(line, utm44)

    assert rel_err(result.values["length_m"], GEOD.geometry_length(lonlat)) < 0.001
    # UTM grid distance is within 0.1% of the real ground distance
    assert rel_err(result.values["length_m"], 2000) < 0.002


def test_source_crs_in_feet_is_converted_to_metres():
    # EPSG:2278: NAD83 / Texas South Central (ftUS). Coordinates are in US survey feet.
    ft_crs = CRS.from_epsg(2278)
    to_ft = Transformer.from_crs(WGS84, ft_crs, always_xy=True).transform
    poly_ll = square(-98.5, 29.4)
    poly_ft = transform(to_ft, poly_ll)

    result = measure_geometry(poly_ft, ft_crs)

    expected = abs(GEOD.geometry_area_perimeter(poly_ll)[0])
    assert rel_err(result.values["area_sq_m"], expected) < 0.001
    assert poly_ft.area > 10 * expected  # sanity: the raw number really is in ft^2, not m^2


# --------------------------------------------- graceful handling of edge cases

def test_points_need_no_measurement():
    for geom in (Point(78.4, 17.4), MultiPoint([(78.4, 17.4), (78.5, 17.5)])):
        result = measure_geometry(geom, WGS84)
        assert result.status == NOT_APPLICABLE
        assert result.values is None


def test_geometry_collection_is_unsupported_not_a_crash():
    gc = GeometryCollection([Point(78.4, 17.4), LineString([(78.4, 17.4), (78.5, 17.5)])])
    result = measure_geometry(gc, WGS84)
    assert result.status == UNSUPPORTED
    assert "GeometryCollection" in result.note


@pytest.mark.parametrize("geom", [None, Polygon(), LineString()])
def test_missing_or_empty_geometry_is_skipped(geom):
    assert measure_geometry(geom, WGS84).status == SKIPPED


def test_coordinates_inconsistent_with_declared_crs_are_reported():
    # Projected-looking numbers wrongly labelled as lon/lat.
    bad = Polygon([(500000, 1920000), (501000, 1920000), (501000, 1921000)])
    result = measure_geometry(bad, WGS84)
    assert result.status == ERROR
    assert "outside valid longitude/latitude" in result.note


# ----------------------------------------------------------------- helpers

@pytest.mark.parametrize(
    "lon,lat,expected",
    [
        (78.4, 17.4, 32644),
        (-0.1, 51.5, 32630),
        (-122.4, 37.8, 32610),
        (151.2, -33.9, 32756),
        (179.9, 0.0, 32660),
        (-179.9, 0.0, 32601),
        (10, 85, 32661),  # UPS north
        (10, -85, 32761),  # UPS south
    ],
)
def test_utm_zone_selection(lon, lat, expected):
    assert utm_epsg_for(lon, lat) == expected


def test_crs_label():
    assert crs_label(CRS.from_epsg(4326)) == "EPSG:4326"
    assert crs_label(CRS.from_epsg(32644)) == "EPSG:32644"
