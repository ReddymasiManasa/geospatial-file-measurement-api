"""CRS helpers: label CRSs, pick projections and build (cached) transformers.

Projection strategy
-------------------
Measuring in lat/lon degrees is wrong (a degree of longitude shrinks towards
the poles), so every geometry is first normalised to WGS84 (EPSG:4326) and then
projected into a CRS chosen for the measurement being made:

* AREA   -> EPSG:6933 (WGS 84 / NSIDC EASE-Grid 2.0 Global). It is an
            *equal-area* projection, so areas are preserved anywhere on Earth.
            One fixed CRS also means one cached transformer for all features.
* LENGTH -> the UTM zone of the feature's centroid. UTM is *conformal* and has
            a scale error within ~0.1% inside its zone, which is fine for lengths.

Normalising through WGS84 first also removes unit problems: a file in a
projected CRS that uses feet (e.g. a US State Plane) still yields metres.
"""

from __future__ import annotations

from functools import lru_cache

from pyproj import CRS, Transformer

WGS84 = "EPSG:4326"
EQUAL_AREA_CRS = "EPSG:6933"

_UPS_NORTH = 32661
_UPS_SOUTH = 32761


def crs_label(crs: CRS) -> str:
    """Return a short human-readable label such as 'EPSG:4326'."""
    authority = crs.to_authority(min_confidence=70)
    if authority:
        return f"{authority[0]}:{authority[1]}"
    return crs.name or "UNKNOWN"


def utm_epsg_for(lon: float, lat: float) -> int:
    """EPSG code of the UTM zone containing (lon, lat); UPS near the poles."""
    if lat >= 84.0:
        return _UPS_NORTH
    if lat < -80.0:
        return _UPS_SOUTH
    zone = min(max(int((lon + 180.0) // 6.0) + 1, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone


@lru_cache(maxsize=512)
def _cached_transformer(src_wkt: str, dst: str) -> Transformer:
    return Transformer.from_crs(CRS.from_wkt(src_wkt), CRS.from_user_input(dst), always_xy=True)


def get_transformer(src: CRS, dst: str) -> Transformer:
    """Transformer from `src` to `dst` using x=lon/easting, y=lat/northing order.

    Building a Transformer is comparatively expensive, so they are cached on the
    (source WKT, destination) pair and reused across features.
    """
    return _cached_transformer(src.to_wkt(), dst)
