import logging
import numpy as np
import pandas as pd
import dask.dataframe as dd

try:
    from .. import _cgal_hull
except ImportError:
    import _cgal_hull

from .features import calculate_kinematic_features_pandas

logger = logging.getLogger(__name__)

def _calculate_rolling_hull_area(points_array):
    """Calculates the area of the convex hull for a set of planar points (in meters) using CGAL."""
    if len(points_array) < 3:
        return 0.0
    return _cgal_hull.convex_hull_area_2(points_array)

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
    sog_col: str = 'sog',
    input_crs: str = "EPSG:4326"
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

    # Dynamic calculation of voyage segmentation and stop features
    gap_threshold_seconds = gap_threshold_seconds
    stop_duration_min = stop_duration_min
    stop_radius_m = stop_radius_m

    def process_single_vessel(v_df):
        if len(v_df) < 2:
            v_df['time_diff_s'] = np.nan
            v_df['rolling_area_m2'] = 0.0
            v_df['trip_id'] = v_df[vessel_id_col].astype(str) + "_1"
            return v_df

        # 1. Sort chronologically
        v_df = v_df.sort_values(by=time_col)
        v_df['time_diff_s'] = v_df[time_col].diff().dt.total_seconds()

        # 2. Compute rolling convex hull area for stop detection
        coords = v_df[[x_col, y_col]].values
        times = v_df[time_col].values.astype('datetime64[ns]')
        window_ns = np.timedelta64(int(round(stop_duration_min * 60)), 's')
        
        # Binary search for window start indices
        starts = np.searchsorted(times, times - window_ns, side='left')
        
        from pyproj import CRS, Transformer
        crs_obj = CRS(input_crs)
        if crs_obj.is_geographic:
            # Use Azimuthal Equidistant projection centered on the first coordinate
            lon0, lat0 = coords[0, 0], coords[0, 1]
            proj_str = f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
            transformer = Transformer.from_crs(crs_obj, proj_str, always_xy=True)
            x_proj, y_proj = transformer.transform(coords[:, 0], coords[:, 1])
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
            return pd.concat(processed_trips)
        return v_df

    processed_vessels = []
    for vid, sub_df in df.groupby(vessel_id_col, observed=True):
        if len(sub_df) == 0:
            continue
        processed_vessels.append(process_single_vessel(sub_df.copy()))
        
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
    n_partitions: int = 128,
    input_crs: str = "EPSG:4326"
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

    if 'geometry' in meta.columns:
        import geopandas as gpd
        crs = getattr(ddf, 'crs', None)
        meta = gpd.GeoDataFrame(meta, geometry='geometry', crs=crs)

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
        input_crs=input_crs,
        meta=meta
    )
    
    return result
