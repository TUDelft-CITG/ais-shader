#!/usr/bin/env python3
"""
crawl_euris.py

Crawls live AIS vessel positions from the EURIS portal WebSocket API
(wss://www.eurisportal.eu/api/AISTracks/Connect) using JSON-RPC 2.0 GetFeatures.

Based on RWS-NL/fis-crawler:
https://github.com/RWS-NL/fis-crawler/blob/main/notebooks/ais_analysis.ipynb

Usage:
  python scripts/crawl_euris.py --duration-minutes 10 --interval-seconds 10
"""

import argparse
import asyncio
import datetime
import json
import logging
import os
import sys
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import Point
import websockets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("crawl_euris")

AIS_WSS_URL = "wss://www.eurisportal.eu/api/AISTracks/Connect"

# Default coordinates provided by user (Lek / Nederrijn / Amsterdam-Rijnkanaal area)
DEFAULT_BBOX = {
    "minLat": 51.93279803741953,
    "minLon": 4.94651227667174,
    "maxLat": 52.17818437072481,
    "maxLon": 5.415196530034308,
}

STATUS_LABELS = {
    0: "Under way (engine)",
    1: "At anchor",
    2: "Not under command",
    3: "Restricted maneuverability",
    5: "Moored",
    15: "Undefined",
}


async def crawl_euris(
    bbox: dict,
    duration_seconds: float = 600.0,
    interval_seconds: float = 10.0,
    output_dir: Path = Path("/scratch-shared/fbaart/data/euris_crawl"),
):
    output_dir.mkdir(parents=True, exist_ok=True)
    start_dt = datetime.datetime.now(datetime.timezone.utc)
    timestamp_tag = start_dt.strftime("%Y%m%d_%H%M%S")

    ndjson_path = output_dir / f"euris_crawl_{timestamp_tag}.ndjson"
    parquet_path = output_dir / f"euris_crawl_{timestamp_tag}.geoparquet"
    geojson_path = output_dir / f"euris_crawl_{timestamp_tag}.geojson"
    gpkg_path = output_dir / f"euris_crawl_{timestamp_tag}.gpkg"

    logger.info("=" * 75)
    logger.info("Starting EURIS AIS Live Crawl")
    logger.info(f"Target duration: {duration_seconds / 60:.1f} minutes ({duration_seconds:.0f}s)")
    logger.info(f"Sampling interval: {interval_seconds:.1f}s")
    logger.info(f"Bounding box: Lat [{bbox['minLat']:.4f}, {bbox['maxLat']:.4f}], Lon [{bbox['minLon']:.4f}, {bbox['maxLon']:.4f}]")
    logger.info(f"Streaming raw NDJSON to: {ndjson_path}")
    logger.info("=" * 75)

    request_payload = {
        "jsonrpc": "2.0",
        "method": "GetFeatures",
        "action": "subscribe",
        "topic": "ais/target",
        "bbox": bbox,
    }

    records = []
    unique_tracks = set()
    start_time = time.monotonic()
    iteration = 0

    logger.info("=" * 75)
    logger.info(f"Starting EURIS AIS Live Crawl for {duration_seconds:.0f}s (Interval: {interval_seconds:.1f}s)")
    logger.info(f"BBox: {bbox}")
    logger.info(f"Streaming NDJSON: {ndjson_path}")
    logger.info("=" * 75)

    with open(ndjson_path, "a", encoding="utf-8") as ndjson_file:
        while True:
            elapsed = time.monotonic() - start_time
            if elapsed >= duration_seconds:
                logger.info(f"Reached target duration ({elapsed:.1f}s >= {duration_seconds:.0f}s). Finishing crawl.")
                break

            poll_start = time.monotonic()
            iteration += 1
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            now_iso = now_utc.strftime("%Y-%m-%d %H:%M:%S")

            try:
                async with websockets.connect(
                    AIS_WSS_URL, open_timeout=10, max_size=10 * 1024 * 1024
                ) as ws:
                    await ws.send(json.dumps(request_payload))
                    raw_resp = await asyncio.wait_for(ws.recv(), timeout=15)
                    data = json.loads(raw_resp)
                    features = data.get("result", {}).get("features", [])

                    batch_count = 0
                    for feat in features:
                        rec = parse_euris_feature(feat, now_iso)
                        if rec is None:
                            continue
                        unique_tracks.add(rec["mmsi"])
                        records.append(rec)
                        ndjson_file.write(json.dumps(rec) + "\n")
                        batch_count += 1

                    ndjson_file.flush()

                    rem_time = max(0.0, duration_seconds - (time.monotonic() - start_time))
                    logger.info(
                        f"Snapshot #{iteration:03d} [{now_iso} UTC]: "
                        f"{batch_count:3d} vessels | Total fixes: {len(records):6,d} | "
                        f"Unique vessels: {len(unique_tracks):3d} | "
                        f"Elapsed: {elapsed:5.1f}s / {duration_seconds:.0f}s (Remaining: {rem_time:5.1f}s)"
                    )

            except Exception as e:
                logger.warning(f"Error during snapshot #{iteration}: {e}. Retrying in next interval...")

            poll_duration = time.monotonic() - poll_start
            sleep_time = max(0.0, interval_seconds - poll_duration)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

    if not records:
        logger.warning("No records were collected during the crawl.")
        return

    # Convert collected records to GeoDataFrame and save GeoParquet & GeoJSON
    logger.info("Converting collected records to GeoDataFrame...")
    df = pd.DataFrame(records)
    df["base_date_time"] = pd.to_datetime(df["base_date_time"])
    geoms = shapely.points(df["longitude"].values, df["latitude"].values)
    gdf = gpd.GeoDataFrame(df, geometry=geoms, crs="EPSG:4326")

    logger.info(f"Saving GeoParquet dataset to {parquet_path}...")
    gdf.to_parquet(parquet_path)

    logger.info(f"Saving GeoPackage dataset to {gpkg_path}...")
    gdf.to_file(gpkg_path, layer="raw_points", driver="GPKG")

    logger.info(f"Saving GeoJSON snapshot to {geojson_path}...")
    gdf.to_file(geojson_path, driver="GeoJSON")

    logger.info("=" * 75)
    logger.info("EURIS AIS Crawl Completed Successfully!")
    logger.info(f"Total fixes recorded: {len(gdf):,}")
    logger.info(f"Total unique vessels: {gdf['mmsi'].nunique():,}")
    logger.info(f"Output GeoPackage: {gpkg_path} ({gpkg_path.stat().st_size / 1024:.1f} KB)")
    logger.info(f"Output GeoParquet: {parquet_path} ({parquet_path.stat().st_size / 1024:.1f} KB)")
    logger.info(f"Output NDJSON: {ndjson_path} ({ndjson_path.stat().st_size / 1024:.1f} KB)")
    logger.info(f"Output GeoJSON: {geojson_path} ({geojson_path.stat().st_size / 1024:.1f} KB)")
    logger.info("=" * 75)


def main():
    parser = argparse.ArgumentParser(description="Crawl live AIS tracks from EURIS WebSocket API")
    parser.add_argument("--duration-minutes", type=float, default=10.0, help="Duration to crawl in minutes (default: 10.0)")
    parser.add_argument("--interval-seconds", type=float, default=10.0, help="Interval between requests in seconds (default: 10.0)")
    parser.add_argument(
        "--bbox",
        type=str,
        default=None,
        help="Optional bbox 'minLon,minLat,maxLon,maxLat' (defaults to user Utrecht/Lek polygon)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/scratch-shared/fbaart/data/euris_crawl"),
        help="Directory to save output files",
    )
    args = parser.parse_args()

    bbox = DEFAULT_BBOX.copy()
    if args.bbox:
        parts = [float(x.strip()) for x in args.bbox.split(",")]
        if len(parts) == 4:
            bbox = {
                "minLon": parts[0],
                "minLat": parts[1],
                "maxLon": parts[2],
                "maxLat": parts[3],
            }

    duration_seconds = args.duration_minutes * 60.0
    asyncio.run(crawl_euris(bbox, duration_seconds, args.interval_seconds, args.output_dir))


if __name__ == "__main__":
    main()
