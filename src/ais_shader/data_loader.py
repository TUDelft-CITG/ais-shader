import logging
from pathlib import Path

import dask_geopandas
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


def detect_hive_partitioning(input_path: Path):
    if not input_path.is_dir():
        return None

    # Walk only the partition-key directory levels (each level is exactly one
    # "key=value" dir deep, e.g. year=/month=/day=), stopping as soon as a
    # level has no more "key=value" children -- a dataset can have many
    # thousands of leaf parquet files, and input_path.rglob("*") would stat
    # every single one of them just to find the much shallower partition
    # directories above them.
    partition_keys = {}
    frontier = [input_path]
    while frontier:
        next_frontier = []
        for d in frontier:
            for child in d.iterdir():
                if child.is_dir() and "=" in child.name:
                    key, value = child.name.split("=", 1)
                    if key:
                        partition_keys.setdefault(key, set()).add(value)
                        next_frontier.append(child)
        frontier = next_frontier

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

    # Load file schema to match type exactly. Only one parquet file's schema
    # is ever needed, so stop at the first match (glob() is a lazy generator)
    # rather than materializing every file in the dataset via list(...).
    file_schema = None
    try:
        first_parquet = next(input_path.glob("**/*.parquet"), None)
        if first_parquet is None:
            for parent in input_path.parents:
                first_parquet = next(parent.glob("*.parquet"), None)
                if first_parquet is not None:
                    break
        if first_parquet is not None:
            file_schema = pq.read_metadata(first_parquet).schema.to_arrow_schema()
    except Exception:
        logger.warning(
            f"Could not read Parquet schema near {input_path}; falling back to "
            "inferring partition column types from directory names.",
            exc_info=True,
        )

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

    # Spatial partitions let render_tiles() prune whole partitions per tile via
    # .cx[] -- a meaningful speedup for country-scale, multi-partition datasets,
    # but unnecessary for a small, single-region dataset preprocessed with
    # --no-spatial-index. .cx[] still filters correctly without them, just
    # without that pruning, so this is a performance note, not a hard error.
    if ddf_geo.spatial_partitions is None:
        logger.warning("Spatial partitions not found in metadata; per-tile filtering will scan all partitions.")

    # Ensure CRS is correct (should be EPSG:3857 from preprocessing)
    if ddf_geo.crs != "EPSG:3857":
        raise ValueError(f"Unexpected CRS: {ddf_geo.crs}. Expected EPSG:3857.")
    
    return ddf_geo
