import logging
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

import dask
import cmcrameri.cm as crameri
import datashader.transfer_functions as tf
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import rioxarray
import xarray as xr
from dask.distributed import Client, as_completed
from datashader.colors import viridis
from PIL import Image
from tqdm import tqdm

logger = logging.getLogger(__name__)

def create_transparent_cmap(base_cmap_name="viridis", min_alpha=0.0, max_alpha=1.0):
    """
    Create a colormap with a gradual alpha channel.
    """
    # Get base colormap
    if isinstance(base_cmap_name, list):
        # Custom list of colors
        base_cmap = mcolors.LinearSegmentedColormap.from_list("custom", base_cmap_name)
    else:
        base_cmap = plt.get_cmap(base_cmap_name)

    # Create new colormap with alpha
    n_colors = 256
    colors = base_cmap(np.linspace(0, 1, n_colors))
    
    # Modify alpha channel (linear gradient)
    alphas = np.linspace(min_alpha, max_alpha, n_colors)
    colors[:, 3] = alphas
    
    return mcolors.ListedColormap(colors)

def render_tile(nc_path, output_path, cmap, global_max, log_scale=True):
    """
    Render a single NetCDF to PNG using global scaling and custom colormap.
    """
    # Open Zarr
    with xr.open_zarr(nc_path) as ds:
        if "counts" not in ds.data_vars:
            raise KeyError(f"'counts' data variable not found in Zarr dataset: {nc_path}")
        var_name = "counts"

        da = ds[var_name]
        
        # Sum over extra dimensions to get 2D (y, x) for visualization
        dims_to_sum = [d for d in da.dims if d not in ('y', 'x')]
        
        if dims_to_sum:
            data = da.sum(dim=dims_to_sum).values
        else:
            data = da.values
        
    # Normalize
    if log_scale:
        # Log scale: log(1 + x) / log(1 + max)
        norm_data = np.log1p(data) / np.log1p(global_max)
    else:
        norm_data = data / global_max
        
    # Clip to 0-1
    norm_data = np.clip(norm_data, 0, 1)
    
    # Apply colormap
    rgba = cmap(norm_data)
    
    # Explicitly set alpha to 0 where data is 0
    # This ensures empty pixels are fully transparent, even if min_alpha > 0
    rgba[data == 0, 3] = 0.0
    
    # Convert to 0-255 uint8
    img_data = (rgba * 255).astype(np.uint8)
    
    # Flip vertically because Datashader/NetCDF y is ascending (bottom-to-top)
    # but PIL expects top-to-bottom
    img_data = np.flipud(img_data)
    
    # Save as PNG
    img = Image.fromarray(img_data, "RGBA")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)

def process_zoom_level(run_dir, zoom, cmap, global_max, client):
    """
    Process all NetCDFs for a specific zoom level.
    """
    nc_dir = run_dir / "zarr"
    png_dir = run_dir / "png"
    
    ncs = list(nc_dir.glob(f"tile_{zoom}_*.zarr"))
    logger.info(f"Found {len(ncs)} tiles for Zoom {zoom}")
    
    if not ncs:
        return

    # Render in parallel using Dask
    futures = []
    for nc in ncs:
        parts = nc.stem.split("_")
        x, y = parts[2], parts[3]
        png_path = png_dir / str(zoom) / x / f"{y}.png"
        futures.append(client.submit(render_tile, nc, png_path, cmap, global_max))
    
    for _ in tqdm(as_completed(futures), total=len(futures), desc=f"Rendering Zoom {zoom}"):
        pass

def generate_pyramid(run_dir, base_zoom, cmap, client):
    """
    Generate lower zoom levels by aggregating upper levels.
    """
    nc_dir = run_dir / "zarr"
    
    # Iterate from base_zoom - 1 down to 0 (must be sequential)
    for z in range(base_zoom - 1, -1, -1):
        logger.info(f"Generating Zoom {z}...")
        
        child_ncs = list(nc_dir.glob(f"tile_{z+1}_*.zarr"))
        
        parents = defaultdict(list)
        for child in child_ncs:
            parts = child.stem.split("_")
            cx, cy = int(parts[2]), int(parts[3])
            px, py = cx // 2, cy // 2
            parent_key = (z, px, py)
            parents[parent_key].append(child)
            
        logger.info(f"Zoom {z}: Found {len(child_ncs)} child tiles, grouped into {len(parents)} parent tiles.")
            
        # Step 1: Aggregate & Save Zarr
        futures = []
        for parent_key, children in parents.items():
            futures.append(client.submit(aggregate_and_save_parent_tile, parent_key, children, nc_dir))
        
        # Wait for all aggregations to finish
        parent_ncs = []
        for f in tqdm(as_completed(futures), total=len(futures), desc=f"Aggregating Zoom {z}"):
            res = f.result()
            if res:
                parent_ncs.append(res)
                
        # Step 2: Calculate Robust Max for this level
        level_max = calculate_robust_max(nc_dir, z)
        
        # Step 3: Render PNGs
        render_futures = []
        png_dir = run_dir / "png"
        for nc in parent_ncs:
            parts = nc.stem.split("_")
            px, py = parts[2], parts[3]
            png_path = png_dir / str(z) / px / f"{py}.png"
            render_futures.append(client.submit(render_tile, nc, png_path, cmap, level_max))
            
        for _ in tqdm(as_completed(render_futures), total=len(render_futures), desc=f"Rendering Zoom {z}"):
            pass

def export_cogs(run_dir, base_zoom, client):
    """
    Convert NetCDF tiles at base_zoom to Cloud Optimized GeoTIFFs.
    """
    nc_dir = run_dir / "zarr"
    tiff_dir = run_dir / "tiff"
    tiff_dir.mkdir(parents=True, exist_ok=True)
    
    ncs = list(nc_dir.glob(f"tile_{base_zoom}_*.zarr"))
    logger.info(f"Exporting {len(ncs)} COGs for Zoom {base_zoom}...")
    
    futures = [client.submit(export_single_cog, nc, tiff_dir) for nc in ncs]
    for _ in tqdm(as_completed(futures), total=len(futures), desc="Exporting COGs"):
        pass

def aggregate_children(parent_key, children):
    """
    Aggregate child NetCDF files into a single parent DataArray.
    """
    z, px, py = parent_key
    child_ds_list = []
    all_categories = set()
    
    for child_path in children:
        with xr.open_zarr(child_path) as ds:
            if "counts" not in ds.data_vars:
                raise KeyError(f"'counts' data variable not found in Zarr dataset: {child_path}")
            da = ds["counts"]

            # Load data into memory to avoid file handle issues during aggregation
            da.load()
            child_ds_list.append((child_path, da))
            
            non_spatial_dims = [d for d in da.dims if d not in ('y', 'x', 'band')]
            if non_spatial_dims:
                cat_dim = non_spatial_dims[0]
                cats = da.coords[cat_dim].values
                all_categories.update(cats)
    
    if not child_ds_list:
        return None

    # Sort categories for consistency
    sorted_categories = sorted(list(all_categories))
    
    # Create Parent DataArray
    tile_size = 1024
    template_da = child_ds_list[0][1]
    parent_dims = list(template_da.dims)
    parent_coords = dict(template_da.coords)
    
    if non_spatial_dims:
        cat_dim = non_spatial_dims[0]
        parent_coords[cat_dim] = sorted_categories
        
        parent_shape = []
        for d in parent_dims:
            if d == 'y': parent_shape.append(tile_size)
            elif d == 'x': parent_shape.append(tile_size)
            elif d == cat_dim: parent_shape.append(len(sorted_categories))
            else: parent_shape.append(template_da.sizes[d])
    else:
        parent_shape = [template_da.sizes[d] if d not in ('y', 'x') else tile_size for d in parent_dims]

    parent_data = np.zeros(parent_shape, dtype=template_da.dtype)
    
    # Fill Parent
    for child_path, da_child in child_ds_list:
        parts = child_path.stem.split("_")
        cx, cy = int(parts[2]), int(parts[3])
        
        # Determine quadrant
        is_right = cx % 2
        is_bottom = cy % 2
        
        # Invert Y-slice logic to construct a Bottom-up array (NetCDF standard)
        y_slice = slice((1 - is_bottom) * 512, (2 - is_bottom) * 512)
        x_slice = slice(is_right * 512, (is_right + 1) * 512)
        
        if non_spatial_dims:
            cat_dim = non_spatial_dims[0]
            da_child_aligned = da_child.reindex({cat_dim: sorted_categories}, fill_value=0)
        else:
            da_child_aligned = da_child
        
        coarsened = da_child_aligned.coarsen(y=2, x=2, boundary="trim").sum()
        
        np_slices = []
        for d in parent_dims:
            if d == 'y': np_slices.append(y_slice)
            elif d == 'x': np_slices.append(x_slice)
            else: np_slices.append(slice(None))
        
        parent_data[tuple(np_slices)] = coarsened.values

    da_parent = xr.DataArray(parent_data, dims=parent_dims, coords=parent_coords)
    da_parent.name = "counts"
    return da_parent

def save_zarr(da, path):
    """
    Save DataArray to Zarr with compression.
    """
    da = da.astype("int32")
    da.to_zarr(path, mode="w", consolidated=True)
    logger.info(f"Saved parent Zarr: {path}")

def aggregate_and_save_parent_tile(parent_key, children, nc_dir):
    """
    Process a single parent tile: aggregate children and save Zarr.
    Returns path to saved Zarr or None.
    """
    z, px, py = parent_key
    # logger.debug(f"Processing parent {parent_key} with {len(children)} children")
    
    # 1. Aggregate
    da_parent = aggregate_children(parent_key, children)
    if da_parent is None:
        logger.warning(f"No children processed for parent {parent_key}")
        return None

    # 2. Save Zarr
    parent_nc_path = nc_dir / f"tile_{z}_{px}_{py}.zarr"
    save_zarr(da_parent, parent_nc_path)
    
    return parent_nc_path

def export_single_cog(nc_path, tiff_dir):
    """
    Convert a single NetCDF tile to COG.
    """
    with xr.open_zarr(nc_path) as ds:
        if "counts" not in ds.data_vars:
            raise KeyError(f"'counts' data variable not found in Zarr dataset: {nc_path}")
        var_name = "counts"

        da = ds[var_name]
        
        # Prepare for GeoTIFF: needs (band, y, x)
        non_spatial = [d for d in da.dims if d not in ('y', 'x')]
        
        if len(non_spatial) > 1:
            if 'band' in da.dims and da.sizes['band'] == 1:
                da = da.squeeze('band')
            
            cat_dim = [d for d in da.dims if d not in ('y', 'x')][0]
            da = da.transpose(cat_dim, 'y', 'x')
            
        elif len(non_spatial) == 1:
            d = non_spatial[0]
            da = da.transpose(d, 'y', 'x')
        
        # Ensure float/int type compatible with TIFF
        da = da.astype("float32")
        
        if not da.rio.crs:
            da.rio.write_crs("EPSG:3857", inplace=True)
        
        descriptions = None
        # The first dimension is now the band/category dimension
        first_dim = da.dims[0]
        if first_dim not in ('y', 'x') and len(da.coords[first_dim]) > 1:
             cats = da.coords[first_dim].values
             descriptions = [str(cat) for cat in cats]
        
        tiff_path = tiff_dir / f"{nc_path.stem}.tif"
        
        # Save as COG
        da.rio.to_raster(tiff_path, tiled=True, compress="DEFLATE")
        
        # Update band descriptions using rasterio directly
        if descriptions:
            import rasterio
            with rasterio.open(tiff_path, "r+") as dst:
                for i, desc in enumerate(descriptions, start=1):
                    dst.set_band_description(i, desc)
        
        return tiff_path

def calculate_robust_max(nc_dir, zoom, sample_size=1_000_000):
    """
    Calculate 98th percentile of non-zero values for a specific zoom level.
    """
    logger.info(f"Calculating robust max (98th percentile) for Zoom {zoom}...")
    ncs = list(nc_dir.glob(f"tile_{zoom}_*.zarr"))
    
    if not ncs:
        logger.warning(f"No tiles found for Zoom {zoom}")
        return 1.0

    samples = []
    
    import random
    random.shuffle(ncs) 
    
    # Limit number of tiles to scan
    tiles_to_scan = ncs[:min(len(ncs), 200)]
    
    for nc in tqdm(tiles_to_scan, desc=f"Sampling Zoom {zoom}"):
        try:
            with xr.open_zarr(nc) as ds:
                if "counts" not in ds.data_vars:
                    raise KeyError(f"'counts' data variable not found in Zarr dataset: {nc}")
                var_name = "counts"

                da = ds[var_name]
                
                # Sum extra dims
                dims_to_sum = [d for d in da.dims if d not in ('y', 'x')]
                if dims_to_sum:
                    data = da.sum(dim=dims_to_sum).values
                else:
                    data = da.values
                
                # Get non-zero values
                non_zeros = data[data > 0].flatten()
                
                if len(non_zeros) > 0:
                    if len(samples) < sample_size:
                        take = min(len(non_zeros), sample_size - len(samples))
                        samples.extend(non_zeros[:take])
                    else:
                        break
        except Exception as e:
            logger.warning(f"Failed to sample {nc}: {e}")

    if samples:
        val = float(np.percentile(samples, 98))
        logger.info(f"Zoom {zoom} Robust Max: {val}")
        return val
    else:
        logger.warning(f"No data found for Zoom {zoom}. Defaulting to 1.0")
        return 1.0

def run_post_processing(run_dir, base_zoom, scheduler, clean_intermediate, cogs, config_file):
    """
    Post-process NetCDFs to PNGs with global scaling and transparency.
    """
    # Initialize Dask Client
    dask_config = {
        "distributed.scheduler.allowed-failures": 0,
    }

    # Load Config for resources if available
    if config_file.exists():
        with open(config_file, "rb") as f:
            config = tomllib.load(f)
        
        if "resources" in config:
            res = config["resources"]
            if "memory_target" in res: dask_config["distributed.worker.memory.target"] = res["memory_target"]
            if "memory_spill" in res: dask_config["distributed.worker.memory.spill"] = res["memory_spill"]
            if "memory_pause" in res: dask_config["distributed.worker.memory.pause"] = res["memory_pause"]
            dask_config["distributed.worker.memory.terminate"] = False

    dask.config.set(dask_config)
    
    if scheduler:
        client = Client(scheduler)
        logger.info(f"Connected to Dask scheduler at {scheduler}")
    else:
        client = Client() # Local cluster
        logger.info(f"Started local Dask cluster: {client.dashboard_link}")

    nc_dir = run_dir / "zarr"
    
    # 1. Calculate Robust Max (98th Percentile) for Base Zoom
    global_max = calculate_robust_max(nc_dir, base_zoom)

    # 2. Define Colormap (Crameri Oslo, L=20% start)
    # oslo goes Black -> Blue -> White
    # Start at L=20 (approx 20% of 256 = 51)
    subset_colors = crameri.oslo(np.linspace(0.2, 1.0, 256))
    base_cmap = mcolors.LinearSegmentedColormap.from_list("crameri_oslo_subset", subset_colors)
    
    # Apply alpha gradient
    n_colors = 256
    colors_array = base_cmap(np.linspace(0, 1, n_colors))
    alphas = np.linspace(0.2, 1.0, n_colors) # min_alpha=0.2
    colors_array[:, 3] = alphas
    cmap = mcolors.ListedColormap(colors_array)
    logger.info("Using Crameri Oslo colormap (L=20% start)")

    # 3. Render Base Zoom
    process_zoom_level(run_dir, base_zoom, cmap, global_max, client)
    
    # 4. Generate Pyramid
    generate_pyramid(run_dir, base_zoom, cmap, client)
    
    # 5. Export COGs
    if cogs:
        export_cogs(run_dir, base_zoom, client)

    # 6. Cleanup Intermediate Files
    if clean_intermediate:
        logger.info("Cleaning up intermediate Zarr files...")
        import shutil

        # Delete all .zarr files where zoom < base_zoom
        for nc in nc_dir.glob("tile_*.zarr"):
            parts = nc.name.split("_")
            z = int(parts[1])
            if z < base_zoom:
                shutil.rmtree(nc)
        logger.info("Cleanup complete.")
