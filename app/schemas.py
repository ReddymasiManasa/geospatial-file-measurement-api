"""Pydantic response models (the public API contract)."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_validator


class FileInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: str | None
    feature_count: int
    crs: str | None
    status: str
    error: str | None = None
    size_bytes: int
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _assume_utc(cls, value: datetime) -> datetime:
        # SQLite drops tzinfo; timestamps are always stored in UTC.
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class FeatureOut(BaseModel):
    """Full description of a feature, as required by the brief."""

    model_config = ConfigDict(from_attributes=True)

    feature_index: int
    geometry_type: str | None
    geometry: dict | None
    crs: str | None
    properties: dict


class FeaturesPage(BaseModel):
    file_id: str
    total: int
    limit: int
    offset: int
    results: list[FeatureOut]


class MeasurementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    feature_index: int
    geometry_type: str | None
    crs: str | None
    properties: dict
    measurement_status: str
    measurements: dict | None
    note: str | None = None
    geometry: dict | None = None  # only populated when include_geometry=true


class MeasurementSummary(BaseModel):
    feature_count: int
    measured_count: int
    total_area_sq_m: float
    total_length_m: float


class MeasurementsPage(BaseModel):
    file_id: str
    summary: MeasurementSummary
    total: int
    limit: int
    offset: int
    results: list[MeasurementOut]
