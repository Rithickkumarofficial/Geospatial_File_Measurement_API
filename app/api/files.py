from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from ..config import Settings
from ..db import Repository
from ..schemas import (
    Feature, FeatureMeasurement, FeaturesResponse, FileInfo,
    MeasurementsResponse, MeasurementSummary,
)
from ..services.processing import detect_kind, process_file
from ..services.readers import ProcessingError

router = APIRouter(prefix="/api/files", tags=["files"])


def get_repo(request: Request) -> Repository:
    return request.app.state.repo


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def _completed_file(repo: Repository, file_id: str) -> dict:
    record = repo.get_file(file_id)
    if record is None:
        raise HTTPException(404, "File not found")
    if record["status"] != "COMPLETED":
        raise HTTPException(
            409, f"File is {record['status']}" + (f": {record['error']}" if record["error"] else "")
        )
    return record


@router.post("/", response_model=FileInfo, status_code=201, summary="Upload and process a file")
def upload_file(
    file: UploadFile,
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_settings),
):
    """Accepts a `.zip` containing a Shapefile, or a `.kml`. Processing is synchronous."""
    filename = Path(file.filename or "").name
    kind = detect_kind(filename)
    if kind is None:
        raise HTTPException(415, "Unsupported file type; upload a .zip (Shapefile) or .kml")

    # Stream to disk with a hard size cap (never hold the upload in memory).
    with tempfile.TemporaryDirectory(prefix="upload-") as tmp:
        saved = Path(tmp) / filename  # basename only, so no path traversal
        size = 0
        with open(saved, "wb") as out:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(413, f"File exceeds {settings.max_upload_bytes} bytes")
                out.write(chunk)
        if size == 0:
            raise HTTPException(400, "Uploaded file is empty")

        file_id = repo.create_file(filename, kind)
        try:
            result = process_file(saved, kind, settings)
            repo.complete_file(file_id, result)
        except ProcessingError as exc:
            repo.mark_failed(file_id, str(exc))
            return JSONResponse(
                status_code=422, content={"detail": str(exc), "id": file_id, "status": "FAILED"}
            )
        except Exception:  # noqa: BLE001 - never leave a record stuck in PROCESSING
            repo.mark_failed(file_id, "Unexpected error while processing file")
            raise
    return repo.get_file(file_id)


@router.get("/{file_id}/", response_model=FileInfo, summary="File information")
def get_file(file_id: str, repo: Repository = Depends(get_repo)):
    record = repo.get_file(file_id)
    if record is None:
        raise HTTPException(404, "File not found")
    return record


@router.get("/{file_id}/features/", response_model=FeaturesResponse, summary="Extracted features")
def get_features(
    file_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    repo: Repository = Depends(get_repo),
):
    record = _completed_file(repo, file_id)
    rows = repo.list_features(file_id, limit, offset)
    return FeaturesResponse(
        id=file_id, total=record["feature_count"], limit=limit, offset=offset,
        features=[
            Feature(index=r["idx"], layer=r["layer"], geometry_type=r["geometry_type"],
                    geometry=r["geometry"], crs=r["crs"], properties=r["properties"])
            for r in rows
        ],
    )


@router.get("/{file_id}/measurements/", response_model=MeasurementsResponse,
            summary="Per-feature measurements")
def get_measurements(
    file_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    repo: Repository = Depends(get_repo),
):
    record = _completed_file(repo, file_id)
    rows = repo.list_features(file_id, limit, offset)
    items = [
        FeatureMeasurement(
            index=r["idx"], layer=r["layer"], geometry_type=r["geometry_type"],
            status=r["m_status"], area_sq_m=r["area_sq_m"], length_m=r["length_m"],
            measurement_crs=r["m_crs"], warnings=r["m_warnings"],
        )
        for r in rows
    ]
    return MeasurementsResponse(
        id=file_id, total=record["feature_count"], limit=limit, offset=offset,
        summary=MeasurementSummary(**repo.measurement_summary(file_id)), measurements=items,
    )
