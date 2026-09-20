import logging
from pathlib import Path
from typing import Optional, Union, Tuple

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import Point, MultiPoint
import numpy as np
import pyproj

import dask
from dask.distributed import Client

from ais_shader.fairway import FairwayAxis, get_utm_crs_for_lon_lat
from ais_shader.preprocessing import to_utc_datetime

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
    'encounter_type',
    'source_mmsi', 'target_mmsi',
    'mmsi_1', 'mmsi_2', 'role_1', 'role_2',
    'trip_id_1', 'trip_id_2',
    'vessel_type_1', 'vessel_type_2', 'vessel_group_1', 'vessel_group_2',
    'length_1', 'length_2', 'width_1', 'width_2',
    'sog_1', 'sog_2', 'speed_mps_1', 'speed_mps_2',
    'heading_1', 'heading_2',
    'is_stationary_1', 'is_stationary_2', 'stationary_role',
    'start_time', 'end_time', 'cpa_time', 'min_distance_m',
    'overtaking_mmsi', 'overtaken_mmsi',
    'is_abaft_beam', 'abaft_beam_deg',
]


def check_abaft_the_beam(
    p_forward: np.ndarray,
    heading_forward: float,
    p_overtaking: np.ndarray,
    sector_deg: float = 67.5,
) -> tuple[bool, float]:
    """Check whether p_overtaking approaches p_forward from > 22°30' abaft the beam.

    Regulatory Context (BPR art. 6.01 & COLREGS Rule 13):
        - BPR (Inland Navigation Police Regulations) art. 6.01(b) ("oplopen"):
          approaching of a vessel by another vessel from a direction more than
          22°30' abaft the beam of that vessel.
        - BPR art. 6.01(c) ("voorbijlopen"): maneuver resulting from overtaking
          until the vessels are entirely clear of each other.
        - COLREGS Rule 13(b) uses the identical geometric definition: approaching
          from a direction more than 22.5° (22°30') abaft her beam (the 135° sternlight
          sector, ±67.5° from dead astern).

    Dead astern is 180° relative to heading_forward.
    Approaching from > 22°30' abaft the beam corresponds to within ±67.5° of dead astern (135° sector).

    Returns:
        (is_abaft, abaft_beam_deg)
        where abaft_beam_deg is degrees abaft the beam (> 22.5 when within the sector).
    """
    diff_x = float(p_overtaking[0] - p_forward[0])
    diff_y = float(p_overtaking[1] - p_forward[1])
    norm = float(np.hypot(diff_x, diff_y))
    if norm < 1e-6:
        return (False, 0.0)
    bearing_from_fwd = (np.degrees(np.arctan2(diff_x, diff_y)) + 360.0) % 360.0
    rel_bearing = (bearing_from_fwd - heading_forward) % 360.0
    dev_from_stern = abs(rel_bearing - 180.0)
    abaft_beam_deg = 90.0 - dev_from_stern
    is_abaft = bool(dev_from_stern <= sector_deg)
    return (is_abaft, abaft_beam_deg)


def classify_encounter(
    heading1: float,
    heading2: float,
    speed1: float = None,
    speed2: float = None,
    ds_start: float = None,
    ds_end: float = None,
    dv_along: float = None,
    is_abaft: bool = None,
    min_moving_speed: float = 0.5,
    min_overtaking_speed_diff: float = 0.5,
) -> str:
    """Classify encounter between two vessels based on relative course, along-track passing, and approach angle.

    Regulatory Context (BPR art. 6.01 & COLREGS Rule 13 / 14 / 15):
      - 'head-on' (BPR art. 6.01(a), "naderen op tegengestelde koersen"): vessels approaching
        each other on courses that are directly or nearly directly opposite (135° <= rel_angle <= 225°).
      - 'overtaking' (BPR art. 6.01(b/c), "oplopen" & "voorbijlopen"):
          * overtaking approach (6.01(b)): approaching from a direction > 22°30' abaft the beam (is_abaft is True);
          * passing maneuver (6.01(c)): along-track passing occurs until vessels are clear.
      - 'parallel_sailing': same general direction (rel_angle <= 45°) without passing (e.g. coupled or co-sailing).
      - 'crossing' (BPR art. 6.01(d), "kruisende koersen"): courses intersecting at an angle (45° < rel_angle < 135°
        or 225° < rel_angle < 315°), or same-direction approaches outside the > 22°30' abaft-the-beam sector
        (approaching from abeam or forward of the beam).
      - 'stationary': both vessels are stationary/moored (< min_moving_speed m/s).

    Returns:
      'head-on', 'overtaking', 'parallel_sailing', 'crossing', or 'stationary'.
    """
    if speed1 is not None and speed2 is not None:
        if speed1 < min_moving_speed and speed2 < min_moving_speed:
            return "stationary"

    diff = (heading1 - heading2) % 360.0
    rel_angle = min(diff, 360.0 - diff)

    if rel_angle <= 45.0:
        if is_abaft is False:
            return "crossing"

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

    is_abaft = None
    if ds_start is not None and abs(ds_start) > 1.0:
        if ds_start > 0:
            is_abaft, _ = check_abaft_the_beam(r1_0, heading1, r2_0)
        else:
            is_abaft, _ = check_abaft_the_beam(r2_0, heading2, r1_0)

    enc_type = classify_encounter(
        heading1, heading2, speed1, speed2,
        ds_start=ds_start, ds_end=ds_end, dv_along=dv_along,
        is_abaft=is_abaft,
    )

    return (cpa_dist_m, cpa_time, p1_cpa, p2_cpa, mid_cpa, heading1, heading2, speed1, speed2,
            enc_type, ds_start, ds_end, dv_along)


def compute_proximity_interval(
    p1_start: np.ndarray,
    p1_end: np.ndarray,
    t1_start: pd.Timestamp,
    t1_end: pd.Timestamp,
    p2_start: np.ndarray,
    p2_end: np.ndarray,
    t2_start: pd.Timestamp,
    t2_end: pd.Timestamp,
    max_distance_m: float,
) -> Optional[Tuple[pd.Timestamp, pd.Timestamp]]:
    """
    Analytically compute the time interval [t_prox_start, t_prox_end] during which
    the distance between two moving segments is <= max_distance_m.

    Coordinates must be in a projected planar CRS (meters).
    Returns (t_prox_start, t_prox_end) or None if the vessels never get within max_distance_m.
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

    offset1 = (t_start - t1_start).total_seconds()
    offset2 = (t_start - t2_start).total_seconds()

    r1_0 = p1_start + v1 * offset1
    r2_0 = p2_start + v2 * offset2

    R0 = r1_0 - r2_0
    delta_v = v1 - v2

    a = float(np.dot(delta_v, delta_v))
    b = float(2.0 * np.dot(R0, delta_v))
    c = float(np.dot(R0, R0))
    d_max_sq = max_distance_m * max_distance_m

    if a > 1e-12:
        disc = b * b - 4.0 * a * (c - d_max_sq)
        if disc < 0:
            return None
        sqrt_disc = np.sqrt(disc)
        tau_1 = (-b - sqrt_disc) / (2.0 * a)
        tau_2 = (-b + sqrt_disc) / (2.0 * a)
        tau_s = max(0.0, tau_1)
        tau_e = min(dt_total, tau_2)
        if tau_s > tau_e:
            return None
        return (
            t_start + pd.to_timedelta(tau_s, unit='s'),
            t_start + pd.to_timedelta(tau_e, unit='s'),
        )
    else:
        if c <= d_max_sq:
            return (t_start, t_end)
        return None


def _evaluate_candidates_in_window(
    segments_metric: gpd.GeoDataFrame,
    t_start: pd.Timestamp,
    t_end: pd.Timestamp,
    max_distance_m: float,
    fairway_axis: Optional[FairwayAxis] = None,
    merge_gap_minutes: Optional[float] = None,
    exclude_stationary: str = 'both',
    min_moving_speed: float = 0.5,
    max_segment_duration_s: float = 1800.0,
) -> list:
    """Evaluate pairwise encounters within a spatio-temporal window.

    Requires segments_metric to be sorted by segment_start_time. Uses binary
    search (np.searchsorted) to bound candidate segments to O(log N) temporal lookups,
    avoiding full-table boolean scans.
    """
    start_times = segments_metric['segment_start_time'].values
    end_times = segments_metric['segment_end_time'].values

    t_min_start = (t_start - pd.Timedelta(seconds=max_segment_duration_s)).to_datetime64()
    t_max_start = t_end.to_datetime64()
    i_left = np.searchsorted(start_times, t_min_start, side='left')
    i_right = np.searchsorted(start_times, t_max_start, side='right')
    if i_left >= i_right:
        return []

    sub_start = start_times[i_left:i_right]
    sub_end = end_times[i_left:i_right]
    sub_mask = (sub_start <= t_end.to_datetime64()) & (sub_end >= t_start.to_datetime64())
    indices = i_left + np.where(sub_mask)[0]

    if len(indices) < 2:
        return []

    bin_df = segments_metric.iloc[indices].reset_index(drop=True)
    geoms = bin_df.geometry.values
    mmsi = bin_df['MMSI'].astype(str).values
    s_t = bin_df['segment_start_time'].values
    e_t = bin_df['segment_end_time'].values
    starts, ends = _segment_start_end_coords(geoms)

    tree = shapely.STRtree(geoms)
    env = shapely.buffer(shapely.envelope(geoms), max_distance_m)
    i_sub, j_sub = tree.query(env)

    mask = (i_sub < j_sub) & (mmsi[i_sub] != mmsi[j_sub])
    i_sub, j_sub = i_sub[mask], j_sub[mask]

    time_overlap = (s_t[i_sub] <= e_t[j_sub]) & (e_t[i_sub] >= s_t[j_sub])
    i_sub, j_sub = i_sub[time_overlap], j_sub[time_overlap]

    if len(i_sub) == 0:
        return []

    trip_ids = bin_df['trip_id'].values if 'trip_id' in bin_df else None
    v_types = bin_df['VesselType'].values if 'VesselType' in bin_df else None
    v_groups = bin_df['VesselGroup'].values if 'VesselGroup' in bin_df else None
    lengths = bin_df['Length'].values if 'Length' in bin_df else None
    widths = bin_df['Width'].values if 'Width' in bin_df else None
    sogs = bin_df['sog'].values if 'sog' in bin_df else None
    fairway_dirs = bin_df['fairway_direction'].values if 'fairway_direction' in bin_df else None
    fairway_speeds = bin_df['fairway_speed_mps'].values if 'fairway_speed_mps' in bin_df else None

    records = []
    for idx_a, idx_b in zip(i_sub, j_sub):
        if mmsi[idx_a] <= mmsi[idx_b]:
            idx1, idx2 = idx_a, idx_b
        else:
            idx1, idx2 = idx_b, idx_a

        t1_s = pd.Timestamp(s_t[idx1])
        t1_e = pd.Timestamp(e_t[idx1])
        t2_s = pd.Timestamp(s_t[idx2])
        t2_e = pd.Timestamp(e_t[idx2])

        dur1 = (t1_e - t1_s).total_seconds()
        dur2 = (t2_e - t2_s).total_seconds()
        if dur1 > max_segment_duration_s or dur2 > max_segment_duration_s:
            continue

        cpa_res = compute_segment_cpa(
            starts[idx1], ends[idx1], t1_s, t1_e,
            starts[idx2], ends[idx2], t2_s, t2_e
        )
        if cpa_res is None:
            continue

        cpa_dist_m, cpa_time, p1_cpa, p2_cpa, mid_cpa, heading1, heading2, speed1, speed2, enc_type, ds_start, ds_end, dv_along = cpa_res
        if cpa_dist_m > max_distance_m:
            continue

        prox_interval = compute_proximity_interval(
            starts[idx1], ends[idx1], t1_s, t1_e,
            starts[idx2], ends[idx2], t2_s, t2_e,
            max_distance_m=max_distance_m,
        )
        if prox_interval is None:
            continue

        prox_start, prox_end = prox_interval
        # Require encounter proximity to overlap the current window
        if prox_start > t_end or prox_end < t_start:
            continue

        sog1_val = sogs[idx1] if sogs is not None and not np.isnan(sogs[idx1]) else None
        sog2_val = sogs[idx2] if sogs is not None and not np.isnan(sogs[idx2]) else None
        if speed1 < min_moving_speed and sog1_val is not None and sog1_val >= 1.0:
            speed1 = sog1_val * 0.514444
        if speed2 < min_moving_speed and sog2_val is not None and sog2_val >= 1.0:
            speed2 = sog2_val * 0.514444

        is_stat_1 = bool(speed1 < min_moving_speed)
        is_stat_2 = bool(speed2 < min_moving_speed)

        if is_stat_1 and is_stat_2:
            stat_role = 'both'
        elif is_stat_1:
            stat_role = 'vessel_1'
        elif is_stat_2:
            stat_role = 'vessel_2'
        else:
            stat_role = 'none'

        if exclude_stationary == 'both' and (is_stat_1 and is_stat_2):
            continue
        elif exclude_stationary in {'any', 'all'} and (is_stat_1 or is_stat_2):
            continue

        dir1 = None
        dir2 = None
        if fairway_axis is not None and fairway_dirs is not None:
            dir1 = fairway_dirs[idx1]
            dir2 = fairway_dirs[idx2]
            if dir1 and dir2:
                enc_type = fairway_axis.classify_fairway_encounter(
                    dir1, dir2, speed1, speed2, ds_start, ds_end, dv_along,
                    heading1=heading1, heading2=heading2,
                )

        overtaking_mmsi = None
        overtaken_mmsi = None
        if enc_type == 'overtaking':
            if fairway_axis is not None and fairway_speeds is not None and dir1 != "outside_fairway" and dir2 != "outside_fairway":
                v_f1 = abs(float(fairway_speeds[idx1] if fairway_speeds[idx1] is not None else speed1))
                v_f2 = abs(float(fairway_speeds[idx2] if fairway_speeds[idx2] is not None else speed2))
                if v_f1 >= v_f2:
                    overtaking_mmsi = mmsi[idx1]
                    overtaken_mmsi = mmsi[idx2]
                else:
                    overtaking_mmsi = mmsi[idx2]
                    overtaken_mmsi = mmsi[idx1]
            elif dv_along > 0:
                overtaking_mmsi = mmsi[idx1]
                overtaken_mmsi = mmsi[idx2]
            elif dv_along < 0:
                overtaking_mmsi = mmsi[idx2]
                overtaken_mmsi = mmsi[idx1]
            else:
                if speed1 >= speed2:
                    overtaking_mmsi = mmsi[idx1]
                    overtaken_mmsi = mmsi[idx2]
                else:
                    overtaking_mmsi = mmsi[idx2]
                    overtaken_mmsi = mmsi[idx1]

        # Determine source (active encountering vessel) vs target (encountered vessel/obstacle)
        if is_stat_1 and not is_stat_2:
            source_mmsi = mmsi[idx2]
            target_mmsi = mmsi[idx1]
            role_1 = 'stationary'
            role_2 = 'moving'
        elif is_stat_2 and not is_stat_1:
            source_mmsi = mmsi[idx1]
            target_mmsi = mmsi[idx2]
            role_1 = 'moving'
            role_2 = 'stationary'
        elif enc_type == 'overtaking' and overtaking_mmsi:
            source_mmsi = str(overtaking_mmsi)
            target_mmsi = str(overtaken_mmsi)
            role_1 = 'overtaking' if mmsi[idx1] == source_mmsi else 'overtaken'
            role_2 = 'overtaking' if mmsi[idx2] == source_mmsi else 'overtaken'
        else:
            source_mmsi = mmsi[idx1]
            target_mmsi = mmsi[idx2]
            role_1 = 'moving' if not is_stat_1 else 'stationary'
            role_2 = 'moving' if not is_stat_2 else 'stationary'
        t_prox_init = max(t1_s, t2_s)
        off1 = (t_prox_init - t1_s).total_seconds()
        off2 = (t_prox_init - t2_s).total_seconds()
        v_seg1 = (ends[idx1] - starts[idx1]) / dur1 if dur1 > 1e-6 else np.zeros(2)
        v_seg2 = (ends[idx2] - starts[idx2]) / dur2 if dur2 > 1e-6 else np.zeros(2)
        p1_init = starts[idx1] + v_seg1 * off1
        p2_init = starts[idx2] + v_seg2 * off2

        is_abaft = None
        abaft_deg = np.nan
        if ds_start is not None and abs(ds_start) > 1.0:
            if ds_start > 0:
                is_abaft, abaft_deg = check_abaft_the_beam(p1_init, heading1, p2_init)
            else:
                is_abaft, abaft_deg = check_abaft_the_beam(p2_init, heading2, p1_init)

        rec = {
            'encounter_type': enc_type,
            'source_mmsi': source_mmsi,
            'target_mmsi': target_mmsi,
            'mmsi_1': mmsi[idx1],
            'mmsi_2': mmsi[idx2],
            'role_1': role_1,
            'role_2': role_2,
            'trip_id_1': trip_ids[idx1] if trip_ids is not None else None,
            'trip_id_2': trip_ids[idx2] if trip_ids is not None else None,
            'vessel_type_1': v_types[idx1] if v_types is not None else None,
            'vessel_type_2': v_types[idx2] if v_types is not None else None,
            'vessel_group_1': v_groups[idx1] if v_groups is not None else None,
            'vessel_group_2': v_groups[idx2] if v_groups is not None else None,
            'length_1': lengths[idx1] if lengths is not None else np.nan,
            'length_2': lengths[idx2] if lengths is not None else np.nan,
            'width_1': widths[idx1] if widths is not None else np.nan,
            'width_2': widths[idx2] if widths is not None else np.nan,
            'sog_1': sogs[idx1] if sogs is not None else np.nan,
            'sog_2': sogs[idx2] if sogs is not None else np.nan,
            'speed_mps_1': speed1,
            'speed_mps_2': speed2,
            'heading_1': heading1,
            'heading_2': heading2,
            'is_stationary_1': is_stat_1,
            'is_stationary_2': is_stat_2,
            'stationary_role': stat_role,
            'start_time': prox_start,
            'end_time': prox_end,
            'cpa_time': cpa_time,
            'min_distance_m': cpa_dist_m,
            'overtaking_mmsi': overtaking_mmsi,
            'overtaken_mmsi': overtaken_mmsi,
            'is_abaft_beam': is_abaft,
            'abaft_beam_deg': abaft_deg,
            '_mid_x': float(mid_cpa[0]),
            '_mid_y': float(mid_cpa[1]),
            '_ds_start': ds_start,
            '_ds_end': ds_end,
            '_dv_along': dv_along,
        }
        records.append(rec)

    if merge_gap_minutes is not None and records:
        raw_df = pd.DataFrame(records).drop_duplicates(subset=['mmsi_1', 'mmsi_2', 'start_time', 'end_time', 'cpa_time'])
        return _merge_encounters(raw_df, merge_gap_minutes)

    return records


def detect_encounters(
    segments_gdf: gpd.GeoDataFrame,
    max_distance_m: float = 100.0,
    time_bin_minutes: float = 60.0,
    merge_gap_minutes: float = 10.0,
    exclude_stationary: str = 'both',
    min_moving_speed: float = 0.5,
    fairway_axis: Optional[FairwayAxis] = None,
    metric_crs: Optional[str] = None,
    client: Optional[Client] = None,
    max_segment_duration_s: float = 1800.0,
) -> gpd.GeoDataFrame:
    """
    Detect encounters (crossings, overtakings, head-on meetings) between vessels.

    Uses a spatio-temporal index (temporal binning + Shapely STRtree) to efficiently
    find pairs of vessel segments within max_distance_m and overlapping in time.
    For each candidate, calculates the exact Closest Point of Approach (CPA) distance
    and time, classifies the encounter, and optionally merges consecutive proximity
    records for the same vessel dyad into a single encounter event.

    Stationary vessels can be handled via exclude_stationary:
      - 'both' (default): excludes pairs where both vessels are stationary (anchored/moored pairs),
        while retaining stationary vessels as target obstacles that moving ships encounter/sail into.
      - 'any' / 'all': excludes encounters if either vessel is stationary (moving-vs-moving only).
      - 'none': retains all proximity pairs including stationary-vs-stationary.

    When fairway_axis is supplied, encounter classification uses the 1D along-fairway
    progression (eliminating river bend compass heading false-crossings), and outputs
    are enriched with river_mile and cross_track_m coordinates.
    """
    _require_columns(segments_gdf, ['MMSI', 'trip_id', 'segment_start_time'], "segments_gdf")

    if segments_gdf.empty:
        return gpd.GeoDataFrame({c: [] for c in ENCOUNTER_COLS}, geometry=[], crs="EPSG:4326")

    gdf = segments_gdf.copy()
    if 'segment_end_time' not in gdf.columns:
        if 'segment_duration_s' in gdf.columns:
            gdf['segment_end_time'] = gdf['segment_start_time'] + pd.to_timedelta(gdf['segment_duration_s'], unit='s')
        else:
            raise KeyError("segments_gdf must have 'segment_end_time' or 'segment_duration_s'.")

    gdf['segment_start_time'] = to_utc_datetime(gdf['segment_start_time'])
    gdf['segment_end_time'] = to_utc_datetime(gdf['segment_end_time'])
    gdf = gdf.sort_values('segment_start_time').reset_index(drop=True)

    t_min = gdf['segment_start_time'].min()
    t_max = gdf['segment_end_time'].max()
    if pd.isna(t_min) or pd.isna(t_max):
        return gpd.GeoDataFrame({c: [] for c in ENCOUNTER_COLS}, geometry=[], crs="EPSG:4326")

    if gdf.crs is None:
        raise ValueError("segments_gdf must have a defined Coordinate Reference System (CRS).")

    if metric_crs is None:
        if fairway_axis is not None:
            metric_crs = fairway_axis.metric_crs
        elif gdf.crs.is_projected:
            metric_crs = str(gdf.crs)
        else:
            bounds = gdf.total_bounds
            mean_lon = float((bounds[0] + bounds[2]) / 2.0)
            mean_lat = float((bounds[1] + bounds[3]) / 2.0)
            metric_crs = get_utm_crs_for_lon_lat(mean_lon, mean_lat)

    if pyproj.CRS.from_user_input(gdf.crs) != pyproj.CRS.from_user_input(metric_crs):
        if client is not None and len(gdf) > 50000:
            logger.info(f"Projecting {len(gdf):,} segments to metric CRS ({metric_crs}) across Dask workers...")
            n_workers = len(client.scheduler_info().get('workers', {})) or 4
            n_chunks = max(8, n_workers * 4)
            chunk_size = int(np.ceil(len(gdf) / n_chunks))
            chunks = [gdf.iloc[i:i + chunk_size].copy() for i in range(0, len(gdf), chunk_size)]
            delayed_proj = [dask.delayed(lambda c: c.to_crs(metric_crs))(c) for c in chunks]
            proj_chunks = client.gather(client.compute(delayed_proj))
            segments_metric = pd.concat(proj_chunks, ignore_index=True)
            segments_metric.crs = metric_crs
        else:
            logger.info(f"Projecting {len(gdf):,} segments to metric CRS ({metric_crs})...")
            segments_metric = gdf.to_crs(metric_crs).reset_index(drop=True)
    else:
        segments_metric = gdf.reset_index(drop=True)

    if fairway_axis is not None and 'fairway_direction' not in segments_metric.columns:
        logger.info("Annotating segments with fairway axis kinematics...")
        if client is not None and len(segments_metric) > 5000:
            n_workers = len(client.scheduler_info().get('workers', {})) or 4
            n_chunks = max(8, n_workers * 4)
            chunk_size = int(np.ceil(len(segments_metric) / n_chunks))
            chunks = [segments_metric.iloc[i:i + chunk_size].copy() for i in range(0, len(segments_metric), chunk_size)]
            delayed_ann = [dask.delayed(fairway_axis.annotate_segments)(c) for c in chunks]
            ann_chunks = client.gather(client.compute(delayed_ann))
            segments_metric = pd.concat(ann_chunks, ignore_index=True)
            segments_metric.crs = metric_crs
        else:
            segments_metric = fairway_axis.annotate_segments(segments_metric)

    bin_delta = pd.Timedelta(minutes=time_bin_minutes)
    n_bins = int(np.ceil((t_max - t_min) / bin_delta)) + 1
    windows = [(t_min + b * bin_delta, t_min + (b + 1) * bin_delta) for b in range(n_bins)]

    if client is not None and len(windows) > 1:
        logger.info(f"Distributing encounter detection across {len(windows)} temporal windows via Dask (exclude_stationary={exclude_stationary})...")
        scattered_metric = client.scatter(segments_metric, broadcast=True)
        tasks = [
            dask.delayed(_evaluate_candidates_in_window)(
                scattered_metric, w[0], w[1], max_distance_m, fairway_axis, merge_gap_minutes,
                exclude_stationary, min_moving_speed, max_segment_duration_s
            )
            for w in windows
        ]
        window_results = client.gather(client.compute(tasks))
        raw_records = [rec for res in window_results for rec in res]
    else:
        raw_records = []
        for w in windows:
            raw_records.extend(
                _evaluate_candidates_in_window(
                    segments_metric, w[0], w[1], max_distance_m, fairway_axis, merge_gap_minutes,
                    exclude_stationary, min_moving_speed, max_segment_duration_s
                )
            )

    if not raw_records:
        return gpd.GeoDataFrame({c: [] for c in ENCOUNTER_COLS}, geometry=[], crs="EPSG:4326")

    raw_df = pd.DataFrame(raw_records)
    raw_df = raw_df.drop_duplicates(subset=['mmsi_1', 'mmsi_2', 'start_time', 'end_time', 'cpa_time'])

    # Merge consecutive proximity alerts for the same vessel dyad
    if merge_gap_minutes is not None:
        merged_records = _merge_encounters(raw_df, merge_gap_minutes)
    else:
        merged_records = raw_df.to_dict('records')

    for i, r in enumerate(merged_records):
        r['encounter_id'] = i

    mid_pts_xy = np.column_stack([
        [r['_mid_x'] for r in merged_records],
        [r['_mid_y'] for r in merged_records]
    ]) if merged_records else np.empty((0, 2))

    to_4326 = pyproj.Transformer.from_crs(metric_crs, "EPSG:4326", always_xy=True)
    if len(mid_pts_xy) > 0:
        lon, lat = to_4326.transform(mid_pts_xy[:, 0], mid_pts_xy[:, 1])
        mid_points_4326 = gpd.GeoSeries(shapely.points(lon, lat), crs="EPSG:4326")
    else:
        mid_points_4326 = gpd.GeoSeries([], crs="EPSG:4326")

    if fairway_axis is not None and merged_records:
        chainage_m, cross_tracks = fairway_axis.project_geometries(mid_pts_xy)
        result_data = {c: [r[c] for r in merged_records] for c in ENCOUNTER_COLS}
        result_data['fairway_chainage_m'] = np.round(chainage_m, 1).tolist()
        result_data['fairway_chainage_km'] = np.round(chainage_m / 1000.0, 3).tolist()
        result_data['fairway_cross_track_m'] = np.round(cross_tracks, 1).tolist()
    else:
        result_data = {c: [r[c] for r in merged_records] for c in ENCOUNTER_COLS}

    events_gdf = gpd.GeoDataFrame(result_data, geometry=mid_points_4326, crs="EPSG:4326")
    return events_gdf.reset_index(drop=True)


def _merge_encounters(raw_df: pd.DataFrame, merge_gap_minutes: float) -> list:
    """Group consecutive encounter records between the same two vessels within merge_gap_minutes."""
    if raw_df.empty:
        return []

    gap = pd.Timedelta(minutes=merge_gap_minutes)
    sortable = raw_df.sort_values(['mmsi_1', 'mmsi_2', 'start_time']).reset_index(drop=True)

    m1 = sortable['mmsi_1'].values
    m2 = sortable['mmsi_2'].values
    st = sortable['start_time'].values
    et = sortable['end_time'].values

    same_pair = (m1[1:] == m1[:-1]) & (m2[1:] == m2[:-1])
    time_close = (st[1:] - et[:-1]) <= np.timedelta64(gap)
    is_new_group = np.empty(len(sortable), dtype=bool)
    is_new_group[0] = True
    is_new_group[1:] = ~(same_pair & time_close)

    group_starts = np.where(is_new_group)[0]
    group_ends = np.r_[group_starts[1:], len(sortable)]

    dist = sortable['min_distance_m'].values
    ds_start = sortable['_ds_start'].values
    ds_end = sortable['_ds_end'].values
    st_vals = sortable['start_time'].values
    et_vals = sortable['end_time'].values

    best_indices = np.empty(len(group_starts), dtype=np.int64)
    new_starts = []
    new_ends = []
    for g_idx, (g_start, g_end) in enumerate(zip(group_starts, group_ends)):
        best_indices[g_idx] = g_start + np.argmin(dist[g_start:g_end])
        new_starts.append(min(st_vals[g_start:g_end]))
        new_ends.append(max(et_vals[g_start:g_end]))

    best_df = sortable.iloc[best_indices].copy()
    best_df['start_time'] = new_starts
    best_df['end_time'] = new_ends
    records = best_df.to_dict('records')

    for g_idx, (g_start, g_end) in enumerate(zip(group_starts, group_ends)):
        sub_ds_start = ds_start[g_start:g_end]
        sub_ds_end = ds_end[g_start:g_end]
        initial_ds = sub_ds_start[0]
        final_ds = sub_ds_end[-1]
        overall_order_flipped = (initial_ds * final_ds < -1.0) or bool(np.any(sub_ds_start * sub_ds_end < -1.0))
        res = records[g_idx]
        overall_dv_along = res.get('_dv_along', 0.0)

        enc_type = res['encounter_type']
        overtaking_mmsi = res.get('overtaking_mmsi')
        overtaken_mmsi = res.get('overtaken_mmsi')

        h1 = res.get('heading_1')
        h2 = res.get('heading_2')
        if h1 is not None and h2 is not None and not np.isnan(h1) and not np.isnan(h2):
            diff = (h1 - h2) % 360.0
            rel_angle = min(diff, 360.0 - diff)
        else:
            rel_angle = 90.0

        if enc_type in {'overtaking', 'parallel_sailing'} or rel_angle <= 45.0 or (overall_order_flipped and rel_angle <= 60.0):
            if res.get('is_abaft_beam') is False:
                enc_type = 'crossing'
                overtaking_mmsi = None
                overtaken_mmsi = None
            else:
                has_speed_diff = abs(overall_dv_along) >= 0.5
                if overall_order_flipped or (has_speed_diff and (initial_ds * final_ds <= 0.0 and abs(initial_ds - final_ds) > 1.0)):
                    enc_type = 'overtaking'
                    if overall_dv_along > 0:
                        overtaking_mmsi = res['mmsi_1']
                        overtaken_mmsi = res['mmsi_2']
                    elif overall_dv_along < 0:
                        overtaking_mmsi = res['mmsi_2']
                        overtaken_mmsi = res['mmsi_1']
                    else:
                        s1 = res.get('speed_mps_1', 0.0) or 0.0
                        s2 = res.get('speed_mps_2', 0.0) or 0.0
                        if s1 >= s2:
                            overtaking_mmsi = res['mmsi_1']
                            overtaken_mmsi = res['mmsi_2']
                        else:
                            overtaking_mmsi = res['mmsi_2']
                            overtaken_mmsi = res['mmsi_1']
                elif rel_angle <= 45.0:
                    enc_type = 'parallel_sailing'
                    overtaking_mmsi = None
                    overtaken_mmsi = None
                else:
                    overtaking_mmsi = None
                    overtaken_mmsi = None

        res['encounter_type'] = enc_type
        res['overtaking_mmsi'] = overtaking_mmsi
        res['overtaken_mmsi'] = overtaken_mmsi

        is_stat_1 = bool(res.get('is_stationary_1', False))
        is_stat_2 = bool(res.get('is_stationary_2', False))

        if is_stat_1 and not is_stat_2:
            res['source_mmsi'] = str(res['mmsi_2'])
            res['target_mmsi'] = str(res['mmsi_1'])
            res['role_1'] = 'stationary'
            res['role_2'] = 'moving'
        elif is_stat_2 and not is_stat_1:
            res['source_mmsi'] = str(res['mmsi_1'])
            res['target_mmsi'] = str(res['mmsi_2'])
            res['role_1'] = 'moving'
            res['role_2'] = 'stationary'
        elif is_stat_1 and is_stat_2:
            res['role_1'] = 'stationary'
            res['role_2'] = 'stationary'
        elif enc_type == 'overtaking' and overtaking_mmsi is not None:
            res['source_mmsi'] = str(overtaking_mmsi)
            res['target_mmsi'] = str(overtaken_mmsi)
            m1_str = str(res['mmsi_1'])
            m2_str = str(res['mmsi_2'])
            res['role_1'] = 'overtaking' if m1_str == str(overtaking_mmsi) else 'overtaken'
            res['role_2'] = 'overtaking' if m2_str == str(overtaking_mmsi) else 'overtaken'
        elif enc_type == 'parallel_sailing':
            res['role_1'] = 'moving'
            res['role_2'] = 'moving'

    return records



TIMESERIES_COLS = [
    'encounter_id',
    'encounter_type',
    'source_mmsi',
    'target_mmsi',
    'mmsi_1',
    'mmsi_2',
    'role_1',
    'role_2',
    'is_stationary_1',
    'is_stationary_2',
    'stationary_role',
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


def _generate_encounter_timeseries_chunk(
    encounters_chunk: gpd.GeoDataFrame,
    lookup: dict,
    step_seconds: float = 30.0,
    fairway_axis: Optional[FairwayAxis] = None,
    metric_crs: str = "EPSG:3857",
    segments_crs: str = "EPSG:4326",
    max_distance_m: Optional[float] = None,
) -> gpd.GeoDataFrame:
    """Generate timeseries connecting lines for a chunk of encounters."""
    empty_cols = TIMESERIES_COLS.copy()
    if fairway_axis is not None:
        empty_cols += ['chainage_m_1', 'chainage_m_2', 'cross_track_m_1', 'cross_track_m_2', 'along_channel_gap_m']

    if encounters_chunk.empty:
        return gpd.GeoDataFrame({c: [] for c in empty_cols}, geometry=[], crs="EPSG:4326")

    records = []
    line_geoms = []

    is_metric_input = pyproj.CRS.from_user_input(segments_crs) == pyproj.CRS.from_user_input(metric_crs)
    is_4326_input = pyproj.CRS.from_user_input(segments_crs) == pyproj.CRS.from_user_input("EPSG:4326")

    to_metric = None if is_metric_input else pyproj.Transformer.from_crs(segments_crs, metric_crs, always_xy=True)
    to_4326 = None if is_4326_input else pyproj.Transformer.from_crs(segments_crs, "EPSG:4326", always_xy=True)

    for idx, enc_row in encounters_chunk.iterrows():
        enc_id = enc_row.get('encounter_id', idx)
        mmsi_1 = str(enc_row['mmsi_1'])
        mmsi_2 = str(enc_row['mmsi_2'])
        enc_type = enc_row.get('encounter_type')
        source_mmsi = enc_row.get('source_mmsi', mmsi_1)
        target_mmsi = enc_row.get('target_mmsi', mmsi_2)
        role_1 = enc_row.get('role_1', 'moving')
        role_2 = enc_row.get('role_2', 'moving')
        is_stat_1 = bool(enc_row.get('is_stationary_1', False))
        is_stat_2 = bool(enc_row.get('is_stationary_2', False))
        stat_role = enc_row.get('stationary_role', 'none')

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

        if is_4326_input:
            coords_4326 = np.column_stack([sub_p1, sub_p2]).reshape(-1, 2, 2)
        else:
            lon1, lat1 = to_4326.transform(sub_p1[:, 0], sub_p1[:, 1])
            lon2, lat2 = to_4326.transform(sub_p2[:, 0], sub_p2[:, 1])
            p1_4326 = np.column_stack([lon1, lat1])
            p2_4326 = np.column_stack([lon2, lat2])
            coords_4326 = np.column_stack([p1_4326, p2_4326]).reshape(-1, 2, 2)
        lines = shapely.linestrings(coords_4326)

        cpa_val = t_cpa.to_datetime64().astype(np.int64)
        cpa_diffs = np.abs(sub_ts - cpa_val)
        min_idx = int(np.argmin(cpa_diffs))
        is_cpa_arr = np.zeros(len(sub_grid), dtype=bool)
        if cpa_diffs[min_idx] <= max(step_seconds, 2.0) * 1e9:
            is_cpa_arr[min_idx] = True

        if is_metric_input:
            x1_m, y1_m = sub_p1[:, 0], sub_p1[:, 1]
            x2_m, y2_m = sub_p2[:, 0], sub_p2[:, 1]
            p1_coords = sub_p1
            p2_coords = sub_p2
        else:
            x1_m, y1_m = to_metric.transform(sub_p1[:, 0], sub_p1[:, 1])
            x2_m, y2_m = to_metric.transform(sub_p2[:, 0], sub_p2[:, 1])
            p1_coords = np.column_stack([x1_m, y1_m])
            p2_coords = np.column_stack([x2_m, y2_m])
        dist_m = np.hypot(x1_m - x2_m, y1_m - y2_m)

        if fairway_axis is not None:
            s1, ct1 = fairway_axis.project_geometries(p1_coords)
            s2, ct2 = fairway_axis.project_geometries(p2_coords)
            along_gap = np.abs(s1 - s2)
        else:
            s1, s2, ct1, ct2, along_gap = None, None, None, None, None

        for k in range(len(sub_grid)):
            d_val = float(dist_m[k])
            # Drop connecting lines that exceed max encounter distance
            if max_distance_m is not None and d_val > max_distance_m:
                continue

            rec = {
                'encounter_id': enc_id,
                'encounter_type': enc_type,
                'source_mmsi': source_mmsi,
                'target_mmsi': target_mmsi,
                'mmsi_1': mmsi_1,
                'mmsi_2': mmsi_2,
                'role_1': role_1,
                'role_2': role_2,
                'is_stationary_1': is_stat_1,
                'is_stationary_2': is_stat_2,
                'stationary_role': stat_role,
                'timestamp': sub_grid[k],
                'distance_m': round(d_val, 2),
                'is_cpa': bool(is_cpa_arr[k]),
            }
            if fairway_axis is not None:
                rec['chainage_m_1'] = round(float(s1[k]), 1)
                rec['chainage_m_2'] = round(float(s2[k]), 1)
                rec['cross_track_m_1'] = round(float(ct1[k]), 1)
                rec['cross_track_m_2'] = round(float(ct2[k]), 1)
                rec['along_channel_gap_m'] = round(float(along_gap[k]), 1)
            records.append(rec)
            line_geoms.append(lines[k])

    if not records:
        return gpd.GeoDataFrame({c: [] for c in empty_cols}, geometry=[], crs="EPSG:4326")

    df_ts = pd.DataFrame(records)
    return gpd.GeoDataFrame(df_ts, geometry=line_geoms, crs="EPSG:4326").reset_index(drop=True)


def generate_encounter_timeseries(
    segments_gdf: gpd.GeoDataFrame,
    encounters_gdf: gpd.GeoDataFrame,
    step_seconds: float = 30.0,
    fairway_axis: Optional[FairwayAxis] = None,
    max_gap_seconds: float = 600.0,
    metric_crs: Optional[str] = None,
    client: Optional[Client] = None,
    max_distance_m: Optional[float] = None,
) -> gpd.GeoDataFrame:
    """
    Generate dynamic time series of connecting lines between encountering vessels.

    For each encounter event, evaluates the instantaneous position of both vessels
    at regular time intervals (and at exact CPA time) throughout the encounter duration.
    Produces a GeoDataFrame of 2-point LineString geometries connecting vessel 1 to vessel 2
    at each timestamp, enabling seamless temporal playback in GIS (QGIS Temporal Controller,
    Kepler.gl, ArcGIS).
    """
    empty_cols = TIMESERIES_COLS.copy()
    if fairway_axis is not None:
        empty_cols += ['chainage_m_1', 'chainage_m_2', 'cross_track_m_1', 'cross_track_m_2', 'along_channel_gap_m']

    if encounters_gdf.empty or segments_gdf.empty:
        return gpd.GeoDataFrame({c: [] for c in empty_cols}, geometry=[], crs="EPSG:4326")

    segments_crs = str(segments_gdf.crs) if segments_gdf.crs else "EPSG:4326"

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

    if client is not None and len(encounters_gdf) > 50:
        logger.info("Distributing time series generation across encounters with Dask...")
        n_workers = len(client.scheduler_info().get('workers', {})) or 4
        n_chunks = max(8, n_workers * 4)
        chunk_size = int(np.ceil(len(encounters_gdf) / n_chunks))
        enc_chunks = [encounters_gdf.iloc[i:i + chunk_size].copy() for i in range(0, len(encounters_gdf), chunk_size)]
        scattered_lookup = client.scatter(lookup, broadcast=True)
        tasks = [
            dask.delayed(_generate_encounter_timeseries_chunk)(
                chunk, scattered_lookup, step_seconds, fairway_axis, metric_crs, segments_crs, max_distance_m
            )
            for chunk in enc_chunks
        ]
        results = client.gather(client.compute(tasks))
        valid_results = [r for r in results if not r.empty]
        if not valid_results:
            return gpd.GeoDataFrame({c: [] for c in empty_cols}, geometry=[], crs="EPSG:4326")
        return pd.concat(valid_results, ignore_index=True)

    return _generate_encounter_timeseries_chunk(
        encounters_gdf, lookup, step_seconds=step_seconds, fairway_axis=fairway_axis, metric_crs=metric_crs, segments_crs=segments_crs, max_distance_m=max_distance_m
    )


def extract_stationary_vessels(
    segments_gdf: gpd.GeoDataFrame,
    fairway_axis: Optional[FairwayAxis] = None,
    min_moving_speed: float = 0.5,
) -> gpd.GeoDataFrame:
    """
    Extract stationary / anchored vessels and their dwell locations from trajectory segments.

    Filters segments where vessel speed is below min_moving_speed or fairway_direction is 'stationary'.
    """
    _require_columns(segments_gdf, ['MMSI', 'segment_start_time'], "segments_gdf")
    if segments_gdf.empty:
        return segments_gdf.copy()

    df = segments_gdf.copy()
    if 'fairway_direction' in df.columns:
        is_stat = (df['fairway_direction'] == 'stationary')
    elif 'segment_speed_mps' in df.columns:
        is_stat = (df['segment_speed_mps'] < min_moving_speed)
    elif 'sog' in df.columns:
        is_stat = (df['sog'].fillna(0.0) * 0.514444 < min_moving_speed)
    elif 'segment_duration_s' in df.columns and df.geometry.crs and df.geometry.crs.is_projected:
        dur = np.where(df['segment_duration_s'].values > 0, df['segment_duration_s'].values, 1.0)
        speeds = df.geometry.length.values / dur
        is_stat = (speeds < min_moving_speed)
    else:
        is_stat = pd.Series(False, index=df.index)

    stat_df = df[is_stat].copy().reset_index(drop=True)
    if fairway_axis is not None and not stat_df.empty and 'chainage_m' not in stat_df.columns:
        if stat_df.crs and str(stat_df.crs) != str(fairway_axis.metric_crs):
            pts_metric = stat_df.to_crs(fairway_axis.metric_crs).geometry.centroid.values
        else:
            pts_metric = stat_df.geometry.centroid.values
        coords = shapely.get_coordinates(pts_metric)
        chainages, c_tracks = fairway_axis.project_geometries(coords)
        stat_df['chainage_m'] = np.round(chainages, 1)
        stat_df['chainage_km'] = np.round(chainages / 1000.0, 3)
        stat_df['cross_track_m'] = np.round(c_tracks, 1)

    return stat_df


def run_encounter_detection(
    segments_file: Path,
    output_file: Path,
    max_distance_m: float = 100.0,
    time_bin_minutes: float = 60.0,
    merge_gap_minutes: float = 10.0,
    exclude_stationary: str = 'both',
    min_moving_speed: float = 0.5,
    fairway_axis: Optional[Union[FairwayAxis, str, Path]] = None,
    river_name: str = "MISSISSIPPI-LO",
    timeseries_file: Optional[Path] = None,
    timeseries_step_seconds: float = 30.0,
    stationary_file: Optional[Path] = None,
    metric_crs: Optional[str] = None,
    scheduler: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    max_segment_duration_s: float = 1800.0,
    event_file: Optional[Path] = None,
) -> None:
    """CLI/script entry point: detect_encounters, reading and writing GeoParquet files."""
    if event_file is not None:
        timeseries_file = event_file
    client = None
    if scheduler:
        logger.info(f"Connecting to Dask scheduler at {scheduler}...")
        client = Client(scheduler)
        logger.info(f"Connected to Dask scheduler: {client.dashboard_link}")

    logger.info(f"Loading segments from {segments_file}...")
    segments_gdf = gpd.read_parquet(segments_file)

    if 'segment_end_time' not in segments_gdf.columns and 'segment_duration_s' in segments_gdf.columns:
        segments_gdf['segment_end_time'] = to_utc_datetime(segments_gdf['segment_start_time']) + pd.to_timedelta(segments_gdf['segment_duration_s'], unit='s')

    start_ts = to_utc_datetime(start_time) if start_time is not None else None
    end_ts = to_utc_datetime(end_time) if end_time is not None else None

    if start_ts is not None:
        segments_gdf['segment_start_time'] = to_utc_datetime(segments_gdf['segment_start_time'])
        if 'segment_end_time' in segments_gdf.columns:
            segments_gdf['segment_end_time'] = to_utc_datetime(segments_gdf['segment_end_time'])
            segments_gdf = segments_gdf[segments_gdf['segment_end_time'] >= start_ts]
        else:
            segments_gdf = segments_gdf[segments_gdf['segment_start_time'] >= start_ts]
        logger.info(f"Filtered segments by end_time >= {start_ts}: {len(segments_gdf):,} segments remaining.")

    if end_ts is not None:
        segments_gdf['segment_start_time'] = to_utc_datetime(segments_gdf['segment_start_time'])
        segments_gdf = segments_gdf[segments_gdf['segment_start_time'] < end_ts]
        logger.info(f"Filtered segments by start_time < {end_ts}: {len(segments_gdf):,} segments remaining.")

    axis_obj = None
    if fairway_axis is not None:
        if isinstance(fairway_axis, FairwayAxis):
            axis_obj = fairway_axis
        else:
            logger.info(f"Loading fairway axis from {fairway_axis} (river={river_name})...")
            axis_obj = FairwayAxis.load(fairway_axis, river_name=river_name, metric_crs=metric_crs)

    if stationary_file is not None:
        logger.info(f"Extracting stationary/anchored vessels (min_speed={min_moving_speed} m/s)...")
        stat_gdf = extract_stationary_vessels(segments_gdf, fairway_axis=axis_obj, min_moving_speed=min_moving_speed)
        if stat_gdf.crs is not None and str(stat_gdf.crs) != "EPSG:4326":
            stat_gdf = stat_gdf.to_crs("EPSG:4326")
        vessel_cnt = stat_gdf['MMSI'].nunique() if not stat_gdf.empty else 0
        logger.info(f"Found {len(stat_gdf):,} stationary segments across {vessel_cnt:,} vessels.")
        stationary_file.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"Saving stationary vessels to {stationary_file}...")
        if stationary_file.suffix in {".parquet", ".geoparquet"}:
            stat_gdf.to_parquet(stationary_file)
        elif stationary_file.suffix == ".gpkg":
            stat_gdf.to_file(stationary_file, driver="GPKG")
        elif stationary_file.suffix in {".geojson", ".json"}:
            stat_gdf.to_file(stationary_file, driver="GeoJSON")
        else:
            stat_gdf.to_file(stationary_file)

    logger.info(f"Detecting encounters (max_distance={max_distance_m}m, exclude_stationary={exclude_stationary})...")
    events_gdf = detect_encounters(
        segments_gdf,
        max_distance_m=max_distance_m,
        time_bin_minutes=time_bin_minutes,
        merge_gap_minutes=merge_gap_minutes,
        exclude_stationary=exclude_stationary,
        min_moving_speed=min_moving_speed,
        fairway_axis=axis_obj,
        metric_crs=metric_crs,
        client=client,
        max_segment_duration_s=max_segment_duration_s,
    )

    if start_ts is not None and not events_gdf.empty:
        events_gdf = events_gdf[events_gdf['cpa_time'] >= start_ts].reset_index(drop=True)
    if end_ts is not None and not events_gdf.empty:
        events_gdf = events_gdf[events_gdf['cpa_time'] < end_ts].reset_index(drop=True)

    logger.info(f"Found {len(events_gdf):,} encounter events.")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Saving encounters to {output_file}...")
    if output_file.suffix in {".parquet", ".geoparquet"}:
        events_gdf.to_parquet(output_file)
    elif output_file.suffix == ".gpkg":
        events_gdf.to_file(output_file, driver="GPKG")
    elif output_file.suffix in {".geojson", ".json"}:
        events_gdf.to_file(output_file, driver="GeoJSON")
    else:
        events_gdf.to_file(output_file)

    if timeseries_file is not None and not events_gdf.empty:
        logger.info(f"Generating encounter time series (step={timeseries_step_seconds}s)...")
        ts_gdf = generate_encounter_timeseries(
            segments_gdf,
            events_gdf,
            step_seconds=timeseries_step_seconds,
            fairway_axis=axis_obj,
            metric_crs=metric_crs,
            client=client,
            max_distance_m=max_distance_m,
        )
        logger.info(f"Generated {len(ts_gdf):,} time series connecting lines.")
        timeseries_file.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"Saving encounter time series to {timeseries_file}...")
        if timeseries_file.suffix in {".parquet", ".geoparquet"}:
            ts_gdf.to_parquet(timeseries_file)
        elif timeseries_file.suffix == ".gpkg":
            ts_gdf.to_file(timeseries_file, driver="GPKG")
        elif timeseries_file.suffix in {".geojson", ".json"}:
            ts_gdf.to_file(timeseries_file, driver="GeoJSON")
        else:
            ts_gdf.to_file(timeseries_file)




