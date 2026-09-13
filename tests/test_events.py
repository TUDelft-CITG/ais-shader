import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Polygon
from ais_shader.events import (
    detect_line_crossings,
    detect_polygon_entry_exit,
    detect_encounters,
    compute_segment_cpa,
    classify_encounter,
    generate_encounter_timeseries,
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


def test_detect_polygon_entry_exit_merge_gap():
    polygon_gdf = gpd.GeoDataFrame(
        {'name': ['test-polygon']},
        geometry=[Polygon([(0.0, 0.0), (0.0, 0.1), (0.1, 0.1), (0.1, 0.0)])],
        crs="EPSG:4326"
    )

    # Vessel enters, exits, re-enters 3 minutes later, exits, re-enters 15 minutes later.
    segments_gdf = gpd.GeoDataFrame(
        [
            # Visit 1: Entry 10:05, Exit 10:16
            _make_segment('111', 'shipA_1', (-0.05, 0.05), (0.05, 0.05), '2026-06-14 10:00:00', 600),
            _make_segment('111', 'shipA_1', (0.06, 0.05), (0.16, 0.05), '2026-06-14 10:12:00', 600),
            # Visit 2: Gap of 3 mins (Exit 10:16 to Entry 10:19). Entry 10:19, Exit 10:24
            _make_segment('111', 'shipA_2', (0.15, 0.05), (0.05, 0.05), '2026-06-14 10:18:00', 300),
            _make_segment('111', 'shipA_2', (0.05, 0.05), (-0.05, 0.05), '2026-06-14 10:23:00', 300),
            # Visit 3: Gap of 16 mins (Exit 10:24 to Entry 10:40). Entry 10:40, Exit 10:46
            _make_segment('111', 'shipA_3', (-0.05, 0.05), (0.05, 0.05), '2026-06-14 10:35:00', 600),
            _make_segment('111', 'shipA_3', (0.06, 0.05), (0.16, 0.05), '2026-06-14 10:42:00', 600),
        ],
        crs="EPSG:4326"
    )

    # Without merging: 3 events
    unmerged = detect_polygon_entry_exit(segments_gdf, polygon_gdf)
    assert len(unmerged) == 3

    # With merge_gap_minutes=5.0: Visit 1 & 2 merge (3 min gap <= 5 min), Visit 3 remains separate (16 min gap > 5 min)
    merged = detect_polygon_entry_exit(segments_gdf, polygon_gdf, merge_gap_minutes=5.0)
    assert len(merged) == 2

    e1 = merged.iloc[0]
    # First merged event spans Visit 1 entry to Visit 2 exit
    assert abs(e1['entry_time'] - pd.Timestamp('2026-06-14 10:05:00')) < pd.Timedelta(seconds=1)
    assert abs(e1['exit_time'] - pd.Timestamp('2026-06-14 10:25:30')) < pd.Timedelta(seconds=1)
    assert len(e1.geometry.geoms) == 2

    e2 = merged.iloc[1]
    assert abs(e2['entry_time'] - pd.Timestamp('2026-06-14 10:40:00')) < pd.Timedelta(seconds=1)
    assert abs(e2['exit_time'] - pd.Timestamp('2026-06-14 10:46:00')) < pd.Timedelta(seconds=1)


def test_classify_encounter():
    assert classify_encounter(0.0, 180.0) == 'head-on'
    assert classify_encounter(10.0, 170.0) == 'head-on'
    assert classify_encounter(90.0, 95.0) == 'overtaking'
    assert classify_encounter(5.0, 355.0) == 'overtaking'
    assert classify_encounter(0.0, 90.0) == 'crossing'
    assert classify_encounter(45.0, 135.0) == 'crossing'
    assert classify_encounter(0.0, 0.0, speed1=0.1, speed2=0.1) == 'stationary'


def test_compute_segment_cpa():
    import numpy as np
    # Vessel 1 moves North from (0, 0) to (0, 1000) over 100s (speed 10 m/s)
    # Vessel 2 moves South from (100, 1000) to (100, 0) over 100s (speed 10 m/s)
    t0 = pd.Timestamp('2026-06-14 12:00:00')
    t1 = pd.Timestamp('2026-06-14 12:01:40')
    res = compute_segment_cpa(
        np.array([0.0, 0.0]), np.array([0.0, 1000.0]), t0, t1,
        np.array([100.0, 1000.0]), np.array([100.0, 0.0]), t0, t1,
    )
    assert res is not None
    cpa_dist, cpa_time, p1_cpa, p2_cpa, mid_cpa, h1, h2, sp1, sp2, enc_type, ds_start, ds_end, dv_along = res
    assert abs(cpa_dist - 100.0) < 1e-4
    assert abs(cpa_time - pd.Timestamp('2026-06-14 12:00:50')) < pd.Timedelta(seconds=1)
    assert enc_type == 'head-on'
    assert abs(mid_cpa[0] - 50.0) < 1e-4
    assert abs(mid_cpa[1] - 500.0) < 1e-4


def test_detect_encounters_head_on():
    # 2 vessels traveling in opposite directions along a channel (close in space & time)
    # Coordinates in EPSG:4326 around longitude 0, latitude 50
    segments_gdf = gpd.GeoDataFrame(
        [
            # Ship 1: moving North
            _make_segment('111', 'ship1', (0.0, 50.0), (0.0, 50.01), '2026-06-14 12:00:00', 600, sog=10.0),
            # Ship 2: moving South
            _make_segment('222', 'ship2', (0.001, 50.01), (0.001, 50.0), '2026-06-14 12:00:00', 600, sog=10.0),
        ],
        crs="EPSG:4326"
    )

    encounters = detect_encounters(segments_gdf, max_distance_m=500.0)
    assert len(encounters) == 1
    enc = encounters.iloc[0]
    assert enc['mmsi_1'] == '111'
    assert enc['mmsi_2'] == '222'
    assert enc['encounter_type'] == 'head-on'
    assert enc['min_distance_m'] < 200.0
    assert abs(enc['cpa_time'] - pd.Timestamp('2026-06-14 12:05:00')) < pd.Timedelta(seconds=2)


def test_detect_encounters_overtaking():
    # Ship 1 faster, overtaking Ship 2 traveling in the same direction
    segments_gdf = gpd.GeoDataFrame(
        [
            # Ship 1: faster (0.0 -> 0.02)
            _make_segment('111', 'ship1', (0.0, 50.0), (0.0, 50.02), '2026-06-14 12:00:00', 600, sog=20.0),
            # Ship 2: slower (0.005 -> 0.015)
            _make_segment('222', 'ship2', (0.0005, 50.005), (0.0005, 50.015), '2026-06-14 12:00:00', 600, sog=10.0),
        ],
        crs="EPSG:4326"
    )

    encounters = detect_encounters(segments_gdf, max_distance_m=500.0)
    assert len(encounters) == 1
    enc = encounters.iloc[0]
    assert enc['encounter_type'] == 'overtaking'
    assert enc['overtaking_mmsi'] == '111'
    assert enc['overtaken_mmsi'] == '222'
    assert enc['min_distance_m'] < 200.0


def test_detect_encounters_parallel_sailing():
    # Two ships cruising side-by-side at the exact same speed (co-sailing/convoy, no passing)
    segments_gdf = gpd.GeoDataFrame(
        [
            # Ship 1: (0.0 -> 0.01) at 10 knots
            _make_segment('111', 'ship1', (0.0, 50.0), (0.0, 50.01), '2026-06-14 12:00:00', 600, sog=10.0),
            # Ship 2: (0.0005 -> 0.0105) alongside at identical speed
            _make_segment('222', 'ship2', (0.0005, 50.0), (0.0005, 50.01), '2026-06-14 12:00:00', 600, sog=10.0),
        ],
        crs="EPSG:4326"
    )

    encounters = detect_encounters(segments_gdf, max_distance_m=500.0)
    assert len(encounters) == 1
    enc = encounters.iloc[0]
    assert enc['encounter_type'] == 'parallel_sailing'
    assert enc['overtaking_mmsi'] is None
    assert enc['overtaken_mmsi'] is None



def test_detect_encounters_spatial_filtering():
    # Vessels at the same time, but spatially far apart (~100km)
    segments_gdf = gpd.GeoDataFrame(
        [
            _make_segment('111', 'ship1', (0.0, 50.0), (0.0, 50.01), '2026-06-14 12:00:00', 600),
            _make_segment('222', 'ship2', (1.0, 50.0), (1.0, 50.01), '2026-06-14 12:00:00', 600),
        ],
        crs="EPSG:4326"
    )
    encounters = detect_encounters(segments_gdf, max_distance_m=500.0)
    assert len(encounters) == 0


def test_detect_encounters_temporal_filtering():
    # Vessels on the exact same line, but 5 hours apart
    segments_gdf = gpd.GeoDataFrame(
        [
            _make_segment('111', 'ship1', (0.0, 50.0), (0.0, 50.01), '2026-06-14 10:00:00', 600),
            _make_segment('222', 'ship2', (0.0, 50.01), (0.0, 50.0), '2026-06-14 15:00:00', 600),
        ],
        crs="EPSG:4326"
    )
    encounters = detect_encounters(segments_gdf, max_distance_m=500.0)
    assert len(encounters) == 0


def test_detect_encounters_merge_consecutive():
    # 2 vessels traveling alongside each other over 2 consecutive segments
    segments_gdf = gpd.GeoDataFrame(
        [
            # Seg 1: 12:00 - 12:05
            _make_segment('111', 'ship1', (0.0, 50.0), (0.0, 50.005), '2026-06-14 12:00:00', 300),
            _make_segment('222', 'ship2', (0.0005, 50.0), (0.0005, 50.005), '2026-06-14 12:00:00', 300),
            # Seg 2: 12:05 - 12:10 (gap = 0 min)
            _make_segment('111', 'ship1', (0.0, 50.005), (0.0, 50.01), '2026-06-14 12:05:00', 300),
            _make_segment('222', 'ship2', (0.0005, 50.005), (0.0005, 50.01), '2026-06-14 12:05:00', 300),
        ],
        crs="EPSG:4326"
    )

    # Without merge: 2 proximity segments
    raw = detect_encounters(segments_gdf, max_distance_m=500.0, merge_gap_minutes=None)
    assert len(raw) == 2

    # With merge: 1 single merged encounter covering 12:00 - 12:10
    merged = detect_encounters(segments_gdf, max_distance_m=500.0, merge_gap_minutes=10.0)
    assert len(merged) == 1
    m = merged.iloc[0]
    assert m['start_time'] == pd.Timestamp('2026-06-14 12:00:00')
    assert m['end_time'] == pd.Timestamp('2026-06-14 12:10:00')


def test_generate_encounter_timeseries():
    # 2 vessels: Ship 1 moving North (0.0, 50.0) -> (0.0, 50.01) from 12:00:00 to 12:10:00 (600s)
    #            Ship 2 moving South (0.001, 50.01) -> (0.001, 50.0) from 12:00:00 to 12:10:00 (600s)
    segments_gdf = gpd.GeoDataFrame(
        [
            _make_segment('111', 'ship1', (0.0, 50.0), (0.0, 50.01), '2026-06-14 12:00:00', 600, sog=10.0),
            _make_segment('222', 'ship2', (0.001, 50.01), (0.001, 50.0), '2026-06-14 12:00:00', 600, sog=10.0),
        ],
        crs="EPSG:4326"
    )

    encounters = detect_encounters(segments_gdf, max_distance_m=500.0)
    assert len(encounters) == 1
    assert encounters.iloc[0]['encounter_id'] == 0

    # Generate time series sampled every 60s
    ts_gdf = generate_encounter_timeseries(segments_gdf, encounters, step_seconds=60.0)
    assert not ts_gdf.empty
    assert len(ts_gdf) >= 5
    assert all(ts_gdf['distance_m'] <= 500.1)
    assert 'geometry' in ts_gdf.columns
    assert ts_gdf.geometry.geom_type.unique().tolist() == ['LineString']
    assert all(ts_gdf['encounter_id'] == 0)
    assert any(ts_gdf['is_cpa'])

    # Check that distance is lowest at CPA
    cpa_row = ts_gdf[ts_gdf['is_cpa']].iloc[0]
    assert cpa_row['distance_m'] <= ts_gdf['distance_m'].min() + 0.1

    # Verify float step_seconds works without frequency string errors
    ts_float_gdf = generate_encounter_timeseries(segments_gdf, encounters, step_seconds=15.5)
    assert not ts_float_gdf.empty
    assert len(ts_float_gdf) > len(ts_gdf)


def test_detect_encounters_custom_metric_crs():
    segments_gdf = gpd.GeoDataFrame(
        [
            _make_segment('111', 'ship1', (0.0, 50.0), (0.0, 50.01), '2026-06-14 12:00:00', 600, sog=10.0),
            _make_segment('222', 'ship2', (0.001, 50.01), (0.001, 50.0), '2026-06-14 12:00:00', 600, sog=10.0),
        ],
        crs="EPSG:4326"
    )

    encounters = detect_encounters(segments_gdf, max_distance_m=500.0, metric_crs="EPSG:32631")
    assert len(encounters) == 1
    assert encounters.iloc[0]['encounter_type'] == 'head-on'
    assert encounters.iloc[0]['min_distance_m'] < 200.0

    ts = generate_encounter_timeseries(segments_gdf, encounters, step_seconds=30.0, metric_crs="EPSG:32631")
    assert not ts.empty


def test_detect_encounters_dask_client():
    from dask.distributed import Client
    client = Client(n_workers=2, threads_per_worker=1)
    try:
        segments_gdf = gpd.GeoDataFrame(
            [
                _make_segment('111', 'ship1', (0.0, 50.0), (0.0, 50.01), '2026-06-14 12:00:00', 600, sog=10.0),
                _make_segment('222', 'ship2', (0.001, 50.01), (0.001, 50.0), '2026-06-14 12:00:00', 600, sog=10.0),
            ],
            crs="EPSG:4326"
        )
        encounters = detect_encounters(segments_gdf, max_distance_m=500.0, client=client)
        assert len(encounters) == 1
        assert encounters.iloc[0]['encounter_type'] == 'head-on'

        ts = generate_encounter_timeseries(segments_gdf, encounters, step_seconds=30.0, client=client)
        assert not ts.empty
    finally:
        client.close()


def test_detect_encounters_exclude_stationary_modes():
    from ais_shader.events import extract_stationary_vessels

    # 3 vessels:
    # A is stationary at (0.0, 50.0)
    # B is stationary at (0.001, 50.0)
    # C is moving past them: (0.0005, 49.99) -> (0.0005, 50.01) at 10 kn
    segments_gdf = gpd.GeoDataFrame(
        [
            _make_segment('111', 'shipA', (0.0, 50.0), (0.0, 50.0), '2026-06-14 12:00:00', 600, sog=0.0),
            _make_segment('222', 'shipB', (0.001, 50.0), (0.001, 50.0), '2026-06-14 12:00:00', 600, sog=0.0),
            _make_segment('333', 'shipC', (0.0005, 49.99), (0.0005, 50.01), '2026-06-14 12:00:00', 600, sog=10.0),
        ],
        crs="EPSG:4326"
    )

    # 1. Mode 'both': drops A-B (both stationary), keeps C-A and C-B (moving vs stationary target)
    enc_both = detect_encounters(segments_gdf, max_distance_m=1000.0, exclude_stationary='both')
    assert len(enc_both) == 2
    pairs = set(zip(enc_both['mmsi_1'], enc_both['mmsi_2']))
    assert ('111', '222') not in pairs  # A-B dropped
    assert ('111', '333') in pairs      # A-C kept
    assert ('222', '333') in pairs      # B-C kept

    # Verify stationary flags
    row_ac = enc_both[enc_both['mmsi_1'] == '111'].iloc[0]
    assert bool(row_ac['is_stationary_1']) is True
    assert bool(row_ac['is_stationary_2']) is False
    assert row_ac['stationary_role'] == 'vessel_1'

    # 2. Mode 'any': drops all encounters involving a stationary vessel -> 0 encounters
    enc_any = detect_encounters(segments_gdf, max_distance_m=1000.0, exclude_stationary='any')
    assert len(enc_any) == 0

    # 3. Mode 'none': keeps all 3 pairs (including A-B)
    enc_none = detect_encounters(segments_gdf, max_distance_m=1000.0, exclude_stationary='none')
    assert len(enc_none) == 3

    # 4. Extraction of stationary vessels
    stat_vessels = extract_stationary_vessels(segments_gdf, min_moving_speed=0.5)
    assert len(stat_vessels) == 2
    assert set(stat_vessels['MMSI']) == {'111', '222'}


def test_encounter_roles_and_timeseries_attributes():
    # Moving vessel '333' passing stationary vessel '111'
    segments_gdf = gpd.GeoDataFrame(
        [
            # Ship 1: stationary at (0, 50)
            _make_segment('111', 'ship1', (0.0, 50.0), (0.0, 50.0), '2026-06-14 12:00:00', 600, sog=0.0),
            # Ship 2: moving past at (0.001, 50.0) -> (0.001, 50.01)
            _make_segment('333', 'ship2', (0.001, 50.0), (0.001, 50.01), '2026-06-14 12:00:00', 600, sog=10.0),
        ],
        crs="EPSG:4326"
    )

    encounters = detect_encounters(segments_gdf, max_distance_m=500.0, exclude_stationary='both')
    assert len(encounters) == 1
    enc = encounters.iloc[0]

    assert enc['mmsi_1'] == '111'
    assert enc['mmsi_2'] == '333'
    assert enc['source_mmsi'] == '333'  # Active moving vessel
    assert enc['target_mmsi'] == '111'  # Stationary target obstacle
    assert enc['role_1'] == 'stationary'
    assert enc['role_2'] == 'moving'
    assert bool(enc['is_stationary_1']) is True
    assert bool(enc['is_stationary_2']) is False

    # Generate timeseries
    ts = generate_encounter_timeseries(segments_gdf, encounters, step_seconds=60.0)
    assert not ts.empty
    assert 'source_mmsi' in ts.columns
    assert 'target_mmsi' in ts.columns
    assert 'role_1' in ts.columns
    assert 'role_2' in ts.columns
    assert 'is_stationary_1' in ts.columns
    assert 'is_stationary_2' in ts.columns
    assert all(ts['source_mmsi'] == '333')
    assert all(ts['target_mmsi'] == '111')
    assert all(ts['is_stationary_1'] == True)
    assert all(ts['is_stationary_2'] == False)


def test_detect_encounters_overtaking_outside_fairway():
    from shapely.geometry import LineString
    from ais_shader.fairway import FairwayAxis

    # Fairway centerline far away (Mississippi River ~150km east)
    centerline = LineString([(-89.5, 29.0), (-89.5, 30.0)])
    axis = FairwayAxis(centerline, metric_crs="EPSG:32616")

    # Ship 1: slow tug (367189510), starts ahead
    # Ship 2: fast tug (367542760), starts behind and overtakes
    segments_gdf = gpd.GeoDataFrame(
        [
            _make_segment('367189510', 'slow_tug', (-91.065, 29.625), (-91.039, 29.625), '2026-03-31 00:00:00', 1800, sog=2.9),
            _make_segment('367542760', 'fast_tug', (-91.070, 29.625), (-91.005, 29.625), '2026-03-31 00:00:00', 1800, sog=6.9),
        ],
        crs="EPSG:4326"
    )

    encounters = detect_encounters(segments_gdf, max_distance_m=500.0, fairway_axis=axis)
    assert len(encounters) == 1
    enc = encounters.iloc[0]

    assert enc['encounter_type'] == 'overtaking'
    assert str(enc['overtaking_mmsi']) == '367542760'
    assert str(enc['overtaken_mmsi']) == '367189510'
    assert str(enc['source_mmsi']) == '367542760'
    assert str(enc['target_mmsi']) == '367189510'
    assert enc['role_1'] == 'overtaken'
    assert enc['role_2'] == 'overtaking'


def test_detect_encounters_missing_crs():
    segments_gdf = gpd.GeoDataFrame(
        [
            _make_segment('111', 'ship1', (0.0, 0.0), (0.0, 0.1), '2026-06-14 12:00:00', 600),
        ]
    )
    segments_gdf.crs = None
    with pytest.raises(ValueError, match="segments_gdf must have a defined Coordinate Reference System"):
        detect_encounters(segments_gdf)
