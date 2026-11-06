import logging
from pathlib import Path
import geopandas as gpd
import pandas as pd
import dask.dataframe as dd
import shapely
import numpy as np
from dask.distributed import performance_report

from ais_shader.crossings import (
    N_LOC_BINS,
    add_line_direction_vectors,
    bin_segment_geometry,
    classify_crossing_direction,
    crossing_points_and_fractions,
    fraction_along,
)
from ais_shader.dask_utils import get_dask_client
from ais_shader.preprocessing import SOG_CAP_KNOTS
from ais_shader.vector_io import read_vector, to_lonlat_crs, to_metric_crs, write_vector

logger = logging.getLogger(__name__)

CROSSING_COLS = ['PassageId', 'speed', 'loc_fraction', 'direction']


def _interpolate_crossing_speed(sog_start, sog_end, f_seg) -> np.ndarray:
    """
    Speed at the crossing point, interpolated between the segment's endpoint SOGs.

    An endpoint whose SOG is missing or at/above the AIS "not available"
    sentinel (SOG_CAP_KNOTS, i.e. raw 1023) falls back to the other endpoint's
    value; when neither endpoint is usable the crossing gets NaN.
    """
    start = np.asarray(sog_start, dtype=float)
    end = np.asarray(sog_end, dtype=float)
    start_unusable = np.isnan(start) | (start >= SOG_CAP_KNOTS)
    end_unusable = np.isnan(end) | (end >= SOG_CAP_KNOTS)
    start_filled = np.where(start_unusable, end, start)
    end_filled = np.where(end_unusable, start, end)
    speed = start_filled + np.asarray(f_seg, dtype=float) * (end_filled - start_filled)
    return np.where(np.isnan(start_filled) | (start_filled >= SOG_CAP_KNOTS), np.nan, speed)


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
        return pd.DataFrame(columns=CROSSING_COLS)
        
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
        return pd.DataFrame(columns=CROSSING_COLS)
        
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
        return pd.DataFrame(columns=CROSSING_COLS)
        
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
        return pd.DataFrame(columns=CROSSING_COLS)
        
    passage_geoms = passage_lines_gdf.geometry.loc[joined['index_right']].values
    segment_geoms = joined.geometry.values
    
    # allow_empty=True: the sjoin predicate should guarantee a real
    # intersection, but a degenerate one is tolerated here (crossing placed at
    # the segment's start) rather than failing a whole Dask partition.
    points, f_segs = crossing_points_and_fractions(segment_geoms, passage_geoms, allow_empty=True)

    joined['speed'] = _interpolate_crossing_speed(
        joined['sog_start'].values, joined['sog_end'].values, f_segs
    )
    joined['loc_fraction'] = [
        fraction_along(pass_geom, point) for pass_geom, point in zip(passage_geoms, points)
    ]
    joined['direction'] = classify_crossing_direction(
        S_x=(joined['x_end'] - joined['x_start']).values,
        S_y=(joined['y_end'] - joined['y_start']).values,
        L_x=passage_lines_gdf['L_x'].loc[joined['index_right']].values,
        L_y=passage_lines_gdf['L_y'].loc[joined['index_right']].values,
    )

    return joined[CROSSING_COLS]


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
    # Held for the whole run: the Client is kept referenced so the local
    # cluster is not torn down mid-computation.
    client = get_dask_client(scheduler)  # noqa: F841

    # Load passage lines into the metric working CRS and pre-calculate each
    # line's own start->end vector, which fixes the up/down direction convention.
    logger.info(f"Loading passage lines from {passage_file}...")
    passage_lines = add_line_direction_vectors(to_metric_crs(read_vector(passage_file)))
    
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
    meta = pd.DataFrame(columns=CROSSING_COLS)
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
    
    # --- Per-location bin segment generation ---
    logger.info(f"Calculating {N_LOC_BINS}-bin segment statistics...")
    loc_bins_list = np.linspace(0.0, 1.0, N_LOC_BINS + 1)
    loc_bin_labels = list(range(N_LOC_BINS))
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
        
    # Ensure every bin/direction column pair exists in output_gdf and is filled/typed correctly
    for i in range(N_LOC_BINS):
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
    output_gdf = to_lonlat_crs(output_gdf)
    
    # Save main velocities output
    logger.info(f"Saving main velocities results to {output_file}...")
    write_vector(output_gdf, output_file)
        
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
    segment_geoms_3857 = [
        bin_segment_geometry(passage_geoms_3857[row['PassageId']], int(row['BinIndex']))
        for _, row in stats_df.iterrows()
    ]
    
    stats_gdf_3857 = gpd.GeoDataFrame(
        stats_df,
        geometry=segment_geoms_3857,
        crs="EPSG:3857"
    )
    
    # Reproject to EPSG:4326 for final output
    stats_gdf_4326 = to_lonlat_crs(stats_gdf_3857)
    stats_gdf_4326 = stats_gdf_4326.rename(columns={'direction': 'Direction'})
    stats_gdf_4326['MedianSpeed'] = stats_gdf_4326['MedianSpeed'].fillna(0.0)
    stats_gdf_4326['ProfileLength'] = stats_gdf_4326['ProfileLength'].fillna(0.0)
    stats_gdf_4326['BinWidth'] = stats_gdf_4326['ProfileLength'] / N_LOC_BINS
    
    # Save segments output next to output_file with matching stem
    segments_output_file = output_file.parent / f"{output_file.stem}_bin_segments.geojson"
    logger.info(f"Saving bin segments results to {segments_output_file}...")
    write_vector(stats_gdf_4326, segments_output_file)

    logger.info("Analysis and segment generation complete!")
