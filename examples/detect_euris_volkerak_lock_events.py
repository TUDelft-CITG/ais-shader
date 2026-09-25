#!/usr/bin/env python3
"""
detect_euris_volkerak_lock_events.py

Detects lock chamber events (chamber entry, exit, lock dwell / lockage cycle,
and chamber occupancy) at Volkeraksluizen using European River Information Services
(EURIS) AIS data and Rijkswaterstaat FIS VNDS lock chamber geometries (sluiskolk_v,
MapServer layer 65).
Exports results as standardized GeoParquet (with explicit EPSG code).

Lock Chambers at Volkeraksluizen:
- Westkolk Volkeraksluizen (ID: 28, ISRS: 9065213)
- Oostkolk Volkeraksluizen (ID: 29, ISRS: 9065215)
- Middenkolk Volkeraksluizen (ID: 134, ISRS: 9065214)
- Sluiskolk Jachtensluis Volkeraksluizen (ID: 96, ISRS: 24942673)

Usage:
  uv run python examples/detect_euris_volkerak_lock_events.py --input-file examples/data/euris_crawl_20260925_115942.geoparquet
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

import click
import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point
from tabulate import tabulate

from ais_shader.events import detect_polygon_entry_exit, extract_stationary_vessels
from ais_shader.preprocessing import get_vessel_group
from ais_shader.rws import fetch_rws_lock_chambers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("detect_volkerak_lock_events")

DEFAULT_VOLKERAK_GEOJSON = Path("examples/data/volkerak.geojson")
DEFAULT_LOCK_CHAMBERS_FILE = Path("examples/data/volkerak_sluiskolken.geoparquet")
DEFAULT_OUTPUT_PARQUET = Path("examples/data/volkerak_lock_events.geoparquet")
DEFAULT_METRIC_CRS = "EPSG:28992"  # Amersfoort / RD New


def classify_vessel_group(shiptype: any) -> str:
    """Classify vessel into standard vessel groups."""
    return get_vessel_group(shiptype, {})


def load_or_fetch_lock_chambers(
    lock_file: Path,
    volkerak_bbox: Tuple[float, float, float, float],
    metric_crs: str = DEFAULT_METRIC_CRS,
) -> gpd.GeoDataFrame:
    """Load lock chamber geometries from GeoParquet or fetch live from RWS MapServer layer 65."""
    if lock_file.exists():
        logger.info(f"Loading lock chambers from cached file: {lock_file}")
        if lock_file.suffix in {".geoparquet", ".parquet"}:
            gdf = gpd.read_parquet(lock_file)
        else:
            gdf = gpd.read_file(lock_file)
    else:
        logger.info(f"Querying RWS MapServer Layer 65 (sluiskolk_v) for Volkerak bbox: {volkerak_bbox}")
        gdf = fetch_rws_lock_chambers(bbox=volkerak_bbox, out_crs="EPSG:4326")
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        gdf.to_parquet(lock_file)
        logger.info(f"Cached {len(gdf)} lock chambers to {lock_file}")

    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    return gdf.to_crs(metric_crs)


def make_segments_from_points(
    gdf_pts: gpd.GeoDataFrame,
    metric_crs: str = DEFAULT_METRIC_CRS,
    max_duration_s: float = 300.0,
) -> Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    Construct 2-point line segments from sorted point fixes per trip_id.

    Returns:
        (gdf_pts_metric, gdf_segs_metric)
    """
    if gdf_pts.empty:
        raise ValueError("Input AIS points GeoDataFrame is empty.")
    if gdf_pts.crs is None:
        raise ValueError("Input AIS points must have a defined CRS.")

    time_col = "base_date_time" if "base_date_time" in gdf_pts.columns else "timestamp"
    if time_col not in gdf_pts.columns:
        raise KeyError("Input AIS data must contain 'base_date_time' or 'timestamp' column.")

    vessel_col = "mmsi" if "mmsi" in gdf_pts.columns else "MMSI"
    if vessel_col not in gdf_pts.columns:
        raise KeyError("Input AIS data must contain 'mmsi' or 'MMSI' column.")

    logger.info(f"Projecting {len(gdf_pts):,} point fixes to {metric_crs}...")
    gdf_metric = gdf_pts.to_crs(metric_crs).copy()
    gdf_metric[time_col] = pd.to_datetime(gdf_metric[time_col])

    if "trip_id" not in gdf_metric.columns:
        gdf_metric["trip_id"] = gdf_metric[vessel_col].astype(str) + "_voyage1"

    sorted_df = gdf_metric.sort_values(by=["trip_id", time_col]).reset_index(drop=True)

    # Deduplicate consecutive identical coordinates for moving vessels
    coords_all = np.column_stack([sorted_df.geometry.x.values, sorted_df.geometry.y.values])
    trips_all = sorted_df["trip_id"].values
    sogs_all = sorted_df["sog"].values if "sog" in sorted_df.columns else np.zeros(len(sorted_df))

    same_trip = trips_all[1:] == trips_all[:-1]
    same_coord = (coords_all[1:, 0] == coords_all[:-1, 0]) & (coords_all[1:, 1] == coords_all[:-1, 1])
    is_moving = sogs_all[1:] >= 0.5
    drop_mask = np.zeros(len(sorted_df), dtype=bool)
    drop_mask[1:] = same_trip & same_coord & is_moving
    n_dedup = int(drop_mask.sum())
    if n_dedup > 0:
        logger.info(f"Deduplicated {n_dedup:,} repeated GPS pings on moving vessels.")
        sorted_df = sorted_df[~drop_mask].copy().reset_index(drop=True)

    shifted = sorted_df.groupby("trip_id").shift(-1)
    mask = shifted[time_col].notna()

    p1 = sorted_df[mask]
    p2 = shifted[mask]

    coords1 = np.column_stack([p1.geometry.x.values, p1.geometry.y.values])
    coords2 = np.column_stack([p2.geometry.x.values, p2.geometry.y.values])
    identical = (coords1 == coords2).all(axis=1)
    if np.any(identical):
        coords2[identical] = coords1[identical] + [0.05, 0.05]
    geoms = [LineString([c1, c2]) for c1, c2 in zip(coords1, coords2)]

    t1 = pd.to_datetime(p1[time_col]).values
    t2 = pd.to_datetime(p2[time_col]).values
    durations = (t2 - t1) / np.timedelta64(1, "s")

    valid = (durations > 0) & (durations <= max_duration_s)
    v_lens = np.array([g.length for g in geoms])
    speeds_mps = v_lens / np.maximum(durations, 1e-3)

    lengths = p1["length"].values if "length" in p1.columns else np.full(len(p1), np.nan)
    widths = p1["beam"].values if "beam" in p1.columns else np.full(len(p1), np.nan)
    drafts = p1["draft"].values if "draft" in p1.columns else np.zeros(len(p1))
    shiptypes = p1["shiptypeAIS"].values if "shiptypeAIS" in p1.columns else np.full(len(p1), 0)
    vessel_groups = [classify_vessel_group(st) for st in shiptypes[valid]]

    df_segs = pd.DataFrame(
        {
            "MMSI": p1[vessel_col].values[valid].astype(str),
            "trip_id": p1["trip_id"].values[valid],
            "VesselType": shiptypes[valid],
            "VesselGroup": vessel_groups,
            "Length": lengths[valid],
            "Width": widths[valid],
            "Draft": drafts[valid],
            "segment_start_time": pd.to_datetime(t1[valid]),
            "segment_end_time": pd.to_datetime(t2[valid]),
            "DurationMinutes": np.round(durations[valid] / 60.0, 2),
            "speed_mps": speeds_mps[valid],
            "sog": p1["sog"].values[valid] if "sog" in p1.columns else np.nan,
            "segment_duration_s": durations[valid],
        }
    )

    geoms_valid = [g for g, v in zip(geoms, valid) if v]
    gdf_segs = gpd.GeoDataFrame(df_segs, geometry=geoms_valid, crs=metric_crs)
    logger.info(f"Generated {len(gdf_segs):,} metric line segments.")
    return gdf_metric, gdf_segs


def make_trajectories_from_points(
    gdf_pts: gpd.GeoDataFrame,
    metric_crs: str = DEFAULT_METRIC_CRS,
) -> gpd.GeoDataFrame:
    """Construct continuous voyage LineStrings from sorted point fixes grouped by trip_id."""
    time_col = "base_date_time" if "base_date_time" in gdf_pts.columns else "timestamp"
    vessel_col = "mmsi" if "mmsi" in gdf_pts.columns else "MMSI"
    sorted_df = gdf_pts.sort_values(by=["trip_id", time_col])

    geoms, records = [], []
    for trip_id, grp in sorted_df.groupby("trip_id"):
        coords = np.column_stack([grp.geometry.x.values, grp.geometry.y.values])
        if len(coords) < 2:
            geom = Point(coords[0])
        else:
            geom = LineString(coords)

        first_row = grp.iloc[0]
        records.append(
            {
                "trip_id": trip_id,
                "MMSI": str(first_row[vessel_col]),
                "VesselGroup": classify_vessel_group(first_row.get("shiptypeAIS", 0)),
                "Length": first_row.get("length", np.nan),
                "Width": first_row.get("beam", np.nan),
                "TrackStartTime": grp[time_col].min(),
                "TrackEndTime": grp[time_col].max(),
                "PointCount": len(grp),
            }
        )
        geoms.append(geom)

    return gpd.GeoDataFrame(pd.DataFrame(records), geometry=geoms, crs=metric_crs)


def detect_chamber_occupancy(
    pts_metric: gpd.GeoDataFrame,
    locks_metric: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Identify vessels currently inside lock chambers with latest status and speed."""
    vessel_col = "mmsi" if "mmsi" in pts_metric.columns else "MMSI"
    time_col = "base_date_time" if "base_date_time" in pts_metric.columns else "timestamp"

    cols = ["name", "objectid", "isrsid", "length", "width", "geometry"]
    joined = gpd.sjoin(pts_metric, locks_metric[cols], predicate="within")
    if joined.empty:
        return gpd.GeoDataFrame(
            columns=["MMSI", "chamber_name", "chamber_id", "isrsid", "sog", "cog", "observation_time", "status", "geometry"],
            geometry=[],
            crs=pts_metric.crs,
        )

    records = []
    geoms = []
    for (mmsi, chamber_name), grp in joined.groupby([vessel_col, "name"]):
        latest = grp.sort_values(by=time_col).iloc[-1]
        mean_sog = float(grp["sog"].mean()) if "sog" in grp.columns else 0.0
        dwell_seconds = (grp[time_col].max() - grp[time_col].min()).total_seconds()
        status_str = "moored_in_chamber" if mean_sog < 0.5 else "transiting_chamber"

        records.append(
            {
                "MMSI": str(mmsi),
                "chamber_name": chamber_name,
                "chamber_id": latest.get("objectid"),
                "isrsid": latest.get("isrsid"),
                "chamber_length": latest.get("length_right"),
                "chamber_width": latest.get("width_right"),
                "sog": float(latest.get("sog", 0.0)),
                "cog": float(latest.get("cog", 0.0)),
                "status": status_str,
                "pings_inside": len(grp),
                "first_seen": grp[time_col].min(),
                "last_seen": grp[time_col].max(),
                "dwell_duration_m": round(dwell_seconds / 60.0, 2),
            }
        )
        geoms.append(latest.geometry)

    return gpd.GeoDataFrame(pd.DataFrame(records), geometry=geoms, crs=pts_metric.crs)


@click.command()
@click.option(
    "--input-file",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Input EURIS AIS points dataset (.geoparquet).",
)
@click.option(
    "--volkerak-file",
    type=click.Path(exists=True, path_type=Path),
    default=DEFAULT_VOLKERAK_GEOJSON,
    help="Path to Volkerak study area GeoJSON polygon.",
)
@click.option(
    "--lock-chambers-file",
    type=click.Path(path_type=Path),
    default=DEFAULT_LOCK_CHAMBERS_FILE,
    help="Path to lock chamber polygons GeoParquet (cached or live queried).",
)
@click.option(
    "--output-parquet",
    type=click.Path(path_type=Path),
    default=DEFAULT_OUTPUT_PARQUET,
    help="Path to output lock events GeoParquet (.geoparquet).",
)
@click.option(
    "--merge-gap-minutes",
    type=float,
    default=5.0,
    help="Merge gap in minutes for brief entry/exit jitter inside lock chambers.",
)
def cli(
    input_file: Path,
    volkerak_file: Path,
    lock_chambers_file: Path,
    output_parquet: Path,
    merge_gap_minutes: float,
):
    """Detect lock chamber events and chamber occupancy at Volkeraksluizen to GeoParquet."""
    logger.info("=" * 80)
    logger.info("EURIS Volkerak Lock Events Detection Pipeline")
    logger.info("=" * 80)

    # 1. Load Volkerak study area polygon
    logger.info(f"Loading Volkerak study area polygon from {volkerak_file}...")
    gdf_study = gpd.read_file(volkerak_file)
    minx, miny, maxx, maxy = gdf_study.total_bounds
    bbox_4326 = (float(minx), float(miny), float(maxx), float(maxy))
    logger.info(
        f"Volkerak Bounding Box (EPSG:4326): Lon [{bbox_4326[0]:.4f}, {bbox_4326[2]:.4f}], Lat [{bbox_4326[1]:.4f}, {bbox_4326[3]:.4f}]"
    )

    # 2. Load Lock Chambers
    lock_chambers_metric = load_or_fetch_lock_chambers(lock_chambers_file, bbox_4326, metric_crs=DEFAULT_METRIC_CRS)
    logger.info(f"Loaded {len(lock_chambers_metric)} lock chambers at Volkeraksluizen (CRS: {lock_chambers_metric.crs}).")

    # 3. Load AIS Points
    logger.info(f"Loading AIS point fixes from {input_file}...")
    if input_file.suffix in {".geoparquet", ".parquet"}:
        gdf_raw = gpd.read_parquet(input_file)
    else:
        gdf_raw = gpd.read_file(input_file)

    if gdf_raw.crs is None:
        gdf_raw = gdf_raw.set_crs("EPSG:4326")

    # 4. Build Segments and Trajectories
    pts_metric, segs_metric = make_segments_from_points(gdf_raw, metric_crs=DEFAULT_METRIC_CRS)
    trajectories_metric = make_trajectories_from_points(pts_metric, metric_crs=DEFAULT_METRIC_CRS)

    # 5. Detect Lock Chamber Transitions (Entry / Exit)
    logger.info("Detecting lock chamber entry and exit events with polygon transition algorithm...")
    lock_events_4326 = detect_polygon_entry_exit(
        segments_gdf=segs_metric,
        polygons_gdf=lock_chambers_metric,
        polygon_id_col="name",
        merge_gap_minutes=merge_gap_minutes,
    )
    lock_events_metric = lock_events_4326.to_crs(DEFAULT_METRIC_CRS) if not lock_events_4326.empty else lock_events_4326

    # 6. Detect Current Chamber Occupancy (dwell / active vessels)
    logger.info("Detecting lock chamber occupancy and vessel dwell fixes...")
    occupancy_metric = detect_chamber_occupancy(pts_metric, lock_chambers_metric)

    # 7. Extract Stationary Vessels
    logger.info("Extracting stationary vessels and moored dwell fixes...")
    stationary_metric = extract_stationary_vessels(segs_metric, min_moving_speed=0.5)

    # 8. Summary Report
    print("\n" + "=" * 80)
    print("VOLKERAKSLUIZEN LOCK CHAMBERS SUMMARY")
    print("=" * 80)
    chambers_table = []
    for _, ch in lock_chambers_metric.iterrows():
        chambers_table.append(
            [
                ch.get("objectid"),
                ch.get("name"),
                ch.get("isrsid"),
                f"{ch.get('length', 0):.1f} m",
                f"{ch.get('width', 0):.1f} m",
            ]
        )
    print(tabulate(chambers_table, headers=["ID", "Chamber Name", "ISRS ID", "Length", "Width"], tablefmt="github"))

    print("\n" + "=" * 80)
    print("CHAMBER OCCUPANCY (VESSELS CURRENTLY INSIDE CHAMBERS)")
    print("=" * 80)
    if not occupancy_metric.empty:
        occ_table = []
        for _, occ in occupancy_metric.iterrows():
            occ_table.append(
                [
                    occ.get("MMSI"),
                    occ.get("chamber_name"),
                    occ.get("status"),
                    f"{occ.get('sog', 0.0):.1f} kts",
                    occ.get("pings_inside"),
                    str(occ.get("last_seen")),
                ]
            )
        print(tabulate(occ_table, headers=["MMSI/Track", "Chamber", "Status", "SOG", "Pings", "Last Seen"], tablefmt="github"))
    else:
        print("No vessels currently detected inside lock chambers.")

    print("\n" + "=" * 80)
    print("LOCK CHAMBER TRANSITION EVENTS")
    print("=" * 80)
    if not lock_events_metric.empty:
        evt_table = []
        for _, ev in lock_events_metric.iterrows():
            entry_t = ev.get("entry_time")
            exit_t = ev.get("exit_time")
            dwell_m = (
                round((exit_t - entry_t).total_seconds() / 60.0, 1)
                if pd.notna(entry_t) and pd.notna(exit_t)
                else "In Chamber"
            )
            evt_table.append(
                [
                    ev.get("MMSI"),
                    ev.get("name"),
                    ev.get("VesselGroup"),
                    str(entry_t),
                    str(exit_t),
                    dwell_m,
                ]
            )
        print(tabulate(evt_table, headers=["MMSI", "Chamber", "Group", "Entry Time", "Exit Time", "Dwell (min)"], tablefmt="github"))
    else:
        print("No chamber boundary crossing transitions observed in current time window.")

    # 9. Save GeoParquet outputs with explicit EPSG:28992 (RD New) CRS
    output_parquet.parent.mkdir(parents=True, exist_ok=True)
    out_stem = output_parquet.stem.replace(".geoparquet", "")
    out_dir = output_parquet.parent

    # Save primary events or occupancy to output_parquet
    primary_output = lock_events_metric if not lock_events_metric.empty else occupancy_metric
    logger.info(f"Saving primary lock events GeoParquet ({primary_output.crs}) to {output_parquet}...")
    primary_output.to_parquet(output_parquet)

    # Save companion layers to standardized GeoParquet files
    occupancy_file = out_dir / f"{out_stem}_chamber_occupancy.geoparquet"
    occupancy_metric.to_parquet(occupancy_file)
    logger.info(f"Saved chamber occupancy GeoParquet ({occupancy_metric.crs}) to {occupancy_file} ({occupancy_file.stat().st_size / 1024:.1f} KB)")

    trajectories_file = out_dir / f"{out_stem}_trajectories.geoparquet"
    trajectories_metric.to_parquet(trajectories_file)
    logger.info(f"Saved trajectories GeoParquet ({trajectories_metric.crs}) to {trajectories_file} ({trajectories_file.stat().st_size / 1024:.1f} KB)")

    stationary_file = out_dir / f"{out_stem}_stationary.geoparquet"
    stationary_metric.to_parquet(stationary_file)
    logger.info(f"Saved stationary vessels GeoParquet ({stationary_metric.crs}) to {stationary_file} ({stationary_file.stat().st_size / 1024:.1f} KB)")

    logger.info("=" * 80)
    logger.info("EURIS Volkerak Lock Events Detection Completed Successfully!")
    logger.info("=" * 80)


if __name__ == "__main__":
    cli()
