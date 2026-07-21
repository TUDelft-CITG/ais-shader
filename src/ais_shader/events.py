import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import Point, MultiPoint
import numpy as np

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
        merged event keeps the first entry_time, the last exit_time, and the
        union of constituent boundary-crossing points.
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


