#!/usr/bin/env python3
"""
detect_mississippi_encounters.py

Demonstrates detecting vessel encounters (crossings, overtakings, head-on meetings)
on an inland waterway (the Mississippi River) using NOAA AIS data:
https://noaaocm.blob.core.windows.net/ais/csv2/csv2026/ais-2026-03-31.csv.zst

Workflow:
1. Extract or stream AIS points for a Mississippi River reach (e.g. Baton Rouge - New Orleans).
2. Trajectorize fixes into voyages per vessel (using moving_dask / trajectorize_dataframe).
3. Generate point-pair line segments with kinematic attributes.
4. Detect encounters using spatio-temporal indexing (time binning + Shapely STRtree).
5. Output encounter statistics and export to GeoParquet and GeoJSON.
"""

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import numpy as np
import shapely

from ais_shader.moving_dask.trajectory import trajectorize_dataframe
from ais_shader.events import detect_encounters, generate_encounter_timeseries
from ais_shader.fairway import FairwayAxis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_NOAA_URL = "https://noaaocm.blob.core.windows.net/ais/csv2/csv2026/ais-2026-03-31.csv.zst"

# Mississippi River corridor: Baton Rouge to New Orleans / river bends
# Approx: Lon [-91.3, -89.8], Lat [29.8, 30.6]
DEFAULT_BBOX = (-91.3, 29.8, -89.8, 30.6)


def stream_noaa_mississippi_slice(
    url: str,
    bbox: tuple = DEFAULT_BBOX,
    max_records: int = 25000,
    byte_limit: int = 80_000_000,
    min_sog: float = 0.5,
) -> pd.DataFrame:
    """
    Stream a slice of the compressed NOAA AIS CSV from Azure blob storage,
    filtering for coordinates in the Mississippi River bbox and moving vessels.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    logger.info(f"Streaming NOAA AIS slice from {url} (range: 0-{byte_limit} bytes, min_sog={min_sog} kn)...")

    curl_cmd = ["curl", "-s", "-r", f"0-{byte_limit}", url]
    zstd_cmd = ["zstd", "-dc"]
    curl_proc = subprocess.Popen(curl_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    zstd_proc = subprocess.Popen(
        zstd_cmd,
        stdin=curl_proc.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if curl_proc.stdout is not None:
        curl_proc.stdout.close()

    records = []
    try:
        header = zstd_proc.stdout.readline()
        if not header:
            raise RuntimeError("Failed to stream data from URL or decompress with zstd.")

        cols = [c.strip() for c in header.split(",")]
        col_idx = {c: i for i, c in enumerate(cols)}

        needed = ['mmsi', 'base_date_time', 'longitude', 'latitude', 'sog', 'cog', 'heading', 'vessel_type', 'length', 'width']
        for c in needed:
            if c not in col_idx:
                raise KeyError(f"Missing column '{c}' in stream. Found: {cols}")

        for line in zstd_proc.stdout:
            parts = line.strip().split(",")
            if len(parts) < len(cols):
                continue
            try:
                lon = float(parts[col_idx['longitude']])
                lat = float(parts[col_idx['latitude']])
                sog = float(parts[col_idx['sog']]) if parts[col_idx['sog']] else 0.0
            except ValueError:
                continue

            if min_sog is not None and sog < min_sog:
                continue

            if min_lon <= lon <= max_lon and min_lat <= lat <= max_lat:
                records.append({
                    'mmsi': int(parts[col_idx['mmsi']]),
                    'base_date_time': parts[col_idx['base_date_time']],
                    'longitude': lon,
                    'latitude': lat,
                    'sog': sog,
                    'cog': float(parts[col_idx['cog']]) if parts[col_idx['cog']] else 0.0,
                    'heading': float(parts[col_idx['heading']]) if parts[col_idx['heading']] else 0.0,
                    'vessel_type': parts[col_idx['vessel_type']],
                    'length': float(parts[col_idx['length']]) if parts[col_idx['length']] else np.nan,
                    'width': float(parts[col_idx['width']]) if parts[col_idx['width']] else np.nan,
                })
                if max_records and len(records) >= max_records:
                    break
    finally:
        for proc in (zstd_proc, curl_proc):
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass

    df = pd.DataFrame(records)
    logger.info(f"Streamed and filtered {len(df):,} Mississippi AIS points.")
    return df


def generate_synthetic_mississippi_data() -> pd.DataFrame:
    """Generate realistic synthetic AIS traffic along a Mississippi River bend."""
    logger.info("Generating synthetic Mississippi River AIS traffic for verification...")
    # Mississippi river channel waypoint coordinates (New Orleans bend)
    # Waypoints: Southbound tows, Northbound tows, and a crossing ferry
    timestamps = pd.date_range("2026-03-31 10:00:00", periods=20, freq="60s")

    records = []
    # Vessel 1 (Downbound Tow): Heading SE along the river, SOG ~ 8 knots
    lons_1 = np.linspace(-90.10, -90.00, 20)
    lats_1 = np.linspace(29.98, 29.92, 20)
    for t, lon, lat in zip(timestamps, lons_1, lats_1):
        records.append({
            'mmsi': 367111222,
            'base_date_time': t,
            'longitude': lon,
            'latitude': lat,
            'sog': 8.0,
            'cog': 130.0,
            'heading': 130.0,
            'vessel_type': '31',
            'length': 60.0,
            'width': 15.0,
        })

    # Vessel 2 (Upbound Tow): Heading NW along the river, SOG ~ 6 knots (opposite course: Head-On)
    lons_2 = np.linspace(-90.002, -90.102, 20)
    lats_2 = np.linspace(29.921, 29.981, 20)
    for t, lon, lat in zip(timestamps, lons_2, lats_2):
        records.append({
            'mmsi': 367333444,
            'base_date_time': t,
            'longitude': lon,
            'latitude': lat,
            'sog': 6.0,
            'cog': 310.0,
            'heading': 310.0,
            'vessel_type': '31',
            'length': 50.0,
            'width': 12.0,
        })

    # Vessel 3 (Fast Crew Boat): Heading SE, overtaking Vessel 1, SOG ~ 16 knots
    lons_3 = np.linspace(-90.15, -89.95, 20)
    lats_3 = np.linspace(30.01, 29.89, 20)
    for t, lon, lat in zip(timestamps, lons_3, lats_3):
        records.append({
            'mmsi': 367555666,
            'base_date_time': t,
            'longitude': lon,
            'latitude': lat,
            'sog': 16.0,
            'cog': 130.0,
            'heading': 130.0,
            'vessel_type': '52',
            'length': 25.0,
            'width': 6.0,
        })

    # Vessel 4 (Algiers Ferry): Crossing the river NE to SW around minute 8-12
    ferry_ts = pd.date_range("2026-03-31 10:07:00", periods=8, freq="60s")
    lons_4 = np.linspace(-90.05, -90.06, 8)
    lats_4 = np.linspace(29.96, 29.94, 8)
    for t, lon, lat in zip(ferry_ts, lons_4, lats_4):
        records.append({
            'mmsi': 367999888,
            'base_date_time': t,
            'longitude': lon,
            'latitude': lat,
            'sog': 5.0,
            'cog': 210.0,
            'heading': 210.0,
            'vessel_type': '60',
            'length': 40.0,
            'width': 10.0,
        })

    return pd.DataFrame(records)


def make_segments_from_points(df: pd.DataFrame, max_segment_duration_s: float = 600.0) -> gpd.GeoDataFrame:
    """Trajectorize point fixes and convert into point-pair segments."""
    df['base_date_time'] = pd.to_datetime(df['base_date_time'])
    geoms = gpd.points_from_xy(df['longitude'], df['latitude'], crs="EPSG:4326")
    gdf_points = gpd.GeoDataFrame(df, geometry=geoms, crs="EPSG:4326")

    if 'trip_id' in gdf_points.columns:
        logger.info("Dataset is already trajectorized (trip_id present). Reusing existing trajectories...")
        trajectorized = gdf_points
    else:
        # Trajectorize
        logger.info("Trajectorizing points into voyages...")
        import dask_geopandas
        ddf_points = dask_geopandas.from_geopandas(gdf_points, npartitions=1)
        trajectorized = trajectorize_dataframe(
            ddf_points,
            vessel_id_col='mmsi',
            time_col='base_date_time',
            gap_threshold_hours=0.5,
        ).compute()

    # Convert point-pairs to segments
    logger.info("Generating segment table...")
    sorted_df = trajectorized.sort_values(by=['trip_id', 'base_date_time'])
    shifted = sorted_df.groupby('trip_id').shift(-1)
    mask = shifted['base_date_time'].notna()

    p1 = sorted_df[mask]
    p2 = shifted[mask]

    seg_duration = (p2['base_date_time'].values - p1['base_date_time'].values).astype('timedelta64[s]').astype(float)
    if max_segment_duration_s is not None:
        valid_dur = (seg_duration <= max_segment_duration_s) & (seg_duration > 0)
        p1 = p1[valid_dur]
        p2 = p2[valid_dur]
        seg_duration = seg_duration[valid_dur]

    coords = np.column_stack([
        p1['longitude'].values, p1['latitude'].values,
        p2['longitude'].values, p2['latitude'].values,
    ]).reshape(-1, 2, 2)
    line_geoms = shapely.linestrings(coords)

    seg_df = pd.DataFrame({
        'MMSI': p1['mmsi'].astype(str).values,
        'trip_id': p1['trip_id'].values,
        'VesselType': p1['vessel_type'].values if 'vessel_type' in p1.columns else 'Unknown',
        'VesselGroup': 'Commercial',
        'Length': p1['length'].values if 'length' in p1.columns else np.nan,
        'Width': p1['width'].values if 'width' in p1.columns else np.nan,
        'Draft': np.nan,
        'sog': p1['sog'].values,
        'speed_mps': p1['speed_mps'].values if 'speed_mps' in p1.columns else (p1['sog'].values * 0.514444),
        'segment_start_time': p1['base_date_time'].values,
        'segment_end_time': p2['base_date_time'].values,
        'segment_duration_s': seg_duration,
    })

    gdf_segments = gpd.GeoDataFrame(seg_df, geometry=line_geoms, crs="EPSG:4326")
    logger.info(f"Constructed {len(gdf_segments):,} trajectory segments.")
    return trajectorized, gdf_segments


def get_default_output_dir() -> Path:
    shared_scratch = Path("/scratch-shared/fbaart/data/ais_encounters")
    if shared_scratch.parent.exists():
        return shared_scratch
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir and Path(tmpdir).exists():
        return Path(tmpdir) / "ais_encounters"
    return Path("output_encounters")


def main():
    default_markers = Path("/scratch-shared/fbaart/data/marine-cadastre/usace_river_mile_markers.gpkg")
    parser = argparse.ArgumentParser(description="Detect vessel encounters on the Mississippi River.")
    parser.add_argument("--url", default=DEFAULT_NOAA_URL, help="NOAA AIS dataset URL.")
    parser.add_argument("--input-file", type=Path, default=None, help="Local CSV or Parquet input file.")
    parser.add_argument("--max-records", type=int, default=10000, help="Max points to stream/sample.")
    parser.add_argument("--max-distance", type=float, default=600.0, help="Max encounter distance in meters (default: 600m).")
    parser.add_argument("--merge-gap", type=float, default=10.0, help="Merge gap in minutes.")
    parser.add_argument("--min-sog", type=float, default=0.5, help="Minimum SOG in knots to exclude stationary/moored vessels (default: 0.5 kn).")
    parser.add_argument("--byte-limit", type=int, default=80_000_000, help="Max compressed bytes to stream from NOAA (default: 80MB ~10 hours).")
    parser.add_argument("--output-dir", type=Path, default=get_default_output_dir(), help="Output directory (defaults to cluster scratch drive if available).")
    parser.add_argument("--fairway-markers", type=Path, default=default_markers if default_markers.exists() else None, help="USACE River Mile Markers GPKG path.")
    parser.add_argument("--river-name", default="MISSISSIPPI-LO", help="River name filter for fairway axis (default: 'MISSISSIPPI-LO').")
    parser.add_argument("--export-timeseries", action="store_true", default=True, help="Export dynamic encounter time series connecting lines (default: True).")
    parser.add_argument("--timeseries-step", type=float, default=30.0, help="Temporal sampling interval in seconds for encounter time series (default: 30s).")
    parser.add_argument("--synthetic", action="store_true", help="Force using synthetic Mississippi River data.")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Optional fairway axis construction
    fairway_axis = None
    if args.fairway_markers and args.fairway_markers.exists():
        logger.info(f"Loading primary fairway axis from {args.fairway_markers} (river={args.river_name})...")
        try:
            fairway_axis = FairwayAxis.from_mile_markers(
                args.fairway_markers,
                river_name=args.river_name,
                spline_sample_interval_m=100.0,
                spline_smoothing=5000.0,
            )
            # Save fairway centerline
            out_centerline_parquet = args.output_dir / "mississippi_fairway_centerline.geoparquet"
            out_centerline_geojson = args.output_dir / "mississippi_fairway_centerline.geojson"
            logger.info(f"Saving fairway centerline to {out_centerline_parquet}...")
            gdf_centerline = fairway_axis.to_geodataframe(crs="EPSG:4326")
            gdf_centerline.to_parquet(out_centerline_parquet)
            gdf_centerline.to_file(out_centerline_geojson, driver="GeoJSON")

            # Save fairway mile marker station points
            out_markers_parquet = args.output_dir / "mississippi_fairway_mile_markers.geoparquet"
            out_markers_geojson = args.output_dir / "mississippi_fairway_mile_markers.geojson"
            logger.info(f"Saving fairway mile markers to {out_markers_parquet}...")
            gdf_markers = fairway_axis.to_mile_points(step_miles=1.0, crs="EPSG:4326")
            gdf_markers.to_parquet(out_markers_parquet)
            gdf_markers.to_file(out_markers_geojson, driver="GeoJSON")
        except Exception as e:
            logger.warning(f"Failed to load fairway axis: {e}. Falling back to standard detection.")

    df_points = None
    if not args.synthetic:
        if args.input_file and args.input_file.exists():
            logger.info(f"Loading local file: {args.input_file}")
            if args.input_file.suffix in {".parquet", ".geoparquet"}:
                df_points = gpd.read_parquet(args.input_file)
            else:
                df_points = pd.read_csv(args.input_file, nrows=args.max_records)
        else:
            try:
                df_points = stream_noaa_mississippi_slice(
                    args.url,
                    max_records=args.max_records,
                    byte_limit=args.byte_limit,
                    min_sog=args.min_sog,
                )
            except Exception as e:
                logger.warning(f"Could not stream NOAA data ({e}); falling back to synthetic Mississippi data.")

    if df_points is None or df_points.empty:
        df_points = generate_synthetic_mississippi_data()

    # Step 1: Trajectorize and generate segments
    trajectorized_points, segments_gdf = make_segments_from_points(df_points)

    if fairway_axis is not None:
        logger.info("Enriching trajectorized points and segments with fairway axis (river miles & direction)...")
        trajectorized_points = fairway_axis.annotate_points(trajectorized_points)
        segments_gdf = fairway_axis.annotate_segments(segments_gdf)

    # Save trajectorized points GeoParquet for point visualization
    out_points_parquet = args.output_dir / "mississippi_trajectorized_points.geoparquet"
    logger.info(f"Saving trajectorized points to {out_points_parquet}...")
    if not isinstance(trajectorized_points, gpd.GeoDataFrame):
        trajectorized_points = gpd.GeoDataFrame(trajectorized_points, geometry='geometry', crs="EPSG:4326")
    trajectorized_points.to_parquet(out_points_parquet)

    # Save segments GeoParquet
    out_segments_parquet = args.output_dir / "mississippi_segments.geoparquet"
    logger.info(f"Saving segments to {out_segments_parquet}...")
    segments_gdf.to_parquet(out_segments_parquet)

    # Step 2: Encounter detection
    logger.info(f"Running spatio-temporal encounter detection (max_distance={args.max_distance}m)...")
    encounters_gdf = detect_encounters(
        segments_gdf,
        max_distance_m=args.max_distance,
        time_bin_minutes=30.0,
        merge_gap_minutes=args.merge_gap,
        fairway_axis=fairway_axis,
    )

    logger.info(f"=== Encounter Detection Results ===")
    logger.info(f"Total encounters detected: {len(encounters_gdf):,}")

    if not encounters_gdf.empty:
        counts = encounters_gdf['encounter_type'].value_counts()
        for enc_type, count in counts.items():
            logger.info(f"  - {enc_type}: {count:,}")

        # Summary print
        print("\n" + "=" * 80)
        print("DETECTED ENCOUNTERS SUMMARY (Mississippi River Corridor)")
        print("=" * 80)
        cols_to_print = ['mmsi_1', 'mmsi_2', 'encounter_type', 'cpa_time', 'min_distance_m', 'speed_mps_1', 'speed_mps_2']
        if 'river_mile' in encounters_gdf.columns:
            cols_to_print.insert(3, 'river_mile')
        display_df = encounters_gdf[cols_to_print].copy()
        display_df['min_distance_m'] = display_df['min_distance_m'].round(1)
        display_df['speed_mps_1'] = display_df['speed_mps_1'].round(2)
        display_df['speed_mps_2'] = display_df['speed_mps_2'].round(2)
        print(display_df.head(25).to_string(index=False))
        if len(display_df) > 25:
            print(f"... and {len(display_df) - 25:,} more encounters.")
        print("=" * 80 + "\n")

        # Save encounter events
        out_parquet = args.output_dir / "mississippi_encounters.geoparquet"
        out_geojson = args.output_dir / "mississippi_encounters.geojson"
        logger.info(f"Saving encounters to {out_parquet}...")
        encounters_gdf.to_parquet(out_parquet)
        logger.info(f"Saving encounters to {out_geojson}...")
        encounters_gdf.to_file(out_geojson, driver="GeoJSON")

        # Step 3: Dynamic encounter connecting lines time series
        if args.export_timeseries:
            logger.info(f"Generating dynamic encounter time series (step={args.timeseries_step}s)...")
            ts_gdf = generate_encounter_timeseries(
                segments_gdf,
                encounters_gdf,
                step_seconds=args.timeseries_step,
                fairway_axis=fairway_axis,
            )
            logger.info(f"Generated {len(ts_gdf):,} dynamic connecting lines across {len(encounters_gdf):,} encounters.")
            out_ts_parquet = args.output_dir / "mississippi_encounter_timeseries.geoparquet"
            out_ts_geojson = args.output_dir / "mississippi_encounter_timeseries.geojson"
            logger.info(f"Saving encounter time series to {out_ts_parquet}...")
            ts_gdf.to_parquet(out_ts_parquet)
            logger.info(f"Saving encounter time series to {out_ts_geojson}...")
            ts_gdf.to_file(out_ts_geojson, driver="GeoJSON")
    else:
        logger.info("No encounters detected within the specified threshold.")


if __name__ == "__main__":
    main()
