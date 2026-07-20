import numpy as np
import pandas as pd
from pyproj import Geod

# Initialize pyproj Geod for WGS84 geodesic calculations
geod = Geod(ellps="WGS84")

def calculate_kinematic_features_pandas(
    df: pd.DataFrame,
    time_col: str,
    x_col: str,
    y_col: str,
    cog_col: str = 'cog',
    heading_col: str = 'heading',
    sog_col: str = 'sog'
) -> pd.DataFrame:
    """
    Computes speed, acceleration, and turn rates for a single vessel trajectory.
    This function operates on a pandas DataFrame which is already sorted by timestamp.
    """
    if len(df) < 2:
        df['speed_mps'] = np.nan
        df['acceleration_mps2'] = np.nan
        df['turn_rate_from_cog'] = np.nan
        df['turn_rate_from_heading'] = np.nan
        return df

    # 1. Compute time difference in seconds
    time_diff_s = pd.to_datetime(df[time_col]).diff().dt.total_seconds().values
    
    # Avoid division by zero/negative time differences
    valid_time_mask = (time_diff_s > 0)

    # 2. Compute Geodesic Distances
    lons = df[x_col].values
    lats = df[y_col].values
    
    # Geod.inv expects arrays of lon1, lat1, lon2, lat2
    lon1, lat1 = lons[:-1], lats[:-1]
    lon2, lat2 = lons[1:], lats[1:]
    if len(lon1) == 1:
        _, _, dists = geod.inv(float(lon1[0]), float(lat1[0]), float(lon2[0]), float(lat2[0]))
        dists = np.array([dists])
    else:
        _, _, dists = geod.inv(lon1, lat1, lon2, lat2)
    # Insert NaN at start to align with DataFrame index
    distances_m = np.insert(dists, 0, np.nan)

    # 3. Speed (m/s)
    speed_mps = np.zeros_like(distances_m)
    speed_mps[valid_time_mask] = distances_m[valid_time_mask] / time_diff_s[valid_time_mask]
    speed_mps[~valid_time_mask] = np.nan
    df['speed_mps'] = speed_mps

    # 4. Acceleration (m/s^2)
    # Change in speed over time
    speed_diff = pd.Series(speed_mps).diff().values
    accel_mps2 = np.zeros_like(speed_diff)
    accel_mps2[valid_time_mask] = speed_diff[valid_time_mask] / time_diff_s[valid_time_mask]
    accel_mps2[~valid_time_mask] = np.nan
    df['acceleration_mps2'] = accel_mps2

    # 5. Turn Rates (degrees / sec)
    def calculate_turn_rate(angles_series):
        # Shortest distance on a circle (-180 to 180 degrees)
        angle_diff = angles_series.diff().values
        angle_diff_wrapped = (angle_diff + 180) % 360 - 180
        
        turn_rate = np.zeros_like(angle_diff_wrapped)
        turn_rate[valid_time_mask] = angle_diff_wrapped[valid_time_mask] / time_diff_s[valid_time_mask]
        turn_rate[~valid_time_mask] = np.nan
        return turn_rate

    if cog_col in df.columns:
        df['turn_rate_from_cog'] = calculate_turn_rate(df[cog_col])
    else:
        df['turn_rate_from_cog'] = np.nan

    if heading_col in df.columns:
        df['turn_rate_from_heading'] = calculate_turn_rate(df[heading_col])
    else:
        df['turn_rate_from_heading'] = np.nan

    return df
