"""HTTP endpoints under /api/files/."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.exceptions import ConflictError, ResourceNotFoundError
from app.models import STATUS_COMPLETED, STATUS_FAILED, Feature, UploadedFile
from app.schemas import (
    FeatureOut,
    FeaturesPage,
    FileInfo,
    MeasurementOut,
    MeasurementsPage,
    MeasurementSummary,
)
from app.services.measurements import MEASURED
from app.services.processing import process_upload

router = APIRouter(prefix="/api/files", tags=["files"])


def _file_info(record: UploadedFile) -> FileInfo:
    info = FileInfo.model_validate(record)
    info.error = record.error_message
    return info


def _get_file_or_404(db: Session, file_id: str) -> UploadedFile:
    record = db.get(UploadedFile, file_id)
    if record is None:
        raise ResourceNotFoundError(f"File '{file_id}' not found.")
    return record


def _require_completed(record: UploadedFile) -> None:
    if record.status == STATUS_FAILED:
        raise ConflictError(
            f"Processing of this file failed: {record.error_message}", file_id=record.id
        )
    if record.status != STATUS_COMPLETED:
        raise ConflictError("File is still being processed.", file_id=record.id)


@router.post("/", response_model=FileInfo, status_code=201, summary="Upload and process a file")
def upload_file(
    file: UploadFile = File(..., description="A .zip containing a Shapefile, or a .kml file"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileInfo:
    record = process_upload(db, file.file, file.filename or "upload", settings)
    return _file_info(record)


@router.get("/{file_id}/", response_model=FileInfo, summary="Information about an uploaded file")
def get_file(file_id: str, db: Session = Depends(get_db)) -> FileInfo:
    return _file_info(_get_file_or_404(db, file_id))


@router.get(
    "/{file_id}/features/",
    response_model=FeaturesPage,
    summary="Features (geometry, CRS, properties) of a file",
)
def get_features(
    file_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> FeaturesPage:
    record = _get_file_or_404(db, file_id)
    _require_completed(record)

    rows = db.scalars(
        select(Feature)
        .where(Feature.file_id == file_id)
        .order_by(Feature.feature_index)
        .limit(limit)
        .offset(offset)
    ).all()
    return FeaturesPage(
        file_id=file_id,
        total=record.feature_count,
        limit=limit,
        offset=offset,
        results=[FeatureOut.model_validate(r) for r in rows],
    )


@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementsPage,
    summary="Measurements for every feature in a file",
)
def get_measurements(
    file_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    include_geometry: bool = Query(False, description="Also return each feature's GeoJSON geometry"),
    db: Session = Depends(get_db),
) -> MeasurementsPage:
    record = _get_file_or_404(db, file_id)
    _require_completed(record)

    rows = db.scalars(
        select(Feature)
        .where(Feature.file_id == file_id)
        .order_by(Feature.feature_index)
        .limit(limit)
        .offset(offset)
    ).all()

    measured_count = db.scalar(
        select(func.count())
        .select_from(Feature)
        .where(Feature.file_id == file_id, Feature.measurement_status == MEASURED)
    )

    results = [
        MeasurementOut(
            feature_index=r.feature_index,
            geometry_type=r.geometry_type,
            crs=r.crs,
            properties=r.properties,
            measurement_status=r.measurement_status,
            measurements=r.measurements,
            note=r.measurement_note,
            geometry=r.geometry if include_geometry else None,
        )
        for r in rows
    ]
    return MeasurementsPage(
        file_id=file_id,
        summary=MeasurementSummary(
            feature_count=record.feature_count,
            measured_count=measured_count or 0,
            total_area_sq_m=record.total_area_sq_m,
            total_length_m=record.total_length_m,
        ),
        total=record.feature_count,
        limit=limit,
        offset=offset,
        results=results,
    )
