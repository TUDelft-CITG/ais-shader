import logging
from pathlib import Path
from typing import Iterable, Optional, Union

import geopandas as gpd

logger = logging.getLogger(__name__)

# Output format is chosen from the output path's extension everywhere in the
# CLI, so the extension -> format mapping is defined once here.
PARQUET_SUFFIXES = {".parquet", ".geoparquet"}
GPKG_SUFFIXES = {".gpkg"}
GEOJSON_SUFFIXES = {".geojson", ".json"}

# AIS source data and all reference vector layers are lon/lat; EPSG:3857 is the
# metric working CRS for length/intersection maths and tiling.
CRS_LONLAT = "EPSG:4326"
CRS_METRIC = "EPSG:3857"


def read_vector(path: Union[str, Path]) -> gpd.GeoDataFrame:
    """Read a vector dataset, dispatching on extension: GeoParquet vs. anything GDAL reads."""
    p = Path(path)
    if p.suffix.lower() in PARQUET_SUFFIXES:
        return gpd.read_parquet(p)
    return gpd.read_file(p)


def write_vector(gdf: gpd.GeoDataFrame, path: Union[str, Path], mkdir: bool = True) -> None:
    """
    Write a vector dataset, dispatching on extension.

    GeoParquet, GeoPackage and GeoJSON get an explicit driver; any other
    extension is handed to GDAL, which infers the driver itself (e.g. .shp).
    """
    p = Path(path)
    if mkdir:
        p.parent.mkdir(parents=True, exist_ok=True)
    suffix = p.suffix.lower()
    if suffix in PARQUET_SUFFIXES:
        gdf.to_parquet(p)
    elif suffix in GPKG_SUFFIXES:
        gdf.to_file(p, driver="GPKG")
    elif suffix in GEOJSON_SUFFIXES:
        gdf.to_file(p, driver="GeoJSON")
    else:
        gdf.to_file(p)


def assume_lonlat(gdf: gpd.GeoDataFrame, label: str = "dataset") -> gpd.GeoDataFrame:
    """Tag a CRS-less GeoDataFrame as lon/lat, which is what every input source here is."""
    if gdf.crs is None:
        logger.warning(f"{label} has no CRS; assuming {CRS_LONLAT}.")
        return gdf.set_crs(CRS_LONLAT)
    return gdf


def to_metric_crs(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Reproject to the metric working CRS (EPSG:3857), assuming lon/lat if unset."""
    gdf = assume_lonlat(gdf)
    return gdf if gdf.crs.to_epsg() == 3857 else gdf.to_crs(CRS_METRIC)


def to_lonlat_crs(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Reproject to lon/lat (EPSG:4326) for GIS output, leaving a CRS-less frame alone."""
    if gdf.crs is None or str(gdf.crs) == CRS_LONLAT:
        return gdf
    return gdf.to_crs(CRS_LONLAT)


def empty_geodataframe(columns: Iterable[str], crs: Optional[str] = CRS_LONLAT) -> gpd.GeoDataFrame:
    """An empty GeoDataFrame carrying `columns` plus a geometry column, for no-result returns."""
    return gpd.GeoDataFrame({c: [] for c in columns}, geometry=[], crs=crs)
