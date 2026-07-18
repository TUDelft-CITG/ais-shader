import logging
from pathlib import Path

import dask_geopandas

logger = logging.getLogger(__name__)


def detect_hive_partitioning(input_path: Path):
    import pyarrow as pa
    import pyarrow.dataset as ds
    import pyarrow.parquet as pq

    if not input_path.is_dir():
        return None

    partition_keys = {}
    for part_dir in input_path.rglob("*"):
        if not part_dir.is_dir() or "=" not in part_dir.name:
            continue
        key, value = part_dir.name.split("=", 1)
        if not key:
            continue
        partition_keys.setdefault(key, set()).add(value)

    # Check path and parents for partition keys
    curr = input_path
    while curr and curr != curr.parent:
        if "=" in curr.name:
            key, val = curr.name.split("=", 1)
            if key:
                partition_keys.setdefault(key, set()).add(val)
        curr = curr.parent

    if not partition_keys:
        return None

    # Load file schema to match type exactly
    file_schema = None
    try:
        # Search for any parquet file in the directory or parents
        parquet_files = list(input_path.glob("**/*.parquet")) + list(input_path.glob("*.parquet"))
        if not parquet_files:
            for parent in input_path.parents:
                parquet_files = list(parent.glob("*.parquet"))
                if parquet_files:
                    break
        if parquet_files:
            file_schema = pq.read_metadata(parquet_files[0]).schema.to_arrow_schema()
    except Exception:
        pass

    partition_fields = []
    for key, values in sorted(partition_keys.items()):
        if file_schema and key in file_schema.names:
            inferred_type = file_schema.field(key).type
        else:
            inferred_type = pa.int64() if all(v.lstrip("-").isdigit() for v in values) else pa.string()
        partition_fields.append((key, inferred_type))

    return ds.partitioning(pa.schema(partition_fields), flavor="hive")


def load_and_process_data(input_file: Path, partitions: int = None):
    """
    Load preprocessed AIS data (already has geometry and spatial partitions).
    """
    logger.info(f"Loading preprocessed data from {input_file}...")

    # Read dask_geopandas object directly
    read_kwargs = {"categories": []}
    partitioning = detect_hive_partitioning(Path(input_file))
    if partitioning is not None:
        read_kwargs["dataset"] = {"partitioning": partitioning}
    ddf_geo = dask_geopandas.read_parquet(input_file, **read_kwargs)
    
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
