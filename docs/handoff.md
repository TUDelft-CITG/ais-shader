# Session Handoff

## 1. Accomplished Objectives

### A. Dutch Inland Waterways & RWS FIS Fairway Integration
* **RWS FIS Fairway Axis (`src/ais_shader/rws.py`)**:
  * Implemented automated ingestion of Rijkswaterstaat Fairway Information Services (FIS VNDS) from ArcGIS REST MapServer 58 (`vaarwegvak`), Layer 55 (`vaarwegen`), and Layer 11 (`kilometermarkering`).
  * Connects disjoint section geometries into a single continuous 1D Fairway Axis (`FairwayAxis`) in the Dutch national projection system (`EPSG:28992` - Amersfoort / RD New).
  * Auto-corrects line segment orientations to ensure monotonically ascending chainage coordinates along the waterway (`routekmbegin` to `routekmend`).
  * Unit tests passing in [`tests/test_rws.py`](file:///home/fbaart/src/ais-shader/tests/test_rws.py).
  * Added CLI command: `ais-shader fairway build-rws-centerline`.

### B. EURIS AIS Ingestion & Crawl Pipeline
* **Live Crawling & Pipeline Automation**:
  * Implemented [`examples/crawl_euris_loop.py`](file:///home/fbaart/src/ais-shader/examples/crawl_euris_loop.py) and [`examples/detect_euris_encounters.py`](file:///home/fbaart/src/ais-shader/examples/detect_euris_encounters.py).
  * Ran a 30-minute continuous crawl capturing 36,360 raw fixes across 208 vessels in the Netherlands.
  * Spatial corridor filtering isolates Amsterdam-Rijnkanaal (corridor width: 300 m lateral buffer).

### C. Master GeoPackage (`.gpkg`) Export with Embedded QGIS Styles
* **Multi-Layer GeoPackage**:
  Replaced loose file exports with a standardized, self-contained GeoPackage (`/scratch-shared/fbaart/data/euris_crawl/euris_encounters.gpkg`, 8.06 MB) in `EPSG:28992`:
  1. `fairway_centerline`: Continuous fairway navigation axis.
  2. `fairway_sections`: Official RWS fairway sections with kilometer markings and fairway names.
  3. `trajectorized_points`: Point fixes with boat dimensions, speed, and heading/COG.
  4. `trajectories`: Continuous voyage LineStrings per trip with `TrackStartTime` and `TrackEndTime`.
  5. `segments`: Point-to-point line segments with speed, duration, and vessel group.
  6. `stationary_vessels`: Moored and anchored vessels identified as potential navigation obstacles.
  7. `encounters`: Detected CPA encounter events (overtakings, head-on meetings, crossings) with distance metrics and along-fairway chainage.
  8. `timeseries`: Synchronous dynamic connecting lines between interacting vessels at 15s intervals.
* **Direct QGIS Style Embedding (`layer_styles`)**:
  * [`scripts/generate_qgis_styles.py`](file:///home/fbaart/src/ais-shader/scripts/generate_qgis_styles.py) automatically embeds all QML layer styles into the SQLite `layer_styles` table inside the `.gpkg`.
  * Opening the `.gpkg` in QGIS loads fully styled layers with instant symbology and temporal axes out of the box.

### D. Algorithmic Refinements & Kinematic Bug Fixes

#### 1. Deduplication of Repeated Pings for Moving Vessels (Encounter #42)
* **Root Cause**: High-frequency API polling (every 10s) on vessels broadcasting AIS Class A position reports every 20–30s generated consecutive identical coordinates. This created 0-meter segments (`speed_mps = 0.0`), which caused active vessels (e.g. `TRK_746020` sailing at 7.2 knots) to be flagged as stationary obstacles. As a result, head-on encounters were misclassified as `overtaking` (passing a moored vessel).
* **Fix**: In [`make_segments_from_points`](file:///home/fbaart/src/ais-shader/examples/detect_euris_encounters.py), consecutive identical GPS fixes for moving vessels (`sog >= 0.5`) are deduplicated. In [`src/ais_shader/events.py`](file:///home/fbaart/src/ais-shader/src/ais_shader/events.py), an SOG fallback was added.
* **Verification**: Encounter #42 between `TRK_746020` (342.5° heading, 7.2 kts) and `TRK_781453` (165.9° heading, 10.1 kts) is now accurately detected and classified as **`head-on`** at CPA 37.4 m.

#### 2. Segments Visibility & Transparency Fix
* **Root Cause**: An experimental data-defined `alpha` expression (`scale_linear(...)`) returned values between 0.2 and 1.0. In QGIS, data-defined opacity expects a percentage (0–100%), interpreting 1.0 as **1% opacity (99% transparent)**, rendering the layer invisible.
* **Fix**: Replaced custom expressions with clean, solid styling (`alpha="1"`, 0.65 mm line width) categorized by `VesselGroup`.

#### 3. OGC Geometry Validity Fix
* **Root Cause**: 4,974 stationary fixes had identical start and end coordinates (`LineString([c, c])`), triggering GEOS topology validation errors in QGIS (`Too few points in geometry component`).
* **Fix**: Added a 0.05 m epsilon offset to identical coordinates for stationary vessels, ensuring 100% valid OGC Simple Feature LineStrings without altering speed calculations.

#### 4. Heading = 0 Fallback to COG
* **Root Cause**: Inland transponders lacking gyrocompasses broadcast `heading = 0` rather than 511, causing arrow markers to point North.
* **Fix**: Updated QGIS marker angle expression:
  ```sql
  coalesce(if("heading" < 360 AND "heading" > 0, "heading", null), if("cog" < 360, "cog", null), 0)
  ```
  with semi-transparency (`alpha="0.438"`) and shape switching (circle for uninstrumented/zero-speed vessels, arrow for moving vessels).

#### 5. Strict Fail-Fast Validation (No Silent Workarounds)
* **Design Rule**: If invalid or underspecified inputs are provided, the code raises explicit errors (`ValueError`, `KeyError`, `RuntimeError`) rather than silently fixing or guessing.
* **Checks Enforced**:
  - `build_rws_fairway`:
    - Rejects input GeoDataFrames without a CRS (`ValueError: Input data has no CRS set.`).
    - Requires at least one query filter if `data is None` (`ValueError: Must specify at least one of data, fairway_id, river_name, or bbox.`).
    - Requires `routekmbegin` column for chainage ordering (`KeyError`).
    - Rejects disconnected multi-part lines instead of picking the longest line (`ValueError: Fairway section geometries do not form a single continuous line...`).
  - `fetch_rws_fairway_sections` & `fetch_rws_kilometer_markers`:
    - Requires at least one filter parameter (`fairway_id`, `name`, `bbox`).
    - In `query_rws_arcgis_layer`, checks response JSON and raises `RuntimeError` on server error messages.
  - `FairwayAxis.from_file`:
    - Rejects `MultiLineString` inputs (`ValueError`).
  - `detect_encounters`:
    - Rejects `segments_gdf` without CRS (`ValueError: segments_gdf must have a defined Coordinate Reference System (CRS).`).
  - `make_segments_from_points`:
    - Rejects empty datasets, missing CRS, or missing timestamp/vessel columns.
    - Removed heuristic guessing that unknown vessels with length $\ge 30\text{ m}$ are "Cargo".

---

## 2. Environment & Tooling

### Snellius Module Environment
To build and execute CGAL C++ extensions, load the following modules:
```bash
module load 2025 CGAL/6.0.1-GCCcore-14.2.0 Boost/1.88.0-GCC-14.2.0 GMP/6.3.0-GCCcore-14.2.0 MPFR/4.2.2-GCCcore-14.2.0
```

### Python Virtual Environment
Always invoke scripts and tools using `uv run`:
```bash
uv run pytest
uv run python examples/detect_euris_encounters.py --input-file /scratch-shared/fbaart/data/euris_crawl/euris_crawl_20260913_174753.geoparquet --output-gpkg /scratch-shared/fbaart/data/euris_crawl/euris_encounters.gpkg
uv run python scripts/generate_qgis_styles.py
```

---

## 3. Active Branches & Pull Requests

* **Current Branch**: `feature/euris-encounters`
* **Pull Request**: [#20 feat: Dutch inland encounter detection (EURIS + RWS FIS) with GeoPackage export](https://github.com/TUDelft-CITG/ais-shader/pull/20)
* **Base Branch**: `main`
* **Test Suite**: `99 passed, 4 xfailed, 0 failed`

---

## 4. Key Artifact Locations

| Artifact | Path | Description |
|---|---|---|
| Master GeoPackage | `/scratch-shared/fbaart/data/euris_crawl/euris_encounters.gpkg` | 8-layer Dutch inland encounters dataset in `EPSG:28992` with embedded QGIS styles |
| 30-min EURIS Crawl | `/scratch-shared/fbaart/data/euris_crawl/euris_crawl_20260913_174753.geoparquet` | 36,360 raw fixes across the Netherlands |
| QGIS QML Styles | `docs/styles/*.qml` | Standalone QML style definitions with temporal controller support |
| Example Screenshot | `docs/images/dutch_inland_encounters.png` | QGIS visualization of overtaking encounter on the Amsterdam-Rijnkanaal |
| Mississippi Dataset | `/scratch-shared/fbaart/data/mississippi_1h/` | Complete 1-hour Mississippi reference dataset |
