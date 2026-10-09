"""Orchestrates: read file -> extract features -> measure each one."""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from shapely.geometry import mapping

from ..config import Settings
from .measurements import Measurement, measure_geometry
from .readers import ProcessingError, ReadResult, read_kml, read_shapefile_zip


@dataclass
class ProcessedFeature:
    index: int
    layer: str
    geometry_type: str | None
    geometry: dict | None  # GeoJSON-style mapping, in the file's own CRS
    crs: str | None
    properties: dict
    measurement: Measurement


@dataclass
class ProcessedFile:
    features: list[ProcessedFeature]
    crs: str | None
    warnings: list[str] = field(default_factory=list)


def detect_kind(filename: str) -> str | None:
    suffix = Path(filename).suffix.lower()
    return {".zip": "shapefile", ".kml": "kml"}.get(suffix)


def process_file(path: Path, kind: str, settings: Settings) -> ProcessedFile:
    with tempfile.TemporaryDirectory(prefix="geo-") as tmp:
        if kind == "shapefile":
            read: ReadResult = read_shapefile_zip(
                path, Path(tmp), settings.max_uncompressed_bytes, settings.max_features
            )
        elif kind == "kml":
            read = read_kml(path, settings.max_features)
        else:  # pragma: no cover - guarded by the API layer
            raise ProcessingError(f"Unsupported file kind: {kind}")

    features: list[ProcessedFeature] = []
    for idx, raw in enumerate(read.features):
        geom = raw.geometry
        features.append(
            ProcessedFeature(
                index=idx,
                layer=raw.layer,
                geometry_type=None if geom is None else geom.geom_type,
                geometry=None if geom is None else mapping(geom),
                crs=raw.crs,
                properties=raw.properties,
                measurement=measure_geometry(geom, raw.crs),
            )
        )

    crs_values = {f.crs for f in features if f.crs}
    if len(crs_values) == 1:
        file_crs = crs_values.pop()
    elif not crs_values:
        file_crs = None
    else:
        file_crs = "MIXED"
        read.warnings.append("Layers use different CRSs; see each feature's 'crs'")
    return ProcessedFile(features, file_crs, read.warnings)
