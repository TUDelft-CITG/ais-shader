# GeoParquet Dataset Specification for `ais-shader`

This document specifies the schema, column definitions, coordinate reference systems (CRS), and directory partitioning expected by the `ais-shader` processing and visualization pipeline.

---

## 1. Geometry & Coordinate Reference Systems (CRS)

| Aspect | Specification | Notes |
|---|---|---|
| **Geometry Column Name** | `geometry` (standard), `geom` (RWS/PostGIS), or `Shape` (US Marine Cadastre WKB) | `ais-shader` standardizes active geometry columns to `geometry`. |
| **Geometry Type** | `Point(x, y)` for raw AIS pings; `LineString` for vessel trajectories; `LineString` (2-point) for segments | 2D coordinates without elevation (Z) or measure (M). |
| **CRS for Trajectorization** | `EPSG:4326` (WGS84 in decimal degrees) | Required for local **Azimuthal Equidistant (AEQD)** projection centering on the first ping of each track to calculate metric distance, area, and speed. |
| **CRS for Rendering & Tiles** | `EPSG:3857` (Web Mercator) | Standard coordinate system for web map tile pyramids (`{z}/{x}/{y}.png`) and Cloud Optimized GeoTIFFs (COG). |

---

## 2. Column Schema & CLI Arguments

`ais-shader` defines a default column name for each field, supports alternative column names that are automatically detected, and allows explicit overrides via CLI arguments across the pipeline.

### 2.1 Essential Trajectory & Spatial Columns

| Default Column Name | Alternative Supported Column Names | CLI Argument | Data Type | Description & Value Range |
|---|---|---|---|---|
| `geometry` | `geom`, `Shape` | *(auto-detected active geometry)* | `Geometry (Point / LineString)` | WKB-encoded 2D geometry in `EPSG:4326` or `EPSG:3857`. |
| `mmsi` | `track_id`, `mmsi_hash` | `--vessel-id-col` | `string` or `int64` | Unique vessel identifier or anonymous tracking hash. |
| `base_date_time` | `timestamp`, `timestamp_recv` | `--time-col` | `timestamp[us, tz=UTC]` / `datetime64[ns, UTC]` | Observation timestamp in UTC. |
| `longitude` | `lon`, `x` | `--x-col` | `float64` | Longitude in decimal degrees ($-180.0 \le x \le 180.0$). |
| `latitude` | `lat`, `y` | `--y-col` | `float64` | Latitude in decimal degrees ($-90.0 \le y \le 90.0$). |
| `sog` | `speed`, `speed_knots` | `--sog-raw-units` *(if 0.1 kn)* | `float64` | Speed Over Ground in knots ($0.0 \le \text{sog} \le 102.2$). |
| `cog` | `course` | — | `float64` | Course Over Ground in degrees ($0.0 \le \text{cog} < 360.0$). |

### 2.2 Vessel Dimensions, Classification & Status

| Default Column Name | Alternative Supported Column Names | CLI Argument | Data Type | Description & Value Range |
|---|---|---|---|---|
| `shiptypeAIS` | `shiptypeais`, `VesselType`, `vessel_type`, `VesselGroup` | `--category-column` *(in render)* | `string` or `int` | AIS ship type code (e.g. `70` = Cargo, `80` = Tanker) or mapped category group name. |
| `draught` | `draft` | — | `float64` | Maximum static draught in meters. |
| `length` | `vessel_length` | — | `float64` | Overall vessel length in meters. |
| `beam` | `width`, `vessel_width` | — | `float64` | Overall vessel beam (width) in meters. |
| `heading` | `true_heading` | — | `float64` | True compass heading ($0.0 \le \text{heading} < 360.0$, `511` = not available). |
| `status` | `navstatus`, `objecttype` | — | `string` or `int` | Navigational status code (`0` = underway using engine, `1` = at anchor, `5` = moored). |

---

## 3. Storage & Partitioning Guidelines

### 3.1 Hive Partitioning

Large multi-day or multi-year AIS collections must be partitioned hierarchically into Hive partition directories:

```text
data/
└── year=2025/
    └── month=01/
        ├── day=02/
        │   └── part-00000.parquet
        ├── day=03/
        │   └── part-00000.parquet
        └── ...
```

* `detect_hive_partitioning()` in `ais_shader.data_loader` automatically discovers `year`, `month`, and `day` partitioning schemes and maps them to PyArrow dataset partition fields.
* Slicing by partition avoids scanning irrelevant time windows (predicate pushdown).

### 3.2 GeoParquet Metadata

Files must follow the [GeoParquet 1.0 or 1.1 Specification](https://geoparquet.org/):
* File-level metadata key: `"geo"` containing JSON with:
  * `"primary_column"`: name of the geometry column (`geometry`, `geom`, or `Shape`).
  * `"columns"`: schema details for the geometry column, including `"encoding": "WKB"`, `"geometry_types"`, and `"crs"`.
* Bounding boxes: Partition-level bounding boxes in file metadata enable spatial index filtering (`ddf.cx[minx:maxx, miny:maxy]`) without loading whole partitions.

---

## 4. Pipeline Stages & File Naming Conventions

`ais-shader` produces intermediate and final datasets following standard naming suffixes:

1. **Raw Input**: `*.parquet` / `*.geoparquet` (point pings in EPSG:4326).
2. **Trajectorized Points**: `<name>-trajectorized.geoparquet`
   * Contains calculated fields: `trip_id`, `time_diff_s`, `speed_mps`, `acceleration_mps2`, `turn_rate_from_cog`.
3. **Aggregated Trajectories**: `<name>-lines.geoparquet`
   * Contains `LineString` or `MultiLineString` geometries per trip.
4. **Point-Pair Segments**: `<name>-segments.geoparquet`
   * Contains 2-point `LineString` geometries connecting consecutive pings for event crossing and proximity interval calculations.
5. **Tile Pyramids**: `tiles/{z}/{x}/{y}.png`
   * Anti-aliased web tile rasters with colormaps and dynamic transparency.
6. **Cloud Optimized GeoTIFF**: `tiles/{z}/cog.tif`
   * Single or multi-band analytical COG with embedded overviews.
