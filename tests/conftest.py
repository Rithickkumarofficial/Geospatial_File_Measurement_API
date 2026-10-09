import io
import zipfile
from pathlib import Path

import numpy as np
import pyogrio.raw
import pytest
import shapely
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(Settings(data_dir=tmp_path / "data")))


def make_shapefile_zip(tmp_path: Path, geoms, crs: str | None = "EPSG:4326", name="test", fields=None) -> bytes:
    """Write a shapefile with pyogrio and return it zipped (bytes)."""
    d = tmp_path / f"shp_{name}"
    d.mkdir(exist_ok=True)
    arr = np.array([shapely.to_wkb(g) if g is not None else None for g in geoms], dtype=object)
    names = ["name"]
    data = [np.array([f"f{i}" for i in range(len(geoms))], dtype=object)]
    gtype = geoms[0].geom_type if geoms[0] is not None else "Unknown"
    pyogrio.raw.write(d / f"{name}.shp", geometry=arr, field_data=data, fields=names,
                      geometry_type=gtype, crs=crs, driver="ESRI Shapefile")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in d.iterdir():
            zf.write(p, p.name)
    return buf.getvalue()


KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>plot</name><description>a plot</description><Polygon><outerBoundaryIs><LinearRing><coordinates>
77.5,12.9,0 77.51,12.9,0 77.51,12.91,0 77.5,12.91,0 77.5,12.9,0
</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
<Placemark><name>road</name><LineString><coordinates>77.5,12.9,0 77.52,12.9,0</coordinates></LineString></Placemark>
<Placemark><name>pin</name><Point><coordinates>77.5,12.9,0</coordinates></Point></Placemark>
<Placemark><name>mixed</name><MultiGeometry>
<Point><coordinates>77.5,12.9,0</coordinates></Point>
<LineString><coordinates>77.5,12.9,0 77.52,12.9,0</coordinates></LineString>
</MultiGeometry></Placemark>
</Document></kml>
"""
