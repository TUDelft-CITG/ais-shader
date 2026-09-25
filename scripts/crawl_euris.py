#!/usr/bin/env python3
"""
crawl_euris.py

Crawls live AIS vessel positions from the EURIS portal WebSocket API
(wss://www.eurisportal.eu/api/AISTracks/Connect) using a persistent WebSocket connection
and JSON-RPC 2.0 GetFeatures requests.
Takes a GeoJSON polygon defining the region of interest, filters fixes to that polygon,
saves data as standardized GeoParquet (EPSG:4326), and removes the temporary streaming buffer.

Usage:
  uv run python scripts/crawl_euris.py --geojson examples/data/volkerak.geojson --duration-minutes 2
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import time
from pathlib import Path
from typing import Optional

import click
import geopandas as gpd
import pandas as pd
import shapely
import websockets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("crawl_euris")

AIS_WSS_URL = "wss://www.eurisportal.eu/api/AISTracks/Connect"

STATUS_LABELS = {
    0: "Under way (engine)",
    1: "At anchor",
    2: "Not under command",
    3: "Restricted maneuverability",
    5: "Moored",
    15: "Undefined",
}


def parse_euris_feature(feat: dict, now_iso: str) -> Optional[dict]:
    """
    Parse a single GeoJSON Feature returned by the EURIS AIS WebSocket into
    a normalized flat record matching ais-shader requirements.
    """
    props = feat.get("properties", {})
    geom = feat.get("geometry", {})
    coords = geom.get("coordinates", [None, None])

    lon = props.get("Lon", coords[0])
    lat = props.get("Lat", coords[1])
    if lon is None or lat is None:
        return None

    mmsi = str(props.get("MMSI") or "")
    if not mmsi or mmsi == "0":
        mmsi = str(props.get("TrackID") or feat.get("id") or "")
    if not mmsi:
        return None

    dim_a = float(props.get("DimA") or 0.0)
    dim_b = float(props.get("DimB") or 0.0)
    dim_c = float(props.get("DimC") or 0.0)
    dim_d = float(props.get("DimD") or 0.0)
    length = dim_a + dim_b
    beam = dim_c + dim_d

    sog = float(props.get("SOG") or 0.0)
    cog = float(props.get("COG") or 0.0)
    th = float(props.get("TH") or 0.0)
    heading = th if th > 0 else cog
    st = int(props.get("ST") or 15)

    return {
        "mmsi": mmsi,
        "base_date_time": now_iso,
        "latitude": float(lat),
        "longitude": float(lon),
        "sog": sog,
        "cog": cog,
        "heading": heading,
        "length": length if length > 0 else None,
        "beam": beam if beam > 0 else None,
        "shiptypeAIS": int(props.get("VT") or props.get("VG") or 0),
        "status": st,
        "status_label": STATUS_LABELS.get(st, "Undefined"),
        "vessel_name": str(props.get("Name") or f"Track {mmsi}"),
    }


async def crawl_euris(
    geojson_path: Path,
    duration_seconds: float = 600.0,
    interval_seconds: float = 10.0,
    output_dir: Path = Path("data/euris_crawl"),
) -> Path:
    # Fail fast on missing GeoJSON input region
    gdf_poly = gpd.read_file(geojson_path)
    if gdf_poly.crs is None:
        gdf_poly = gdf_poly.set_crs("EPSG:4326")
    elif gdf_poly.crs.to_epsg() != 4326:
        gdf_poly = gdf_poly.to_crs("EPSG:4326")

    union_polygon = shapely.unary_union(gdf_poly.geometry.values)
    minx, miny, maxx, maxy = gdf_poly.total_bounds
    bbox = {
        "minLon": float(minx),
        "minLat": float(miny),
        "maxLon": float(maxx),
        "maxLat": float(maxy),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    start_dt = datetime.datetime.now(datetime.timezone.utc)
    timestamp_tag = start_dt.strftime("%Y%m%d_%H%M%S")

    ndjson_path = output_dir / f"euris_crawl_{timestamp_tag}.ndjson"
    parquet_path = output_dir / f"euris_crawl_{timestamp_tag}.geoparquet"

    logger.info("=" * 75)
    logger.info("Starting EURIS AIS Live Crawl")
    logger.info(f"Input Region (GeoJSON): {geojson_path}")
    logger.info(f"Target duration: {duration_seconds / 60:.1f} minutes ({duration_seconds:.0f}s)")
    logger.info(f"Sampling interval: {interval_seconds:.1f}s")
    logger.info(
        f"Bounding box: Lat [{bbox['minLat']:.4f}, {bbox['maxLat']:.4f}], Lon [{bbox['minLon']:.4f}, {bbox['maxLon']:.4f}]"
    )
    logger.info(f"Streaming live buffer: {ndjson_path}")
    logger.info("=" * 75)

    records = []
    unique_tracks = set()
    start_time = time.monotonic()
    iteration = 0

    with open(ndjson_path, "a", encoding="utf-8") as ndjson_file:
        while (time.monotonic() - start_time) < duration_seconds:
            try:
                # Maintain a persistent WebSocket connection across polling intervals
                async with websockets.connect(
                    AIS_WSS_URL, open_timeout=15, max_size=10 * 1024 * 1024
                ) as ws:
                    logger.info("Connected to EURIS AIS WebSocket.")
                    while (time.monotonic() - start_time) < duration_seconds:
                        poll_start = time.monotonic()
                        iteration += 1
                        now_utc = datetime.datetime.now(datetime.timezone.utc)
                        now_iso = now_utc.strftime("%Y-%m-%d %H:%M:%S")

                        request_payload = {
                            "jsonrpc": "2.0",
                            "method": "GetFeatures",
                            "params": {
                                "filters": {
                                    "boundingBox": bbox,
                                    "filterQuery": "()",
                                }
                            },
                            "id": iteration,
                        }

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

                        elapsed = time.monotonic() - start_time
                        rem_time = max(0.0, duration_seconds - elapsed)
                        logger.info(
                            f"Snapshot #{iteration:03d} [{now_iso} UTC]: "
                            f"{batch_count:3d} vessels | Total fixes: {len(records):6,d} | "
                            f"Unique vessels: {len(unique_tracks):3d} | "
                            f"Elapsed: {elapsed:5.1f}s / {duration_seconds:.0f}s (Remaining: {rem_time:5.1f}s)"
                        )

                        poll_duration = time.monotonic() - poll_start
                        sleep_time = max(0.0, interval_seconds - poll_duration)
                        if sleep_time > 0 and (time.monotonic() - start_time) < duration_seconds:
                            await asyncio.sleep(sleep_time)

            except Exception as e:
                elapsed = time.monotonic() - start_time
                if elapsed >= duration_seconds:
                    break
                logger.warning(f"WebSocket connection error ({e}). Reconnecting in {interval_seconds:.1f}s...")
                await asyncio.sleep(interval_seconds)

    if not records:
        logger.warning("No records were collected during the crawl.")
        ndjson_path.unlink(missing_ok=True)
        return parquet_path

    logger.info("Converting collected records to GeoDataFrame (EPSG:4326)...")
    df = pd.DataFrame(records)
    df["base_date_time"] = pd.to_datetime(df["base_date_time"])
    geoms = shapely.points(df["longitude"].values, df["latitude"].values)
    gdf = gpd.GeoDataFrame(df, geometry=geoms, crs="EPSG:4326")

    # Spatially filter to exact GeoJSON polygon boundary
    initial_count = len(gdf)
    inside_mask = gdf.geometry.within(union_polygon)
    gdf = gdf[inside_mask].copy().reset_index(drop=True)
    logger.info(f"Spatial filtering to GeoJSON polygon: {len(gdf):,} fixes retained ({initial_count - len(gdf):,} outside boundary dropped).")

    logger.info(f"Saving canonical GeoParquet dataset (EPSG:4326) to {parquet_path}...")
    gdf.to_parquet(parquet_path)

    # Clean up temporary streaming ndjson buffer
    ndjson_path.unlink()
    logger.info(f"Removed temporary stream buffer {ndjson_path.name}.")

    logger.info("=" * 75)
    logger.info("EURIS AIS Crawl Completed Successfully!")
    logger.info(f"Total fixes recorded in polygon: {len(gdf):,}")
    logger.info(f"Total unique vessels: {gdf['mmsi'].nunique():,}")
    logger.info(f"CRS: {gdf.crs.to_string()}")
    logger.info(f"Output GeoParquet: {parquet_path} ({parquet_path.stat().st_size / 1024:.1f} KB)")
    logger.info("=" * 75)
    return parquet_path


@click.command()
@click.option(
    "--geojson",
    type=click.Path(exists=True, path_type=Path),
    default=Path("examples/data/volkerak.geojson"),
    help="Path to GeoJSON file defining the input region (default: examples/data/volkerak.geojson)",
)
@click.option(
    "--duration-minutes",
    type=float,
    default=2.0,
    help="Duration to crawl in minutes (default: 2.0)",
)
@click.option(
    "--interval-seconds",
    type=float,
    default=10.0,
    help="Interval between requests in seconds (default: 10.0)",
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    default=Path("examples/data"),
    help="Directory to save output GeoParquet file (default: examples/data)",
)
def main(
    geojson: Path,
    duration_minutes: float,
    interval_seconds: float,
    output_dir: Path,
):
    """Crawl live AIS vessel positions from EURIS WebSocket API to GeoParquet for a GeoJSON region."""
    duration_seconds = duration_minutes * 60.0
    asyncio.run(crawl_euris(geojson, duration_seconds, interval_seconds, output_dir))


if __name__ == "__main__":
    main()
