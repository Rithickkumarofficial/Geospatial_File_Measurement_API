"""Reading geospatial files (zipped Shapefile, KML) into plain Python objects."""
from __future__ import annotations

import math
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyogrio
import pyogrio.raw
import shapely
from shapely.geometry.base import BaseGeometry

from .crs import describe_crs

SHAPEFILE_PARTS = {".shp", ".shx", ".dbf", ".prj", ".cpg"}


class ProcessingError(Exception):
    """The upload could not be turned into features (bad archive, unreadable file...)."""


@dataclass
class RawFeature:
    layer: str
    geometry: BaseGeometry | None
    crs: str | None
    properties: dict[str, Any]


@dataclass
class ReadResult:
    features: list[RawFeature] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _json_safe(value: Any) -> Any:
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def extract_shapefile_zip(zip_path: Path, dest: Path, max_uncompressed: int) -> list[Path]:
    """Safely extract the shapefile component files from ``zip_path`` into ``dest``.

    Only known shapefile extensions are extracted, and always to a flat
    ``dest/<basename>`` path, so entries like ``../../etc/x`` (zip-slip) can't
    escape. Total declared size is checked before anything is written.
    """
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise ProcessingError("File is not a valid zip archive") from exc

    with zf:
        members = [
            i
            for i in zf.infolist()
            if not i.is_dir()
            and not Path(i.filename).name.startswith(("._", "."))  # macOS/hidden junk
            and "__MACOSX" not in Path(i.filename).parts
            and Path(i.filename).suffix.lower() in SHAPEFILE_PARTS
        ]
        if sum(i.file_size for i in members) > max_uncompressed:
            raise ProcessingError("Zip archive is too large when uncompressed")

        extracted: list[Path] = []
        seen: set[str] = set()
        for info in members:
            name = Path(info.filename).name
            if name.lower() in seen:
                raise ProcessingError(
                    f"Archive contains more than one file named '{name}' (in different folders)"
                )
            seen.add(name.lower())
            target = dest / name
            with zf.open(info) as src, open(target, "wb") as out:
                written = 0
                while chunk := src.read(1024 * 1024):
                    written += len(chunk)
                    if written > max_uncompressed:  # header may lie about size
                        raise ProcessingError("Zip archive is too large when uncompressed")
                    out.write(chunk)
            extracted.append(target)

    shps = [p for p in extracted if p.suffix.lower() == ".shp"]
    if not shps:
        raise ProcessingError("Zip archive does not contain a .shp file")
    return shps


def _read_layers(path: Path, max_features: int) -> ReadResult:
    result = ReadResult()
    try:
        layers = pyogrio.list_layers(path)
    except Exception as exc:  # noqa: BLE001 - GDAL raises a variety of errors
        raise ProcessingError(f"Could not open '{path.name}' as a geospatial file: {exc}") from exc

    for layer_name, _gtype in layers:
        layer_name = str(layer_name)
        try:
            meta, _fids, geoms, field_data = pyogrio.raw.read(
                path, layer=layer_name, datetime_as_string=True
            )
        except Exception as exc:  # noqa: BLE001
            raise ProcessingError(f"Could not read layer '{layer_name}': {exc}") from exc

        crs = describe_crs(meta.get("crs"))
        if crs is None:
            result.warnings.append(
                f"Layer '{layer_name}' has no CRS; features are returned but not measured"
            )
        names = [str(f) for f in meta["fields"]]
        if len(result.features) + len(geoms) > max_features:
            raise ProcessingError(f"File has more than {max_features} features")

        for i in range(len(geoms)):
            raw = geoms[i]
            geom = shapely.from_wkb(raw) if raw is not None else None
            props = {n: _json_safe(col[i]) for n, col in zip(names, field_data)}
            result.features.append(RawFeature(layer_name, geom, crs, props))
    return result


def read_shapefile_zip(zip_path: Path, workdir: Path, max_uncompressed: int, max_features: int) -> ReadResult:
    shps = extract_shapefile_zip(zip_path, workdir, max_uncompressed)
    combined = ReadResult()
    for shp in sorted(shps):
        part = _read_layers(shp, max_features - len(combined.features))
        combined.features.extend(part.features)
        combined.warnings.extend(part.warnings)
    return combined


def read_kml(kml_path: Path, max_features: int) -> ReadResult:
    return _read_layers(kml_path, max_features)
