import logging
from pathlib import Path

import dask_geopandas

logger = logging.getLogger(__name__)

def load_and_process_data(input_file: Path, partitions: int = None):
    """
    Load preprocessed AIS data (already has geometry and spatial partitions).
    """
    logger.info(f"Loading preprocessed data from {input_file}...")

    # Read dask_geopandas object directly
    ddf_geo = dask_geopandas.read_parquet(input_file)
    
    # Subset partitions if requested
    if partitions is not None:
        logger.info(f"Using first {partitions} partitions...")
        # Slicing drops spatial_partitions, so we need to preserve them
        original_spatial_partitions = ddf_geo.spatial_partitions
        ddf_geo = ddf_geo.partitions[:partitions]
        if original_spatial_partitions is not None:
             ddf_geo.spatial_partitions = original_spatial_partitions[:partitions]

    # Check for spatial partitions
    if ddf_geo.spatial_partitions is None:
        raise ValueError("Spatial partitions not found in metadata. Spatial partitioning is required.")

    # Ensure CRS is correct (should be EPSG:3857 from preprocessing)
    if ddf_geo.crs != "EPSG:3857":
        raise ValueError(f"Unexpected CRS: {ddf_geo.crs}. Expected EPSG:3857.")
    
    return ddf_geo

