import logging
import numpy as np
import pandas as pd
import dask.dataframe as dd
from pyproj import Geod
from shapely.geometry import MultiPoint, Polygon

try:
    from .. import _cgal_hull
except ImportError:
    import _cgal_hull

from .features import calculate_kinematic_features_pandas

logger = logging.getLogger(__name__)
geod = Geod(ellps="WGS84")

def _calculate_rolling_hull_area(points_array, planar=False):
    """Calculates the area of the convex hull for a set of points.
    If planar=True, points_array is in planar meters, and we compute the planar area.
    If planar=False, points_array is in lon/lat, and we compute geodesic area.
    This version requires CGAL and will raise exceptions on failure.
    """
    if len(points_array) < 3:
        return 0.0
    
    if planar:
        area = _cgal_hull.convex_hull_area_2(points_array)
        return area
            
    # Geodesic area using CGAL
    hull_coords = _cgal_hull.convex_hull_2(points_array)
    if len(hull_coords) < 3:
        return 0.0
    hull = Polygon(hull_coords)
    area, _ = geod.geometry_area_perimeter(hull)
    return abs(area)

def process_single_vessel_partition(
    df: pd.DataFrame,
    vessel_id_col: str,
    time_col: str,
    x_col: str,
    y_col: str,
    gap_threshold_seconds: float,
    stop_duration_min: float,
    stop_radius_m: float,
    cog_col: str = 'cog',
    heading_col: str = 'heading',
    sog_col: str = 'sog'
) -> pd.DataFrame:
    """
    Processes a pandas DataFrame partition containing one or more vessels:
    1. Sorts chronologically.
    2. Groups by vessel_id and performs stop detection and trip segmentation.
    3. Groups by the newly created trip_id and calculates behavioral/kinematic features.
    """
    if df.empty:
        return df

    # Enforce correct datetime type only if not already datetime
    if not pd.api.types.is_datetime64_any_dtype(df[time_col]):
        df[time_col] = pd.to_datetime(df[time_col])

    # We will build list of processed vessel dataframes
    processed_vessels = []
    
    # Group by vessel ID locally
    for vid, sub_df in df.groupby(vessel_id_col, observed=True):
        if len(sub_df) == 0:
            continue
            
        # Sort chronologically
        v_df = sub_df.sort_values(by=time_col).copy()
        
        # 1. Compute time differences
        v_df['time_diff_s'] = v_df[time_col].diff().dt.total_seconds()
        
        # 2. Compute rolling convex hull area for stop detection
        coords = v_df[[x_col, y_col]].values
        times = v_df[time_col].values.astype('datetime64[ns]')
        window_ns = np.timedelta64(int(stop_duration_min * 60), 's')
        
        # Binary search for window start indices
        starts = np.searchsorted(times, times - window_ns, side='left')
        
        # Check if coordinates are in degrees (WGS84) or already projected planar meters
        # Lon/lat values will typically reside within [-180, 180] and [-90, 90] respectively.
        is_deg = False
        if len(coords) > 0:
            c_min = coords.min(axis=0)
            c_max = coords.max(axis=0)
            is_deg = (c_min[0] >= -180.0 and c_max[0] <= 180.0 and 
                      c_min[1] >= -90.0 and c_max[1] <= 90.0)
        
        if is_deg:
            # Fast local projection (Equirectangular approximation centered on the first ping)
            rad_pts = np.radians(coords)
            cos_lat0 = np.cos(rad_pts[0, 1])
            x_proj = 6371000.0 * (rad_pts[:, 0] - rad_pts[0, 0]) * cos_lat0
            y_proj = 6371000.0 * (rad_pts[:, 1] - rad_pts[0, 1])
            planar_coords = np.column_stack((x_proj, y_proj))
        else:
            planar_coords = coords
        
        # Compute rolling areas entirely in C++ using CGAL
        rolling_areas = _cgal_hull.rolling_convex_hull_area_2(planar_coords, starts.astype(np.intp))
        v_df['rolling_area_m2'] = rolling_areas
        
        # 3. Trip Segmentation
        # Split on gap threshold
        is_new_trip_by_gap = (v_df['time_diff_s'] > gap_threshold_seconds) | (v_df['time_diff_s'].isna())
        
        # Split on stop detection
        stop_area_threshold = np.pi * stop_radius_m**2
        is_stopped = v_df['rolling_area_m2'] < stop_area_threshold
        starts_moving_after_stop = (is_stopped.shift(1, fill_value=False) & ~is_stopped)
        
        is_new_trip = is_new_trip_by_gap | starts_moving_after_stop
        trip_segment = is_new_trip.astype(int).cumsum()
        
        v_df['trip_id'] = v_df[vessel_id_col].astype(str) + '_' + trip_segment.astype(str)
        
        # 4. Behavioral Feature Calculation (grouped locally by trip_id)
        processed_trips = []
        for tid, trip_df in v_df.groupby('trip_id', observed=True):
            trip_df = calculate_kinematic_features_pandas(
                df=trip_df,
                time_col=time_col,
                x_col=x_col,
                y_col=y_col,
                cog_col=cog_col,
                heading_col=heading_col,
                sog_col=sog_col
            )
            processed_trips.append(trip_df)
            
        if processed_trips:
            processed_vessels.append(pd.concat(processed_trips))
            
    if processed_vessels:
        return pd.concat(processed_vessels)
    return pd.DataFrame(columns=df.columns)

def trajectorize_dataframe(
    ddf: dd.DataFrame,
    vessel_id_col: str = "mmsi",
    time_col: str = "base_date_time",
    x_col: str = "longitude",
    y_col: str = "latitude",
    cog_col: str = 'cog',
    heading_col: str = 'heading',
    sog_col: str = 'sog',
    gap_threshold_hours: float = 1.0,
    stop_duration_min: float = 20.0,
    stop_radius_m: float = 50.0,
    shuffle_backend: str = "tasks",
    n_partitions: int = 128
) -> dd.DataFrame:
    """
    Dask-compatible entrypoint to perform voyage segmentation and feature engineering.
    """
    # Repartition if the number of partitions is too small to prevent worker OOM during shuffle
    if ddf.npartitions < n_partitions:
        logger.info(f"DataFrame has only {ddf.npartitions} partitions. Repartitioning to {n_partitions} partitions for load balancing...")
        ddf = ddf.repartition(npartitions=n_partitions)

    # Shuffle so same vessel IDs are guaranteed to be in the same partition
    ddf_shuffled = ddf.shuffle(on=vessel_id_col, shuffle=shuffle_backend)
    
    # Construct metadata for map_partitions
    meta = ddf._meta.copy()
    meta[time_col] = pd.to_datetime(meta[time_col])
    meta['time_diff_s'] = pd.Series(dtype='float64')
    meta['rolling_area_m2'] = pd.Series(dtype='float64')
    meta['trip_id'] = pd.Series(dtype='str')
    meta['speed_mps'] = pd.Series(dtype='float64')
    meta['acceleration_mps2'] = pd.Series(dtype='float64')
    meta['turn_rate_from_cog'] = pd.Series(dtype='float64')
    meta['turn_rate_from_heading'] = pd.Series(dtype='float64')

    logger.info("Applying partition-wise stop detection, segmentation, and feature engineering...")
    gap_threshold_seconds = gap_threshold_hours * 3600.0
    
    result = ddf_shuffled.map_partitions(
        process_single_vessel_partition,
        vessel_id_col=vessel_id_col,
        time_col=time_col,
        x_col=x_col,
        y_col=y_col,
        gap_threshold_seconds=gap_threshold_seconds,
        stop_duration_min=stop_duration_min,
        stop_radius_m=stop_radius_m,
        cog_col=cog_col,
        heading_col=heading_col,
        sog_col=sog_col,
        meta=meta
    )
    
    return result
