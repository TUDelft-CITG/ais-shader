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

def get_parquet_bounds(dataset_path: str, x_col: str, y_col: str, time_col: str):
    from pathlib import Path
    import pyarrow.parquet as pq
    
    path = Path(dataset_path)
    if path.is_dir():
        files = list(path.glob("**/*.parquet"))
        if not files:
            files = list(path.glob("*.parquet"))
    else:
        files = [path]
        
    if not files:
        raise ValueError(f"No parquet files found at path: {dataset_path}")
        
    x_mins, x_maxs = [], []
    y_mins, y_maxs = [], []
    t_mins, t_maxs = [], []
    
    for f in files:
        meta = pq.read_metadata(f)
        for rg_idx in range(meta.num_row_groups):
            rg = meta.row_group(rg_idx)
            for col_idx in range(meta.num_columns):
                col = rg.column(col_idx)
                if col.path_in_schema == x_col:
                    if not col.is_stats_set:
                        raise ValueError(f"Metadata statistics missing for column '{x_col}' in file {f}")
                    x_mins.append(col.statistics.min)
                    x_maxs.append(col.statistics.max)
                elif col.path_in_schema == y_col:
                    if not col.is_stats_set:
                        raise ValueError(f"Metadata statistics missing for column '{y_col}' in file {f}")
                    y_mins.append(col.statistics.min)
                    y_maxs.append(col.statistics.max)
                elif col.path_in_schema == time_col:
                    if not col.is_stats_set:
                        raise ValueError(f"Metadata statistics missing for column '{time_col}' in file {f}")
                    t_mins.append(col.statistics.min)
                    t_maxs.append(col.statistics.max)
                    
    if not (x_mins and y_mins and t_mins):
        raise ValueError(f"Could not retrieve all required statistics for {x_col}, {y_col}, {time_col} from parquet metadata at {dataset_path}")
        
    return min(x_mins), max(x_maxs), min(y_mins), max(y_maxs), min(t_mins), max(t_maxs)

def _calculate_rolling_hull_area(points_array):
    """Calculates the area of the convex hull for a set of planar points (in meters) using CGAL."""
    if len(points_array) < 3:
        return 0.0
    return _cgal_hull.convex_hull_area_2(points_array)

def encode_3d_hilbert_numpy(coords: np.ndarray, p: int) -> np.ndarray:
    """
    Vectorized 3D Hilbert Curve encoding for NumPy arrays.
    coords: np.ndarray of shape (N, 3) with integer values in [0, 2^p - 1].
    p: number of bits (order of Hilbert curve).
    """
    N = coords.shape[0]
    point = coords.copy().astype(np.int64)
    
    m = 1 << (p - 1)
    
    q = m
    while q > 1:
        p_val = q - 1
        for i in range(3):
            mask = (point[:, i] & q) > 0
            point[mask, 0] ^= p_val
            
            not_mask = ~mask
            t = (point[not_mask, 0] ^ point[not_mask, i]) & p_val
            point[not_mask, 0] ^= t
            point[not_mask, i] ^= t
        q >>= 1
        
    for i in range(1, 3):
        point[:, i] ^= point[:, i - 1]
        
    t = np.zeros(N, dtype=np.int64)
    q = m
    while q > 1:
        mask = (point[:, 2] & q) > 0
        t[mask] ^= (q - 1)
        q >>= 1
        
    for i in range(3):
        point[:, i] ^= t
        
    h_int = np.zeros(N, dtype=np.int64)
    for bit in range(p - 1, -1, -1):
        for dim in range(3):
            b = (point[:, dim] >> bit) & 1
            h_int = (h_int << 1) | b
            
    return h_int

def add_hilbert_index(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    time_col: str,
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    t_min_epoch: float,
    t_max_epoch: float,
    p: int
) -> pd.DataFrame:
    if df.empty:
        df['hilbert_index'] = pd.Series(dtype='int64')
        return df
    
    assert pd.api.types.is_datetime64_any_dtype(df[time_col]), f"Column {time_col} must be of datetime64 dtype"
    grid_size = (1 << p) - 1
    
    xs = df[x_col].values
    ys = df[y_col].values
    ts = df[time_col].values.view('int64') // 10**9
    
    xd = x_max - x_min if x_max != x_min else 1.0
    yd = y_max - y_min if y_max != y_min else 1.0
    td = t_max_epoch - t_min_epoch if t_max_epoch != t_min_epoch else 1.0
    
    x_int = np.clip((xs - x_min) / xd * grid_size, 0, grid_size).astype(np.int64)
    y_int = np.clip((ys - y_min) / yd * grid_size, 0, grid_size).astype(np.int64)
    t_int = np.clip((ts - t_min_epoch) / td * grid_size, 0, grid_size).astype(np.int64)
    
    coords = np.column_stack((x_int, y_int, t_int))
    df['hilbert_index'] = encode_3d_hilbert_numpy(coords, p)
    return df

def apply_halo(
    curr_df: pd.DataFrame,
    prev_df: pd.DataFrame,
    next_df: pd.DataFrame,
    x_col: str,
    y_col: str,
    time_col: str,
    vessel_id_col: str,
    spatial_halo_coord: float,
    stop_duration_min: float
) -> pd.DataFrame:
    if curr_df is None or curr_df.empty:
        if curr_df is not None and '_is_halo' not in curr_df.columns:
            curr_df = curr_df.copy()
            curr_df['_is_halo'] = pd.Series(dtype='bool')
        return curr_df
        
    curr_df = curr_df.copy()
    curr_df['_is_halo'] = False
    
    x_min, x_max = curr_df[x_col].min(), curr_df[x_col].max()
    y_min, y_max = curr_df[y_col].min(), curr_df[y_col].max()
    t_min, t_max = curr_df[time_col].min(), curr_df[time_col].max()
    
    dt = pd.Timedelta(minutes=stop_duration_min)
    
    extra_dfs = []
    if prev_df is not None and not prev_df.empty:
        prev_filtered = prev_df[
            (prev_df[time_col] >= t_min - dt) &
            (prev_df[time_col] <= t_min) &
            (prev_df[x_col] >= x_min - spatial_halo_coord) &
            (prev_df[x_col] <= x_max + spatial_halo_coord) &
            (prev_df[y_col] >= y_min - spatial_halo_coord) &
            (prev_df[y_col] <= y_max + spatial_halo_coord)
        ].copy()
        if not prev_filtered.empty:
            prev_filtered['_is_halo'] = True
            extra_dfs.append(prev_filtered)
            
    if next_df is not None and not next_df.empty:
        next_filtered = next_df[
            (next_df[time_col] >= t_max) &
            (next_df[time_col] <= t_max + dt) &
            (next_df[x_col] >= x_min - spatial_halo_coord) &
            (next_df[x_col] <= x_max + spatial_halo_coord) &
            (next_df[y_col] >= y_min - spatial_halo_coord) &
            (next_df[y_col] <= y_max + spatial_halo_coord)
        ].copy()
        if not next_filtered.empty:
            next_filtered['_is_halo'] = True
            extra_dfs.append(next_filtered)
            
    if extra_dfs:
        combined = pd.concat([curr_df] + extra_dfs, ignore_index=True)
        combined = combined.drop_duplicates(subset=[vessel_id_col, time_col])
        return combined
        
    return curr_df

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
    has_halo_col = '_is_halo' in df.columns

    if df.empty:
        if has_halo_col and '_is_halo' not in df.columns:
            df['_is_halo'] = pd.Series(dtype='bool')
        return df

    # Enforce correct datetime type only if not already datetime
    if not pd.api.types.is_datetime64_any_dtype(df[time_col]):
        df[time_col] = pd.to_datetime(df[time_col])

    if '_is_halo' not in df.columns:
        df['_is_halo'] = False

    # Pre-project coordinates once for the entire partition if CRS is geographic
    from pyproj import CRS, Transformer
    crs_obj = CRS(input_crs)
    if crs_obj.is_geographic:
        # Center the projection on the mean of the partition coordinates
        lon0 = df[x_col].mean()
        lat0 = df[y_col].mean()
        proj_str = f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
        transformer = Transformer.from_crs(crs_obj, proj_str, always_xy=True)
        x_proj, y_proj = transformer.transform(df[x_col].values, df[y_col].values)
        df['_x_proj'] = x_proj
        df['_y_proj'] = y_proj
    else:
        df['_x_proj'] = df[x_col]
        df['_y_proj'] = df[y_col]

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
        planar_coords = v_df[['_x_proj', '_y_proj']].values
        times = v_df[time_col].values.astype('datetime64[ns]')
        window_ns = np.timedelta64(int(round(stop_duration_min * 60)), 's')
        
        # Binary search for window start indices
        starts = np.searchsorted(times, times - window_ns, side='left')
        
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
        result = pd.concat(processed_vessels)
        if '_x_proj' in result.columns:
            result = result.drop(columns=['_x_proj', '_y_proj'])
        if not has_halo_col and '_is_halo' in result.columns:
            result = result.drop(columns=['_is_halo'])
        elif has_halo_col and '_is_halo' in result.columns:
            cols = [c for c in result.columns if c != '_is_halo'] + ['_is_halo']
            result = result[cols]
        return result
        
    empty_df = pd.DataFrame(columns=df.columns)
    if '_x_proj' in empty_df.columns:
        empty_df = empty_df.drop(columns=['_x_proj', '_y_proj'])
    if not has_halo_col and '_is_halo' in empty_df.columns:
        empty_df = empty_df.drop(columns=['_is_halo'])
    elif has_halo_col and '_is_halo' in empty_df.columns:
        cols = [c for c in empty_df.columns if c != '_is_halo'] + ['_is_halo']
        empty_df = empty_df[cols]
    return empty_df

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
    input_crs: str = "EPSG:4326",
    partition_method: str = "spatiotemporal",  # "vessel" or "spatiotemporal"
    hilbert_p: int = 16,
    dataset_path: str = None,
    global_bounds: dict = None
) -> dd.DataFrame:
    """
    Dask-compatible entrypoint to perform voyage segmentation and feature engineering.
    """
    import dask
    
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

    gap_threshold_seconds = gap_threshold_hours * 3600.0

    if partition_method == "spatiotemporal":
        if global_bounds is not None:
            logger.info("Using explicit global_bounds for spatio-temporal partitioning...")
            x_min = global_bounds["x_min"]
            x_max = global_bounds["x_max"]
            y_min = global_bounds["y_min"]
            y_max = global_bounds["y_max"]
            t_min = global_bounds["t_min"]
            t_max = global_bounds["t_max"]
        else:
            if not dataset_path:
                raise ValueError("dataset_path must be provided for spatio-temporal partitioning to retrieve metadata statistics")

            logger.info(f"Retrieving global bounds from Parquet metadata at {dataset_path}...")
            x_min, x_max, y_min, y_max, t_min, t_max = get_parquet_bounds(dataset_path, x_col, y_col, time_col)
        
        # Enforce correct datetime type only if not already datetime
        if not pd.api.types.is_datetime64_any_dtype(ddf[time_col]):
            ddf = ddf.copy()
            ddf[time_col] = dd.to_datetime(ddf[time_col])
            
        t_min_epoch = pd.to_datetime(t_min).timestamp()
        t_max_epoch = pd.to_datetime(t_max).timestamp()
        
        logger.info(f"Global bounds: X=[{x_min}, {x_max}], Y=[{y_min}, {y_max}], Time=[{t_min}, {t_max}]")
        
        # 2. Compute 3D Hilbert Coordinates & Index
        meta_hilbert = ddf._meta.copy()
        meta_hilbert['hilbert_index'] = pd.Series(dtype='int64')
        if 'geometry' in meta_hilbert.columns:
            import geopandas as gpd
            crs = getattr(ddf, 'crs', None)
            meta_hilbert = gpd.GeoDataFrame(meta_hilbert, geometry='geometry', crs=crs)
            
        ddf_hilbert = ddf.map_partitions(
            add_hilbert_index,
            x_col=x_col,
            y_col=y_col,
            time_col=time_col,
            x_min=x_min,
            x_max=x_max,
            y_min=y_min,
            y_max=y_max,
            t_min_epoch=t_min_epoch,
            t_max_epoch=t_max_epoch,
            p=hilbert_p,
            meta=meta_hilbert
        )
        
        # 3. Sort/partition by hilbert_index
        logger.info(f"Estimating partitions divisions on a 1% sample of hilbert_index...")
        sample_indices = ddf_hilbert['hilbert_index'].sample(frac=0.01).compute()
        import numpy as np
        quantiles = np.linspace(0, 1, n_partitions + 1)
        divisions = list(sample_indices.quantile(quantiles))
        
        # Ensure divisions are unique and monotonic
        divisions = sorted(list(set(divisions)))
        # Dask set_index requires at least 2 unique divisions
        if len(divisions) < 2:
            h_min = ddf_hilbert['hilbert_index'].min().compute()
            h_max = ddf_hilbert['hilbert_index'].max().compute()
            if h_min == h_max:
                divisions = [h_min, h_min + 1]
            else:
                divisions = [h_min, h_max]
                
        logger.info(f"Setting index to 'hilbert_index' using estimated divisions...")
        ddf_sorted = ddf_hilbert.set_index('hilbert_index', divisions=divisions, shuffle=shuffle_backend)
        
        # 4. Construct delayed boundary halos (Overlap/Lookback)
        logger.info("Constructing overlapping boundaries/halos...")
        parts = ddf_sorted.to_delayed()
        new_parts = []
        n_delayed = len(parts)
        
        # Determine spatial halo unit
        from pyproj import CRS
        crs_obj = CRS(input_crs)
        if crs_obj.is_geographic:
            spatial_halo_coord = stop_radius_m / 111320.0
        else:
            spatial_halo_coord = stop_radius_m
            
        for i in range(n_delayed):
            prev_part = parts[i - 1] if i > 0 else None
            curr_part = parts[i]
            next_part = parts[i + 1] if i < n_delayed - 1 else None
            
            new_part = dask.delayed(apply_halo)(
                curr_part, prev_part, next_part,
                x_col=x_col, y_col=y_col, time_col=time_col,
                vessel_id_col=vessel_id_col,
                spatial_halo_coord=spatial_halo_coord,
                stop_duration_min=stop_duration_min
            )
            new_parts.append(new_part)
            
        meta_halo = ddf_sorted._meta.copy()
        meta_halo['_is_halo'] = pd.Series(dtype='bool')
        ddf_halo = dd.from_delayed(new_parts, meta=meta_halo)
        
        # 5. Process local partitions (stop detection, feature engineering)
        logger.info("Processing spatio-temporal partitions (stops, features)...")
        meta_halo_out = ddf._meta.copy()
        meta_halo_out[time_col] = pd.to_datetime(meta_halo_out[time_col])
        meta_halo_out['time_diff_s'] = pd.Series(dtype='float64')
        meta_halo_out['rolling_area_m2'] = pd.Series(dtype='float64')
        meta_halo_out['trip_id'] = pd.Series(dtype='str')
        meta_halo_out['speed_mps'] = pd.Series(dtype='float64')
        meta_halo_out['acceleration_mps2'] = pd.Series(dtype='float64')
        meta_halo_out['turn_rate_from_cog'] = pd.Series(dtype='float64')
        meta_halo_out['turn_rate_from_heading'] = pd.Series(dtype='float64')
        meta_halo_out['_is_halo'] = pd.Series(dtype='bool')

        if 'geometry' in meta_halo_out.columns:
            import geopandas as gpd
            crs = getattr(ddf, 'crs', None)
            meta_halo_out = gpd.GeoDataFrame(meta_halo_out, geometry='geometry', crs=crs)

        result = ddf_halo.map_partitions(
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
            meta=meta_halo_out
        )
        
        # 6. Crop and save: discard halo points and clean up
        logger.info("Cropping boundary halos from result...")
        result = result[~result['_is_halo']]
        result = result.drop(columns=['_is_halo'])
        
        return result

    else:
        # Repartition if the number of partitions is too small to prevent worker OOM during shuffle
        if ddf.npartitions < n_partitions:
            logger.info(f"DataFrame has only {ddf.npartitions} partitions. Repartitioning to {n_partitions} partitions for load balancing...")
            ddf = ddf.repartition(npartitions=n_partitions)

        # Shuffle so same vessel IDs are guaranteed to be in the same partition
        ddf_shuffled = ddf.shuffle(on=vessel_id_col, shuffle=shuffle_backend)
        
        logger.info("Applying partition-wise stop detection, segmentation, and feature engineering...")
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
        
        # Drop _is_halo if it was added
        if '_is_halo' in result.columns:
            result = result.drop(columns=['_is_halo'])
            
        return result
