import geopandas as gpd
import pandas as pd
import numpy as np
from shapely.geometry import LineString
from ais_shader.analysis import process_partition

def test_passage_crossing():
    # 1. Create a synthetic passage line: horizontal line crossing longitude 0.0 at latitude 0.05
    # Let's define it in EPSG:4326 and convert to EPSG:3857
    passage_line_gdf = gpd.GeoDataFrame(
        {'PassageId': ['test-line-1']},
        geometry=[LineString([(-0.05, 0.05), (0.05, 0.05)])],
        crs="EPSG:4326"
    ).to_crs("EPSG:3857")
    
    # Precompute L_x and L_y vectors
    coords_list = [g.coords for g in passage_line_gdf.geometry]
    passage_line_gdf['L_x'] = np.array([c[-1][0] - c[0][0] for c in coords_list])
    passage_line_gdf['L_y'] = np.array([c[-1][1] - c[0][1] for c in coords_list])
    
    # 2. Create synthetic vessel tracks crossing the line:
    # Track 1 (vessel-1): (0.0, 0.0) -> (0.0, 0.1) (crossing upward, direction='up')
    # Track 2 (vessel-2): (0.0, 0.1) -> (0.0, 0.0) (crossing downward, direction='down')
    df = pd.DataFrame({
        'track_id': ['vessel-1', 'vessel-1', 'vessel-2', 'vessel-2'],
        'longitude': [0.0, 0.0, 0.0, 0.0],
        'latitude': [0.0, 0.1, 0.1, 0.0],
        'timestamp': [
            pd.Timestamp('2026-06-14 12:00:00'), pd.Timestamp('2026-06-14 12:10:00'),
            pd.Timestamp('2026-06-14 12:00:00'), pd.Timestamp('2026-06-14 12:10:00')
        ],
        'sog': [10.0, 20.0, 15.0, 25.0]
    })
    
    # 3. Process the partition
    minx, miny, maxx, maxy = passage_line_gdf.total_bounds
    res = process_partition(df, passage_line_gdf, minx, miny, maxx, maxy, max_time_gap_seconds=7200.0)
    
    # 4. Verify results
    assert not res.empty, "Should find crossings"
    assert len(res) == 2, "Should find exactly 2 crossings"
    
    v1_res = res[res['direction'] == 'down']
    v2_res = res[res['direction'] == 'up']
    
    assert len(v1_res) == 1, "Vessel 1 should cross 'down'"
    assert len(v2_res) == 1, "Vessel 2 should cross 'up'"
    
    # Halfway crossing should interpolate to 15.0 knots and loc_fraction to 0.5
    v1_speed = v1_res.iloc[0]['speed']
    v1_loc = v1_res.iloc[0]['loc_fraction']
    assert abs(v1_speed - 15.0) < 0.1, f"Expected speed ~15.0, got {v1_speed}"
    assert abs(v1_loc - 0.5) < 0.01, f"Expected loc_fraction ~0.5, got {v1_loc}"
    
    # Vessel 2 crossing should interpolate to 20.0 knots
    v2_speed = v2_res.iloc[0]['speed']
    assert abs(v2_speed - 20.0) < 0.1, f"Expected speed ~20.0, got {v2_speed}"
    
    print("test_passage_crossing with directionality PASSED!")


if __name__ == "__main__":
    test_passage_crossing()
