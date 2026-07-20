import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point, Polygon, MultiPoint
from ais_shader.events import (
    detect_line_crossings, detect_box_entry_exit, enrich_crossings_with_port_sections,
    build_port_bridge_linestrings,
)

SEGMENT_VESSEL_DEFAULTS = {
    'VesselType': '70', 'VesselGroup': 'Cargo', 'Length': 100.0, 'Width': 20.0, 'Draft': 5.0, 'speed_mps': 5.0
}


def _make_segment(mmsi, trip_id, start_xy, end_xy, start_time, duration_s, sog=10.0):
    row = dict(SEGMENT_VESSEL_DEFAULTS)
    row.update({
        'MMSI': mmsi,
        'trip_id': trip_id,
        'sog': sog,
        'segment_start_time': pd.Timestamp(start_time),
        'segment_end_time': pd.Timestamp(start_time) + pd.Timedelta(seconds=duration_s),
        'segment_duration_s': float(duration_s),
        'geometry': LineString([start_xy, end_xy]),
    })
    return row


def test_detect_line_crossings():
    # Same reference line/direction convention as test_analysis.py's
    # test_passage_crossing, but via the segment-table entry point
    # (detect_line_crossings takes plain EPSG:4326 inputs and handles
    # reprojection/L_x,L_y internally).
    passage_lines_gdf = gpd.GeoDataFrame(
        {'PassageId': ['test-line-1']},
        geometry=[LineString([(-0.05, 0.05), (0.05, 0.05)])],
        crs="EPSG:4326"
    )

    segments_gdf = gpd.GeoDataFrame(
        [
            _make_segment('111', 'ship1_1', (0.0, 0.0), (0.0, 0.1), '2026-06-14 12:00:00', 600, sog=15.0),
            _make_segment('222', 'ship2_1', (0.0, 0.1), (0.0, 0.0), '2026-06-14 12:00:00', 600, sog=20.0),
        ],
        crs="EPSG:4326"
    )

    events = detect_line_crossings(segments_gdf, passage_lines_gdf)

    assert len(events) == 2
    down = events[events['direction'] == 'down'].iloc[0]
    up = events[events['direction'] == 'up'].iloc[0]

    assert down['MMSI'] == '111'
    assert down['PassageId'] == 'test-line-1'
    assert abs(down['event_time'] - pd.Timestamp('2026-06-14 12:05:00')) < pd.Timedelta(seconds=1)
    assert len(down.geometry.geoms) == 1
    assert abs(down.geometry.geoms[0].y - 0.05) < 1e-6

    assert up['MMSI'] == '222'
    assert abs(up['event_time'] - pd.Timestamp('2026-06-14 12:05:00')) < pd.Timedelta(seconds=1)


def test_detect_box_entry_exit():
    box_gdf = gpd.GeoDataFrame(
        {'name': ['test-box']},
        geometry=[Polygon([(0.0, 0.0), (0.0, 0.1), (0.1, 0.1), (0.1, 0.0)])],
        crs="EPSG:4326"
    )

    segments_gdf = gpd.GeoDataFrame(
        [
            # Trip A: enters, dwells (fully inside, no new event), then exits.
            _make_segment('111', 'shipA_1', (-0.05, 0.05), (0.05, 0.05), '2026-06-14 10:00:00', 600),
            _make_segment('111', 'shipA_1', (0.05, 0.05), (0.06, 0.05), '2026-06-14 10:10:00', 120),
            _make_segment('111', 'shipA_1', (0.06, 0.05), (0.16, 0.05), '2026-06-14 10:12:00', 600),
            # Trip B: enters and never leaves (AIS window ends inside).
            _make_segment('222', 'shipB_1', (-0.05, 0.05), (0.05, 0.05), '2026-06-14 11:00:00', 600),
        ],
        crs="EPSG:4326"
    )

    events = detect_box_entry_exit(segments_gdf, box_gdf)

    assert len(events) == 2
    a = events[events['MMSI'] == '111'].iloc[0]
    b = events[events['MMSI'] == '222'].iloc[0]

    # Entry crosses x=0.0 halfway through the first segment (-0.05 -> 0.05).
    assert abs(a['entry_time'] - pd.Timestamp('2026-06-14 10:05:00')) < pd.Timedelta(seconds=1)
    # Exit crosses x=0.1 at 40% through the third segment (0.06 -> 0.16).
    assert abs(a['exit_time'] - pd.Timestamp('2026-06-14 10:16:00')) < pd.Timedelta(seconds=1)
    assert len(a.geometry.geoms) == 2

    assert abs(b['entry_time'] - pd.Timestamp('2026-06-14 11:05:00')) < pd.Timedelta(seconds=1)
    assert pd.isna(b['exit_time'])
    assert len(b.geometry.geoms) == 1


def test_enrich_crossings_with_port_sections():
    line_crossings_gdf = gpd.GeoDataFrame(
        {
            'MMSI': ['111', '222'],
            'event_time': [pd.Timestamp('2026-06-14 09:00:00'), pd.Timestamp('2026-06-14 09:00:00')],
        },
        geometry=[MultiPoint([Point(0, 0)]), MultiPoint([Point(0, 0)])],
        crs="EPSG:4326"
    )

    box_events_gdf = gpd.GeoDataFrame(
        {
            'MMSI': ['111', '111', '222'],
            'name': ['Amazonehaven', 'Europahaven', 'Amazonehaven'],
            'entry_time': [
                pd.Timestamp('2026-06-14 07:30:00'),
                pd.Timestamp('2026-06-14 10:00:00'),
                pd.Timestamp('2026-06-10 07:30:00'),  # far outside the lookback window
            ],
            'exit_time': [
                pd.Timestamp('2026-06-14 08:00:00'),
                pd.NaT,
                pd.Timestamp('2026-06-10 08:00:00'),
            ],
        },
        geometry=[MultiPoint([Point(0, 0)])] * 3,
        crs="EPSG:4326"
    )

    result = enrich_crossings_with_port_sections(
        line_crossings_gdf, box_events_gdf,
        port_box_names=['Amazonehaven', 'Europahaven'],
        max_lookback_hours=24.0, max_lookahead_hours=24.0,
    )

    v111 = result[result['MMSI'] == '111'].iloc[0]
    v222 = result[result['MMSI'] == '222'].iloc[0]

    assert v111['origin_port'] == 'Amazonehaven'
    assert v111['destination_port'] == 'Europahaven'

    # Vessel 222's only Amazonehaven visit is 4 days earlier, outside the window.
    assert pd.isna(v222['origin_port'])
    assert pd.isna(v222['destination_port'])


def test_enrich_crossings_with_port_sections_bridge_box_anchors():
    # Bridge-zone box events (e.g. the "Suurhoffbrug" box) as the anchor
    # table: origin is searched before entry_time, destination after
    # exit_time -- not a single shared instant like the line-crossing case.
    bridge_events_gdf = gpd.GeoDataFrame(
        {
            'MMSI': ['111', '222'],
            'entry_time': [pd.Timestamp('2026-06-14 09:00:00'), pd.Timestamp('2026-06-14 09:00:00')],
            'exit_time': [pd.Timestamp('2026-06-14 09:30:00'), pd.NaT],  # 222 never confirmed exiting the bridge zone
        },
        geometry=[MultiPoint([Point(0, 0)]), MultiPoint([Point(0, 0)])],
        crs="EPSG:4326"
    )

    box_events_gdf = gpd.GeoDataFrame(
        {
            'MMSI': ['111', '111', '222'],
            'name': ['Amazonehaven', 'Europahaven', 'Amazonehaven'],
            'entry_time': [
                pd.Timestamp('2026-06-14 07:00:00'),
                pd.Timestamp('2026-06-14 10:00:00'),
                pd.Timestamp('2026-06-14 07:00:00'),
            ],
            'exit_time': [
                pd.Timestamp('2026-06-14 08:00:00'),
                pd.NaT,
                pd.Timestamp('2026-06-14 08:00:00'),
            ],
        },
        geometry=[MultiPoint([Point(0, 0)])] * 3,
        crs="EPSG:4326"
    )

    result = enrich_crossings_with_port_sections(
        bridge_events_gdf, box_events_gdf,
        port_box_names=['Amazonehaven', 'Europahaven'],
        backward_anchor_col='entry_time', forward_anchor_col='exit_time',
        max_lookback_hours=24.0, max_lookahead_hours=24.0,
    )

    v111 = result[result['MMSI'] == '111'].iloc[0]
    v222 = result[result['MMSI'] == '222'].iloc[0]

    assert v111['origin_port'] == 'Amazonehaven'
    assert v111['destination_port'] == 'Europahaven'

    # 222 has no confirmed exit_time, so the forward (destination) search
    # has no anchor to search from -- origin still resolves from entry_time.
    assert v222['origin_port'] == 'Amazonehaven'
    assert pd.isna(v222['destination_port'])


def test_enrich_crossings_with_port_sections_blocks_stale_match_across_other_crossings():
    # Regression test: a vessel that crosses the bridge twice with no port
    # visit in between must not have the second crossing's origin/
    # destination filled in from a port visit that really belongs to the
    # first crossing -- an intervening bridge crossing (a non-port box
    # event) has to block the nearest-in-time port match, not be invisible
    # to it.
    box_events_gdf = gpd.GeoDataFrame(
        {
            'MMSI': ['333'] * 4,
            'name': ['Mississippihaven', 'Suurhoffbrug', 'Suurhoffbrug', 'Mississippihaven'],
            'entry_time': [
                pd.Timestamp('2026-06-14 06:00:00'),
                pd.Timestamp('2026-06-14 06:20:00'),  # crossing #1: consumes the 06:10 port exit as its origin
                pd.Timestamp('2026-06-14 10:00:00'),  # crossing #2: nothing between it and crossing #1
                pd.Timestamp('2026-06-14 14:00:00'),
            ],
            'exit_time': [
                pd.Timestamp('2026-06-14 06:10:00'),
                pd.Timestamp('2026-06-14 06:25:00'),
                pd.Timestamp('2026-06-14 10:05:00'),
                pd.Timestamp('2026-06-14 14:10:00'),
            ],
        },
        geometry=[MultiPoint([Point(0, 0), Point(0, 1)])] * 4,
        crs="EPSG:4326"
    )
    bridge_events_gdf = box_events_gdf[box_events_gdf['name'] == 'Suurhoffbrug']

    result = enrich_crossings_with_port_sections(
        bridge_events_gdf, box_events_gdf,
        port_box_names=['Mississippihaven'],
        backward_anchor_col='entry_time', forward_anchor_col='exit_time',
        max_lookback_hours=24.0, max_lookahead_hours=24.0,
    )

    crossing_1 = result[result['entry_time'] == pd.Timestamp('2026-06-14 06:20:00')].iloc[0]
    crossing_2 = result[result['entry_time'] == pd.Timestamp('2026-06-14 10:00:00')].iloc[0]

    assert crossing_1['origin_port'] == 'Mississippihaven'
    # Crossing #2 intervenes before any port entry, so crossing #1 gets no destination.
    assert pd.isna(crossing_1['destination_port'])

    # The bug: origin_port must NOT be 'Mississippihaven' here just because
    # it's the nearest port exit within the window -- crossing #1 sits
    # between it and this crossing, so it isn't this crossing's origin.
    assert pd.isna(crossing_2['origin_port'])
    assert pd.isna(crossing_2['origin_entry_point'])
    assert pd.isna(crossing_2['origin_exit_point'])
    assert crossing_2['destination_port'] == 'Mississippihaven'


def test_build_port_bridge_linestrings():
    # Reuse the same enrich_crossings_with_port_sections output shape this
    # builds on: a bridge-zone box event (MultiPoint entry[, exit]) tagged
    # with origin_port/origin_entry_point/origin_exit_point and
    # destination_port/destination_entry_point/destination_exit_point.
    enriched_gdf = gpd.GeoDataFrame(
        {
            'MMSI': ['111', '222'],
            'origin_port': ['Amazonehaven', 'Amazonehaven'],
            'destination_port': ['Europahaven', None],
            'origin_entry_point': [Point(-2, 0), Point(-2, 0)],
            'origin_exit_point': [Point(-1, 0), Point(-1, 0)],
            'destination_entry_point': [Point(1, 0), None],
            'destination_exit_point': [Point(2, 0), None],
        },
        # 111: entry+exit (both legs possible); 222: entry-only (origin leg only).
        geometry=[MultiPoint([Point(0, 0), Point(0.1, 0)]), MultiPoint([Point(0, 0)])],
        crs="EPSG:4326"
    )

    lines_gdf = build_port_bridge_linestrings(enriched_gdf)

    assert len(lines_gdf) == 3  # 111: origin + destination, 222: origin only
    v111_legs = set(lines_gdf[lines_gdf['MMSI'] == '111']['leg'])
    v222_legs = set(lines_gdf[lines_gdf['MMSI'] == '222']['leg'])
    assert v111_legs == {'origin', 'destination'}
    assert v222_legs == {'origin'}

    # Origin leg traces the full recorded path through the origin port box
    # (entry point -> exit point) before continuing to the bridge entry.
    origin_line = lines_gdf[(lines_gdf['MMSI'] == '111') & (lines_gdf['leg'] == 'origin')].iloc[0]
    assert origin_line['port'] == 'Amazonehaven'
    assert list(origin_line.geometry.coords) == [(-2, 0), (-1, 0), (0, 0)]

    # Destination leg continues from the bridge exit through the
    # destination port box's own entry and exit points.
    destination_line = lines_gdf[(lines_gdf['MMSI'] == '111') & (lines_gdf['leg'] == 'destination')].iloc[0]
    assert destination_line['port'] == 'Europahaven'
    assert list(destination_line.geometry.coords) == [(0.1, 0), (1, 0), (2, 0)]


if __name__ == "__main__":
    test_detect_line_crossings()
    test_detect_box_entry_exit()
    test_enrich_crossings_with_port_sections()
    test_enrich_crossings_with_port_sections_bridge_box_anchors()
    test_enrich_crossings_with_port_sections_blocks_stale_match_across_other_crossings()
    test_build_port_bridge_linestrings()
