# Geospatial File Measurement API

A FastAPI service that accepts a **zipped Shapefile** or a **KML** file, extracts every
feature (ID, geometry type, geometry, CRS, attributes) and returns **projection-correct
measurements**: area for polygons, length for lines.

- **Framework:** FastAPI + SQLAlchemy (SQLite)
- **Geospatial stack:** GeoPandas / pyogrio (GDAL) for reading, Shapely for geometry, pyproj for CRS
- **Tests:** 64 pytest tests; measurements are validated against an independent geodesic calculation

---

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

uvicorn app.main:app --reload
```

The API is now on <http://localhost:8000>. Interactive docs (Swagger UI) are at
<http://localhost:8000/docs>.

**Try it with the bundled sample files**

```bash
python scripts/make_sample_data.py          # (re)generates ./sample_data
curl -F "file=@sample_data/survey.kml" http://localhost:8000/api/files/
```

**Run the tests**

```bash
pytest
```

**Docker**

```bash
docker build -t geo-api .
docker run -p 8000:8000 -v geo-data:/srv/data geo-api
```

**Configuration** (environment variables, all optional)

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/app.db` | SQLAlchemy database URL |
| `UPLOAD_DIR` | `./data/uploads` | Where original uploads are stored |
| `MAX_UPLOAD_MB` | `50` | Max size of an uploaded file |
| `MAX_UNCOMPRESSED_MB` | `300` | Max size of a zip's contents after decompression |
| `MAX_ZIP_MEMBERS` | `200` | Max number of files inside a zip |

---

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/files/` | Upload and process a file |
| `GET` | `/api/files/{id}/` | Information about an uploaded file |
| `GET` | `/api/files/{id}/measurements/` | Measurements for every feature |
| `GET` | `/api/files/{id}/features/` | Geometry, CRS and properties of every feature |
| `GET` | `/health` | Liveness check |

### `POST /api/files/`

Multipart form with a `file` field: a `.zip` (containing one Shapefile with at least
`.shp`, `.shx`, `.dbf` and a `.prj`) or a `.kml`.

```bash
curl -F "file=@sample_data/parcels_wgs84.zip" http://localhost:8000/api/files/
```

`201 Created`

```json
{
  "id": "cfe259508617411d8354f2504742e7a5",
  "filename": "parcels_wgs84.zip",
  "file_type": "shapefile",
  "feature_count": 2,
  "crs": "EPSG:4326",
  "status": "COMPLETED",
  "error": null,
  "size_bytes": 867,
  "created_at": "2026-10-07T06:33:59.018092Z"
}
```

### `GET /api/files/{id}/`

Returns the same object as above. `status` is `COMPLETED` or `FAILED` (with `error` explaining why).

### `GET /api/files/{id}/measurements/`

Query parameters: `limit` (1-1000, default 100), `offset` (default 0),
`include_geometry` (default `false`).

```json
{
  "file_id": "cfe259508617411d8354f2504742e7a5",
  "summary": {
    "feature_count": 2,
    "measured_count": 2,
    "total_area_sq_m": 1763936.252,
    "total_length_m": 0.0
  },
  "total": 2,
  "limit": 100,
  "offset": 0,
  "results": [
    {
      "feature_index": 0,
      "geometry_type": "Polygon",
      "crs": "EPSG:4326",
      "properties": { "name": "Parcel 1", "zone": "residential" },
      "measurement_status": "MEASURED",
      "measurements": {
        "area_sq_m": 1175957.501,
        "area_hectares": 117.59575,
        "projected_crs": "EPSG:6933"
      },
      "note": null,
      "geometry": null
    }
  ]
}
```

A line feature returns `{"length_m": 4523.437, "length_km": 4.523437, "projected_crs": "EPSG:32644"}`.

**`measurement_status` values**

| Status | Meaning |
|---|---|
| `MEASURED` | Area/length computed. `note` may carry a warning (e.g. invalid geometry). |
| `NOT_APPLICABLE` | Points: no measurement is required. |
| `UNSUPPORTED` | A geometry type that is not measured (e.g. mixed `GeometryCollection`). |
| `SKIPPED` | Feature has no / empty geometry. |
| `ERROR` | This one feature could not be measured (reason in `note`); other features are unaffected. |

### `GET /api/files/{id}/features/`

Same pagination. Each result contains `feature_index`, `geometry_type`, `geometry` (GeoJSON),
`crs` and `properties`.

### Errors

Errors are JSON: `{"detail": "...", "file_id": "..."}` (`file_id` present when a record exists).

| Code | When |
|---|---|
| `404` | Unknown file id |
| `409` | Measurements/features requested for a file whose processing failed |
| `413` | Upload larger than `MAX_UPLOAD_MB` |
| `415` | Not a `.zip` or `.kml` |
| `422` | File received but unusable: corrupt, not a zip, missing `.shp`/`.shx`/`.dbf`/`.prj`, several shapefiles in one zip... A `FAILED` record is kept so the reason can be looked up with `GET /api/files/{id}/`. |

---

## Architecture

### Application structure

```
app/
  main.py              app factory, error handler, startup (creates tables)
  config.py            settings from environment variables
  database.py          engine / session / get_db dependency
  models.py            UploadedFile 1---* Feature
  schemas.py           Pydantic response models (the API contract)
  exceptions.py        domain errors, each carrying its HTTP status
  api/files.py         HTTP layer only: validation + (de)serialisation
  services/
    storage.py         saving uploads, safe zip extraction
    readers.py         Shapefile / KML  ->  features + CRS
    crs.py             CRS labels, UTM zone selection, cached transformers
    measurements.py    area / length of one geometry
    processing.py      orchestrates the whole pipeline
tests/                 unit + API tests
scripts/               sample data generator
```

The HTTP layer contains no geospatial logic. All of it sits in `services/`, which has no
FastAPI dependency and is therefore unit-testable on its own.

### File-processing flow

```
POST /api/files/
   |  1. check extension (.zip / .kml)                -> 415
   |  2. stream to disk, enforcing the size limit      -> 413
   |  3. create UploadedFile row (PROCESSING)
   |  4. .zip: safely extract the one shapefile        -> 422 on any problem
   |     .kml: use the file directly
   |  5. read with GDAL via GeoPandas  -> features + CRS
   |       (KML: every <Folder> is a layer; all are merged)
   |  6. measure each feature (see below)
   |  7. bulk-insert Feature rows, store totals, status = COMPLETED
   v  8. on any failure: status = FAILED + error message, rollback features
201 + file info
```

Processing is synchronous inside the request, because the brief defines the upload as
"uploads **and processes** a file" and `status` is always final when the response arrives.

### Measurement-calculation flow

```
geometry (source CRS)
  -> drop altitude (force 2D)
  -> reproject to WGS84 lon/lat; sanity-check the coordinate ranges
  -> Polygon / MultiPolygon : project to EPSG:6933 (equal-area)  -> area
     LineString / MultiLineString : project to local UTM zone    -> length
     Point / MultiPoint : NOT_APPLICABLE
     anything else / empty : UNSUPPORTED / SKIPPED
```

Each feature is measured inside its own try/except, so one bad feature produces an `ERROR`
entry instead of failing the file.

### CRS handling

Calculating area or distance in degrees is wrong because a degree of longitude shrinks
towards the poles (at 60 degrees N it is half as long as at the equator). Every geometry is
therefore projected before it is measured:

1. **Normalise to WGS84.** This makes every input look the same, including projected CRSs
   in non-metre units (a State Plane file in US feet still yields square metres).
2. **Area -> EPSG:6933**, a global equal-area projection. Equal-area means the area of a
   shape is preserved wherever on Earth it is, so one fixed CRS is both correct and cheap
   (a single cached transformer).
3. **Length -> UTM zone of the feature's centroid** (UPS near the poles). UTM is conformal
   and has a scale error within about 0.1% inside its zone (0.04% at the zone's centre line).

The CRS actually used is returned in `projected_crs` for every measurement.

- **Shapefile:** the CRS comes from the `.prj`. A shapefile without one is rejected
  rather than guessed.
- **KML:** always WGS84 per the KML specification.

**Verification:** the tests compare results with pyproj's ellipsoidal geodesic calculations
(an independent method that never projects). Areas and lengths agree within 0.1% at
latitudes from the equator to 60 degrees N and at 34 degrees S, in both hemispheres, for polygons with holes, and for
projected and feet-based source CRSs. Deliberately reintroducing the "measure in degrees"
bug makes 7 tests fail.

---

## Design decisions

| Decision | Alternatives considered | Why |
|---|---|---|
| **FastAPI** | Django + DRF | Less boilerplate for a small, API-only service; typed request/response models and automatic OpenAPI docs. |
| **GeoPandas + pyogrio (GDAL)** to read both formats | Pure-Python `pyshp` + hand-written KML parser; `fiona` | One code path for both formats; GDAL handles encodings, `.cpg`, curve types and CRS parsing. pyogrio wheels bundle GDAL, so no system install is needed. |
| **Equal-area CRS for area, local UTM for length** | One UTM zone per feature for both; pyproj `Geod` (geodesic) | Matches the brief ("transform to a projected CRS"), uses the property each projection is actually good at, and needs only one cached transformer for areas. Geodesic is used only as the test oracle. |
| **Always go through WGS84 first** | Use the source CRS directly if it is already projected | Source CRSs can use feet or have distortion that is unsuitable for the area; one normalised path is simpler and unit-safe. |
| **Reject a shapefile with no `.prj`** | Assume EPSG:4326 | A silent wrong guess produces plausible but wrong numbers. A clear 422 is safer. |
| **Synchronous processing** | Celery / background tasks | Matches "upload and process", keeps the setup to one process. Processing time is small for typical files; see future scope. |
| **SQLite + JSON columns** | PostGIS | Zero setup to run locally. Geometry is stored as GeoJSON; no spatial queries are required yet. |
| **Store features and measurements at upload time** | Re-read the file on every GET | GETs are fast and cheap, and the original is not needed again. |
| **`FAILED` records kept, `413`/`415` not** | Never store failures | A failed upload has a meaningful id and reason the client can query. Oversized and wrong-type files are rejected before they are worth recording. |
| **Per-feature failure isolation** | Fail the whole file on the first bad feature | One corrupt feature in a 10,000-feature file should not discard the other 9,999. |
| **Fixed filenames when extracting zips** | Extract using member names | Member names are never used as paths, which removes zip-slip by construction. Zip-bomb limits cap the bytes written. |
| **Invalid polygons are measured and flagged** | Auto-repair with `make_valid`; reject | Silently changing geometry changes the answer; a `note` lets the caller decide. |

**Known limitations**

- A single UTM zone is used per line feature, so very long lines crossing several zones lose
  accuracy (a geodesic fallback would fix this).
- Features crossing the antimeridian or enclosing a pole are not handled specially.
- One shapefile per zip; shapefiles without `.prj` are rejected.
- Mixed `GeometryCollection` features are reported as `UNSUPPORTED` rather than split into parts.
- No authentication or rate limiting.

---

## Learning and future scope

### What I learned

- Why "lat/lon degrees" cannot be measured directly, and how equal-area and conformal
  projections each preserve a different property.
- That CRS axis order differs between conventions (EPSG:4326 is lat/lon officially) and that
  `always_xy=True` avoids a classic class of bugs.
- GDAL exposes each KML `<Folder>` as a separate layer, and adds many empty style fields to
  every feature that have to be filtered out.
- Zip uploads are an attack surface (zip-slip, zip bombs), and pandas' `NaT` is a `datetime`
  subclass, so null checks must come before type checks.
- Testing against an independent method (geodesic) is far stronger than testing against
  numbers produced by the code itself.

### Future scope

- Background processing (Celery/RQ or FastAPI background tasks) with polling via `status`,
  for very large files.
- PostgreSQL + PostGIS for spatial queries (bounding-box search, features within a polygon).
- Geodesic fallback for geometries that span several UTM zones; antimeridian handling.
- Support GeoJSON, GeoPackage and KMZ; optional `assume_crs` for shapefiles without a `.prj`.
- Split `GeometryCollection` features into measurable parts; add polygon perimeter.
- Authentication, per-user files, `DELETE /api/files/{id}/` and retention policy.
- Alembic migrations, structured logging, and CI (GitHub Actions) running the test suite.
