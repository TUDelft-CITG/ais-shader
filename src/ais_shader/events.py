import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import Point, MultiPoint, LineString
import numpy as np

# --- Event table: passage-line crossings & box entry/exit ---
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
    return gdf.to_crs("EPSG:3857") if gdf.crs.to_string() != "EPSG:3857" else gdf


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
    polygon corner).
    """
    isect = seg_geom.intersection(ref_geom)
    if isect.is_empty:
        point = Point(seg_geom.coords[0])
    elif isinstance(isect, Point):
        point = isect
    else:
        point = isect.centroid
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
    schema parity with detect_box_entry_exit's 1-2 point events.
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

    starts, ends = _segment_start_end_coords(seg_geoms)
    S_x, S_y = ends[:, 0] - starts[:, 0], ends[:, 1] - starts[:, 1]
    L_x_val = passage_lines_3857['L_x'].loc[joined['index_right']].values
    L_y_val = passage_lines_3857['L_y'].loc[joined['index_right']].values
    dot_product = S_x * (-L_y_val) + S_y * L_x_val
    direction = np.where(dot_product >= 0, 'down', 'up')

    event_time = _interpolate_time(
        pd.to_datetime(joined['segment_start_time'].values), joined['segment_duration_s'].values, f_seg_arr
    )

    points_4326 = gpd.GeoSeries(points_3857, crs="EPSG:3857").to_crs("EPSG:4326")
    geometry = [MultiPoint([pt]) for pt in points_4326]

    data = {col: joined[col].values for col in SEGMENT_VESSEL_COLS}
    data['PassageId'] = joined['PassageId'].values
    data['direction'] = direction
    data['event_time'] = event_time
    return gpd.GeoDataFrame(data, geometry=geometry, crs="EPSG:4326").reset_index(drop=True)


def detect_box_entry_exit(
    segments_gdf: gpd.GeoDataFrame, boxes_gdf: gpd.GeoDataFrame, box_id_col: str = "name"
) -> gpd.GeoDataFrame:
    """
    Detect vessel entry into (and potential exit from) polygon "event boxes".

    A vessel may spend many consecutive segments inside a box; entry is the
    outside->inside transition of a single segment's endpoints, exit is the
    next inside->outside transition for the same trip_id within the same
    box. Segments that don't touch a box at all can't be part of a
    transition and are filtered out up front via `gpd.sjoin` (backed by each
    GeoDataFrame's spatial index), so only segments whose 2-point line
    intersects, enters, or exits a box are considered -- this also means we
    never need the full point-by-point trajectory, just the segments that
    already touch the box.

    One output row per entry: geometry is a MultiPoint with the entry point
    alone if no matching exit was found before the trip's segments ran out
    (e.g. the AIS window ends while the vessel is still inside), or the
    entry and exit points together (2 points) otherwise. A trip may produce
    several separate events per box if it enters and exits more than once.
    """
    _require_columns(
        segments_gdf, SEGMENT_VESSEL_COLS + ['segment_start_time', 'segment_duration_s'], "segments_gdf"
    )
    _require_columns(boxes_gdf, [box_id_col], "boxes_gdf")

    empty_cols = SEGMENT_VESSEL_COLS + [box_id_col, 'entry_time', 'exit_time']
    if segments_gdf.empty:
        return gpd.GeoDataFrame({c: [] for c in empty_cols}, geometry=[], crs="EPSG:4326")

    segments_3857 = _to_crs_3857(segments_gdf)
    # As in detect_line_crossings: keep only the id + geometry columns from
    # the reference file to avoid gpd.sjoin silently suffixing any column
    # name shared with the segment table.
    boxes_3857 = _to_crs_3857(boxes_gdf)[[box_id_col, 'geometry']].reset_index(drop=True)
    boundaries = boxes_3857.geometry.boundary

    joined = gpd.sjoin(segments_3857, boxes_3857, predicate='intersects', how='inner')
    if joined.empty:
        return gpd.GeoDataFrame({c: [] for c in empty_cols}, geometry=[], crs="EPSG:4326")

    seg_geoms = joined.geometry.values
    box_geoms = boxes_3857.geometry.loc[joined['index_right']].values
    starts, ends = _segment_start_end_coords(seg_geoms)

    start_inside = shapely.covers(box_geoms, shapely.points(starts))
    end_inside = shapely.covers(box_geoms, shapely.points(ends))

    joined = joined.assign(
        _start_inside=start_inside,
        _end_inside=end_inside,
        _box_key=boxes_3857[box_id_col].loc[joined['index_right']].values,
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
    transitions['_event_time'] = _interpolate_time(
        pd.to_datetime(transitions['segment_start_time'].values),
        transitions['segment_duration_s'].values,
        np.array(f_segs, dtype=float),
    )
    transitions['_is_entry'] = ~transitions['_start_inside'] & transitions['_end_inside']
    transitions = transitions.sort_values(['_box_key', 'trip_id', 'segment_start_time'])

    events = []
    open_entry = None
    for _, row in transitions.iterrows():
        key = (row['_box_key'], row['trip_id'])
        if open_entry is not None and open_entry['key'] != key:
            events.append(_finish_box_event(open_entry, exit_row=None))
            open_entry = None

        if row['_is_entry']:
            if open_entry is not None:
                # A second entry before an exit was seen for the same
                # trip/box (e.g. a concave box boundary graze) -- flush the
                # unmatched entry as entry-only before starting the new one.
                events.append(_finish_box_event(open_entry, exit_row=None))
            open_entry = {'key': key, 'row': row}
        else:
            if open_entry is not None and open_entry['key'] == key:
                events.append(_finish_box_event(open_entry, exit_row=row))
                open_entry = None
            # An exit with no open entry means the trip started already
            # inside the box (no earlier segment to detect entry from) --
            # nothing to pair it with, so it's dropped.

    if open_entry is not None:
        events.append(_finish_box_event(open_entry, exit_row=None))

    if not events:
        return gpd.GeoDataFrame({c: [] for c in empty_cols}, geometry=[], crs="EPSG:4326")

    data = {col: [] for col in SEGMENT_VESSEL_COLS}
    data[box_id_col] = []
    data['entry_time'] = []
    data['exit_time'] = []
    geometry = []
    for entry_row, exit_row, points_3857_pair in events:
        for col in SEGMENT_VESSEL_COLS:
            data[col].append(entry_row[col])
        data[box_id_col].append(entry_row['_box_key'])
        data['entry_time'].append(entry_row['_event_time'])
        data['exit_time'].append(exit_row['_event_time'] if exit_row is not None else pd.NaT)
        points_4326 = gpd.GeoSeries(points_3857_pair, crs="EPSG:3857").to_crs("EPSG:4326")
        geometry.append(MultiPoint(list(points_4326)))

    return gpd.GeoDataFrame(data, geometry=geometry, crs="EPSG:4326").reset_index(drop=True)


def _finish_box_event(open_entry: dict, exit_row) -> tuple:
    entry_row = open_entry['row']
    points_3857 = [entry_row['_point_3857']]
    if exit_row is not None:
        points_3857.append(exit_row['_point_3857'])
    return entry_row, exit_row, points_3857


def _asof_join_nullable(
    base: pd.DataFrame, anchor_col: str, other: pd.DataFrame, other_time_col: str, direction: str, tolerance
) -> pd.DataFrame:
    """pd.merge_asof, but rows with a null anchor come back with a null match instead of erroring.

    merge_asof requires its "on" column to be non-null; a box entry/exit
    event's exit_time can be NaT (see detect_box_entry_exit's entry-only
    case), so rows using that as the forward-search anchor need to be
    split off and rejoined afterward rather than passed straight through.

    `other`'s time column is renamed to a private, always-unique name before
    the merge: when `base` is itself a box entry/exit table (both
    entry_time and exit_time present), `other_time_col` can collide with
    one of `base`'s own columns even though it's a different logical field
    (e.g. anchoring on entry_time while matching against port exit_time),
    and merge_asof would otherwise silently suffix both into
    exit_time_x/exit_time_y instead of erroring.
    """
    has_anchor = base.dropna(subset=[anchor_col]).sort_values(anchor_col)
    other = other.rename(columns={other_time_col: '_asof_ref_time'})
    matched = pd.merge_asof(
        has_anchor, other,
        left_on=anchor_col, right_on='_asof_ref_time',
        by='MMSI', direction=direction, tolerance=tolerance,
    ).drop(columns=['_asof_ref_time'])
    without_anchor = base[base[anchor_col].isna()]
    return pd.concat([matched, without_anchor], ignore_index=True)


def enrich_crossings_with_port_sections(
    crossings_gdf: gpd.GeoDataFrame,
    box_events_gdf: gpd.GeoDataFrame,
    port_box_names: list,
    box_id_col: str = "name",
    backward_anchor_col: str = "event_time",
    forward_anchor_col: str = "event_time",
    max_lookback_hours: float = 24.0,
    max_lookahead_hours: float = 24.0,
) -> gpd.GeoDataFrame:
    """
    Tag each crossing event (e.g. a bridge passage, or a bridge-zone box
    entry/exit) with the port section it most likely originated from and is
    headed to, using only MMSI + nearest-in-time matching against box
    entry/exit events (not trip_id): a vessel's port stop and its bridge
    passage can end up as separate trips if `trajectory compute`'s gap
    threshold splits the voyage during loading/unloading, so a trip_id join
    would miss those.

    `origin_port` = the `port_box_names` box whose exit_time is the closest
    one before `crossings_gdf[backward_anchor_col]` for the same MMSI
    (within `max_lookback_hours`); `destination_port` = the closest box
    entry_time after `crossings_gdf[forward_anchor_col]` (within
    `max_lookahead_hours`). Either can be null if no matching box event
    falls within the window, if the nearest preceding box visit never got a
    confirmed exit (detect_box_entry_exit's entry-only case), if the anchor
    column itself is null for that row (e.g. forward_anchor_col='exit_time'
    on a bridge-zone event with no confirmed exit), OR if the truly nearest
    neighboring box event isn't a port at all (see below) -- a vessel that
    shuttles back and forth through the bridge zone many times without
    revisiting a port in between must not have its origin/destination
    filled in from some other, non-adjacent bridge crossing's port visit.

    The nearest-neighbor search runs against *every* box in
    `box_events_gdf`, not just `port_box_names`: if the box event truly
    closest in time to this crossing is another bridge-zone crossing rather
    than a port, that's a real intervening event, and the nearest *port*
    visit beyond it almost certainly belongs to that other crossing instead
    of this one -- attributing it here anyway would be a stale, spuriously
    distant match. Restricting the search to ports upfront can't see that
    intervening event and would wrongly reuse it.

    Also attaches the matched port visit's own points (plain shapely
    Points, not the GeoDataFrame's primary geometry) -- the actual places
    the vessel crossed that box's boundary, not a box centroid, so callers
    like build_port_bridge_linestrings can draw the real recorded path
    through the port box rather than stopping at a single point:
    `origin_entry_point`/`origin_exit_point` (the port visit always has
    both, since a visit only counts as a candidate origin once it has a
    confirmed exit -- see detect_box_entry_exit's entry-only case) and
    `destination_entry_point`/`destination_exit_point` (the latter is null
    if that particular port visit has no confirmed exit yet). All are null
    whenever origin_port/destination_port are.

    For a plain line-crossing table (one instant per event), leave both
    anchor columns at their default 'event_time'. For a bridge-zone *box*
    entry/exit table (see detect_box_entry_exit), pass
    backward_anchor_col='entry_time', forward_anchor_col='exit_time' so the
    origin is searched before the vessel entered the bridge zone and the
    destination after it left, rather than relative to a single instant.
    """
    _require_columns(crossings_gdf, ['MMSI', backward_anchor_col, forward_anchor_col], "crossings_gdf")
    _require_columns(box_events_gdf, ['MMSI', box_id_col, 'entry_time', 'exit_time'], "box_events_gdf")

    exits = (
        box_events_gdf.dropna(subset=['exit_time'])[['MMSI', box_id_col, 'exit_time', 'geometry']]
        .rename(columns={box_id_col: '_origin_box'})
        .assign(
            origin_entry_point=lambda d: [mp.geoms[0] for mp in d['geometry']],
            origin_exit_point=lambda d: [mp.geoms[-1] for mp in d['geometry']],
        )
        .drop(columns=['geometry'])
        .sort_values('exit_time')
    )
    entries = (
        box_events_gdf[['MMSI', box_id_col, 'entry_time', 'geometry']]
        .rename(columns={box_id_col: '_destination_box'})
        .assign(
            destination_entry_point=lambda d: [mp.geoms[0] for mp in d['geometry']],
            destination_exit_point=lambda d: [mp.geoms[-1] if len(mp.geoms) > 1 else None for mp in d['geometry']],
        )
        .drop(columns=['geometry'])
        .sort_values('entry_time')
    )

    crossings = pd.DataFrame(crossings_gdf).reset_index(drop=True)
    crossings['_row_id'] = crossings.index

    with_origin = _asof_join_nullable(
        crossings, backward_anchor_col, exits, 'exit_time', 'backward', pd.Timedelta(hours=max_lookback_hours)
    )
    with_both = _asof_join_nullable(
        with_origin, forward_anchor_col, entries, 'entry_time', 'forward', pd.Timedelta(hours=max_lookahead_hours)
    )

    # Only keep the match if the truly nearest neighboring event is a port;
    # otherwise something else (most often another bridge crossing) sits
    # between this crossing and the nearest port visit, so that visit
    # belongs to the other crossing, not this one.
    is_origin_port = with_both['_origin_box'].isin(port_box_names)
    is_destination_port = with_both['_destination_box'].isin(port_box_names)
    with_both['origin_port'] = with_both['_origin_box'].where(is_origin_port)
    with_both['destination_port'] = with_both['_destination_box'].where(is_destination_port)
    with_both.loc[~is_origin_port, ['origin_entry_point', 'origin_exit_point']] = None
    with_both.loc[~is_destination_port, ['destination_entry_point', 'destination_exit_point']] = None
    with_both = with_both.drop(columns=['_origin_box', '_destination_box'])

    with_both = with_both.sort_values('_row_id').drop(columns=['_row_id']).reset_index(drop=True)
    return gpd.GeoDataFrame(with_both, geometry='geometry', crs=crossings_gdf.crs)


def build_port_bridge_linestrings(enriched_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Turn enrich_crossings_with_port_sections' output into one LineString per
    matched leg, tracing the vessel's actual recorded path through the port
    box (both the point where it crossed into the box and the point where
    it crossed back out), not just a single point -- stopping at one side
    of the port box and never reaching the other would misrepresent a
    real, fully-detected crossing as a half-finished one.

    'origin' leg: [origin_entry_point, origin_exit_point, bridge_entry_point]
    (always 3 points: a port visit only counts as a candidate origin once
    it has a confirmed exit). 'destination' leg:
    [bridge_exit_point, destination_entry_point, destination_exit_point]
    when that port visit also has a confirmed exit, else just
    [bridge_exit_point, destination_entry_point] (2 points) if the vessel's
    subsequent stay there is still open-ended in this data. A crossing with
    both origin_port and destination_port matched produces two rows; one
    with neither matched contributes no rows.
    """
    _require_columns(enriched_gdf, ['MMSI', 'geometry'], "enriched_gdf")
    other_cols = [c for c in enriched_gdf.columns if c not in (
        'geometry', 'origin_port', 'destination_port',
        'origin_entry_point', 'origin_exit_point', 'destination_entry_point', 'destination_exit_point',
    )]

    rows = []
    for _, row in enriched_gdf.iterrows():
        bridge_points = list(row.geometry.geoms)
        bridge_entry_point = bridge_points[0]
        bridge_exit_point = bridge_points[-1] if len(bridge_points) > 1 else None

        if pd.notna(row.get('origin_port')):
            rows.append({
                **{c: row[c] for c in other_cols},
                'leg': 'origin', 'port': row['origin_port'],
                'geometry': LineString([row['origin_entry_point'], row['origin_exit_point'], bridge_entry_point]),
            })
        if bridge_exit_point is not None and pd.notna(row.get('destination_port')):
            destination_points = [bridge_exit_point, row['destination_entry_point']]
            if row.get('destination_exit_point') is not None:
                destination_points.append(row['destination_exit_point'])
            rows.append({
                **{c: row[c] for c in other_cols},
                'leg': 'destination', 'port': row['destination_port'],
                'geometry': LineString(destination_points),
            })

    if not rows:
        return gpd.GeoDataFrame({c: [] for c in other_cols + ['leg', 'port']}, geometry=[], crs=enriched_gdf.crs)
    return gpd.GeoDataFrame(rows, crs=enriched_gdf.crs)
