import logging
from pathlib import Path
import geopandas as gpd
import pandas as pd
import dask.dataframe as dd
import shapely
from shapely.geometry import Point, LineString
import numpy as np
from dask.distributed import Client, performance_report

logger = logging.getLogger(__name__)

def process_partition(
    df: pd.DataFrame, 
    passage_lines_gdf: gpd.GeoDataFrame, 
    minx: float, 
    miny: float, 
    maxx: float, 
    maxy: float, 
    max_time_gap_seconds: float
) -> pd.DataFrame:
    """
    Process a single partition of AIS points:
    1. Sort by track_id and timestamp.
    2. Direct coordinate reprojection using PyProj (bypasses Shapely geometry overhead).
    3. Construct shifts to represent consecutive point pairs.
    4. Filter segments by time gap, same track_id, and bounding box overlap check with passage lines.
    5. Construct LineString geometries only for the filtered subset using vectorized Shapely 2.0.
    6. Perform spatial join with passage lines and compute crossing speeds and locations along passage lines.
    """
    if df.empty:
        return pd.DataFrame(columns=['PassageId', 'speed', 'loc_fraction', 'direction'])
        
    # Sort points by track_id and timestamp
    df_sorted = df.sort_values(by=['track_id', 'timestamp']).copy()
    
    # Direct coordinate reprojection using PyProj (bypasses Shapely geometry overhead)
    from pyproj import Transformer
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    x, y = transformer.transform(df_sorted['longitude'].values, df_sorted['latitude'].values)
    
    coords = np.column_stack([x, y])
    track_ids = df_sorted['track_id'].values
    timestamps = df_sorted['timestamp'].values
    sogs = df_sorted['sog'].values
    
    # Create shifts (consecutive pairs)
    coords_start = coords[:-1]
    coords_end = coords[1:]
    track_start = track_ids[:-1]
    track_end = track_ids[1:]
    time_start = timestamps[:-1]
    time_end = timestamps[1:]
    sog_start = sogs[:-1]
    sog_end = sogs[1:]
    
    # Check time diffs and same track ID
    time_diffs = (time_end - time_start) / np.timedelta64(1, 's')
    mask = (track_start == track_end) & (time_diffs <= max_time_gap_seconds)
    
    # Filter arrays to those matching time gap and track_id
    coords_start_m = coords_start[mask]
    coords_end_m = coords_end[mask]
    
    if len(coords_start_m) == 0:
        return pd.DataFrame(columns=['PassageId', 'speed', 'loc_fraction', 'direction'])
        
    x_start_m = coords_start_m[:, 0]
    y_start_m = coords_start_m[:, 1]
    x_end_m = coords_end_m[:, 0]
    y_end_m = coords_end_m[:, 1]
    
    # Check bounding box overlap of the segments with the passage lines bounds
    seg_minx = np.minimum(x_start_m, x_end_m)
    seg_maxx = np.maximum(x_start_m, x_end_m)
    seg_miny = np.minimum(y_start_m, y_end_m)
    seg_maxy = np.maximum(y_start_m, y_end_m)
    
    bbox_overlap = (seg_maxx >= minx) & (seg_minx <= maxx) & (seg_maxy >= miny) & (seg_miny <= maxy)
    
    # Apply bbox overlap filter
    coords_start_f = coords_start_m[bbox_overlap]
    coords_end_f = coords_end_m[bbox_overlap]
    sog_start_f = sog_start[mask][bbox_overlap]
    sog_end_f = sog_end[mask][bbox_overlap]
    track_f = track_start[mask][bbox_overlap]
    
    if len(track_f) == 0:
        return pd.DataFrame(columns=['PassageId', 'speed', 'loc_fraction', 'direction'])
        
    # Create LineStrings in a vectorized way using shapely.linestrings
    segment_coords = np.stack([coords_start_f, coords_end_f], axis=1)
    geoms = shapely.linestrings(segment_coords)
    
    segments_gdf = gpd.GeoDataFrame(
        {
            'track_id': track_f,
            'sog_start': sog_start_f,
            'sog_end': sog_end_f,
            'x_start': coords_start_f[:, 0],
            'y_start': coords_start_f[:, 1],
            'x_end': coords_end_f[:, 0],
            'y_end': coords_end_f[:, 1]
        },
        geometry=geoms,
        crs="EPSG:3857"
    )
    
    # Spatial join
    joined = gpd.sjoin(segments_gdf, passage_lines_gdf, predicate='intersects')
    
    if joined.empty:
        return pd.DataFrame(columns=['PassageId', 'speed', 'loc_fraction', 'direction'])
        
    passage_geoms = passage_lines_gdf.geometry.loc[joined['index_right']].values
    segment_geoms = joined.geometry.values
    
    speeds = []
    loc_fractions = []
    for i in range(len(joined)):
        seg_geom = segment_geoms[i]
        pass_geom = passage_geoms[i]
        
        isect = seg_geom.intersection(pass_geom)
        if isect.is_empty:
            f_seg = 0.0
            f_pass = 0.0
        elif not isinstance(isect, Point):
            isect = isect.centroid
            f_seg = seg_geom.project(isect, normalized=True)
            f_pass = pass_geom.project(isect, normalized=True)
        else:
            f_seg = seg_geom.project(isect, normalized=True)
            f_pass = pass_geom.project(isect, normalized=True)
            
        row = joined.iloc[i]
        sog_start_val = row['sog_start']
        sog_end_val = row['sog_end']
        
        # Handle nan or invalid sog (e.g. 1023 or >= 102.2)
        if pd.isna(sog_start_val) or sog_start_val >= 102.2:
            sog_start_val = sog_end_val
        if pd.isna(sog_end_val) or sog_end_val >= 102.2:
            sog_end_val = sog_start_val
            
        if pd.isna(sog_start_val) or sog_start_val >= 102.2:
            speed = np.nan
        else:
            speed = sog_start_val + f_seg * (sog_end_val - sog_start_val)
        speeds.append(speed)
        loc_fractions.append(f_pass)
        
    joined['speed'] = speeds
    joined['loc_fraction'] = loc_fractions
    
    # Calculate direction: dot product of segment vector and normal vector of passage line
    L_x_val = passage_lines_gdf['L_x'].loc[joined['index_right']].values
    L_y_val = passage_lines_gdf['L_y'].loc[joined['index_right']].values
    S_x = (joined['x_end'] - joined['x_start']).values
    S_y = (joined['y_end'] - joined['y_start']).values
    
    dot_product = S_x * (-L_y_val) + S_y * L_x_val
    joined['direction'] = np.where(dot_product >= 0, 'down', 'up')
    
    return joined[['PassageId', 'speed', 'loc_fraction', 'direction']]


def run_passage_analysis(
    passage_file: Path,
    ais_dir: Path,
    output_file: Path,
    max_time_gap: float,
    scheduler: str = None
):
    """
    Run the passage analysis using Dask with performance profiling.
    """
    # Connect to Dask
    if scheduler:
        logger.info(f"Connecting to Dask scheduler at {scheduler}...")
        client = Client(scheduler)
    else:
        logger.info("Starting Local Dask Client...")
        client = Client()
    logger.info(f"Dashboard: {client.dashboard_link}")
    
    # Load passage lines
    logger.info(f"Loading passage lines from {passage_file}...")
    passage_lines = gpd.read_file(passage_file)
    if passage_lines.crs != "EPSG:3857":
        logger.info("Reprojecting passage lines to EPSG:3857...")
        passage_lines = passage_lines.to_crs("EPSG:3857")
        
    # Pre-calculate passage line vectors
    coords_list = [g.coords for g in passage_lines.geometry]
    L_x = np.array([c[-1][0] - c[0][0] for c in coords_list])
    L_y = np.array([c[-1][1] - c[0][1] for c in coords_list])
    passage_lines['L_x'] = L_x
    passage_lines['L_y'] = L_y
    
    # Get total bounds of passage lines for pre-filtering
    minx, miny, maxx, maxy = passage_lines.total_bounds
    
    # Read AIS data
    logger.info(f"Reading AIS Parquet directory from {ais_dir}...")
    ddf = dd.read_parquet(ais_dir)
    logger.info(f"Total partitions: {ddf.npartitions}")
    
    # Process crossings with Dask performance report
    # Ensure output directory exists before generating report
    output_file.parent.mkdir(parents=True, exist_ok=True)
    report_path = output_file.parent / f"{output_file.stem}-dask-report.html"
    logger.info(f"Computing crossings per partition and saving performance report to {report_path}...")
    meta = pd.DataFrame(columns=['PassageId', 'speed', 'loc_fraction', 'direction'])
    with performance_report(filename=str(report_path)):
        crossings_df = ddf.map_partitions(
            process_partition,
            passage_lines_gdf=passage_lines,
            minx=minx,
            miny=miny,
            maxx=maxx,
            maxy=maxy,
            max_time_gap_seconds=max_time_gap,
            meta=meta
        ).compute()
    
    logger.info(f"Found {len(crossings_df)} crossings.")
    
    # Filter passage lines to only those with crossings
    logger.info("Filtering out passage lines with no crossings...")
    active_passage_ids = crossings_df['PassageId'].unique()
    passage_lines = passage_lines[passage_lines['PassageId'].isin(active_passage_ids)].copy()
    
    if len(passage_lines) == 0:
        logger.warning("No passage lines had crossings in the dataset!")
        return
        
    # --- Calculate overall frequencies and median speeds per PassageId and direction ---
    logger.info("Aggregating overall frequencies and median speeds per direction...")
    overall_stats = crossings_df.groupby(['PassageId', 'direction'], observed=False).agg(
        frequency=('speed', 'count'),
        median_speed=('speed', 'median')
    ).reset_index()
    
    # Pivot to get columns: frequency_up, frequency_down, median_speed_up, median_speed_down
    pivot_df = overall_stats.pivot(index='PassageId', columns='direction')
    pivot_df.columns = [f"{col[0]}_{col[1]}" for col in pivot_df.columns]
    pivot_df = pivot_df.reset_index()
    
    # Merge back to original passage lines
    logger.info("Merging overall stats back to passage lines...")
    output_gdf = passage_lines.merge(pivot_df, on='PassageId', how='left')
    
    # Ensure all target overall columns exist in output_gdf
    for col in ['frequency_up', 'frequency_down', 'median_speed_up', 'median_speed_down']:
        if col not in output_gdf.columns:
            output_gdf[col] = np.nan
            
    # Fill NAs
    output_gdf['frequency_up'] = output_gdf['frequency_up'].fillna(0).astype(int)
    output_gdf['frequency_down'] = output_gdf['frequency_down'].fillna(0).astype(int)
    output_gdf['median_speed_up'] = output_gdf['median_speed_up'].fillna(0.0)
    output_gdf['median_speed_down'] = output_gdf['median_speed_down'].fillna(0.0)
    
    # --- 20-Bin Segments generation ---
    logger.info("Calculating 20-bin segment statistics...")
    loc_bins_list = np.linspace(0.0, 1.0, 21)
    loc_bin_labels = list(range(20))
    # Clip loc_fraction to [0.0, 1.0 - 1e-9] to handle loc_fraction == 1.0 safely when right=False
    loc_fraction_clipped = crossings_df['loc_fraction'].clip(0.0, 1.0 - 1e-9)
    crossings_df['BinIndex'] = pd.cut(loc_fraction_clipped, bins=loc_bins_list, labels=loc_bin_labels, right=False)
    
    # Group by PassageId, BinIndex, and direction
    stats_df = crossings_df.groupby(['PassageId', 'BinIndex', 'direction'], observed=False).agg(
        Frequency=('speed', 'count'),
        MedianSpeed=('speed', 'median')
    ).reset_index()
    
    # Pivot bin stats to get loc_bin_{i}_{direction} and median_speed_loc_{i}_{direction}
    if not stats_df.empty:
        bin_stats_pivot = stats_df.pivot(
            index='PassageId',
            columns=['BinIndex', 'direction'],
            values=['Frequency', 'MedianSpeed']
        )
        # Rename columns to: loc_bin_{BinIndex}_{direction} and median_speed_loc_{BinIndex}_{direction}
        bin_stats_pivot.columns = [
            f"loc_bin_{col[1]}_{col[2]}" if col[0] == 'Frequency' else f"median_speed_loc_{col[1]}_{col[2]}"
            for col in bin_stats_pivot.columns
        ]
        bin_stats_pivot = bin_stats_pivot.reset_index()
        
        # Merge bin_stats_pivot back to output_gdf
        output_gdf = output_gdf.merge(bin_stats_pivot, on='PassageId', how='left')
        
    # Ensure all 80 bin columns exist in output_gdf and are filled/typed correctly
    for i in range(20):
        for d in ['up', 'down']:
            freq_col = f"loc_bin_{i}_{d}"
            median_col = f"median_speed_loc_{i}_{d}"
            if freq_col not in output_gdf.columns:
                output_gdf[freq_col] = 0
            else:
                output_gdf[freq_col] = output_gdf[freq_col].fillna(0).astype(int)
            
            if median_col not in output_gdf.columns:
                output_gdf[median_col] = 0.0
            else:
                output_gdf[median_col] = output_gdf[median_col].fillna(0.0)

    # Drop calculated helper vectors from output GeoJSON properties
    output_gdf = output_gdf.drop(columns=['L_x', 'L_y'])
    
    # Reproject back to EPSG:4326 for GIS compatibility
    logger.info("Reprojecting velocities output back to EPSG:4326...")
    output_gdf = output_gdf.to_crs("EPSG:4326")
    
    # Save main velocities output
    logger.info(f"Saving main velocities results to {output_file}...")
    if output_file.suffix.lower() == '.gpkg':
        output_gdf.to_file(output_file, driver="GPKG")
    else:
        output_gdf.to_file(output_file, driver="GeoJSON")
        
    # Filter to segments with crossings
    stats_df = stats_df[stats_df['Frequency'] > 0].copy()
    
    # Pre-map passage lines geometries and lengths in EPSG:3857 for accurate segment interpolation
    passage_geoms_3857 = {
        row['PassageId']: row['geometry']
        for _, row in passage_lines.iterrows()
    }
    if 'Length' in passage_lines.columns:
        passage_lengths = {
            row['PassageId']: float(row['Length'])
            for _, row in passage_lines.iterrows()
        }
    else:
        passage_lengths = {
            row['PassageId']: float(row['geometry'].length)
            for _, row in passage_lines.iterrows()
        }
    
    # Map ProfileLength into stats_df
    stats_df['ProfileLength'] = stats_df['PassageId'].map(passage_lengths)
    
    logger.info("Generating physical LineString geometries for active bin segments...")
    segment_geoms_3857 = []
    for _, row in stats_df.iterrows():
        pid = row['PassageId']
        bin_idx = int(row['BinIndex'])
        geom_3857 = passage_geoms_3857[pid]
        p1 = geom_3857.interpolate(bin_idx / 20.0, normalized=True)
        p2 = geom_3857.interpolate((bin_idx + 1) / 20.0, normalized=True)
        segment_geoms_3857.append(LineString([p1, p2]))
        
    stats_gdf_3857 = gpd.GeoDataFrame(
        stats_df,
        geometry=segment_geoms_3857,
        crs="EPSG:3857"
    )
    
    # Reproject to EPSG:4326 for final output
    stats_gdf_4326 = stats_gdf_3857.to_crs("EPSG:4326")
    stats_gdf_4326 = stats_gdf_4326.rename(columns={'direction': 'Direction'})
    stats_gdf_4326['MedianSpeed'] = stats_gdf_4326['MedianSpeed'].fillna(0.0)
    stats_gdf_4326['ProfileLength'] = stats_gdf_4326['ProfileLength'].fillna(0.0)
    stats_gdf_4326['BinWidth'] = stats_gdf_4326['ProfileLength'] / 20.0
    
    # Save segments output next to output_file with matching stem
    segments_output_file = output_file.parent / f"{output_file.stem}_bin_segments.geojson"
    logger.info(f"Saving bin segments results to {segments_output_file}...")
    stats_gdf_4326.to_file(segments_output_file, driver="GeoJSON")
    
    logger.info("Analysis and segment generation complete!")
