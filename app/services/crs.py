"""CRS helpers: normalisation, transformer cache, projected-CRS selection."""
from __future__ import annotations

from functools import lru_cache

from pyproj import CRS, Transformer

WGS84 = "EPSG:4326"


def describe_crs(crs_like: str | None) -> str | None:
    """Return a compact identifier ('EPSG:4326') or, failing that, the CRS name."""
    if not crs_like:
        return None
    try:
        crs = CRS.from_user_input(crs_like)
    except Exception:  # noqa: BLE001 - pyproj raises several exception types
        return None
    epsg = crs.to_epsg(min_confidence=70)
    if epsg:
        return f"EPSG:{epsg}"
    return crs.name or crs.to_wkt()


@lru_cache(maxsize=256)
def transformer(src: str, dst: str) -> Transformer:
    # always_xy: coordinates are always (x=lon/easting, y=lat/northing),
    # which is the order GDAL hands us regardless of the CRS's axis definition.
    return Transformer.from_crs(CRS.from_user_input(src), CRS.from_user_input(dst), always_xy=True)


def utm_crs_for(lon: float, lat: float) -> str:
    """UTM zone (WGS84) containing the point; UPS polar stereographic beyond 84N/80S."""
    if lat >= 84.0:
        return "EPSG:32661"
    if lat <= -80.0:
        return "EPSG:32761"
    zone = int((lon + 180.0) // 6.0) % 60 + 1
    return f"EPSG:{32600 + zone if lat >= 0 else 32700 + zone}"


def laea_crs_for(lon: float, lat: float) -> str:
    """Lambert Azimuthal Equal-Area centred on the point (area-preserving, metres)."""
    return f"+proj=laea +lat_0={lat:.8f} +lon_0={lon:.8f} +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
