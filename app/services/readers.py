"""Readers that turn an uploaded file into a list of features plus a CRS.

Both formats are read through GDAL/OGR (via pyogrio + GeoPandas), so the rest of
the application never needs to care which format a file came from.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app.exceptions import FileProcessingError

KML_DEFAULT_CRS = CRS.from_epsg(4326)  # the KML specification mandates WGS84

# Presentation-only fields GDAL adds to every KML feature; they are not real attributes.
KML_STYLE_FIELDS = {
    "timestamp", "begin", "end", "altitudeMode", "tessellate",
    "extrude", "visibility", "drawOrder", "icon", "id",
}


@dataclass
class ParsedFeature:
    index: int
    geometry: BaseGeometry | None
    properties: dict = field(default_factory=dict)


@dataclass
class ParsedDataset:
    file_type: str
    crs: CRS
    features: list[ParsedFeature]


def _is_null(value) -> bool:
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):  # lists/arrays etc. are not scalar nulls
        return False


def _jsonable(value):
    """Convert numpy/pandas/datetime values to plain JSON-serialisable Python."""
    if value is None or _is_null(value):  # must come first: pd.NaT is a datetime subclass
        return None
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if hasattr(value, "item") and not isinstance(value, (str, list, dict)):
        try:
            value = value.item()  # numpy scalar -> python scalar
        except (ValueError, AttributeError):
            pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _rows_to_features(
    gdf: gpd.GeoDataFrame,
    start_index: int,
    drop_nulls: bool,
    extra: dict | None = None,
    ignore_columns: frozenset[str] | set[str] = frozenset(),
) -> list[ParsedFeature]:
    geom_col = gdf.geometry.name
    columns = [c for c in gdf.columns if c != geom_col and c not in ignore_columns]
    features: list[ParsedFeature] = []
    for offset, (_, row) in enumerate(gdf.iterrows()):
        props = {col: _jsonable(row[col]) for col in columns}
        if drop_nulls:
            props = {k: v for k, v in props.items() if v is not None}
        if extra:
            props.update(extra)
        geom = row[geom_col]
        features.append(
            ParsedFeature(
                index=start_index + offset,
                geometry=None if geom is None or _is_null(geom) else geom,
                properties=props,
            )
        )
    return features


def read_shapefile(shp_path: Path) -> ParsedDataset:
    try:
        gdf = gpd.read_file(shp_path, engine="pyogrio")
    except Exception as exc:
        raise FileProcessingError(f"Could not read the shapefile: {exc}") from exc

    if gdf.crs is None:
        raise FileProcessingError(
            "The shapefile has no CRS (missing or unreadable .prj file). "
            "Include the .prj so measurements can be projected correctly."
        )

    return ParsedDataset(
        file_type="shapefile",
        crs=CRS.from_user_input(gdf.crs),
        features=_rows_to_features(gdf, start_index=0, drop_nulls=False),
    )


def read_kml(kml_path: Path) -> ParsedDataset:
    """Read every layer of a KML file.

    GDAL exposes each KML <Folder>/<Document> as its own layer, so all layers are
    read and concatenated. The layer name is kept in the 'layer' property.
    """
    try:
        layers = pyogrio.list_layers(kml_path)
    except Exception as exc:
        raise FileProcessingError(f"Could not read the KML file: {exc}") from exc

    features: list[ParsedFeature] = []
    for layer_name, _geom_type in layers:
        try:
            gdf = gpd.read_file(kml_path, layer=layer_name, engine="pyogrio")
        except Exception as exc:
            raise FileProcessingError(
                f"Could not read layer '{layer_name}' of the KML file: {exc}"
            ) from exc
        features.extend(
            _rows_to_features(
                gdf,
                start_index=len(features),
                drop_nulls=True,  # KML exposes many always-empty style fields
                extra={"layer": str(layer_name)},
                ignore_columns=KML_STYLE_FIELDS,
            )
        )

    return ParsedDataset(file_type="kml", crs=KML_DEFAULT_CRS, features=features)
