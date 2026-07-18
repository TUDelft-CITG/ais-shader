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
    tile_size = config["visualization"]["tile_size"]
    transform = from_bounds(bbox.left, bbox.bottom, bbox.right, bbox.top, tile_size, tile_size)
    
    # Calculate coordinate size of a single pixel
    dx = (bbox.right - bbox.left) / tile_size
    dy = (bbox.top - bbox.bottom) / tile_size

    # Define expanded parameters for rendering with border
    border = 8
    expanded_size = tile_size + 2 * border
    expanded_left = bbox.left - border * dx
    expanded_right = bbox.right + border * dx
    expanded_bottom = bbox.bottom - border * dy
    expanded_top = bbox.top + border * dy

    # Define expanded canvas
    cvs = ds.Canvas(
        plot_width=expanded_size, 
        plot_height=expanded_size,
        x_range=(expanded_left, expanded_right),
        y_range=(expanded_bottom, expanded_top)
    )

    # --- Clip Geometries to Expanded Tile Bounds ---
    import shapely
    tile_box = shapely.box(
        bbox.left - (border + 2) * dx, 
        bbox.bottom - (border + 2) * dy, 
        bbox.right + (border + 2) * dx, 
        bbox.top + (border + 2) * dy
    )
    
    gdf_local = gdf_local.copy()
    clipped = gdf_local.geometry.intersection(tile_box)
    
    gdf_local['geometry'] = clipped
    gdf_local = gdf_local[~gdf_local.geometry.is_empty]
    
    # If this is a LineString/MultiLineString dataset, keep only line geometries
    geom_types = gdf_local.geom_type.unique()
    is_line_dataset = any('LineString' in gt for gt in geom_types)
    if is_line_dataset:
        gdf_local = gdf_local[gdf_local.geom_type.isin(['LineString', 'MultiLineString'])]

    if len(gdf_local) == 0:
        return

    # Aggregate
    line_width = config["visualization"]["line_width"]
    bands = config["visualization"].get("bands")
    
    if bands:
        da_list = []
        for b in bands:
            if b == "transit_count":
                if line_width == 0:
                    agg_band = cvs.line(gdf_local, geometry='geometry', agg=ds.count())
                else:
                    agg_band = cvs.line(gdf_local, geometry='geometry', agg=ds.count(), line_width=line_width)
            elif b == "sog":
                agg_band = cvs.line(gdf_local, geometry='geometry', agg=ds.mean('sog'))
            elif b == "speed_mps":
                agg_band = cvs.line(gdf_local, geometry='geometry', agg=ds.mean('speed_mps'))
            else:
                raise ValueError(f"Unsupported band: {b}")
            
            da_band = agg_band.fillna(0).astype("float32").expand_dims(dim={'band': [b]})
            da_list.append(da_band)
        da = xr.concat(da_list, dim='band')
        da.name = "metrics"
    else:
        category_column = config["visualization"].get("category_column")
        value_column = config["visualization"].get("value_column")
        aggregation_type = config["visualization"].get("aggregation", "count")
        
        if category_column:
            gdf_local[category_column] = gdf_local[category_column].astype("category")
            agg = cvs.line(gdf_local, geometry='geometry', agg=ds.by(category_column, ds.count()))
        elif value_column:
            if aggregation_type == "mean":
                agg = cvs.line(gdf_local, geometry='geometry', agg=ds.mean(value_column))
            elif aggregation_type == "max":
                agg = cvs.line(gdf_local, geometry='geometry', agg=ds.max(value_column))
            else:
                raise ValueError(f"Unsupported aggregation: {aggregation_type}")
        elif line_width == 0:
            agg = cvs.line(gdf_local, geometry='geometry', agg=ds.count())
        else:
            agg = cvs.line(gdf_local, geometry='geometry', agg=ds.count(), line_width=line_width)

        # --- Save Zarr (Counts or Speeds) ---
        
        # Prepare DataArray for saving
        if isinstance(agg, xr.Dataset):
            da = agg.to_array(dim="band")
            da = da.fillna(0)
            if not value_column:
                da = da.astype("int32")
            else:
                da = da.astype("float32")
        else:
            da = agg.fillna(0)
            if not value_column:
                da = da.astype("int32")
            else:
                da = da.astype("float32")
            da = da.expand_dims(dim={'band': 1})
        da.name = value_column or "counts"

    # Crop to the original tile size (discarding the border)
    da = da.isel(
        x=slice(border, border + tile_size),
        y=slice(border, border + tile_size)
    )

    # Set CRS and Transform
    da.rio.write_crs("EPSG:3857", inplace=True)
    da.rio.write_transform(transform, inplace=True)
    
    # Save as Zarr
    zarr_path = zarr_dir / f"tile_{tile.z}_{tile.x}_{tile.y}.zarr"
    
    # Encoding: disable compression for spatial_ref
    encoding = {"spatial_ref": {"compressor": None}}
    
    da.to_zarr(zarr_path, mode="w", consolidated=True, encoding=encoding)
    
    # Logging stats (print to stdout for Dask capture)
    total_sum = float(da.sum())
    max_val = float(da.max())
    logger.info(f"Tile {tile} stats: sum={total_sum}, max={max_val}")

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
            tile_size = config["visualization"]["tile_size"]
            dx = (bbox.right - bbox.left) / tile_size
            dy = (bbox.top - bbox.bottom) / tile_size
            
            # Expand spatial query slightly to ensure we load segments just outside the boundary 
            # whose anti-aliasing kernels overlap the edge pixels
            buffer_x = 2.0 * dx
            buffer_y = 2.0 * dy
            
            # Filter data for this tile using spatial index (Lazy)
            subset = coords_ddf.cx[
                bbox.left - buffer_x : bbox.right + buffer_x, 
                bbox.bottom - buffer_y : bbox.top + buffer_y
            ]
            
            # Submit the compute task (returns a Future to the pandas DataFrame)
            future_gdf = client.compute(subset)
            
            # Submit the rendering task, dependent on future_gdf
            future_render = client.submit(render_tile_task, future_gdf, tile, zarr_dir, config)
            futures.append(future_render)
        
        if futures:
            logger.info(f"Waiting for {len(futures)} tasks in current batch...")
            wait(futures)
            
            # Check for errors and crash immediately
            for f in futures:
                if f.status == 'error':
                    f.result() # Crash immediately
            
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
