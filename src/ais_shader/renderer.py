import gc
import logging
import sys
import threading
import time
import tomllib
from datetime import datetime
from pathlib import Path

import dask
import dask.dataframe as dd
import datashader as ds
import morecantile
import psutil
import rioxarray
import xarray as xr
from dask.distributed import Client, get_client, wait
from rasterio.transform import from_bounds

# Import from src modules
from .data_loader import load_and_process_data

logger = logging.getLogger(__name__)

def render_tile_task(gdf_local, tile, zarr_dir, config):
    """
    Render a single tile from a computed GeoDataFrame.
    This runs on a worker.
    """
    if len(gdf_local) == 0:
        return

    tms = morecantile.tms.get("WebMercatorQuad")
    bbox = tms.xy_bounds(tile)
    
    # Define canvas
    tile_size = config["visualization"]["tile_size"]
    cvs = ds.Canvas(
        plot_width=tile_size, 
        plot_height=tile_size,
        x_range=(bbox.left, bbox.right),
        y_range=(bbox.bottom, bbox.top)
    )

    # Aggregate
    line_width = config["visualization"]["line_width"]
    category_column = config["visualization"].get("category_column")
    
    if category_column:
        gdf_local[category_column] = gdf_local[category_column].astype("category")
        agg = cvs.line(gdf_local, geometry='geometry', agg=ds.by(category_column, ds.count()))
    elif line_width == 0:
        agg = cvs.line(gdf_local, geometry='geometry', agg=ds.count())
    else:
        agg = cvs.line(gdf_local, geometry='geometry', line_width=line_width)

    # --- Save Zarr (Counts) ---
    # Create transform
    transform = from_bounds(bbox.left, bbox.bottom, bbox.right, bbox.top, tile_size, tile_size)
    
    # Prepare DataArray for saving
    if isinstance(agg, xr.Dataset):
        da = agg.to_array(dim="band")
        da = da.fillna(0).astype("int32")
    else:
        da = agg.fillna(0).astype("int32")
        da = da.expand_dims(dim={'band': 1})

    # Set CRS and Transform
    da.rio.write_crs("EPSG:3857", inplace=True)
    da.rio.write_transform(transform, inplace=True)
    
    # Save as Zarr
    zarr_path = zarr_dir / f"tile_{tile.z}_{tile.x}_{tile.y}.zarr"
    
    da.name = "counts"
    
    # Encoding: disable compression for spatial_ref
    encoding = {"spatial_ref": {"compressor": None}}
    
    da.to_zarr(zarr_path, mode="w", consolidated=True, encoding=encoding)
    
    # Logging stats (print to stdout for Dask capture)
    if isinstance(agg, xr.Dataset):
        total_sum = float(da.sum())
        max_val = float(da.max())
        logger.info(f"Tile {tile} stats: sum={total_sum}, max={max_val}, categories={len(agg.data_vars)}")
    else:
        agg_sum = float(agg.sum())
        agg_max = float(agg.max())
        logger.info(f"Tile {tile} stats: sum={agg_sum}, max={agg_max}")

    logger.info(f"Saved Zarr for tile {tile}")



def monitor_resources(interval=5, stop_event=None):
    """
    Monitor system resources in a background thread.
    """
    while not stop_event.is_set():
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        
        logger.info(f"Resource Monitor - CPU: {cpu_percent}%, Memory: {memory.percent}% (Used: {memory.used / (1024**3):.2f} GB)")
        
        if memory.percent > 90:
            logger.warning("High memory usage detected!")
            
        time.sleep(interval)

def render_tiles(coords_ddf, output_dir: Path, config: dict):
    """
    Render tiles for the US using Datashader and save as Zarr.
    Submits all tasks to Dask at once.
    """
    try:
        client = get_client()
    except ValueError:
        logger.error("No Dask client found. Please start a client before calling render_tiles.")
        return

    # Define TileMatrixSet (WebMercatorQuad)
    tms = morecantile.tms.get("WebMercatorQuad")
    
    # Validate category column presence at startup
    category_column = config["visualization"].get("category_column")
    if category_column and category_column not in coords_ddf.columns:
        raise ValueError(f"Category column '{category_column}' not found in dataset schema. Available columns: {list(coords_ddf.columns)}")
    
    # Define US Bounding Box
    us_bbox = tuple(config["visualization"]["bbox"])
    zoom = config["visualization"]["zoom"]
    
    logger.info(f"Generating tiles for BBox {us_bbox} at Zoom {zoom}...")
    
    tiles = list(tms.tiles(*us_bbox, zooms=[zoom]))
    
    # Create subdirectories
    zarr_dir = output_dir / "zarr"
    zarr_dir.mkdir(parents=True, exist_ok=True)
    
    # Process tiles in batches to manage memory
    total_tiles = len(tiles)
    batch_size = config["visualization"].get("batch_size", 20)
    logger.info(f"Found {total_tiles} tiles to render. Processing in batches of {batch_size}...")
    
    for i in range(0, total_tiles, batch_size):
        batch_tiles = tiles[i:i + batch_size]
        logger.info(f"Submitting batch {i // batch_size + 1}/{(total_tiles + batch_size - 1) // batch_size}...")
        
        futures = []
        for tile in batch_tiles:
            # Check if output already exists
            zarr_path = zarr_dir / f"tile_{tile.z}_{tile.x}_{tile.y}.zarr"
            if zarr_path.exists():
                # logger.info(f"Tile {tile} already exists. Skipping.")
                continue

            bbox = tms.xy_bounds(tile)
            
            # Filter data for this tile using spatial index (Lazy)
            subset = coords_ddf.cx[bbox.left:bbox.right, bbox.bottom:bbox.top]
            
            # Submit the compute task (returns a Future to the pandas DataFrame)
            future_gdf = client.compute(subset)
            
            # Submit the rendering task, dependent on future_gdf
            future_render = client.submit(render_tile_task, future_gdf, tile, zarr_dir, config)
            futures.append(future_render)
        
        if futures:
            logger.info(f"Waiting for {len(futures)} tasks in current batch...")
            wait(futures)
            
            # Check for errors
            for f in futures:
                if f.status == 'error':
                    try:
                        f.result() # This will re-raise the exception from the worker
                    except Exception as e:
                        logger.error(f"Task failed for tile: {f.args[1]} with error: {e}")
            
            # Explicitly release futures to free memory
            del futures
            gc.collect()
            time.sleep(1) # Let the scheduler settle
            
    logger.info("All tasks completed.")

def run_rendering(config_file: Path, output_dir: Path, scheduler: str, input_file: Path, resume_dir: Path = None, bbox: tuple = None, zoom: int = None):
    """
    Main entry point for rendering.
    """
    # Load Config
    with open(config_file, "rb") as f:
        config = tomllib.load(f)
        
    logger.info(f"Loaded configuration from {config_file}")

    # Override config with CLI arguments
    if bbox:
        config["visualization"]["bbox"] = list(bbox)
        logger.info(f"Overriding bbox from CLI: {bbox}")
    if zoom:
        config["visualization"]["zoom"] = zoom
        logger.info(f"Overriding zoom from CLI: {zoom}")
    
    # Get input file from CLI or config
    if input_file:
        logger.info(f"Using input file from CLI: {input_file}")
    else:
        input_file = Path(config["data"]["input_file"])
        logger.info(f"Using input file from config: {input_file}")

    # Initialize Dask Client
    dask_config = {
        "distributed.scheduler.allowed-failures": 0,
    }
    
    # Apply resource limits from config
    if "resources" in config:
        res = config["resources"]
        if "memory_target" in res: dask_config["distributed.worker.memory.target"] = res["memory_target"]
        if "memory_spill" in res: dask_config["distributed.worker.memory.spill"] = res["memory_spill"]
        if "memory_pause" in res: dask_config["distributed.worker.memory.pause"] = res["memory_pause"]
        dask_config["distributed.worker.memory.terminate"] = False # Avoid killing workers on laptop

    dask.config.set(dask_config)
    
    if scheduler:
        logger.info(f"Connecting to Dask scheduler at {scheduler}...")
        client = Client(scheduler)
    else:
        logger.info("Starting Local Dask Client...")
        client = Client()
        
    logger.info(f"Dask Dashboard link: {client.dashboard_link}")

    # Create or use existing run directory
    if resume_dir:
        run_dir = resume_dir
        if not run_dir.exists():
            raise ValueError(f"Resume directory does not exist: {run_dir}")
        logger.info(f"Resuming run in: {run_dir}")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = output_dir / f"run_{timestamp}"
        run_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Output will be saved to: {run_dir}")

    # Save metadata
    import json
    metadata = {
        "timestamp": timestamp,
        "input_file": str(input_file),
        "config": config,
        "scheduler": scheduler,
        "command": " ".join(sys.argv)
    }
    with open(run_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)
    logger.info("Saved metadata.json")

    # Start Resource Monitor
    stop_monitor = threading.Event()
    monitor_thread = threading.Thread(target=monitor_resources, args=(5, stop_monitor))
    monitor_thread.start()

    try:
        # Load Data
        coords_ddf = load_and_process_data(input_file) 
        
        # Render Tiles
        render_tiles(coords_ddf, run_dir, config)
        
        logger.info("Done!")
        
    except Exception as e:
        logger.error(f"An error occurred: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        stop_monitor.set()
        monitor_thread.join()
        client.close()
