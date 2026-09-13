#!/usr/bin/env python3
"""
detect_euris_encounters.py

Demonstrates vessel encounter detection (overtakings, head-on meetings, crossings,
and stationary obstacle interactions) on Dutch inland waterways (e.g. Amsterdam-Rijnkanaal,
Lek, Waal) using European River Information Services (EURIS) AIS data and Rijkswaterstaat
(RWS) FIS VNDS fairway centerlines.

Data Sources:
1. AIS Data: Live or recorded EURIS WebSocket fixes (EPSG:4326).
2. Fairway Data: Rijkswaterstaat ArcGIS REST API (MapServer 58: vaarwegvak,
   MapServer 55: vaarwegen, MapServer 11: kilometermarkering).

Output Format:
All spatial products are packaged directly into a single comprehensive GeoPackage (.gpkg)
file in the Dutch national coordinate system (Amersfoort / RD New - EPSG:28992):
- Layer 'fairway_centerline'
- Layer 'fairway_sections'
- Layer 'trajectorized_points'
- Layer 'segments'
- Layer 'encounters'
- Layer 'stationary_vessels'
- Layer 'timeseries'

Usage:
  uv run python examples/detect_euris_encounters.py
  uv run python examples/detect_euris_encounters.py --fairway-id 27861 --river-name "Lek"
"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import sys
from pathlib import Path
from typing import Optional, Tuple

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import LineString, Point

from ais_shader.events import detect_encounters, generate_encounter_timeseries, extract_stationary_vessels
from ais_shader.fairway import FairwayAxis
from ais_shader.rws import (
    build_rws_fairway,
    fetch_rws_fairway_sections,
    fetch_rws_kilometer_markers,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("detect_euris_encounters")

DEFAULT_CRAWL_PARQUET = Path("/scratch-shared/fbaart/data/euris_crawl/euris_crawl_20260913_121627.geoparquet")
DEFAULT_OUTPUT_GPKG = Path("/scratch-shared/fbaart/data/euris_crawl/euris_encounters.gpkg")

# Default: Amsterdam-Rijnkanaal (fairway ID 15384)
DEFAULT_FAIRWAY_ID = 15384
DEFAULT_FAIRWAY_NAME = "Amsterdam-Rijnkanaal"
DEFAULT_METRIC_CRS = "EPSG:28992"  # Amersfoort / RD New


def make_segments_from_points(
    gdf_pts: gpd.GeoDataFrame,
    metric_crs: str = DEFAULT_METRIC_CRS,
    max_duration_s: float = 300.0,
) -> Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    Project points to metric CRS and generate 2-point line segments.

    Returns:
        (gdf_pts_metric, gdf_segs_metric)
    """
    logger.info(f"Projecting {len(gdf_pts):,} point fixes to metric CRS ({metric_crs})...")
    gdf_metric = gdf_pts.to_crs(metric_crs)

    time_col = "base_date_time" if "base_date_time" in gdf_metric.columns else "timestamp"
    gdf_metric[time_col] = pd.to_datetime(gdf_metric[time_col])

    if "trip_id" not in gdf_metric.columns:
        logger.info("Assigning single trip_id per MMSI...")
        gdf_metric["trip_id"] = gdf_metric["mmsi"].astype(str) + "_voyage1"

    logger.info("Constructing 2-point line segments from consecutive fixes...")
    sorted_df = gdf_metric.sort_values(by=["trip_id", time_col])
    shifted = sorted_df.groupby("trip_id").shift(-1)
    mask = shifted[time_col].notna()

    p1 = sorted_df[mask]
    p2 = shifted[mask]

    coords1 = np.column_stack([p1.geometry.x.values, p1.geometry.y.values])
    coords2 = np.column_stack([p2.geometry.x.values, p2.geometry.y.values])
    geoms = [LineString([c1, c2]) for c1, c2 in zip(coords1, coords2)]

    t1 = pd.to_datetime(p1[time_col]).values
    t2 = pd.to_datetime(p2[time_col]).values
    durations = (t2 - t1) / np.timedelta64(1, "s")

    valid = (durations > 0) & (durations <= max_duration_s)
    n_dropped = int((~valid).sum())
    if n_dropped > 0:
        logger.info(f"Dropped {n_dropped:,} segments exceeding max duration ({max_duration_s}s).")

    v_lens = np.array([g.length for g in geoms])
    speeds_mps = v_lens / np.maximum(durations, 1e-3)

    lengths = p1["length"].values if "length" in p1.columns else np.full(len(p1), np.nan)
    widths = p1["beam"].values if "beam" in p1.columns else np.full(len(p1), np.nan)
    shiptypes = p1["shiptypeAIS"].values if "shiptypeAIS" in p1.columns else np.full(len(p1), None)

    df_segs = pd.DataFrame({
        "MMSI": p1["mmsi"].values[valid],
        "trip_id": p1["trip_id"].values[valid],
        "VesselType": shiptypes[valid],
        "VesselGroup": "commercial",
        "Length": lengths[valid],
        "Width": widths[valid],
        "segment_start_time": t1[valid],
        "segment_end_time": t2[valid],
        "speed_mps": speeds_mps[valid],
        "sog": p1["sog"].values[valid] if "sog" in p1.columns else np.nan,
        "segment_duration_s": durations[valid],
    })

    geoms_valid = [g for g, v in zip(geoms, valid) if v]
    gdf_segs = gpd.GeoDataFrame(df_segs, geometry=geoms_valid, crs=metric_crs)
    logger.info(f"Generated {len(gdf_segs):,} valid metric line segments across {gdf_segs['MMSI'].nunique()} vessels.")
    return gdf_metric, gdf_segs


def save_layer_to_gpkg(
    gdf: gpd.GeoDataFrame,
    gpkg_path: Path,
    layer_name: str,
    first_layer: bool = False,
) -> None:
    """Save a GeoDataFrame as a named layer in a GeoPackage."""
    gpkg_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if first_layer else "a"
    logger.info(f"Writing layer '{layer_name}' ({len(gdf):,} records) to {gpkg_path}...")
    gdf.to_file(gpkg_path, layer=layer_name, driver="GPKG", mode=mode)


def run_euris_encounter_detection(
    input_file: Path,
    output_gpkg: Path,
    fairway_id: Optional[int] = DEFAULT_FAIRWAY_ID,
    river_name: Optional[str] = DEFAULT_FAIRWAY_NAME,
    metric_crs: str = DEFAULT_METRIC_CRS,
    max_distance_m: float = 100.0,
    corridor_buffer_m: float = 300.0,
    timeseries_step_s: float = 15.0,
    exclude_stationary: str = "both",
) -> None:
    logger.info("=" * 80)
    logger.info("Starting Dutch Inland Encounter Detection (EURIS + RWS Fairway)")
    logger.info(f"Input AIS data: {input_file}")
    logger.info(f"Output GeoPackage: {output_gpkg}")
    logger.info(f"Fairway: {river_name} (ID: {fairway_id})")
    logger.info(f"Metric CRS: {metric_crs} (Amersfoort / RD New)")
    logger.info(f"Max Proximity Distance: {max_distance_m:.1f} m")
    logger.info("=" * 80)

    # 1. Fetch RWS Fairway Centerline and Sections
    logger.info(f"Fetching fairway centerline from Rijkswaterstaat FIS MapServer 58 for '{river_name}'...")
    fairway_axis = build_rws_fairway(
        fairway_id=fairway_id,
        river_name=river_name,
        metric_crs=metric_crs,
    )
    logger.info(
        f"Built continuous fairway axis: {fairway_axis.fairway_name} "
        f"(length={fairway_axis.length_m / 1000.0:.2f} km, start={fairway_axis.chainage_start_m / 1000.0:.2f} km)"
    )

    fairway_centerline_gdf = fairway_axis.to_geodataframe(crs=metric_crs)

    # Also fetch individual sections for rich corridor reference
    fairway_sections_gdf = fetch_rws_fairway_sections(
        fairway_id=fairway_id,
        name=river_name,
        metric_crs=metric_crs,
    )

    # 2. Load and Preprocess AIS Points
    logger.info(f"Loading EURIS AIS data from {input_file}...")
    if input_file.suffix in {".parquet", ".geoparquet"}:
        gdf_raw = gpd.read_parquet(input_file)
    else:
        gdf_raw = gpd.read_file(input_file)

    gdf_pts_metric, gdf_segs_metric = make_segments_from_points(
        gdf_raw, metric_crs=metric_crs, max_duration_s=300.0
    )

    # 3. Spatial Filter: Restrict to Fairway Corridor
    logger.info(f"Filtering segments within {corridor_buffer_m:.0f}m lateral corridor of {fairway_axis.fairway_name}...")
    corridor_poly = fairway_axis.centerline_geom.buffer(corridor_buffer_m)
    in_corridor_mask = gdf_segs_metric.geometry.intersects(corridor_poly)
    gdf_segs_corridor = gdf_segs_metric[in_corridor_mask].copy().reset_index(drop=True)
    logger.info(
        f"Retained {len(gdf_segs_corridor):,} segments ({len(gdf_segs_corridor) / len(gdf_segs_metric) * 100:.1f}%) "
        f"across {gdf_segs_corridor['MMSI'].nunique()} vessels within the fairway corridor."
    )

    # Also filter points within corridor for display
    pts_in_corridor = gdf_pts_metric[gdf_pts_metric.geometry.intersects(corridor_poly)].copy().reset_index(drop=True)

    # 4. Extract Stationary Vessels
    logger.info("Extracting stationary vessels and moored obstacles...")
    stat_gdf = extract_stationary_vessels(
        gdf_segs_corridor,
        fairway_axis=fairway_axis,
        min_moving_speed=0.5,
    )
    logger.info(f"Identified {len(stat_gdf):,} stationary fixes across {stat_gdf['MMSI'].nunique() if not stat_gdf.empty else 0} vessels.")

    # 5. Detect Fairway-Aligned Encounters
    logger.info(f"Running encounter detection (max_dist={max_distance_m}m, exclude_stationary='{exclude_stationary}')...")
    events_gdf = detect_encounters(
        gdf_segs_corridor,
        max_distance_m=max_distance_m,
        time_bin_minutes=15.0,
        merge_gap_minutes=5.0,
        exclude_stationary=exclude_stationary,
        min_moving_speed=0.5,
        fairway_axis=fairway_axis,
        metric_crs=metric_crs,
        max_segment_duration_s=300.0,
    )
    logger.info(f"Detected {len(events_gdf):,} total encounter events.")

    # 6. Generate Dynamic Time Series Connecting Lines
    ts_gdf = gpd.GeoDataFrame()
    if not events_gdf.empty:
        logger.info(f"Generating synchronous dynamic time series connecting lines (step={timeseries_step_s}s)...")
        ts_gdf = generate_encounter_timeseries(
            gdf_segs_corridor,
            events_gdf,
            step_seconds=timeseries_step_s,
            fairway_axis=fairway_axis,
            metric_crs=metric_crs,
            max_distance_m=max_distance_m,
        )
        logger.info(f"Generated {len(ts_gdf):,} time series connecting lines.")

    # 7. Ensure uniform CRS across all GeoPackage layers (EPSG:28992)
    if not events_gdf.empty and metric_crs and events_gdf.crs != metric_crs:
        events_gdf = events_gdf.to_crs(metric_crs)
    if not ts_gdf.empty and metric_crs and ts_gdf.crs != metric_crs:
        ts_gdf = ts_gdf.to_crs(metric_crs)

    # 8. Package All Layers into Master GeoPackage (.gpkg)
    if output_gpkg.exists():
        output_gpkg.unlink()

    save_layer_to_gpkg(fairway_centerline_gdf, output_gpkg, "fairway_centerline", first_layer=True)
    if not fairway_sections_gdf.empty:
        save_layer_to_gpkg(fairway_sections_gdf, output_gpkg, "fairway_sections")
    save_layer_to_gpkg(pts_in_corridor, output_gpkg, "trajectorized_points")
    save_layer_to_gpkg(gdf_segs_corridor, output_gpkg, "segments")
    if not stat_gdf.empty:
        save_layer_to_gpkg(stat_gdf, output_gpkg, "stationary_vessels")
    if not events_gdf.empty:
        save_layer_to_gpkg(events_gdf, output_gpkg, "encounters")
    if not ts_gdf.empty:
        save_layer_to_gpkg(ts_gdf, output_gpkg, "timeseries")

    # 8. Print Results & Summary
    logger.info("=" * 80)
    logger.info(f"EURIS Encounter Detection Run Completed Successfully!")
    logger.info(f"Master GeoPackage: {output_gpkg} ({output_gpkg.stat().st_size / (1024*1024):.2f} MB)")
    if not events_gdf.empty:
        logger.info("\nEncounter Breakdown by Type:")
        for enc_type, count in events_gdf["encounter_type"].value_counts().items():
            logger.info(f"  - {enc_type:20s}: {count:4d} ({count / len(events_gdf) * 100:.1f}%)")
        logger.info("\nEncounter Distance Metrics (m):")
        logger.info(
            f"  - Min CPA distance: {events_gdf['min_distance_m'].min():.1f} m, "
            f"Median: {events_gdf['min_distance_m'].median():.1f} m, "
            f"Max: {events_gdf['min_distance_m'].max():.1f} m"
        )
    logger.info("=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description="Detect vessel encounters on Dutch inland waterways using EURIS AIS data and RWS FIS Fairway"
    )
    parser.add_argument(
        "--input-file",
        "-i",
        type=Path,
        default=DEFAULT_CRAWL_PARQUET,
        help=f"Input EURIS AIS points dataset (default: {DEFAULT_CRAWL_PARQUET})",
    )
    parser.add_argument(
        "--output-gpkg",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT_GPKG,
        help=f"Output master GeoPackage path (default: {DEFAULT_OUTPUT_GPKG})",
    )
    parser.add_argument(
        "--fairway-id",
        type=int,
        default=DEFAULT_FAIRWAY_ID,
        help=f"RWS Fairway ID (default: {DEFAULT_FAIRWAY_ID} for Amsterdam-Rijnkanaal, 27861 for Lek, 33192 for Waal)",
    )
    parser.add_argument(
        "--river-name",
        type=str,
        default=DEFAULT_FAIRWAY_NAME,
        help=f"Fairway / river name (default: {DEFAULT_FAIRWAY_NAME})",
    )
    parser.add_argument(
        "--metric-crs",
        type=str,
        default=DEFAULT_METRIC_CRS,
        help=f"Metric CRS (default: {DEFAULT_METRIC_CRS} - Amersfoort / RD New)",
    )
    parser.add_argument(
        "--max-distance",
        type=float,
        default=100.0,
        help="Maximum distance in meters for encounter detection (default: 100.0m)",
    )
    parser.add_argument(
        "--corridor-width",
        type=float,
        default=300.0,
        help="Fairway lateral corridor buffer in meters (default: 300.0m)",
    )
    parser.add_argument(
        "--timeseries-step",
        type=float,
        default=15.0,
        help="Sampling step in seconds for dynamic connecting lines (default: 15.0s)",
    )
    parser.add_argument(
        "--exclude-stationary",
        type=str,
        default="both",
        choices=["both", "any", "none"],
        help="Stationary vessel handling policy (default: both)",
    )

    args = parser.parse_args()

    if not args.input_file.exists():
        logger.error(f"Input file not found: {args.input_file}")
        sys.exit(1)

    run_euris_encounter_detection(
        input_file=args.input_file,
        output_gpkg=args.output_gpkg,
        fairway_id=args.fairway_id,
        river_name=args.river_name,
        metric_crs=args.metric_crs,
        max_distance_m=args.max_distance,
        corridor_buffer_m=args.corridor_width,
        timeseries_step_s=args.timeseries_step,
        exclude_stationary=args.exclude_stationary,
    )


if __name__ == "__main__":
    main()
