"""Pydantic response models (these also drive the OpenAPI docs)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

FileStatus = Literal["PROCESSING", "COMPLETED", "FAILED"]
MeasureStatusLiteral = Literal["MEASURED", "NOT_APPLICABLE", "UNSUPPORTED", "SKIPPED", "ERROR"]


class FileInfo(BaseModel):
    id: str
    filename: str
    file_type: Literal["shapefile", "kml"]
    status: FileStatus
    feature_count: int
    crs: str | None = Field(None, description="Source CRS, or 'MIXED' if layers differ")
    error: str | None = None
    warnings: list[str] = []
    created_at: str
    completed_at: str | None = None


class Feature(BaseModel):
    index: int
    layer: str
    geometry_type: str | None
    geometry: dict[str, Any] | None = Field(None, description="GeoJSON geometry in the source CRS")
    crs: str | None
    properties: dict[str, Any]


class FeatureMeasurement(BaseModel):
    index: int
    layer: str
    geometry_type: str | None
    status: MeasureStatusLiteral
    area_sq_m: float | None = None
    length_m: float | None = None
    measurement_crs: str | None = Field(None, description="Projected CRS the maths was done in")
    warnings: list[str] = []


class MeasurementSummary(BaseModel):
    total_area_sq_m: float
    total_length_m: float
    status_counts: dict[str, int]


class Page(BaseModel):
    total: int
    limit: int
    offset: int


class FeaturesResponse(Page):
    id: str
    features: list[Feature]


class MeasurementsResponse(Page):
    id: str
    summary: MeasurementSummary
    measurements: list[FeatureMeasurement]
