import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Polygon
from ais_shader.events import detect_line_crossings, detect_polygon_entry_exit

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


def test_detect_polygon_entry_exit():
    polygon_gdf = gpd.GeoDataFrame(
        {'name': ['test-polygon']},
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

    events = detect_polygon_entry_exit(segments_gdf, polygon_gdf)

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


if __name__ == "__main__":
    test_detect_line_crossings()
    test_detect_polygon_entry_exit()
