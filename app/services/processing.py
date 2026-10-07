"""Orchestrates the upload -> parse -> measure -> persist pipeline."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

import shapely
from sqlalchemy.orm import Session

from app.config import Settings
from app.exceptions import ApiError, FileProcessingError, UnsupportedFileTypeError
from app.models import STATUS_COMPLETED, STATUS_FAILED, Feature, UploadedFile
from app.services import measurements as m
from app.services.crs import crs_label
from app.services.readers import ParsedDataset, read_kml, read_shapefile
from app.services.storage import extract_shapefile, remove_tree, save_upload

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".zip": "shapefile", ".kml": "kml"}


def detect_file_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    try:
        return SUPPORTED_EXTENSIONS[ext]
    except KeyError:
        raise UnsupportedFileTypeError(
            "Unsupported file type. Upload a .zip containing a Shapefile, or a .kml file."
        ) from None


def _parse(file_type: str, stored: Path, workdir: Path, settings: Settings) -> ParsedDataset:
    if file_type == "shapefile":
        shp = extract_shapefile(
            stored,
            workdir / "extracted",
            max_uncompressed=settings.max_uncompressed_bytes,
            max_members=settings.max_zip_members,
        )
        return read_shapefile(shp)
    return read_kml(stored)


def process_upload(
    db: Session, stream, filename: str, settings: Settings
) -> UploadedFile:
    """Store, parse and measure an uploaded file; persist the results.

    Raises ApiError subclasses for client mistakes. If the file was received but
    could not be processed, a FAILED record is kept so the client can look it up.
    """
    safe_name = Path(filename or "upload").name[:255] or "upload"
    file_type = detect_file_type(safe_name)  # fail fast, before touching disk

    file_id = uuid.uuid4().hex
    workdir = settings.upload_dir / file_id
    stored = workdir / f"original{Path(safe_name).suffix.lower()}"

    record = UploadedFile(id=file_id, filename=safe_name, file_type=file_type)

    try:
        record.size_bytes = save_upload(stream, stored, settings.max_upload_bytes)
    except ApiError:
        remove_tree(workdir)
        raise  # too large: nothing worth keeping

    db.add(record)
    db.commit()

    try:
        dataset = _parse(file_type, stored, workdir, settings)
        features, total_area, total_length = _build_features(file_id, dataset)

        db.add_all(features)
        record.feature_count = len(features)
        record.crs = crs_label(dataset.crs)
        record.total_area_sq_m = round(total_area, 3)
        record.total_length_m = round(total_length, 3)
        record.status = STATUS_COMPLETED
        db.commit()
        return record

    except FileProcessingError as exc:
        db.rollback()
        _mark_failed(db, record, exc.message)
        exc.file_id = file_id
        raise
    except Exception as exc:  # unexpected: record it, then let the 500 handler respond
        logger.exception("Unexpected error while processing file %s", file_id)
        db.rollback()
        _mark_failed(db, record, "Unexpected internal error while processing the file.")
        raise FileProcessingError(
            "Unexpected internal error while processing the file.", file_id=file_id
        ) from exc
    finally:
        remove_tree(workdir / "extracted")  # keep only the original upload


def _mark_failed(db: Session, record: UploadedFile, message: str) -> None:
    record.status = STATUS_FAILED
    record.error_message = message
    db.add(record)
    db.commit()


def _build_features(
    file_id: str, dataset: ParsedDataset
) -> tuple[list[Feature], float, float]:
    label = crs_label(dataset.crs)
    rows: list[Feature] = []
    total_area = total_length = 0.0

    for parsed in dataset.features:
        geom = parsed.geometry
        result = m.measure_geometry(geom, dataset.crs)

        if result.status == m.MEASURED and result.values:
            total_area += result.values.get("area_sq_m", 0.0)
            total_length += result.values.get("length_m", 0.0)

        has_geom = geom is not None and not geom.is_empty
        rows.append(
            Feature(
                file_id=file_id,
                feature_index=parsed.index,
                geometry_type=geom.geom_type if geom is not None else None,
                geometry=json.loads(shapely.to_geojson(geom)) if has_geom else None,
                crs=label,
                properties=parsed.properties,
                measurement_status=result.status,
                measurements=result.values,
                measurement_note=result.note,
            )
        )
    return rows, total_area, total_length
