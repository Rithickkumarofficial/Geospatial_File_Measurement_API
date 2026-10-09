"""Measurement calculation for individual geometries.

Strategy
--------
1. Reproject the geometry to WGS84 lon/lat (no-op if it already is).
2. Pick a projected CRS from the geometry's bounding-box centre:
   * Polygons -> Lambert Azimuthal Equal-Area centred on the feature. Equal-area
     is the property that matters for area, and a custom centre keeps distortion
     negligible anywhere on Earth.
   * Lines    -> the UTM zone (WGS84) containing the feature (UPS near the poles).
     UTM is conformal and has <0.04% scale error inside its zone.
3. Transform to that CRS and use planar shapely area/length (metres).

Degrees are never fed to area/length.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import shapely
from shapely.geometry.base import BaseGeometry

from .crs import WGS84, laea_crs_for, transformer, utm_crs_for


class MeasureStatus(str, Enum):
    MEASURED = "MEASURED"
    NOT_APPLICABLE = "NOT_APPLICABLE"  # supported geometry with no defined measurement (points)
    UNSUPPORTED = "UNSUPPORTED"  # geometry type / content we do not measure
    SKIPPED = "SKIPPED"  # cannot measure safely (e.g. unknown CRS)
    ERROR = "ERROR"  # unexpected failure for this feature only


@dataclass
class Measurement:
    status: MeasureStatus
    area_sq_m: float | None = None
    length_m: float | None = None
    measurement_crs: str | None = None
    warnings: list[str] = field(default_factory=list)


_POLYGONAL = {"Polygon", "MultiPolygon"}
_LINEAR = {"LineString", "MultiLineString"}
_POINTLIKE = {"Point", "MultiPoint"}


def _project(geom: BaseGeometry, src_crs: str, dst_crs: str) -> BaseGeometry:
    return shapely.transform(geom, transformer(src_crs, dst_crs).transform, interleaved=False)


def measure_geometry(geom: BaseGeometry | None, src_crs: str | None) -> Measurement:
    """Measure one geometry. Never raises: failures become a status on the result."""
    if geom is None or geom.is_empty:
        return Measurement(MeasureStatus.UNSUPPORTED, warnings=["Feature has no geometry"])

    gtype = geom.geom_type
    if gtype in _POINTLIKE:
        return Measurement(MeasureStatus.NOT_APPLICABLE)
    if gtype not in _POLYGONAL | _LINEAR:
        return Measurement(
            MeasureStatus.UNSUPPORTED, warnings=[f"Measurement not supported for {gtype}"]
        )
    if not src_crs:
        return Measurement(
            MeasureStatus.SKIPPED,
            warnings=["Source CRS is unknown; refusing to guess units for measurement"],
        )

    try:
        geom = shapely.force_2d(geom)
        warnings: list[str] = []
        if gtype in _POLYGONAL and not geom.is_valid:
            geom = shapely.make_valid(geom)
            warnings.append("Invalid polygon geometry was repaired before measuring")

        geo = geom if src_crs == WGS84 else _project(geom, src_crs, WGS84)
        minx, miny, maxx, maxy = geo.bounds
        lon, lat = (minx + maxx) / 2.0, (miny + maxy) / 2.0

        if gtype in _POLYGONAL:
            target = laea_crs_for(lon, lat)
            projected = _project(geo, WGS84, target)
            # make_valid may yield a GeometryCollection; .area only counts polygonal parts.
            return Measurement(
                MeasureStatus.MEASURED,
                area_sq_m=float(projected.area),
                measurement_crs=f"LAEA(lon_0={lon:.4f}, lat_0={lat:.4f})",
                warnings=warnings,
            )

        target = utm_crs_for(lon, lat)
        projected = _project(geo, WGS84, target)
        return Measurement(
            MeasureStatus.MEASURED,
            length_m=float(projected.length),
            measurement_crs=target,
            warnings=warnings,
        )
    except Exception as exc:  # noqa: BLE001 - isolate per-feature failures
        return Measurement(MeasureStatus.ERROR, warnings=[f"Measurement failed: {exc}"])
