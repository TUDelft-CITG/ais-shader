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
import pandas as pd
import psutil
import rioxarray
import shapely
import xarray as xr
from dask.distributed import Client, get_client, wait
from rasterio.transform import from_bounds

# Import from src modules
from .data_loader import load_and_process_data

logger = logging.getLogger(__name__)

# Pixel margin rendered beyond each tile's true edge, then cropped away after
# aggregation, so that line segments continuing into a neighboring tile aren't
# wrongly end-capped at this tile's boundary. Must be at least as large as the
# longest on-canvas pixel footprint a segment can have near the boundary; 8px
# comfortably covers the AIS segment lengths seen in practice for this zoom
# level (see tests/test_tile_edge_lines.py). render_tile_task's own pre-clip
# box must match this exactly (not a wider margin): a geometry that survives
# a wider pre-clip but lies entirely outside the canvas's own x_range/y_range
# crashes datashader's cvs.line(), which doesn't handle all-out-of-range input
# gracefully. CLIP_MARGIN_PX only pads the coarser, non-crashing .cx[]
# spatial prefetch in render_tiles(), so it can never exclude data the render
# border needs.
TILE_BORDER_PX = 8
CLIP_MARGIN_PX = 2


def _canonicalize_line_direction(geom):
    """Reorder a line's endpoints to match datashader's own internal flip_order
    convention (datashader/glyphs/line.py: flip_order = y1 < y0 or (y1 == y0 and
    x1 < x0)). Datashader's antialiased line renderer has a direction-dependent
    rounding bug: rendering the same physical segment with endpoints swapped can
    produce a slightly different result (see upstream issue in progress). Rendering
    every segment in this canonical order -- regardless of the arbitrary
    chronological order points were digitized in -- means every segment takes the
    same internal code branch, sidestepping the bug."""
    if geom is None or geom.is_empty:
        return geom
    if geom.geom_type == "LineString":
        coords = list(geom.coords)
        if len(coords) < 2:
            return geom
        x0, y0 = coords[0][:2]
        x1, y1 = coords[-1][:2]
        if y1 < y0 or (y1 == y0 and x1 < x0):
            return shapely.LineString(coords[::-1])
        return geom
    if geom.geom_type == "MultiLineString":
        return shapely.MultiLineString([_canonicalize_line_direction(g) for g in geom.geoms])
    return geom


def _reduction_for_band(band):
    if band == "transit_count":
        return ds.count()
    if band == "sog":
        return ds.mean("sog")
    if band == "speed_mps":
        return ds.mean("speed_mps")
    raise ValueError(f"Unsupported band: {band}")


def _line_kwargs_for_band(band, line_width):
    """kwargs for cvs.line() matching the pre-existing per-band behavior: only
    transit_count has ever respected line_width, and only when non-zero (zero
    omits the argument entirely rather than passing 0, since datashader picks
    aliased vs. antialiased line rendering based on the argument's presence,
    not its value)."""
    if band == "transit_count" and line_width != 0:
        return {"line_width": line_width}
    return {}


def _sanitize_band_name(value):
    """Band names become Zarr/COG band labels and PNG subdirectory names, so
    strip characters ('/') that would otherwise be read as a path separator
    (e.g. the 'Pleasure Craft/Sailing' vessel group)."""
    return str(value).replace("/", "-")


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
    border = TILE_BORDER_PX
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
    # Must match the render canvas's own (expanded_left, expanded_right,
    # expanded_bottom, expanded_top) exactly, not a wider margin: a geometry
    # that survives this clip but lies entirely outside the canvas's own
    # x_range/y_range crashes datashader's cvs.line() (it doesn't handle an
    # all-out-of-range input gracefully). Using the same bounds here as the
    # canvas guarantees geometry.intersection() only ever leaves behind
    # geometry that's genuinely within (or empty, and therefore filtered out
    # below).
    tile_box = shapely.box(expanded_left, expanded_bottom, expanded_right, expanded_top)
    
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

    # Canonicalize each segment's endpoint order to avoid a direction-dependent
    # antialiasing rounding bug in datashader's line renderer (see
    # _canonicalize_line_direction docstring).
    if is_line_dataset:
        gdf_local['geometry'] = gdf_local.geometry.apply(_canonicalize_line_direction)

    # Aggregate
    line_width = config["visualization"]["line_width"]
    bands = config["visualization"].get("bands")

    if bands:
        category_column = config["visualization"].get("category_column")
        categories = config["visualization"].get("_categories")
        if category_column and categories:
            gdf_local[category_column] = gdf_local[category_column].astype(
                pd.CategoricalDtype(categories=categories)
            )

        da_list = []
        for b in bands:
            reduction = _reduction_for_band(b)
            line_kwargs = _line_kwargs_for_band(b, line_width)

            agg_band = cvs.line(gdf_local, geometry='geometry', agg=reduction, **line_kwargs)
            da_band = agg_band.fillna(0).astype("float32").expand_dims(dim={'band': [b]})
            da_list.append(da_band)

            if category_column and categories:
                agg_cat = cvs.line(
                    gdf_local, geometry='geometry', agg=ds.by(category_column, reduction), **line_kwargs
                )
                agg_cat = agg_cat.fillna(0).astype("float32")
                for cat in categories:
                    da_cat = (
                        agg_cat.sel({category_column: cat})
                        .drop_vars(category_column)
                        .expand_dims(dim={'band': [f"{b}__{_sanitize_band_name(cat)}"]})
                    )
                    da_list.append(da_cat)

        da = xr.concat(da_list, dim='band')
        da.name = "metrics"
    else:
        category_column = config["visualization"].get("category_column")
        value_column = config["visualization"].get("value_column")
        aggregation_type = config["visualization"].get("aggregation", "count")

        if category_column:
            gdf_local[category_column] = gdf_local[category_column].astype("category")
            agg = cvs.line(gdf_local, geometry='geometry', agg=ds.by(category_column, ds.count()))
            # ds.by returns a DataArray with an extra category dimension (not a
            # Dataset), so rename it to 'band' directly rather than the old
            # isinstance(agg, xr.Dataset) branch, which datashader's current
            # ds.by output never actually takes.
            da = agg.fillna(0).astype("int32").rename({category_column: "band"})
            da = da.assign_coords(band=[_sanitize_band_name(c) for c in da["band"].values])
        elif value_column:
            if aggregation_type == "mean":
                agg = cvs.line(gdf_local, geometry='geometry', agg=ds.mean(value_column))
            elif aggregation_type == "max":
                agg = cvs.line(gdf_local, geometry='geometry', agg=ds.max(value_column))
            else:
                raise ValueError(f"Unsupported aggregation: {aggregation_type}")
            da = agg.fillna(0).astype("float32").expand_dims(dim={'band': 1})
        else:
            if line_width == 0:
                agg = cvs.line(gdf_local, geometry='geometry', agg=ds.count())
            else:
                agg = cvs.line(gdf_local, geometry='geometry', agg=ds.count(), line_width=line_width)
            da = agg.fillna(0).astype("int32").expand_dims(dim={'band': 1})
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
    
    # Validate category column presence at startup, and fix the set of category
    # values now so every tile emits the same set of bands regardless of which
    # categories happen to be present in that tile's local subset of the data.
    config = {**config, "visualization": dict(config["visualization"])}
    category_column = config["visualization"].get("category_column")
    if category_column:
        if category_column not in coords_ddf.columns:
            raise ValueError(f"Category column '{category_column}' not found in dataset schema. Available columns: {list(coords_ddf.columns)}")
        categories = sorted(coords_ddf[category_column].dropna().unique().compute().tolist())
        logger.info(f"Found {len(categories)} categories in '{category_column}': {categories}")
        config["visualization"]["_categories"] = categories

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
            
            # Prefetch a buffer around the tile so we load segments just outside
            # the boundary that render_tile_task's border-expanded canvas will
            # actually draw on. Sized to match render_tile_task's own clip box
            # (TILE_BORDER_PX + CLIP_MARGIN_PX) exactly, so this can never
            # exclude data the border rendering needs.
            buffer_x = (TILE_BORDER_PX + CLIP_MARGIN_PX) * dx
            buffer_y = (TILE_BORDER_PX + CLIP_MARGIN_PX) * dy
            
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
