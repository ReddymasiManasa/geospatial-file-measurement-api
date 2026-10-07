"""Measurement of single geometries (area for polygons, length for lines)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import shapely
from pyproj import CRS
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

from app.services.crs import EQUAL_AREA_CRS, WGS84, get_transformer, utm_epsg_for

# Measurement statuses
MEASURED = "MEASURED"
NOT_APPLICABLE = "NOT_APPLICABLE"  # e.g. points: nothing to measure
UNSUPPORTED = "UNSUPPORTED"  # geometry type we deliberately do not measure
SKIPPED = "SKIPPED"  # missing / empty geometry
ERROR = "ERROR"  # measuring this one feature failed

POLYGON_TYPES = {"Polygon", "MultiPolygon"}
LINE_TYPES = {"LineString", "MultiLineString"}
POINT_TYPES = {"Point", "MultiPoint"}


@dataclass(frozen=True)
class MeasurementResult:
    status: str
    values: dict | None = None
    note: str | None = None


def _project(geom: BaseGeometry, src_crs: CRS, dst: str) -> BaseGeometry:
    transformer = get_transformer(src_crs, dst)
    return shapely_transform(transformer.transform, geom)


def _to_wgs84(geom: BaseGeometry, src_crs: CRS) -> BaseGeometry:
    """Reproject to lon/lat and sanity-check the result."""
    if src_crs.equals(CRS.from_user_input(WGS84)):
        lonlat = geom
    else:
        lonlat = _project(geom, src_crs, WGS84)

    minx, miny, maxx, maxy = lonlat.bounds
    if not all(math.isfinite(v) for v in (minx, miny, maxx, maxy)):
        raise ValueError("coordinates could not be transformed to WGS84")
    if minx < -180 or maxx > 180 or miny < -90 or maxy > 90:
        raise ValueError(
            "coordinates fall outside valid longitude/latitude ranges; "
            "the file's CRS definition may not match its coordinates"
        )
    return lonlat


def measure_geometry(geom: BaseGeometry | None, src_crs: CRS) -> MeasurementResult:
    """Measure one geometry. Never raises: problems are reported in the result."""
    if geom is None or geom.is_empty:
        return MeasurementResult(SKIPPED, note="Feature has no geometry.")

    geom_type = geom.geom_type

    if geom_type in POINT_TYPES:
        return MeasurementResult(NOT_APPLICABLE, note="No measurement is defined for points.")

    if geom_type not in POLYGON_TYPES | LINE_TYPES:
        return MeasurementResult(
            UNSUPPORTED, note=f"Measurement is not supported for geometry type '{geom_type}'."
        )

    try:
        flat = shapely.force_2d(geom)  # altitude (e.g. from KML) is irrelevant here
        lonlat = _to_wgs84(flat, src_crs)
        warning = None

        if geom_type in POLYGON_TYPES:
            projected = _project(lonlat, CRS.from_user_input(WGS84), EQUAL_AREA_CRS)
            area = float(projected.area)
            if not math.isfinite(area):
                raise ValueError("area is not a finite number")
            if not shapely.is_valid(flat):
                warning = (
                    "Geometry is invalid (e.g. self-intersecting); the area may be unreliable. "
                    f"Reason: {shapely.is_valid_reason(flat)}"
                )
            values = {
                "area_sq_m": round(area, 3),
                "area_hectares": round(area / 10_000, 6),
                "projected_crs": EQUAL_AREA_CRS,
            }
        else:
            centroid = lonlat.centroid
            utm = f"EPSG:{utm_epsg_for(centroid.x, centroid.y)}"
            projected = _project(lonlat, CRS.from_user_input(WGS84), utm)
            length = float(projected.length)
            if not math.isfinite(length):
                raise ValueError("length is not a finite number")
            values = {
                "length_m": round(length, 3),
                "length_km": round(length / 1000, 6),
                "projected_crs": utm,
            }

        return MeasurementResult(MEASURED, values=values, note=warning)

    except Exception as exc:  # one bad feature must not fail the whole file
        return MeasurementResult(ERROR, note=f"Could not measure geometry: {exc}")
