import logging
from pathlib import Path
import dask.dataframe as dd
import dask_geopandas
import geopandas as gpd
import pandas as pd
from dask.distributed import Client

logger = logging.getLogger(__name__)

def convert_to_gdf(df: pd.DataFrame) -> gpd.GeoDataFrame:
    """Convert WKB to GeoDataFrame."""
    if "Shape" not in df.columns:
        return df
    gs = gpd.GeoSeries.from_wkb(df["Shape"])
    gdf = gpd.GeoDataFrame(geometry=gs, crs="EPSG:4269")
    return gdf

def run_preprocessing(input_file: Path, output_file: Path, partitions: int, scheduler: str):
    """
    Preprocess AIS data: GeoParquet/GPKG -> Reproject -> Spatial Partition -> Save.
    """
    if scheduler:
        logger.info(f"Connecting to Dask scheduler at {scheduler}...")
        client = Client(scheduler)
    else:
        logger.info("Starting Local Dask Client...")
        client = Client()
    
    logger.info(f"Dashboard: {client.dashboard_link}")

    logger.info(f"Reading {input_file}...")
    
    if input_file.suffix == ".gpkg":
        logger.info("Detected GPKG format. Reading with dask_geopandas...")
        ddf_geo = dask_geopandas.read_file(input_file, npartitions=partitions if partitions else 4)
    else:
        logger.info("Reading as GeoParquet...")
        ddf_geo = dask_geopandas.read_parquet(input_file, gather_spatial_partitions=False)
        if partitions:
            ddf_geo = ddf_geo.partitions[:partitions]
        
        # Drop Shape_bbox if present
        if "Shape_bbox" in ddf_geo.columns:
            ddf_geo = ddf_geo.drop(columns=["Shape_bbox"])

        # Rename Shape to geometry if present
        if "Shape" in ddf_geo.columns:
            ddf_geo = ddf_geo.rename(columns={"Shape": "geometry"})
            ddf_geo = ddf_geo.set_geometry("geometry")

    # Reproject
    logger.info("Reprojecting to EPSG:3857...")
    ddf_geo = ddf_geo.to_crs("EPSG:3857")
    
    # Persist to ensure data is available for spatial partitioning calculation
    ddf_geo = ddf_geo.persist()

    # Calculate Spatial Partitions
    logger.info("Calculating spatial partitions...")
    ddf_geo.calculate_spatial_partitions()
    
    if ddf_geo.spatial_partitions is None:
         logger.warning("Spatial partitions not set after call!")

    # Save
    logger.info(f"Saving to {output_file}...")
    ddf_geo.to_parquet(output_file)
    logger.info("Done!")

def run_wkb_conversion(input_file: Path, output_file: Path, partitions: int, scheduler: str):
    """
    Convert a WKB-based Parquet file (containing a 'Shape' WKB column)
    to a standard GeoParquet file.
    """
    if scheduler:
        logger.info(f"Connecting to Dask scheduler at {scheduler}...")
        client = Client(scheduler)
    else:
        logger.info("Starting Local Dask Client...")
        client = Client()
    
    logger.info(f"Dashboard: {client.dashboard_link}")
    logger.info(f"Reading WKB Parquet from {input_file}...")
    
    ddf = dd.read_parquet(input_file, engine="pyarrow")
    
    if partitions:
        logger.info(f"Using first {partitions} partitions...")
        ddf = ddf.partitions[:partitions]

    # Convert to GeoDataFrame
    logger.info("Converting WKB to GeoDataFrame...")
    meta_gdf = gpd.GeoDataFrame(geometry=gpd.GeoSeries([], dtype="object"), crs="EPSG:4269")
    ddf_geo = ddf.map_partitions(convert_to_gdf, meta=meta_gdf)
    ddf_geo = dask_geopandas.from_dask_dataframe(ddf_geo, geometry="geometry")

    logger.info(f"Saving converted GeoParquet to {output_file}...")
    ddf_geo.to_parquet(output_file)
    logger.info("Done!")

