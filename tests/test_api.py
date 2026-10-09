import io
import zipfile

import pytest
from pyproj import Geod
from shapely.geometry import LineString, Point, Polygon, box

from .conftest import KML, make_shapefile_zip

GEOD = Geod(ellps="WGS84")


def upload(client, name, data, ctype="application/octet-stream"):
    return client.post("/api/files/", files={"file": (name, data, ctype)})


def by_type(measurements):
    return {m["geometry_type"]: m for m in measurements}


def test_kml_end_to_end(client):
    r = upload(client, "survey.kml", KML.encode())
    assert r.status_code == 201, r.text
    info = r.json()
    assert info["status"] == "COMPLETED"
    assert info["crs"] == "EPSG:4326"
    assert info["feature_count"] == 4
    assert info["filename"] == "survey.kml"

    assert client.get(f"/api/files/{info['id']}/").json() == info

    body = client.get(f"/api/files/{info['id']}/measurements/").json()
    ms = by_type(body["measurements"])

    # Independent geodesic ground truth (WGS84 ellipsoid)
    poly = Polygon([(77.5, 12.9), (77.51, 12.9), (77.51, 12.91), (77.5, 12.91)])
    expected_area = abs(GEOD.geometry_area_perimeter(poly)[0])
    expected_len = GEOD.geometry_length(LineString([(77.5, 12.9), (77.52, 12.9)]))

    assert ms["Polygon"]["status"] == "MEASURED"
    assert ms["Polygon"]["area_sq_m"] == pytest.approx(expected_area, rel=1e-4)
    assert 1.1e6 < ms["Polygon"]["area_sq_m"] < 1.3e6  # ~1.1 km x 1.08 km, not degrees^2
    assert ms["LineString"]["length_m"] == pytest.approx(expected_len, rel=1e-3)
    assert ms["LineString"]["measurement_crs"] == "EPSG:32643"
    assert ms["Point"]["status"] == "NOT_APPLICABLE"
    # GeometryCollection (KML MultiGeometry of mixed types) degrades gracefully
    assert ms["GeometryCollection"]["status"] == "UNSUPPORTED"
    assert body["summary"]["status_counts"] == {
        "MEASURED": 2, "NOT_APPLICABLE": 1, "UNSUPPORTED": 1}
    assert body["summary"]["total_area_sq_m"] == pytest.approx(expected_area, rel=1e-4)


def test_features_endpoint_returns_geometry_crs_properties(client):
    fid = upload(client, "a.kml", KML.encode()).json()["id"]
    body = client.get(f"/api/files/{fid}/features/", params={"limit": 2}).json()
    assert body["total"] == 4 and len(body["features"]) == 2
    f = body["features"][0]
    assert f["index"] == 0 and f["geometry_type"] == "Polygon"
    assert f["geometry"]["type"] == "Polygon"
    assert f["crs"] == "EPSG:4326"
    assert f["properties"]["Name"] == "plot"


def test_shapefile_projected_crs_exact_area(client, tmp_path):
    # 100 m x 100 m square in UTM 43N -> 10,000 m2 and a 250 m line
    z = make_shapefile_zip(tmp_path, [box(500000, 1427000, 500100, 1427100)], crs="EPSG:32643")
    info = upload(client, "plots.zip", z).json()
    assert info["crs"] == "EPSG:32643"
    m = client.get(f"/api/files/{info['id']}/measurements/").json()["measurements"][0]
    assert m["area_sq_m"] == pytest.approx(10_000, rel=1e-3)  # equal-area reprojection


def test_shapefile_geographic_line(client, tmp_path):
    z = make_shapefile_zip(tmp_path, [LineString([(0, 0), (0, 1)])], crs="EPSG:4326")
    fid = upload(client, "l.zip", z).json()["id"]
    m = client.get(f"/api/files/{fid}/measurements/").json()["measurements"][0]
    assert m["length_m"] == pytest.approx(110_574, rel=2e-3)  # 1 deg of latitude at equator


def test_shapefile_without_crs_is_not_measured(client, tmp_path):
    z = make_shapefile_zip(tmp_path, [box(0, 0, 1, 1)], crs=None)
    info = upload(client, "nocrs.zip", z).json()
    assert info["status"] == "COMPLETED" and info["crs"] is None and info["warnings"]
    m = client.get(f"/api/files/{info['id']}/measurements/").json()["measurements"][0]
    assert m["status"] == "SKIPPED" and m["area_sq_m"] is None


def test_points_shapefile(client, tmp_path):
    z = make_shapefile_zip(tmp_path, [Point(1, 1), Point(2, 2)])
    fid = upload(client, "p.zip", z).json()["id"]
    body = client.get(f"/api/files/{fid}/measurements/").json()
    assert {m["status"] for m in body["measurements"]} == {"NOT_APPLICABLE"}


def test_invalid_polygon_is_repaired_with_warning(client, tmp_path):
    bowtie = Polygon([(0, 0), (0.01, 0.01), (0.01, 0), (0, 0.01)])
    z = make_shapefile_zip(tmp_path, [bowtie])
    fid = upload(client, "bow.zip", z).json()["id"]
    m = client.get(f"/api/files/{fid}/measurements/").json()["measurements"][0]
    assert m["status"] == "MEASURED" and m["area_sq_m"] > 0 and m["warnings"]


def test_pagination(client, tmp_path):
    z = make_shapefile_zip(tmp_path, [Point(i, i) for i in range(5)])
    fid = upload(client, "p.zip", z).json()["id"]
    body = client.get(f"/api/files/{fid}/measurements/", params={"limit": 2, "offset": 3}).json()
    assert [m["index"] for m in body["measurements"]] == [3, 4] and body["total"] == 5


# ---- error handling -------------------------------------------------
def test_unsupported_extension(client):
    assert upload(client, "x.geojson", b"{}").status_code == 415


def test_empty_upload(client):
    assert upload(client, "x.kml", b"").status_code == 400


def test_corrupt_kml_marks_failed(client):
    r = upload(client, "bad.kml", b"this is not xml")
    assert r.status_code == 422
    fid = r.json()["id"]
    assert client.get(f"/api/files/{fid}/").json()["status"] == "FAILED"
    assert client.get(f"/api/files/{fid}/measurements/").status_code == 409


def test_zip_without_shp(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hi")
    assert upload(client, "e.zip", buf.getvalue()).status_code == 422


def test_not_a_zip(client):
    assert upload(client, "e.zip", b"nope").status_code == 422


def test_zip_slip_entries_stay_inside(client, tmp_path):
    good = make_shapefile_zip(tmp_path, [Point(1, 1)])
    src = zipfile.ZipFile(io.BytesIO(good))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n in src.namelist():
            zf.writestr(f"../../evil/{n}", src.read(n))
    r = upload(client, "slip.zip", buf.getvalue())
    assert r.status_code == 201  # flattened to basenames, nothing written outside
    assert not (tmp_path.parent / "evil").exists()


def test_unknown_id(client):
    assert client.get("/api/files/nope/").status_code == 404
    assert client.get("/api/files/nope/measurements/").status_code == 404


def test_size_limit(tmp_path):
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.main import create_app
    c = TestClient(create_app(Settings(data_dir=tmp_path / "d", max_upload_bytes=10)))
    assert upload(c, "big.kml", b"x" * 100).status_code == 413
