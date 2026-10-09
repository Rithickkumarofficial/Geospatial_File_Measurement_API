# Geo Measure API

A FastAPI service that accepts a **zipped Shapefile** or a **KML**, extracts every feature
(index, geometry type, geometry, CRS, properties) and computes **area** (polygons) and
**length** (lines) in metres, using a properly chosen projected CRS, never raw degrees.

## Setup

Requires Python 3.10+. Wheels for `pyogrio`, `shapely` and `pyproj` bundle GDAL/GEOS/PROJ, so no
system GDAL is needed.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --reload          # http://localhost:8000  (docs at /docs)
pytest                                 # run the test-suite
```

Or with Docker: `docker build -t geo-measure . && docker run -p 8000:8000 -v geo-data:/data geo-measure`

| Env var | Default | Meaning |
|---|---|---|
| `GEO_DATA_DIR` | `./data` | Where the SQLite DB lives |
| `GEO_MAX_UPLOAD_BYTES` | 50 MiB | Max upload size (413 above this) |
| `GEO_MAX_UNCOMPRESSED_BYTES` | 250 MiB | Max uncompressed shapefile size inside a zip |
| `GEO_MAX_FEATURES` | 200 000 | Max features per file |

## API

Interactive OpenAPI docs: `GET /docs`. A sample file is in `examples/sample.kml`.

### `POST /api/files/`: upload and process
Multipart form, field `file` = `.kml` or `.zip` (containing `.shp/.shx/.dbf/.prj`).

```bash
curl -F "file=@examples/sample.kml" http://localhost:8000/api/files/
```
`201 Created`
```json
{"id": "dee97c11...", "filename": "sample.kml", "file_type": "kml", "status": "COMPLETED",
 "feature_count": 4, "crs": "EPSG:4326", "error": null, "warnings": [],
 "created_at": "2026-10-09T04:00:14+00:00", "completed_at": "2026-10-09T04:00:14+00:00"}
```
Errors: `415` unsupported extension · `413` too large · `400` empty · `422` unreadable
(bad zip, no `.shp`, corrupt KML). A `422` still returns `{"detail", "id", "status": "FAILED"}`
and the record is queryable.

### `GET /api/files/{id}/`: file information
Returns the object above. `status` is `PROCESSING | COMPLETED | FAILED`; `crs` is `"MIXED"` if
layers differ and `null` if the file has no CRS. `404` for unknown ids.

### `GET /api/files/{id}/measurements/?limit=100&offset=0`: measurements
```json
{
  "id": "dee97c11...", "total": 4, "limit": 100, "offset": 0,
  "summary": {"total_area_sq_m": 1200618.2, "total_length_m": 2171.7,
              "status_counts": {"MEASURED": 2, "NOT_APPLICABLE": 1, "UNSUPPORTED": 1}},
  "measurements": [
    {"index": 0, "layer": "sample", "geometry_type": "Polygon", "status": "MEASURED",
     "area_sq_m": 1200618.2, "length_m": null,
     "measurement_crs": "LAEA(lon_0=77.5050, lat_0=12.9050)", "warnings": []},
    {"index": 1, "layer": "sample", "geometry_type": "LineString", "status": "MEASURED",
     "area_sq_m": null, "length_m": 2171.7, "measurement_crs": "EPSG:32643", "warnings": []},
    {"index": 2, "layer": "sample", "geometry_type": "Point", "status": "NOT_APPLICABLE", "...": "..."},
    {"index": 3, "layer": "sample", "geometry_type": "GeometryCollection", "status": "UNSUPPORTED",
     "warnings": ["Measurement not supported for GeometryCollection"], "...": "..."}
  ]
}
```
Per-feature `status`: `MEASURED`, `NOT_APPLICABLE` (points), `UNSUPPORTED` (e.g. GeometryCollection,
null geometry), `SKIPPED` (source CRS unknown), `ERROR` (that one feature failed). One bad feature
never fails the request. `409` if the file is not `COMPLETED`.

### `GET /api/files/{id}/features/?limit=100&offset=0`: extracted features (extra)
Each feature: `index`, `layer`, `geometry_type`, `geometry` (GeoJSON, in the source CRS), `crs`,
`properties`.

`GET /health` is a liveness probe.

## Architecture

```
app/
  main.py             app factory, wiring
  config.py           env-based settings
  api/files.py        HTTP layer: validation, upload streaming, response shaping
  schemas.py          pydantic response models
  db.py               SQLite repository (files + features tables)
  services/
    readers.py        zip extraction + GDAL (pyogrio) reading -> RawFeature
    crs.py            CRS normalisation, transformer cache, UTM / LAEA selection
    measurements.py   per-geometry measurement, never raises
    processing.py     orchestrates read -> features -> measure
tests/                end-to-end API tests (checked against pyproj Geod and exact projected areas)
```

**File-processing flow**
1. Validate extension, stream upload to a temp dir with a size cap, create a `PROCESSING` record.
2. *Shapefile zip:* extract only `.shp/.shx/.dbf/.prj/.cpg` entries, flattened to basenames
   (zip-slip safe), with an uncompressed-size cap (zip-bomb safe). *KML:* use directly.
3. pyogrio reads every layer (KML folders become layers; a zip may hold several `.shp`). Geometries
   come back as WKB -> shapely; attributes are made JSON-safe (NaN -> null, numpy -> Python).
4. Each feature is measured, then all features + the `COMPLETED` status are written in **one
   transaction**, so a file is never half-stored. Failures set `FAILED` + a message.

**Measurement flow** (`measure_geometry`)
- Point/MultiPoint -> `NOT_APPLICABLE`. Polygon/MultiPolygon -> area. LineString/MultiLineString ->
  length. Anything else or null geometry -> `UNSUPPORTED` with a warning. Z values are dropped for
  measuring (2-D maths). Invalid polygons are repaired with `make_valid` and flagged in `warnings`.

**CRS handling**
- Source CRS is read from the file (`.prj`; KML is always EPSG:4326) and reported as `EPSG:xxxx`.
- Each geometry is first brought to WGS84 lon/lat, then projected for measurement:
  - **Polygons -> Lambert Azimuthal Equal-Area centred on the feature's bbox centre.**
  - **Lines -> UTM zone (WGS84) of the bbox centre** (UPS beyond 84°N / 80°S).
- Planar shapely `area` / `length` run only on those metre-based coordinates.
- If a file has no CRS the features are returned but measurements are `SKIPPED`, with a warning,
  rather than guessing.

## Design decisions & alternatives

- **FastAPI over Django/DRF.** Small, API-only service; FastAPI gives typed models and OpenAPI docs
  for free. Django would earn its keep with auth, admin or GeoDjango/PostGIS.
- **pyogrio + shapely + pyproj instead of geopandas/fiona.** Same GDAL underneath, far fewer
  dependencies, and reading to WKB avoids building a DataFrame we'd immediately unpack. One reader
  handles both formats (and others, later).
- **Per-feature projected CRS.** A single CRS for the whole file (e.g. the UTM zone of the centroid) is
  simpler but gets inaccurate for files spanning zones or continents. Choosing per feature costs
  little (transformers are cached) and keeps error low everywhere. *Equal-area for polygons* because
  area is exactly what that property preserves; *UTM for lines* because it is conformal with
  <0.04 % scale error in-zone. Alternative: geodesic measurement (`pyproj.Geod`) is the most accurate,
  but the brief asks for projected-CRS measurement, and I use `Geod` in the tests as ground truth.
- **Synchronous processing.** Parsing + measuring tens of thousands of features takes well under a
  second to a few seconds, so the upload returns the finished result and the API stays simple. The
  `status` field and `PROCESSING/FAILED` states are already there so moving to a queue
  (Celery/RQ/arq: return `202` and poll `GET /api/files/{id}/`) wouldn't change the contract.
- **SQLite via stdlib, measurements precomputed at upload.** Reads are cheap and paginated, no ORM or
  migration tooling for two tables. For multi-instance deployments swap in Postgres/PostGIS.
  The uploaded original is not retained, only the extracted features; keep it in object storage if
  re-processing is needed.
- **Graceful degradation over rejection.** Per-feature statuses mean one odd feature (collection,
  null geometry, failed transform) doesn't sink a 10 000-feature upload.

**Known limitations:** geometries crossing the antimeridian get a poor projection centre; very large
features spanning many UTM zones (long lines) carry UTM distortion; no authentication or rate limiting;
KML ground overlays, `NetworkLink`s and styling are ignored.
