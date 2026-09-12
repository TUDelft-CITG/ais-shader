import logging
from pathlib import Path
from typing import Optional, Union

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import Point, MultiPoint
import numpy as np

from ais_shader.fairway import FairwayAxis, get_utm_crs_for_lon_lat

logger = logging.getLogger(__name__)

# --- Event table: passage-line crossings & polygon entry/exit ---
#
# Both detection routines below consume the point-pair segment table
# produced by `run_segment_generation` (trajectory to-segment): 2-point
# LineString geometries in EPSG:4326, one row per consecutive point pair
# within a trip, carrying vessel identity/dimensions and
# segment_start_time/segment_end_time/segment_duration_s. Unlike
# analysis.py's process_partition/run_passage_analysis (which aggregate raw
# AIS points into passage line statistics and discard per-crossing detail),
# these keep one output row per event with full vessel/time/location detail.

SEGMENT_VESSEL_COLS = [
    'MMSI', 'trip_id', 'VesselType', 'VesselGroup', 'Length', 'Width', 'Draft', 'sog', 'speed_mps'
]


def _require_columns(gdf: gpd.GeoDataFrame, columns: list, label: str) -> None:
    missing = [c for c in columns if c not in gdf.columns]
    if missing:
        raise KeyError(f"{label} is missing required column(s) {missing}. Available: {list(gdf.columns)}")


def _to_crs_3857(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    return gdf if gdf.crs.to_epsg() == 3857 else gdf.to_crs("EPSG:3857")


def _segment_start_end_coords(geoms: np.ndarray) -> tuple:
    """Start/end coordinate arrays for an array of 2-point LineStrings, vectorized."""
    coords = shapely.get_coordinates(geoms)
    return coords[0::2], coords[1::2]


def _crossing_point_and_fraction(seg_geom, ref_geom) -> tuple:
    """Where a 2-point segment crosses a reference line/boundary, as (Point, fraction along segment).

    Mirrors process_partition's intersection handling (analysis.py, lines
    124-134): a straight 2-point segment against a straight reference line
    normally intersects at a single Point, but defensively falls back to the
    centroid for the rare non-Point result (e.g. the segment grazing a
    polygon corner). Callers only invoke this on segments already known to
    intersect ref_geom (via an `intersects` sjoin), so an empty intersection
    here indicates a bug rather than a real geometric case.
    """
    isect = seg_geom.intersection(ref_geom)
    if isect.is_empty:
        raise ValueError(f"Segment {seg_geom.wkt} does not intersect reference geometry {ref_geom.wkt}")
    point = isect if isinstance(isect, Point) else isect.centroid
    f_seg = seg_geom.project(point, normalized=True)
    return point, f_seg


def _interpolate_time(start_time, duration_s: float, f_seg: float) -> pd.Timestamp:
    return start_time + pd.to_timedelta(f_seg * duration_s, unit='s')


def detect_line_crossings(segments_gdf: gpd.GeoDataFrame, passage_lines_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Detect where AIS trajectory segments cross reference passage lines.

    One output row per crossing: vessel identity/dimensions (from the
    segment), the passage line crossed (PassageId), direction ('up'/'down',
    via the same normal-vector convention as process_partition/
    run_passage_analysis in analysis.py), the interpolated crossing time
    (segment_start_time + fraction-along-segment * segment_duration_s --
    process_partition never computes this, only an interpolated speed), and
    the exact intersection point as a 1-point MultiPoint (EPSG:4326), for
    schema parity with detect_polygon_entry_exit's 1-2 point events.
    """
    _require_columns(segments_gdf, SEGMENT_VESSEL_COLS + ['segment_start_time', 'segment_duration_s'], "segments_gdf")
    _require_columns(passage_lines_gdf, ['PassageId'], "passage_lines_gdf")

    empty_cols = SEGMENT_VESSEL_COLS + ['PassageId', 'direction', 'event_time']
    if segments_gdf.empty:
        return gpd.GeoDataFrame({c: [] for c in empty_cols}, geometry=[], crs="EPSG:4326")

    segments_3857 = _to_crs_3857(segments_gdf)
    # Keep only the columns we need from the reference file: passage-line
    # datasets (e.g. EuRIS PassageLine exports) carry their own rich
    # property set, which can collide with segment column names (e.g. both
    # have a "Length" -- the passage line's own physical length vs. the
    # vessel's) and get silently suffixed by gpd.sjoin instead of erroring.
    passage_lines_3857 = _to_crs_3857(passage_lines_gdf)[['PassageId', 'geometry']].reset_index(drop=True)

    # Precompute each passage line's own direction vector (line's own
    # start->end), used below to classify crossing direction.
    coords_list = [g.coords for g in passage_lines_3857.geometry]
    passage_lines_3857['L_x'] = np.array([c[-1][0] - c[0][0] for c in coords_list])
    passage_lines_3857['L_y'] = np.array([c[-1][1] - c[0][1] for c in coords_list])

    joined = gpd.sjoin(segments_3857, passage_lines_3857, predicate='intersects', how='inner')
    if joined.empty:
        return gpd.GeoDataFrame({c: [] for c in empty_cols}, geometry=[], crs="EPSG:4326")

    seg_geoms = joined.geometry.values
    pass_geoms = passage_lines_3857.geometry.loc[joined['index_right']].values

    points_3857, f_segs = [], []
    for seg_geom, pass_geom in zip(seg_geoms, pass_geoms):
        point, f_seg = _crossing_point_and_fraction(seg_geom, pass_geom)
        points_3857.append(point)
        f_segs.append(f_seg)
    f_seg_arr = np.array(f_segs, dtype=float)

    # Classify crossing direction via the sign of the cross product between
    # the segment's own vector (S) and each passage line's start->end vector
    # (L): rotating L by -90 degrees gives its right-hand normal, and the dot
    # of S with that normal is >=0 when the segment crosses left-to-right of
    # L's direction ('down'), negative when right-to-left ('up'). This is the
    # same normal-vector convention process_partition uses in analysis.py.
    starts, ends = _segment_start_end_coords(seg_geoms)
    S_x, S_y = ends[:, 0] - starts[:, 0], ends[:, 1] - starts[:, 1]
    L_x_val = passage_lines_3857['L_x'].loc[joined['index_right']].values
    L_y_val = passage_lines_3857['L_y'].loc[joined['index_right']].values
    dot_product = S_x * (-L_y_val) + S_y * L_x_val
    direction = np.where(dot_product >= 0, 'down', 'up')

    segment_start_time = pd.to_datetime(joined['segment_start_time'].values)
    segment_duration_s = joined['segment_duration_s'].values
    event_time = _interpolate_time(segment_start_time, segment_duration_s, f_seg_arr)

    points_4326 = gpd.GeoSeries(points_3857, crs="EPSG:3857").to_crs("EPSG:4326")
    geometry = [MultiPoint([pt]) for pt in points_4326]

    data = {col: joined[col].values for col in SEGMENT_VESSEL_COLS}
    data['PassageId'] = joined['PassageId'].values
    data['direction'] = direction
    data['event_time'] = event_time
    events_gdf = gpd.GeoDataFrame(data, geometry=geometry, crs="EPSG:4326")
    events_gdf = events_gdf.reset_index(drop=True)
    return events_gdf


def detect_polygon_entry_exit(
    segments_gdf: gpd.GeoDataFrame,
    polygons_gdf: gpd.GeoDataFrame,
    polygon_id_col: str = "name",
    merge_gap_minutes: float = None,
) -> gpd.GeoDataFrame:
    """
    Detect vessel entry into (and potential exit from) polygon "event polygons".

    A vessel may spend many consecutive segments inside a polygon; entry is the
    outside->inside transition of a single segment's endpoints, exit is the
    next inside->outside transition for the same trip_id within the same
    polygon. Segments that don't touch a polygon at all can't be part of a
    transition and are filtered out up front via `gpd.sjoin` (backed by each
    GeoDataFrame's spatial index), so only segments whose 2-point line
    intersects, enters, or exits a polygon are considered -- this also means we
    never need the full point-by-point trajectory, just the segments that
    already touch the polygon.

    One output row per entry: geometry is a MultiPoint with the entry point
    alone if no matching exit was found before the trip's segments ran out
    (e.g. the AIS window ends while the vessel is still inside), or the
    entry and exit points together (2 points) otherwise. A trip may produce
    several separate events per polygon if it enters and exits more than once.

    merge_gap_minutes : float, optional
        AIS reception inside enclosed spaces like lock chambers is often
        noisy: a vessel sitting still can flicker in and out of the polygon
        boundary many times in a few minutes as fixes jitter, producing a
        burst of short, spurious entry/exit pairs for what is really a
        single visit. If set, consecutive events for the same (MMSI,
        polygon) are merged whenever the gap between one event's exit_time
        and the next event's entry_time is within this many minutes -- the
        merged event keeps the first entry_time, the last exit_time, the
        entry point of the first visit, and the exit point of the last visit.
    """
    _require_columns(
        segments_gdf, SEGMENT_VESSEL_COLS + ['segment_start_time', 'segment_duration_s'], "segments_gdf"
    )
    _require_columns(polygons_gdf, [polygon_id_col], "polygons_gdf")

    empty_cols = SEGMENT_VESSEL_COLS + [polygon_id_col, 'entry_time', 'exit_time']
    if segments_gdf.empty:
        return gpd.GeoDataFrame({c: [] for c in empty_cols}, geometry=[], crs="EPSG:4326")

    segments_3857 = _to_crs_3857(segments_gdf)
    # As in detect_line_crossings: keep only the id + geometry columns from
    # the reference file to avoid gpd.sjoin silently suffixing any column
    # name shared with the segment table.
    polygons_3857 = _to_crs_3857(polygons_gdf)[[polygon_id_col, 'geometry']].reset_index(drop=True)
    boundaries = polygons_3857.geometry.boundary

    joined = gpd.sjoin(segments_3857, polygons_3857, predicate='intersects', how='inner')
    if joined.empty:
        return gpd.GeoDataFrame({c: [] for c in empty_cols}, geometry=[], crs="EPSG:4326")

    seg_geoms = joined.geometry.values
    polygon_geoms = polygons_3857.geometry.loc[joined['index_right']].values
    starts, ends = _segment_start_end_coords(seg_geoms)

    start_inside = shapely.covers(polygon_geoms, shapely.points(starts))
    end_inside = shapely.covers(polygon_geoms, shapely.points(ends))

    joined = joined.assign(
        _start_inside=start_inside,
        _end_inside=end_inside,
        _polygon_key=polygons_3857[polygon_id_col].loc[joined['index_right']].values,
    )
    # Only segments whose endpoints actually straddle the boundary are
    # transitions; fully-inside segments (mid-dwell) carry no new event.
    transitions = joined[joined['_start_inside'] != joined['_end_inside']].copy()
    if transitions.empty:
        return gpd.GeoDataFrame({c: [] for c in empty_cols}, geometry=[], crs="EPSG:4326")

    boundary_geoms = boundaries.loc[transitions['index_right']].values
    trans_seg_geoms = transitions.geometry.values
    points_3857, f_segs = [], []
    for seg_geom, boundary_geom in zip(trans_seg_geoms, boundary_geoms):
        point, f_seg = _crossing_point_and_fraction(seg_geom, boundary_geom)
        points_3857.append(point)
        f_segs.append(f_seg)
    transitions['_point_3857'] = points_3857
    transitions_start_time = pd.to_datetime(transitions['segment_start_time'].values)
    transitions_duration_s = transitions['segment_duration_s'].values
    f_seg_arr = np.array(f_segs, dtype=float)
    transitions['_event_time'] = _interpolate_time(transitions_start_time, transitions_duration_s, f_seg_arr)
    transitions['_is_entry'] = ~transitions['_start_inside'] & transitions['_end_inside']
    transitions = transitions.sort_values(['_polygon_key', 'trip_id', 'segment_start_time'])

    events = _pair_polygon_transitions(transitions)
    if not events:
        return gpd.GeoDataFrame({c: [] for c in empty_cols}, geometry=[], crs="EPSG:4326")

    data, geometry = _build_polygon_event_table(events, polygon_id_col)
    events_gdf = gpd.GeoDataFrame(data, geometry=geometry, crs="EPSG:4326")
    events_gdf = events_gdf.reset_index(drop=True)

    if merge_gap_minutes is not None:
        events_gdf = _merge_close_polygon_events(events_gdf, polygon_id_col, merge_gap_minutes)

    return events_gdf


def _pair_polygon_transitions(transitions: pd.DataFrame) -> list:
    """Pair each entry transition with the next matching exit for the same (polygon, trip).

    transitions must be sorted by (_polygon_key, trip_id, segment_start_time)
    and carry the _is_entry/_point_3857/_event_time columns computed above.
    """
    events = []
    open_entry = None
    for _, row in transitions.iterrows():
        key = (row['_polygon_key'], row['trip_id'])
        if open_entry is not None and open_entry['key'] != key:
            events.append(_finish_polygon_event(open_entry, exit_row=None))
            open_entry = None

        if row['_is_entry']:
            if open_entry is not None:
                # A second entry before an exit was seen for the same
                # trip/polygon (e.g. a concave polygon boundary graze) -- flush the
                # unmatched entry as entry-only before starting the new one.
                events.append(_finish_polygon_event(open_entry, exit_row=None))
            open_entry = {'key': key, 'row': row}
        else:
            if open_entry is not None and open_entry['key'] == key:
                events.append(_finish_polygon_event(open_entry, exit_row=row))
                open_entry = None
            else:
                # An exit with no open entry means the trip started already
                # inside the polygon (no earlier segment to detect entry from) --
                # nothing to pair it with, so it's dropped.
                pass

    if open_entry is not None:
        events.append(_finish_polygon_event(open_entry, exit_row=None))

    return events


def _finish_polygon_event(open_entry: dict, exit_row) -> tuple:
    entry_row = open_entry['row']
    points_3857 = [entry_row['_point_3857']]
    if exit_row is not None:
        points_3857.append(exit_row['_point_3857'])
    return entry_row, exit_row, points_3857


def _build_polygon_event_table(events: list, polygon_id_col: str) -> tuple:
    """Flatten (entry_row, exit_row, points_3857) events into GeoDataFrame data + geometry columns."""
    data = {col: [] for col in SEGMENT_VESSEL_COLS}
    data[polygon_id_col] = []
    data['entry_time'] = []
    data['exit_time'] = []
    geometry = []
    for entry_row, exit_row, points_3857_pair in events:
        for col in SEGMENT_VESSEL_COLS:
            data[col].append(entry_row[col])
        data[polygon_id_col].append(entry_row['_polygon_key'])
        data['entry_time'].append(entry_row['_event_time'])
        data['exit_time'].append(exit_row['_event_time'] if exit_row is not None else pd.NaT)
        points_4326 = gpd.GeoSeries(points_3857_pair, crs="EPSG:3857").to_crs("EPSG:4326")
        geometry.append(MultiPoint(list(points_4326)))
    return data, geometry


def _merge_close_polygon_events(
    events_gdf: gpd.GeoDataFrame, polygon_id_col: str, merge_gap_minutes: float
) -> gpd.GeoDataFrame:
    """Tie together consecutive same-vessel/same-polygon events separated by a short gap.

    Repeated brief entry/exit flicker (e.g. AIS jitter in a lock chamber
    while a vessel is actually sitting still) shows up as a run of
    short-lived events for the same (MMSI, polygon) in quick succession.
    Events are merged in chronological order whenever the previous event has
    a known exit_time and the next event's entry_time follows within
    merge_gap_minutes; an event left open (exit_time is NaT, e.g. the AIS
    window ended while still inside) can't be compared to what follows, so it
    always closes out the current merge run.
    """
    if events_gdf.empty:
        return events_gdf

    def _finalize(rec: dict) -> dict:
        points = [rec.pop('_first_point')]
        last_point = rec.pop('_last_point')
        if pd.notna(rec['exit_time']):
            points.append(last_point)
        rec['geometry'] = MultiPoint(points)
        return rec

    gap = pd.Timedelta(minutes=merge_gap_minutes)
    sortable = events_gdf.sort_values(['MMSI', polygon_id_col, 'entry_time'])

    merged_records = []
    current = None
    for _, row in sortable.iterrows():
        if (
            current is not None
            and row['MMSI'] == current['MMSI']
            and row[polygon_id_col] == current[polygon_id_col]
            and pd.notna(current['exit_time'])
            and (row['entry_time'] - current['exit_time']) <= gap
        ):
            current['exit_time'] = row['exit_time']
            current['_last_point'] = list(row['geometry'].geoms)[-1]
        else:
            if current is not None:
                merged_records.append(_finalize(current))
            current = row.to_dict()
            row_points = list(row['geometry'].geoms)
            current['_first_point'] = row_points[0]
            current['_last_point'] = row_points[-1]
    if current is not None:
        merged_records.append(_finalize(current))

    merged_gdf = gpd.GeoDataFrame(merged_records, columns=events_gdf.columns, crs=events_gdf.crs)
    return merged_gdf.reset_index(drop=True)


def _read_vector_file(path: Path) -> gpd.GeoDataFrame:
    """Read a reference vector file, dispatching to gpd.read_parquet for (Geo)Parquet and gpd.read_file otherwise."""
    if path.suffix.lower() in {".parquet", ".geoparquet"}:
        return gpd.read_parquet(path)
    return gpd.read_file(path)


def run_line_crossing_detection(segments_file: Path, passage_file: Path, output_file: Path) -> None:
    """CLI/script entry point: detect_line_crossings, reading/writing GeoParquet/GeoJSON files."""
    logger.info(f"Loading segments from {segments_file}...")
    segments_gdf = gpd.read_parquet(segments_file)

    logger.info(f"Loading passage lines from {passage_file}...")
    passage_lines_gdf = _read_vector_file(passage_file)
    if passage_lines_gdf.crs is None:
        passage_lines_gdf = passage_lines_gdf.set_crs("EPSG:4326")

    logger.info("Detecting line crossings...")
    events_gdf = detect_line_crossings(segments_gdf, passage_lines_gdf)
    logger.info(f"Found {len(events_gdf):,} crossing events.")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Saving events to {output_file}...")
    events_gdf.to_parquet(output_file)


def run_polygon_entry_exit_detection(
    segments_file: Path,
    polygons_file: Path,
    output_file: Path,
    polygon_id_col: str = "name",
    merge_gap_minutes: float = None,
) -> None:
    """CLI/script entry point: detect_polygon_entry_exit, reading/writing GeoParquet/GeoJSON files."""
    logger.info(f"Loading segments from {segments_file}...")
    segments_gdf = gpd.read_parquet(segments_file)

    logger.info(f"Loading reference polygons from {polygons_file}...")
    polygons_gdf = _read_vector_file(polygons_file)
    if polygons_gdf.crs is None:
        polygons_gdf = polygons_gdf.set_crs("EPSG:4326")

    logger.info("Detecting polygon entry/exit events...")
    events_gdf = detect_polygon_entry_exit(
        segments_gdf, polygons_gdf, polygon_id_col=polygon_id_col, merge_gap_minutes=merge_gap_minutes
    )
    logger.info(f"Found {len(events_gdf):,} events.")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Saving events to {output_file}...")
    events_gdf.to_parquet(output_file)


# --- Encounter detection: crossings, overtakings, head-on meetings ---

ENCOUNTER_COLS = [
    'encounter_id',
    'mmsi_1', 'mmsi_2', 'trip_id_1', 'trip_id_2',
    'vessel_type_1', 'vessel_type_2', 'vessel_group_1', 'vessel_group_2',
    'length_1', 'length_2', 'width_1', 'width_2',
    'sog_1', 'sog_2', 'speed_mps_1', 'speed_mps_2',
    'heading_1', 'heading_2',
    'start_time', 'end_time', 'cpa_time', 'min_distance_m',
    'encounter_type',
    'overtaking_mmsi', 'overtaken_mmsi',
]


def classify_encounter(
    heading1: float,
    heading2: float,
    speed1: float = None,
    speed2: float = None,
    ds_start: float = None,
    ds_end: float = None,
    dv_along: float = None,
    min_moving_speed: float = 0.5,
    min_overtaking_speed_diff: float = 0.5,
) -> str:
    """
    Classify encounter between two vessels based on relative course and along-track passing.

    Returns:
      'head-on'          : opposite courses (135 <= rel_angle <= 225)
      'overtaking'       : same general direction (rel_angle <= 45) AND along-track passing occurs
                           (ds_start * ds_end <= 0 or active passing with speed differential),
                           or defaults to 'overtaking' when ds_start/ds_end are not provided
      'parallel_sailing' : same general direction (rel_angle <= 45) without passing (co-sailing abreast)
      'crossing'         : courses intersecting at an angle (45 < rel_angle < 135 or 225 < rel_angle < 315)
      'stationary'       : both vessels are stationary/moored (< min_moving_speed m/s)
    """
    if speed1 is not None and speed2 is not None:
        if speed1 < min_moving_speed and speed2 < min_moving_speed:
            return "stationary"

    diff = (heading1 - heading2) % 360.0
    rel_angle = min(diff, 360.0 - diff)

    if rel_angle <= 45.0:
        if ds_start is not None and ds_end is not None:
            order_flipped = (ds_start * ds_end < -1.0)
            has_speed_diff = (dv_along is not None and abs(dv_along) >= min_overtaking_speed_diff)
            if order_flipped or (has_speed_diff and (ds_start * ds_end <= 0.0 and abs(ds_start - ds_end) > 1.0)):
                return "overtaking"
            else:
                return "parallel_sailing"
        return "overtaking"
    elif rel_angle >= 135.0:
        return "head-on"
    else:
        return "crossing"


def compute_segment_cpa(
    p1_start: np.ndarray,
    p1_end: np.ndarray,
    t1_start: pd.Timestamp,
    t1_end: pd.Timestamp,
    p2_start: np.ndarray,
    p2_end: np.ndarray,
    t2_start: pd.Timestamp,
    t2_end: pd.Timestamp,
) -> tuple:
    """
    Analytically compute Closest Point of Approach (CPA) between two time-overlapping moving segments.

    Coordinates must be in a projected planar CRS (e.g. EPSG:3857, in meters).

    Returns:
        (cpa_dist_m, cpa_time, p1_cpa, p2_cpa, mid_cpa, heading1, heading2, speed1, speed2,
         encounter_type, ds_start, ds_end, dv_along)
        or None if there is no temporal overlap.
    """
    t_start = max(t1_start, t2_start)
    t_end = min(t1_end, t2_end)
    if t_start >= t_end:
        return None

    dt_total = (t_end - t_start).total_seconds()

    dur1 = (t1_end - t1_start).total_seconds()
    dur2 = (t2_end - t2_start).total_seconds()

    v1 = (p1_end - p1_start) / dur1 if dur1 > 1e-6 else np.zeros(2)
    v2 = (p2_end - p2_start) / dur2 if dur2 > 1e-6 else np.zeros(2)

    speed1 = float(np.hypot(v1[0], v1[1]))
    speed2 = float(np.hypot(v2[0], v2[1]))

    heading1 = float((np.degrees(np.arctan2(v1[0], v1[1])) + 360.0) % 360.0)
    heading2 = float((np.degrees(np.arctan2(v2[0], v2[1])) + 360.0) % 360.0)

    offset1 = (t_start - t1_start).total_seconds()
    offset2 = (t_start - t2_start).total_seconds()

    r1_0 = p1_start + v1 * offset1
    r2_0 = p2_start + v2 * offset2

    R0 = r1_0 - r2_0
    delta_v = v1 - v2

    a = float(np.dot(delta_v, delta_v))
    b = float(2.0 * np.dot(R0, delta_v))
    c = float(np.dot(R0, R0))

    if a > 1e-12:
        tau_star = -b / (2.0 * a)
        tau_cpa = max(0.0, min(dt_total, tau_star))
    else:
        tau_cpa = 0.0 if (c <= c + b * dt_total) else dt_total

    cpa_time = t_start + pd.to_timedelta(tau_cpa, unit='s')
    p1_cpa = r1_0 + v1 * tau_cpa
    p2_cpa = r2_0 + v2 * tau_cpa
    mid_cpa = (p1_cpa + p2_cpa) / 2.0
    diff_cpa = p1_cpa - p2_cpa
    cpa_dist_m = float(np.hypot(diff_cpa[0], diff_cpa[1]))

    # Along-track projection: along shared average heading
    v_mean = v1 + v2
    v_mean_norm = float(np.hypot(v_mean[0], v_mean[1]))
    if v_mean_norm > 1e-3:
        u_track = v_mean / v_mean_norm
        ds_start = float(np.dot(R0, u_track))
        r_end = (r1_0 + v1 * dt_total) - (r2_0 + v2 * dt_total)
        ds_end = float(np.dot(r_end, u_track))
        dv_along = float(np.dot(delta_v, u_track))
    else:
        ds_start = 0.0
        ds_end = 0.0
        dv_along = 0.0

    enc_type = classify_encounter(
        heading1, heading2, speed1, speed2,
        ds_start=ds_start, ds_end=ds_end, dv_along=dv_along,
    )

    return (cpa_dist_m, cpa_time, p1_cpa, p2_cpa, mid_cpa, heading1, heading2, speed1, speed2,
            enc_type, ds_start, ds_end, dv_along)


def detect_encounters(
    segments_gdf: gpd.GeoDataFrame,
    max_distance_m: float = 500.0,
    time_bin_minutes: float = 60.0,
    merge_gap_minutes: float = 10.0,
    fairway_axis: Optional[FairwayAxis] = None,
    metric_crs: Optional[str] = None,
) -> gpd.GeoDataFrame:
    """
    Detect encounters (crossings, overtakings, head-on meetings) between vessels.

    Uses a spatio-temporal index (temporal binning + Shapely STRtree) to efficiently
    find pairs of vessel segments within max_distance_m and overlapping in time.
    For each candidate, calculates the exact Closest Point of Approach (CPA) distance
    and time, classifies the encounter, and optionally merges consecutive proximity
    records for the same vessel dyad into a single encounter event.

    When fairway_axis is supplied, encounter classification uses the 1D along-fairway
    progression (eliminating river bend compass heading false-crossings), and outputs
    are enriched with river_mile and cross_track_m coordinates.
    """
    _require_columns(segments_gdf, ['MMSI', 'trip_id', 'segment_start_time'], "segments_gdf")

    if segments_gdf.empty:
        return gpd.GeoDataFrame({c: [] for c in ENCOUNTER_COLS}, geometry=[], crs="EPSG:4326")

    gdf = segments_gdf.copy()
    if fairway_axis is not None and 'fairway_direction' not in gdf.columns:
        logger.info("Annotating segments with fairway axis kinematics...")
        gdf = fairway_axis.annotate_segments(gdf)
    if 'segment_end_time' not in gdf.columns:
        if 'segment_duration_s' in gdf.columns:
            gdf['segment_end_time'] = gdf['segment_start_time'] + pd.to_timedelta(gdf['segment_duration_s'], unit='s')
        else:
            raise KeyError("segments_gdf must have 'segment_end_time' or 'segment_duration_s'.")

    gdf['segment_start_time'] = pd.to_datetime(gdf['segment_start_time'])
    gdf['segment_end_time'] = pd.to_datetime(gdf['segment_end_time'])

    t_min = gdf['segment_start_time'].min()
    t_max = gdf['segment_end_time'].max()
    if pd.isna(t_min) or pd.isna(t_max):
        return gpd.GeoDataFrame({c: [] for c in ENCOUNTER_COLS}, geometry=[], crs="EPSG:4326")

    if metric_crs is None:
        if fairway_axis is not None:
            metric_crs = fairway_axis.metric_crs
        elif gdf.crs is not None and gdf.crs.is_projected:
            metric_crs = str(gdf.crs)
        else:
            bounds = gdf.total_bounds
            mean_lon = float((bounds[0] + bounds[2]) / 2.0)
            mean_lat = float((bounds[1] + bounds[3]) / 2.0)
            metric_crs = get_utm_crs_for_lon_lat(mean_lon, mean_lat)

    segments_metric = gdf.to_crs(metric_crs).reset_index(drop=True)
    geoms_metric = segments_metric.geometry.values
    starts, ends = _segment_start_end_coords(geoms_metric)

    start_times = gdf['segment_start_time'].values
    end_times = gdf['segment_end_time'].values
    mmsi_vals = gdf['MMSI'].astype(str).values

    # Spatio-temporal index: partition time into windows to prune non-coincident pairs,
    # then query Shapely STRtree within each window for spatial proximity.
    bin_delta = pd.Timedelta(minutes=time_bin_minutes)
    n_bins = int(np.ceil((t_max - t_min) / bin_delta)) + 1

    candidate_pairs = set()
    for bin_idx in range(n_bins):
        bin_t0 = t_min + bin_idx * bin_delta
        bin_t1 = bin_t0 + bin_delta

        in_bin = (start_times <= bin_t1.to_datetime64()) & (end_times >= bin_t0.to_datetime64())
        indices = np.where(in_bin)[0]
        if len(indices) < 2:
            continue

        bin_geoms = geoms_metric[indices]
        tree = shapely.STRtree(bin_geoms)
        i_sub, j_sub = tree.query(bin_geoms, predicate='dwithin', distance=max_distance_m)

        valid_mask = i_sub < j_sub
        orig_i = indices[i_sub[valid_mask]]
        orig_j = indices[j_sub[valid_mask]]

        for idx_a, idx_b in zip(orig_i, orig_j):
            if mmsi_vals[idx_a] != mmsi_vals[idx_b]:
                if idx_a < idx_b:
                    candidate_pairs.add((idx_a, idx_b))
                else:
                    candidate_pairs.add((idx_b, idx_a))

    if not candidate_pairs:
        return gpd.GeoDataFrame({c: [] for c in ENCOUNTER_COLS}, geometry=[], crs="EPSG:4326")

    raw_records = []
    for idx_a, idx_b in candidate_pairs:
        # Enforce canonical MMSI ordering: vessel 1 has smaller MMSI
        if mmsi_vals[idx_a] <= mmsi_vals[idx_b]:
            idx1, idx2 = idx_a, idx_b
        else:
            idx1, idx2 = idx_b, idx_a

        t1_s = pd.Timestamp(start_times[idx1])
        t1_e = pd.Timestamp(end_times[idx1])
        t2_s = pd.Timestamp(start_times[idx2])
        t2_e = pd.Timestamp(end_times[idx2])

        cpa_res = compute_segment_cpa(
            starts[idx1], ends[idx1], t1_s, t1_e,
            starts[idx2], ends[idx2], t2_s, t2_e
        )
        if cpa_res is None:
            continue

        cpa_dist_m, cpa_time, p1_cpa, p2_cpa, mid_cpa, heading1, heading2, speed1, speed2, enc_type, ds_start, ds_end, dv_along = cpa_res
        if cpa_dist_m > max_distance_m:
            continue

        row1 = gdf.iloc[idx1]
        row2 = gdf.iloc[idx2]

        if fairway_axis is not None:
            dir1 = row1.get('fairway_direction')
            dir2 = row2.get('fairway_direction')
            if dir1 and dir2:
                enc_type = fairway_axis.classify_fairway_encounter(
                    dir1, dir2, speed1, speed2, ds_start, ds_end, dv_along
                )

        overtaking_mmsi = None
        overtaken_mmsi = None
        if enc_type == 'overtaking':
            if fairway_axis is not None and 'fairway_speed_mps' in row1 and 'fairway_speed_mps' in row2:
                v_f1 = abs(float(row1.get('fairway_speed_mps', speed1)))
                v_f2 = abs(float(row2.get('fairway_speed_mps', speed2)))
                if v_f1 >= v_f2:
                    overtaking_mmsi = mmsi_vals[idx1]
                    overtaken_mmsi = mmsi_vals[idx2]
                else:
                    overtaking_mmsi = mmsi_vals[idx2]
                    overtaken_mmsi = mmsi_vals[idx1]
            elif dv_along > 0:
                overtaking_mmsi = mmsi_vals[idx1]
                overtaken_mmsi = mmsi_vals[idx2]
            else:
                overtaking_mmsi = mmsi_vals[idx2]
                overtaken_mmsi = mmsi_vals[idx1]

        rec = {
            'mmsi_1': mmsi_vals[idx1],
            'mmsi_2': mmsi_vals[idx2],
            'trip_id_1': row1.get('trip_id'),
            'trip_id_2': row2.get('trip_id'),
            'vessel_type_1': row1.get('VesselType'),
            'vessel_type_2': row2.get('VesselType'),
            'vessel_group_1': row1.get('VesselGroup'),
            'vessel_group_2': row2.get('VesselGroup'),
            'length_1': row1.get('Length', np.nan),
            'length_2': row2.get('Length', np.nan),
            'width_1': row1.get('Width', np.nan),
            'width_2': row2.get('Width', np.nan),
            'sog_1': row1.get('sog', np.nan),
            'sog_2': row2.get('sog', np.nan),
            'speed_mps_1': speed1,
            'speed_mps_2': speed2,
            'heading_1': heading1,
            'heading_2': heading2,
            'start_time': max(t1_s, t2_s),
            'end_time': min(t1_e, t2_e),
            'cpa_time': cpa_time,
            'min_distance_m': cpa_dist_m,
            'encounter_type': enc_type,
            'overtaking_mmsi': overtaking_mmsi,
            'overtaken_mmsi': overtaken_mmsi,
            '_mid_x': float(mid_cpa[0]),
            '_mid_y': float(mid_cpa[1]),
            '_ds_start': ds_start,
            '_ds_end': ds_end,
            '_dv_along': dv_along,
        }
        raw_records.append(rec)

    if not raw_records:
        return gpd.GeoDataFrame({c: [] for c in ENCOUNTER_COLS}, geometry=[], crs="EPSG:4326")

    raw_df = pd.DataFrame(raw_records)

    # Merge consecutive proximity alerts for the same vessel dyad
    if merge_gap_minutes is not None:
        merged_records = _merge_encounters(raw_df, merge_gap_minutes)
    else:
        merged_records = raw_df.to_dict('records')

    for i, r in enumerate(merged_records):
        r['encounter_id'] = i

    mid_points_4326 = gpd.GeoSeries(
        gpd.points_from_xy(
            [r['_mid_x'] for r in merged_records],
            [r['_mid_y'] for r in merged_records],
            crs=metric_crs
        ),
        crs=metric_crs
    ).to_crs("EPSG:4326")

    if fairway_axis is not None and merged_records:
        pts_metric = mid_points_4326.to_crs(fairway_axis.metric_crs).values
        _, r_miles, c_tracks = fairway_axis.project_geometries(pts_metric)
        river_miles = np.round(r_miles, 2).tolist()
        cross_tracks = np.round(c_tracks, 1).tolist()
        result_data = {c: [r[c] for r in merged_records] for c in ENCOUNTER_COLS}
        result_data['river_mile'] = river_miles
        result_data['cross_track_m'] = cross_tracks
    else:
        result_data = {c: [r[c] for r in merged_records] for c in ENCOUNTER_COLS}

    events_gdf = gpd.GeoDataFrame(result_data, geometry=mid_points_4326, crs="EPSG:4326")
    return events_gdf.reset_index(drop=True)


def _merge_encounters(raw_df: pd.DataFrame, merge_gap_minutes: float) -> list:
    """Group consecutive encounter records between the same two vessels within merge_gap_minutes."""
    gap = pd.Timedelta(minutes=merge_gap_minutes)
    sortable = raw_df.sort_values(['mmsi_1', 'mmsi_2', 'start_time'])

    merged = []
    current_group = []

    def _flush_group(group: list) -> dict:
        # Pick the record that achieved the absolute minimum CPA distance
        best_rec = min(group, key=lambda r: r['min_distance_m'])
        res = dict(best_rec)
        res['start_time'] = min(r['start_time'] for r in group)
        res['end_time'] = max(r['end_time'] for r in group)

        # Refine encounter classification across the entire group span
        initial_ds = group[0]['_ds_start']
        final_ds = group[-1]['_ds_end']
        overall_order_flipped = (initial_ds * final_ds < -1.0) or any(r['_ds_start'] * r['_ds_end'] < -1.0 for r in group)
        overall_dv_along = best_rec.get('_dv_along', 0.0)

        enc_type = best_rec['encounter_type']
        overtaking_mmsi = None
        overtaken_mmsi = None

        if enc_type in {'overtaking', 'parallel_sailing'}:
            has_speed_diff = abs(overall_dv_along) >= 0.5
            if overall_order_flipped or (has_speed_diff and (initial_ds * final_ds <= 0.0 and abs(initial_ds - final_ds) > 1.0)):
                enc_type = 'overtaking'
                if overall_dv_along > 0:
                    overtaking_mmsi = best_rec['mmsi_1']
                    overtaken_mmsi = best_rec['mmsi_2']
                else:
                    overtaking_mmsi = best_rec['mmsi_2']
                    overtaken_mmsi = best_rec['mmsi_1']
            else:
                enc_type = 'parallel_sailing'

        res['encounter_type'] = enc_type
        res['overtaking_mmsi'] = overtaking_mmsi
        res['overtaken_mmsi'] = overtaken_mmsi
        return res

    for _, row in sortable.iterrows():
        rec = row.to_dict()
        if not current_group:
            current_group.append(rec)
            continue

        prev = current_group[-1]
        same_pair = (rec['mmsi_1'] == prev['mmsi_1']) and (rec['mmsi_2'] == prev['mmsi_2'])
        time_close = (rec['start_time'] - prev['end_time']) <= gap

        if same_pair and time_close:
            current_group.append(rec)
        else:
            merged.append(_flush_group(current_group))
            current_group = [rec]

    if current_group:
        merged.append(_flush_group(current_group))

    return merged



TIMESERIES_COLS = [
    'encounter_id',
    'encounter_type',
    'mmsi_1',
    'mmsi_2',
    'timestamp',
    'distance_m',
    'is_cpa',
]


def _build_trajectory_lookup(segments_gdf: gpd.GeoDataFrame, max_gap_seconds: float = 600.0) -> dict:
    """
    Build piecewise-linear trajectory lookup tables for all vessels in segments_gdf.

    Returns a dict mapping MMSI string to a tuple:
        (t_starts_ns, t_ends_ns, p_starts, p_ends)
    where p_starts and p_ends are Nx2 arrays of coordinates in EPSG:4326.
    """
    _require_columns(segments_gdf, ['MMSI', 'segment_start_time', 'segment_end_time'], "segments_gdf")
    lookup = {}
    grouped = segments_gdf.groupby('MMSI')

    for mmsi, group in grouped:
        sorted_group = group.sort_values('segment_start_time')
        coords = shapely.get_coordinates(sorted_group.geometry.values)
        if len(coords) < 2:
            continue
        c_start = coords[0::2]
        c_end = coords[1::2]

        t_starts = pd.to_datetime(sorted_group['segment_start_time']).values.astype('datetime64[ns]').astype(np.int64)
        t_ends = pd.to_datetime(sorted_group['segment_end_time']).values.astype('datetime64[ns]').astype(np.int64)

        all_starts = []
        all_ends = []
        all_p_start = []
        all_p_end = []

        n = len(sorted_group)
        for i in range(n):
            all_starts.append(t_starts[i])
            all_ends.append(t_ends[i])
            all_p_start.append(c_start[i])
            all_p_end.append(c_end[i])

            if i < n - 1:
                gap_s = (t_starts[i + 1] - t_ends[i]) / 1e9
                if 0 < gap_s <= max_gap_seconds:
                    all_starts.append(t_ends[i])
                    all_ends.append(t_starts[i + 1])
                    all_p_start.append(c_end[i])
                    all_p_end.append(c_start[i + 1])

        lookup[str(mmsi)] = (
            np.array(all_starts, dtype=np.int64),
            np.array(all_ends, dtype=np.int64),
            np.array(all_p_start, dtype=float),
            np.array(all_p_end, dtype=float),
        )

    return lookup


def _query_vessel_positions(lookup_entry, ts_arr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Query vessel positions at target timestamps (int64 ns). Returns (pts_lonlat, valid_mask)."""
    t_starts, t_ends, p_starts, p_ends = lookup_entry
    if len(t_starts) == 0:
        return np.full((len(ts_arr), 2), np.nan), np.zeros(len(ts_arr), dtype=bool)

    idx = np.searchsorted(t_starts, ts_arr, side='right') - 1
    valid = (idx >= 0) & (idx < len(t_starts)) & (ts_arr <= t_ends[np.clip(idx, 0, len(t_starts) - 1)])

    pts = np.full((len(ts_arr), 2), np.nan)
    if not np.any(valid):
        return pts, valid

    valid_idx = idx[valid]
    dt = (t_ends[valid_idx] - t_starts[valid_idx]).astype(float)
    f = np.where(dt > 0, (ts_arr[valid] - t_starts[valid_idx]) / dt, 0.0)
    pts[valid] = p_starts[valid_idx] + f[:, None] * (p_ends[valid_idx] - p_starts[valid_idx])
    return pts, valid


def generate_encounter_timeseries(
    segments_gdf: gpd.GeoDataFrame,
    encounters_gdf: gpd.GeoDataFrame,
    step_seconds: float = 30.0,
    fairway_axis: Optional[FairwayAxis] = None,
    max_gap_seconds: float = 600.0,
    metric_crs: Optional[str] = None,
) -> gpd.GeoDataFrame:
    """
    Generate dynamic time series of connecting lines between encountering vessels.

    For each encounter event, evaluates the instantaneous position of both vessels
    at regular time intervals (and at exact CPA time) throughout the encounter duration.
    Produces a GeoDataFrame of 2-point LineString geometries connecting vessel 1 to vessel 2
    at each timestamp, enabling seamless temporal playback in GIS (QGIS Temporal Controller,
    Kepler.gl, ArcGIS).

    Parameters
    ----------
    segments_gdf : gpd.GeoDataFrame
        Trajectory segments with MMSI, segment_start_time, segment_end_time, geometry.
    encounters_gdf : gpd.GeoDataFrame
        Detected encounters with mmsi_1, mmsi_2, start_time, end_time, cpa_time, encounter_type.
    step_seconds : float, default 30.0
        Temporal sampling interval in seconds.
    fairway_axis : Optional[FairwayAxis], default None
        If provided, computes instantaneous river mile and cross-track offset for each vessel.
    max_gap_seconds : float, default 600.0
        Maximum time gap across which to interpolate vessel position.
    metric_crs : Optional[str], default None
        Projected metric CRS for distance calculations. Defaults to fairway metric CRS or UTM.

    Returns
    -------
    gpd.GeoDataFrame
        Table with columns:
        - encounter_id: ID of the corresponding encounter
        - encounter_type: classification (overtaking, head-on, crossing, etc.)
        - mmsi_1: MMSI of vessel 1
        - mmsi_2: MMSI of vessel 2
        - timestamp: UTC timestamp of the observation
        - distance_m: instantaneous distance between vessels in meters
        - is_cpa: boolean indicating if this time step represents closest point of approach
        - geometry: 2-point LineString connecting P1(t) to P2(t) in EPSG:4326
        - (Optional if fairway_axis): river_mile_1, river_mile_2, cross_track_m_1, cross_track_m_2, along_channel_gap_m
    """
    empty_cols = TIMESERIES_COLS.copy()
    if fairway_axis is not None:
        empty_cols += ['river_mile_1', 'river_mile_2', 'cross_track_m_1', 'cross_track_m_2', 'along_channel_gap_m']

    if encounters_gdf.empty or segments_gdf.empty:
        return gpd.GeoDataFrame({c: [] for c in empty_cols}, geometry=[], crs="EPSG:4326")

    if metric_crs is None:
        if fairway_axis is not None:
            metric_crs = fairway_axis.metric_crs
        elif segments_gdf.crs is not None and segments_gdf.crs.is_projected:
            metric_crs = str(segments_gdf.crs)
        else:
            bounds = segments_gdf.total_bounds
            mean_lon = float((bounds[0] + bounds[2]) / 2.0)
            mean_lat = float((bounds[1] + bounds[3]) / 2.0)
            metric_crs = get_utm_crs_for_lon_lat(mean_lon, mean_lat)

    lookup = _build_trajectory_lookup(segments_gdf, max_gap_seconds=max_gap_seconds)

    records = []
    line_geoms = []

    for idx, enc_row in encounters_gdf.iterrows():
        enc_id = enc_row.get('encounter_id', idx)
        mmsi_1 = str(enc_row['mmsi_1'])
        mmsi_2 = str(enc_row['mmsi_2'])
        enc_type = enc_row.get('encounter_type')

        if mmsi_1 not in lookup or mmsi_2 not in lookup:
            continue

        t_start = pd.Timestamp(enc_row['start_time'])
        t_end = pd.Timestamp(enc_row['end_time'])
        t_cpa = pd.Timestamp(enc_row['cpa_time'])

        if step_seconds > 0 and t_end > t_start:
            grid = pd.date_range(t_start, t_end, freq=pd.Timedelta(seconds=step_seconds)).tolist()
        else:
            grid = [t_start]

        if not any(abs((t - t_cpa).total_seconds()) < 0.5 for t in grid):
            if t_start <= t_cpa <= t_end:
                grid.append(t_cpa)
        grid = sorted(grid)

        ts_eval = np.array([t.to_datetime64().astype(np.int64) for t in grid])
        pts1, valid1 = _query_vessel_positions(lookup[mmsi_1], ts_eval)
        pts2, valid2 = _query_vessel_positions(lookup[mmsi_2], ts_eval)

        valid = valid1 & valid2
        if not np.any(valid):
            continue

        sub_grid = [grid[i] for i in range(len(grid)) if valid[i]]
        sub_ts = ts_eval[valid]
        sub_p1 = pts1[valid]
        sub_p2 = pts2[valid]

        coords = np.column_stack([sub_p1, sub_p2]).reshape(-1, 2, 2)
        lines = shapely.linestrings(coords)

        cpa_val = t_cpa.to_datetime64().astype(np.int64)
        cpa_diffs = np.abs(sub_ts - cpa_val)
        min_idx = int(np.argmin(cpa_diffs))
        is_cpa_arr = np.zeros(len(sub_grid), dtype=bool)
        if cpa_diffs[min_idx] <= max(step_seconds, 2.0) * 1e9:
            is_cpa_arr[min_idx] = True

        p1_m = gpd.GeoSeries(shapely.points(sub_p1), crs="EPSG:4326").to_crs(metric_crs).values
        p2_m = gpd.GeoSeries(shapely.points(sub_p2), crs="EPSG:4326").to_crs(metric_crs).values
        p1_coords = shapely.get_coordinates(p1_m)
        p2_coords = shapely.get_coordinates(p2_m)
        dist_m = np.hypot(p1_coords[:, 0] - p2_coords[:, 0], p1_coords[:, 1] - p2_coords[:, 1])

        if fairway_axis is not None:
            s1, rm1, ct1 = fairway_axis.project_geometries(p1_m)
            s2, rm2, ct2 = fairway_axis.project_geometries(p2_m)
            along_gap = np.abs(s1 - s2)
        else:
            rm1, rm2, ct1, ct2, along_gap = None, None, None, None, None

        for k in range(len(sub_grid)):
            rec = {
                'encounter_id': enc_id,
                'encounter_type': enc_type,
                'mmsi_1': mmsi_1,
                'mmsi_2': mmsi_2,
                'timestamp': sub_grid[k],
                'distance_m': round(float(dist_m[k]), 2),
                'is_cpa': bool(is_cpa_arr[k]),
            }
            if fairway_axis is not None:
                rec['river_mile_1'] = round(float(rm1[k]), 2)
                rec['river_mile_2'] = round(float(rm2[k]), 2)
                rec['cross_track_m_1'] = round(float(ct1[k]), 1)
                rec['cross_track_m_2'] = round(float(ct2[k]), 1)
                rec['along_channel_gap_m'] = round(float(along_gap[k]), 1)
            records.append(rec)
            line_geoms.append(lines[k])

    if not records:
        return gpd.GeoDataFrame({c: [] for c in empty_cols}, geometry=[], crs="EPSG:4326")

    df_ts = pd.DataFrame(records)
    return gpd.GeoDataFrame(df_ts, geometry=line_geoms, crs="EPSG:4326").reset_index(drop=True)


def run_encounter_detection(
    segments_file: Path,
    output_file: Path,
    max_distance_m: float = 500.0,
    time_bin_minutes: float = 60.0,
    merge_gap_minutes: float = 10.0,
    fairway_axis: Optional[Union[FairwayAxis, str, Path]] = None,
    river_name: str = "MISSISSIPPI-LO",
    timeseries_file: Optional[Path] = None,
    timeseries_step_seconds: float = 30.0,
    metric_crs: Optional[str] = None,
) -> None:
    """CLI/script entry point: detect_encounters, reading and writing GeoParquet files."""
    logger.info(f"Loading segments from {segments_file}...")
    segments_gdf = gpd.read_parquet(segments_file)

    axis_obj = None
    if fairway_axis is not None:
        if isinstance(fairway_axis, FairwayAxis):
            axis_obj = fairway_axis
        else:
            logger.info(f"Loading fairway axis from {fairway_axis} (river={river_name})...")
            axis_obj = FairwayAxis.from_mile_markers(fairway_axis, river_name=river_name)

    logger.info(f"Detecting encounters (max_distance={max_distance_m}m)...")
    events_gdf = detect_encounters(
        segments_gdf,
        max_distance_m=max_distance_m,
        time_bin_minutes=time_bin_minutes,
        merge_gap_minutes=merge_gap_minutes,
        fairway_axis=axis_obj,
        metric_crs=metric_crs,
    )
    logger.info(f"Found {len(events_gdf):,} encounter events.")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Saving encounters to {output_file}...")
    events_gdf.to_parquet(output_file)

    if timeseries_file is not None and not events_gdf.empty:
        logger.info(f"Generating encounter time series (step={timeseries_step_seconds}s)...")
        ts_gdf = generate_encounter_timeseries(
            segments_gdf,
            events_gdf,
            step_seconds=timeseries_step_seconds,
            fairway_axis=axis_obj,
            metric_crs=metric_crs,
        )
        logger.info(f"Generated {len(ts_gdf):,} time series connecting lines.")
        timeseries_file.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"Saving encounter time series to {timeseries_file}...")
        if timeseries_file.suffix in {".parquet", ".geoparquet"}:
            ts_gdf.to_parquet(timeseries_file)
        else:
            ts_gdf.to_file(timeseries_file, driver="GeoJSON")




