import numpy as np
import pandas as pd
import dask.dataframe as dd
from dask.distributed import Client
import pytest

from ais_shader.moving_dask.features import calculate_kinematic_features_pandas
from ais_shader.moving_dask.trajectory import trajectorize_dataframe, _calculate_rolling_hull_area

def test_calculate_kinematic_features_pandas():
    # Construct a simple trajectory: straight line northward, 1 ping every 10 seconds
    # EPSG:4326 coords: (0.0, 0.0) -> (0.0, 0.001) -> (0.0, 0.002)
    # Geodesic distance for 0.001 degree of latitude is roughly 111 meters.
    # So speed should be ~ 11.1 m/s.
    df = pd.DataFrame({
        'timestamp': [
            pd.Timestamp('2026-06-14 12:00:00'),
            pd.Timestamp('2026-06-14 12:00:10'),
            pd.Timestamp('2026-06-14 12:00:20')
        ],
        'longitude': [0.0, 0.0, 0.0],
        'latitude': [0.0, 0.001, 0.002],
        'cog': [10.0, 20.0, 350.0],  # 20.0 to 350.0 is -30.0 wrap
        'heading': [0.0, 350.0, 10.0], # 350.0 to 10.0 is +20.0 wrap
    })
    
    res = calculate_kinematic_features_pandas(
        df=df,
        time_col='timestamp',
        x_col='longitude',
        y_col='latitude',
        cog_col='cog',
        heading_col='heading'
    )
    
    assert 'speed_mps' in res.columns
    assert 'acceleration_mps2' in res.columns
    assert 'turn_rate_from_cog' in res.columns
    assert 'turn_rate_from_heading' in res.columns
    
    # First row must be NaN because it has no predecessor
    assert pd.isna(res.iloc[0]['speed_mps'])
    
    # Speed check
    speed1 = res.iloc[1]['speed_mps']
    assert 11.0 < speed1 < 12.0  # roughly ~11.1 m/s
    
    # Turn rate check (degrees/sec)
    # cog change: 20 - 10 = 10 deg over 10 sec -> 1.0 deg/sec
    assert abs(res.iloc[1]['turn_rate_from_cog'] - 1.0) < 0.01
    
    # cog wrap change: 350 - 20 = 330 deg -> shortest is -30 deg over 10 sec -> -3.0 deg/sec
    assert abs(res.iloc[2]['turn_rate_from_cog'] - (-3.0)) < 0.01
    
    # heading wrap change: 350 to 10 -> +20 deg over 10 sec -> 2.0 deg/sec
    assert abs(res.iloc[2]['turn_rate_from_heading'] - 2.0) < 0.01


def test_calculate_rolling_hull_area():
    # 0, 1, or 2 points should yield 0 area
    pts_0 = np.array([])
    pts_1 = np.array([[0.0, 0.0]])
    pts_2 = np.array([[0.0, 0.0], [0.0, 0.001]])
    
    assert _calculate_rolling_hull_area(pts_0) == 0.0
    assert _calculate_rolling_hull_area(pts_1) == 0.0
    assert _calculate_rolling_hull_area(pts_2) == 0.0
    
    # 3 points in a line (collinear) should yield 0 area
    pts_collinear = np.array([[0.0, 0.0], [0.0, 0.001], [0.0, 0.002]])
    assert _calculate_rolling_hull_area(pts_collinear) == 0.0
    
    # 3 points forming a triangle (area should be non-zero)
    pts_triangle = np.array([[0.0, 0.0], [0.0, 0.001], [0.001, 0.0]])
    area = _calculate_rolling_hull_area(pts_triangle)
    assert area > 0.0


def test_trajectorize_dataframe():
    # Start Dask Local Cluster for test
    client = Client(n_workers=1, threads_per_worker=1)
    
    try:
        # Create a small dataset with 2 vessels
        # Vessel 1: stays in place (stops), then moves
        # Vessel 2: moves quickly (continuous trip)
        data = {
            'mmsi': [1, 1, 1, 1, 1, 2, 2, 2],
            'base_date_time': [
                # Vessel 1 pings
                '2025-12-01 00:00:00',
                '2025-12-01 00:05:00',
                '2025-12-01 00:10:00', # stopped during first 10 min
                '2025-12-01 00:30:00', # starts moving (long time gap)
                '2025-12-01 00:40:00',
                # Vessel 2 pings
                '2025-12-01 00:00:00',
                '2025-12-01 00:05:00',
                '2025-12-01 00:10:00'
            ],
            'longitude': [
                0.0, 0.00001, 0.0, 0.05, 0.1,  # V1
                2.0, 2.01, 2.02                # V2
            ],
            'latitude': [
                0.0, 0.0, 0.00001, 0.05, 0.1,  # V1
                4.0, 4.01, 4.02                # V2
            ],
            'cog': [0.0, 0.0, 0.0, 45.0, 45.0, 10.0, 10.0, 10.0],
            'heading': [0, 0, 0, 45, 45, 10, 10, 10],
            'sog': [0.0, 0.1, 0.0, 15.0, 15.0, 10.0, 10.0, 10.0]
        }
        
        df = pd.DataFrame(data)
        ddf = dd.from_pandas(df, npartitions=2)
        
        # Run Dask trajectorize
        res_ddf = trajectorize_dataframe(
            ddf=ddf,
            vessel_id_col='mmsi',
            time_col='base_date_time',
            x_col='longitude',
            y_col='latitude',
            gap_threshold_hours=0.25, # 15 minutes gap threshold
            stop_duration_min=15.0,
            stop_radius_m=2000.0,      # Huge stop radius to trigger stops easily
            shuffle_backend='tasks'
        )
        
        res_df = res_ddf.compute()
        
        assert 'trip_id' in res_df.columns
        assert 'speed_mps' in res_df.columns
        assert 'rolling_area_m2' in res_df.columns
        
        # Check number of unique trips
        # Vessel 1 should be split into multiple trips due to stops and gaps
        # Vessel 2 should be a single trip
        v1_trips = res_df[res_df['mmsi'] == 1]['trip_id'].unique()
        v2_trips = res_df[res_df['mmsi'] == 2]['trip_id'].unique()
        
        assert len(v1_trips) > 1, f"Expected multiple trips for Vessel 1, got {v1_trips}"
        assert len(v2_trips) == 1, f"Expected 1 trip for Vessel 2, got {v2_trips}"
        
    finally:
        client.close()
