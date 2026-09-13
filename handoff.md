# Session Handoff

## 1. Accomplished Objectives

### A. Inland Encounter Workflow & Documentation
* **Methodology Document**: Created [`docs/encounter_methodology.md`](file:///home/fbaart/src/ais-shader/docs/encounter_methodology.md) with comprehensive theory, mathematical derivations, and CLI examples.
* **Interactive Mermaid Diagram**: Embedded an interactive 7-stage pipeline diagram in `docs/encounter_methodology.md` capturing preprocessing, fairway modeling, stationary vessel handling, spatio-temporal indexing, analytical CPA, fairway classification, and dynamic playback.
* **Native D2 Diagram**: Created [`docs/inland_encounter_workflow.d2`](file:///home/fbaart/src/ais-shader/docs/inland_encounter_workflow.d2) matching the exact visual style, classes, and conventions of [`docs/pipeline.d2`](file:///home/fbaart/src/ais-shader/docs/pipeline.d2).
* **Architecture & Readme Updates**: Updated `docs/architecture.md` (sections 7, 8, 9 for encounters, stationary obstacles, and dynamic time series) and `README.md`.

### B. Resolution of False 110 km "Head-on" Encounter Bug
* **Root Cause Analysis**:
  1. A multi-hour AIS tracking gap (e.g. 2.6 hours for vessel 538011321 and 2.6 hours for vessel 352001671) caused `to-segment` to construct a single ~56–113 km segment bridging the outage.
  2. Because the segment started at `00:02:25`, it slipped past `--end-time 01:00:00`.
  3. Proximity detection set encounter duration to the entire segment duration `[00:04:46, 02:40:21]` (2h 35m) even though proximity ($d \le 600\text{ m}$) only lasted ~54 seconds around CPA (`02:40:21`). Sampling dynamic timeseries at `00:04:46` drew a 101 km line between ships.
* **Mathematical & Algorithmic Fixes**:
  1. **Analytical Proximity Interval**: Added [`compute_proximity_interval`](file:///home/fbaart/src/ais-shader/src/ais_shader/events.py) solving $a\tau^2 + b\tau + (c - D_{\max}^2) \le 0$ to set `start_time` and `end_time` strictly to the exact interval where vessel distance is $\le D_{\max}$.
  2. **Tracking Gap Capping**: Added `max_segment_duration_s` (default 1800s / 30m) across `run_segment_generation`, `detect_encounters`, and `_evaluate_candidates_in_window`.
  3. **Strict Window Post-Filtering**: Ensured encounters are bounded by `cpa_time >= start_time` and `cpa_time < end_time`.
  4. **Strict Timeseries Clamping**: Clamped all connecting lines to $d \le D_{\max}$.
  5. **Attribution & Roles**: Added `source_mmsi`, `target_mmsi`, `role_1`, `role_2`, and propagated `is_stationary_1`, `is_stationary_2`, `stationary_role` to both encounters and timeseries connecting lines.

### C. Clean Single-Line QGIS Symbology (No White Casings)
* **Single-Line Symbology**: Removed all compound white casing/border layers (`pass="0"`) across all line styles (`trajectories.qml`, `segments.qml`, `stationary.qml`, `timeseries.qml`, `fairway_centerline.qml`) per user feedback ("dubbele lijntjes"). Every line layer now renders as a single, clean anti-aliased line.
* **Unified Theme**: All layers share consistent HSV saturation (~0.8), distinct vessel-group hues, and standard transparency scaling.

### D. Resolution of False "Crossing" Classification Outside Fairway Corridor (Encounter 2295)
* **Root Cause Analysis**:
  * Encounter 2295 occurred in Morgan City, LA (Atchafalaya basin / Intracoastal Waterway), **46.4 km** west of the Mississippi fairway centerline.
  * Projecting segments onto the distant Mississippi axis clamped them to a single endpoint, yielding along-fairway speed $v_s = 0\text{ m/s}$ and non-zero cross velocity $v_n$, erroneously tagging vessel 367542760 as `crossing`.
  * The merge step previously discarded encounters initially marked `crossing`, even though relative angle was $6.5^\circ$ and along-track order flipped.
* **Implementation Fixes**:
  1. **Fairway Corridor Boundary**: In [`src/ais_shader/fairway.py`](file:///home/fbaart/src/ais-shader/src/ais_shader/fairway.py), segments with lateral cross-track distance $> 3000\text{ m}$ are tagged as `outside_fairway`.
  2. **Kinematic & COLREGS Fallback**: [`classify_fairway_encounter`](file:///home/fbaart/src/ais-shader/src/ais_shader/fairway.py) receives vessel headings and falls back to kinematic relative heading when either vessel is `outside_fairway`. Vessels with relative heading $\le 45^\circ$ can never be classified as `crossing`.
  3. **Merge Step & Role Assignment**: In [`src/ais_shader/events.py`](file:///home/fbaart/src/ais-shader/src/ais_shader/events.py), `_merge_encounters` evaluates relative heading, allows overtaking for $\text{rel\_angle} \le 45^\circ$ (or flipped order $\le 60^\circ$), and updates `overtaking_mmsi`, `overtaken_mmsi`, `source_mmsi`, `target_mmsi`, and roles (`overtaking` vs `overtaken`), while strictly preserving stationary roles when a vessel is moored/stopped.

### E. Ship Marker & Temporal Expression Enhancements
* **Dot Fallback for Uninstrumented Vessels**: In [`docs/styles/trajectorized_points.qml`](file:///home/fbaart/src/ais-shader/docs/styles/trajectorized_points.qml), if both `heading` and `cog` are missing or invalid ($\ge 360$), the marker shape switches from `arrow` to a circular dot (`circle`) with diameter scaled to vessel beam (`coalesce("beam", 12)` meters).
* **Segment Temporal Expression**: In [`docs/styles/segments.qml`](file:///home/fbaart/src/ais-shader/docs/styles/segments.qml), fixed `mode="4"` temporal expressions for the 15-minute trailing tail:
  * `startExpression="\"segment_start_time\""`
  * `endExpression="\"segment_end_time\" + make_interval(minutes:=15)"`
  * Prevents QGIS `second()` extraction issues (which only returned 0-59 seconds rather than interval duration).

### F. Standard ~100m Distance & Min-Distance Filtering
* **Rationale**: In inland waterways, close encounters, overtakings, and head-on passes occur within $\approx 100\text{ m}$. A 500–600m threshold captures non-interacting traffic across opposite banks or distant bayous.
* **CLI & Pipeline Defaults**: Changed default `--max-distance` in [`src/ais_shader/cli.py`](file:///home/fbaart/src/ais-shader/src/ais_shader/cli.py) and [`src/ais_shader/events.py`](file:///home/fbaart/src/ais-shader/src/ais_shader/events.py) from `500.0` to `100.0` meters.
* **QGIS Layer & Symbology Filtering**:
  * In [`docs/styles/encounters.qml`](file:///home/fbaart/src/ais-shader/docs/styles/encounters.qml): added provider `<subsetString>&quot;min_distance_m&quot; &lt;= 100</subsetString>` and data-defined opacity rule `if("min_distance_m" <= 100, 1.0, 0.0)`.
  * In [`docs/styles/timeseries.qml`](file:///home/fbaart/src/ais-shader/docs/styles/timeseries.qml): added provider `<subsetString>&quot;distance_m&quot; &lt;= 100</subsetString>` and data-defined opacity rule `if("distance_m" <= 100, 1.0, 0.0)`.
  * Both styles immediately focus on the 900 true inland encounters and 4,128 close connecting lines.

### G. Generated Complete Artifact Suites (EPSG:4326 GeoParquet & GeoJSON)

#### 1. Mississippi 1-Hour Corridor (`/scratch-shared/fbaart/data/mississippi_1h/`):
* `mississippi_fairway_centerline.geoparquet` (250 KB) & `.geojson` (673 KB): Continuous B-spline fairway centerline (`EPSG:4326`).
* `mississippi_fairway_mile_markers.geoparquet` (31 KB) & `.geojson` (174 KB): USACE River Mile reference markers (`EPSG:4326`).
* `mississippi_1h_trajectorized_points.geoparquet` (2.52 MB): **45,433 points** across 1,196 vessels.
* `mississippi_1h_trajectories.geoparquet` (0.39 MB): **1,603 continuous LineString trajectories** per trip.
* `mississippi_1h_segments.geoparquet` (1.56 MB): **43,796 2-point line segments** (gaps $> 1800\text{s}$ dropped).
* `mississippi_1h_stationary.geoparquet` (1.46 MB): **34,546 stationary segments** across 1,071 vessels.
* `mississippi_1h_encounters.geoparquet` (0.56 MB): **4,298 encounter events** at exact CPA with `source_mmsi`, `target_mmsi`, roles.
* `mississippi_1h_timeseries.geoparquet` (3.20 MB): **52,713 dynamic connecting lines** (all $\le 600\text{ m}$) with `source_mmsi`, `target_mmsi`, and stationary flags.
* Up-to-date QML styles for all 7 layers.

#### 2. EURIS 10-Minute Live Crawl (`/scratch-shared/fbaart/data/euris_crawl/`):
* `euris_crawl_20260913_121627.geoparquet` (223 KB): **12,007 fixes** across 205 vessels.
* `euris_crawl_20260913_121627_trajectories.geoparquet` (78 KB): **200 continuous LineString trajectories**.
* `euris_crawl_20260913_121627_segments.geoparquet` (129 KB): **11,802 2-point line segments**.
* `euris_crawl_20260913_121627.geojson` (5.4 MB) & `.ndjson` (4.0 MB).

---

## 2. Test Suite Status
* `uv run pytest tests/test_events.py tests/test_fairway.py`: **25 passed, 20 warnings**.
