import logging
import sys
import tomllib
import warnings
from pathlib import Path

import click
import dask_geopandas
from dask.distributed import Client

# Import from src modules
from .renderer import run_rendering
from .postprocessing import run_post_processing
from .preprocessing import run_preprocessing, run_wkb_conversion, run_ndjson_conversion, run_csv_conversion, run_linestring_generation, run_segment_generation, run_outlier_filtering, normalize_to_epoch
from .analysis import run_passage_analysis
from .data_loader import detect_hive_partitioning
from .moving_dask.trajectory import trajectorize_dataframe

# Suppress specific, known-noisy warnings that don't indicate real problems
# for CLI users -- pandas/dask FutureWarnings about upcoming default changes
# this project doesn't control, and zarr's informational notices about its
# own format-spec status -- rather than blanket-suppressing every warning
# category (which would also hide genuine issues like RuntimeWarning for
# invalid numeric operations).
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*[Cc]onsolidated metadata.*")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

def _default_output_path(input_path: Path, suffix: str) -> Path:
    stem = input_path.name
    # Strip common suffixes sequentially (e.g., .csv.zip -> .csv -> base) using pathlib
    while True:
        ext = Path(stem).suffix
        if ext.lower() in {".zip", ".7z", ".csv", ".ndjson", ".parquet", ".geoparquet"}:
            stem = stem[:-len(ext)]
        else:
            break
    # Strip trailing trajectory processing suffixes to avoid accumulation
    for s in ["-trajectorized", "-lines", "-segments", "-cleaned"]:
        if stem.endswith(s):
            stem = stem[:-len(s)]
    return input_path.with_name(f"{stem}{suffix}")


@click.group()
def cli():
    """
    AIS Visualization Pipeline CLI.
    """
    pass

@cli.command()
@click.option(
    "--config-file",
    type=click.Path(exists=True, path_type=Path),
    default=Path("config.toml"),
    help="Path to the configuration file.",
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    default=Path("rendered"),
    help="Base directory for output.",
)
@click.option(
    "--scheduler",
    type=str,
    default=None,
    help="Address of the Dask scheduler (e.g., tcp://127.0.0.1:8786). If None, starts a local cluster.",
)
@click.option(
    "--input-file",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to input Parquet file (overrides config.toml).",
)
@click.option(
    "--resume-dir",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to a run directory to resume.",
)
@click.option(
    "--bbox",
    nargs=4,
    type=float,
    default=None,
    help="Bounding box (minx miny maxx maxy) to override config.",
)
@click.option(
    "--zoom",
    type=int,
    default=None,
    help="Zoom level to override config.",
)
def render(config_file, output_dir, scheduler, input_file, resume_dir, bbox, zoom):
    """
    Render tiles from AIS data using Datashader.
    """
    run_rendering(config_file, output_dir, scheduler, input_file, resume_dir, bbox, zoom)

@cli.command()
@click.option("--run-dir", type=click.Path(exists=True, path_type=Path), required=True, help="Path to the run directory.")
@click.option("--base-zoom", type=int, default=7, help="Base zoom level to render.")
@click.option("--scheduler", type=str, default=None, help="Address of the Dask scheduler (e.g., tcp://127.0.0.1:8786).")
@click.option("--clean-intermediate", is_flag=True, help="Delete intermediate NetCDF files (Zoom 0 to base-zoom-1) after processing.")
@click.option("--cogs", is_flag=True, help="Export Cloud Optimized GeoTIFFs for the base zoom level.")
@click.option("--config-file", type=click.Path(exists=True, path_type=Path), default=Path("config.toml"), help="Path to the configuration file.")
def postprocess(run_dir, base_zoom, scheduler, clean_intermediate, cogs, config_file):
    """
    Postprocess Zarr tiles to PNGs and COGs.
    """
    run_post_processing(run_dir, base_zoom, scheduler, clean_intermediate, cogs, config_file)

@cli.command()
@click.option(
    "--input-file",
    type=click.Path(exists=True, path_type=Path),
    default=Path("/Users/baart_f/data/ais/AISVesselTracks2023.parquet"),
    help="Path to input Parquet file.",
)
@click.option(
    "--output-file",
    type=click.Path(path_type=Path),
    default=Path("/Users/baart_f/data/ais/AISVesselTracks2023_processed.parquet"),
    help="Path to output processed Parquet file.",
)
@click.option(
    "--partitions",
    type=int,
    default=None,
    help="Number of partitions to process (for testing).",
)
@click.option(
    "--scheduler",
    type=str,
    default=None,
    help="Address of the Dask scheduler (e.g., tcp://127.0.0.1:8786). If None, starts a local cluster.",
)
@click.option(
    "--spatial-index/--no-spatial-index",
    default=True,
    help="Calculate dask-geopandas spatial partitions. Only useful for "
         "country-scale datasets spanning many partitions/regions; skip it "
         "for small, single-region datasets to avoid needless overhead.",
)
def preprocess(input_file, output_file, partitions, scheduler, spatial_index):
    """
    Preprocess AIS data (GeoParquet/GPKG -> Reproject -> Spatial Partition).
    """
    run_preprocessing(input_file, output_file, partitions, scheduler, spatial_index)

@click.group(name="convert")
def convert():
    """
    Consolidated commands for converting different raw data formats to standard flat GeoParquet.
    """
    pass


@convert.command(name="wkb")
@click.argument(
    "input-file",
    type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "--output-file",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to output GeoParquet file. Defaults to input file name with .geoparquet extension.",
)
@click.option(
    "--partitions",
    type=int,
    default=None,
    help="Number of partitions to process (for testing).",
)
@click.option(
    "--scheduler",
    type=str,
    default=None,
    help="Address of the Dask scheduler. If None, starts a local cluster.",
)
def convert_wkb(input_file, output_file, partitions, scheduler):
    """
    Convert a WKB-based Parquet file (e.g. from marinecadastre.gov) to a standard GeoParquet file.
    """
    output_file = output_file or _default_output_path(input_file, ".geoparquet")
    run_wkb_conversion(input_file, output_file, partitions, scheduler)


@convert.command(name="ndjson")
@click.argument(
    "input-file",
    type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "--output-file",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to output GeoParquet file. Defaults to input file name with .geoparquet extension.",
)
@click.option(
    "--scheduler",
    type=str,
    default=None,
    help="Address of the Dask scheduler. If None, starts a local cluster.",
)
def convert_ndjson(input_file, output_file, scheduler):
    """
    Convert an NDJSON file (e.g. from Rijkswaterstaat) to a standard flat GeoParquet file using Dask Bag.
    """
    output_file = output_file or _default_output_path(input_file, ".geoparquet")
    run_ndjson_conversion(input_file, output_file, scheduler)


@convert.command(name="csv")
@click.argument(
    "input-file",
    type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "--output-file",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to output GeoParquet file. Defaults to input file name with .geoparquet extension.",
)
@click.option(
    "--scheduler",
    type=str,
    default=None,
    help="Address of the Dask scheduler. If None, starts a local cluster.",
)
def convert_csv(input_file, output_file, scheduler):
    """
    Convert a CSV (or zipped CSV) file (e.g. from aisdata.ais.dk) to a standard flat GeoParquet file.
    """
    output_file = output_file or _default_output_path(input_file, ".geoparquet")
    run_csv_conversion(input_file, output_file, scheduler)


@cli.command()
@click.option(
    "--passage-file",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to the passage line GeoJSON file.",
)
@click.option(
    "--ais-dir",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to the AIS Parquet directory.",
)
@click.option(
    "--output-file",
    type=click.Path(path_type=Path),
    required=True,
    help="Path to output GeoJSON/GPKG file.",
)
@click.option(
    "--max-time-gap",
    type=float,
    default=7200.0,
    help="Maximum time gap between points to construct a segment (in seconds).",
)
@click.option(
    "--scheduler",
    type=str,
    default=None,
    help="Address of the Dask scheduler (e.g., tcp://127.0.0.1:8786). If None, starts a local cluster.",
)
def analyze_passage(passage_file, ais_dir, output_file, max_time_gap, scheduler):
    """
    Compute velocities along passage lines using AIS parquet files.
    """
    run_passage_analysis(passage_file, ais_dir, output_file, max_time_gap, scheduler)


@click.group(name="trajectory")
def trajectory():
    """
    Consolidated commands for AIS trajectory creation, segmentation, and feature engineering.
    """
    pass



@trajectory.command(name="filter-outliers")
@click.argument(
    "input-file",
    type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "--output-file",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to output cleaned GeoParquet file. Defaults to input file name with -cleaned.geoparquet extension.",
)
@click.option(
    "--config-file",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Optional path to a TOML config file with an [outlier] section (v_max, d_min). Falls back to defaults (v_max=50.0, d_min=300.0) if omitted.",
)
def filter_outliers(input_file, output_file, config_file):
    """
    Drop speed-implausible position outliers per vessel from preprocessed points.
    """
    output_file = output_file or _default_output_path(input_file, "-cleaned.geoparquet")
    v_max, d_min = None, None
    if config_file:
        with open(config_file, "rb") as f:
            config = tomllib.load(f)
        outlier_cfg = config.get("outlier", {})
        v_max = outlier_cfg.get("v_max")
        d_min = outlier_cfg.get("d_min")
    run_outlier_filtering(input_file, output_file, v_max=v_max, d_min=d_min)


@trajectory.command(name="compute")
@click.argument(
    "input-file",
    type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "--output-file",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to output trajectorized Parquet directory. Defaults to input file name with -trajectorized.geoparquet extension.",
)
@click.option(
    "--vessel-id-col",
    type=str,
    default="mmsi",
    help="Vessel identifier column name.",
)
@click.option(
    "--time-col",
    type=str,
    default="base_date_time",
    help="Timestamp column name.",
)
@click.option(
    "--x-col",
    type=str,
    default="longitude",
    help="Longitude column name.",
)
@click.option(
    "--y-col",
    type=str,
    default="latitude",
    help="Latitude column name.",
)
@click.option(
    "--scheduler",
    type=str,
    default=None,
    help="Dask scheduler URL.",
)
@click.option(
    "--shuffle-backend",
    type=click.Choice(["tasks", "p2p", "disk"]),
    default="disk",
    help="Shuffle backend to use for Dask.",
)
@click.option(
    "--n-partitions",
    type=int,
    default=128,
    help="Number of Dask partitions to split the dataset into.",
)
@click.option(
    "--gap-threshold-hours",
    type=float,
    default=1.0,
    help="Maximum time gap in hours to segment trips.",
)
@click.option(
    "--input-crs",
    type=str,
    default="EPSG:4326",
    help="Coordinate reference system of the input coordinates.",
)
@click.option(
    "--partition-method",
    type=click.Choice(["vessel", "spatiotemporal"]),
    default="spatiotemporal",
    help="Partitioning method to use.",
)
@click.option(
    "--hilbert-p",
    type=int,
    default=16,
    help="Hilbert curve resolution order.",
)
@click.option(
    "--epoch-time",
    is_flag=True,
    default=False,
    help="Represent timestamps as epoch-relative times (projected to 1970-01-01).",
)
def compute(input_file, output_file, vessel_id_col, time_col, x_col, y_col, scheduler, shuffle_backend, n_partitions, input_crs, gap_threshold_hours, partition_method, hilbert_p, epoch_time):
    """
    Voyage segmentation and feature engineering on Dask.
    """
    output_file = output_file or _default_output_path(input_file, "-trajectorized.geoparquet")

    if scheduler:
        client = Client(scheduler)
    else:
        client = Client()
        
    try:
        logger.info(f"Reading input from {input_file}...")
        read_kwargs = {}
        partitioning = detect_hive_partitioning(Path(input_file))
        if partitioning is not None:
            read_kwargs["dataset"] = {"partitioning": partitioning}

        try:
            ddf = dask_geopandas.read_parquet(input_file, **read_kwargs)
        except Exception as exc:
            raise click.ClickException(
                f"Input {input_file} must be a GeoParquet dataset readable by dask_geopandas."
            ) from exc

        if not isinstance(ddf, dask_geopandas.GeoDataFrame):
            raise click.ClickException(
                f"Input {input_file} must be a GeoParquet dataset readable by dask_geopandas."
            )

        # Drop empty coordinates immediately so they don't distort spatial partitioning/Hilbert divisions
        ddf = ddf.dropna(subset=[x_col, y_col])

        # Run pipeline
        res_ddf = trajectorize_dataframe(
            ddf=ddf,
            vessel_id_col=vessel_id_col,
            time_col=time_col,
            x_col=x_col,
            y_col=y_col,
            gap_threshold_hours=gap_threshold_hours,
            shuffle_backend=shuffle_backend,
            n_partitions=n_partitions,
            input_crs=input_crs,
            partition_method=partition_method,
            hilbert_p=hilbert_p,
            dataset_path=input_file
        )

        if epoch_time:
            logger.info("Normalizing timestamps to epoch-relative (start at 1970-01-01)...")
            res_ddf = res_ddf.map_partitions(normalize_to_epoch, time_col=time_col, meta=res_ddf._meta)

        logger.info(f"Saving trajectorized dataset to {output_file}...")
        res_ddf = res_ddf.reset_index(drop=True)
        res_ddf.to_parquet(output_file, overwrite=True)
        logger.info("Trajectorization complete!")
    finally:
        client.close()


@trajectory.command(name="to-linestring")
@click.argument(
    "input-file",
    type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "--output-file",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to output GeoParquet file. Defaults to input file name with -lines.geoparquet extension.",
)
@click.option(
    "--vessel-codes-json",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to JSON mapping file for vessel type classification.",
)
def to_linestring(input_file, output_file, vessel_codes_json):
    """
    Aggregate points to LineString/MultiLineString trajectories matching Marine Cadastre schema.
    """
    output_file = output_file or _default_output_path(input_file, "-lines.geoparquet")
    run_linestring_generation(input_file, output_file, vessel_codes_json)


@trajectory.command(name="to-segment")
@click.argument(
    "input-file",
    type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "--output-file",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to output segments GeoParquet file. Defaults to input file name with -segments.geoparquet extension.",
)
@click.option(
    "--epoch-time",
    is_flag=True,
    default=False,
    help="Represent segment start/end timestamps as epoch-relative times.",
)
@click.option(
    "--vessel-codes-json",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to JSON mapping file for vessel type classification.",
)
@click.option(
    "--sog-raw-units/--sog-knots",
    "sog_raw_units",
    default=False,
    help="Whether the source 'sog' column is still in raw AIS units (0.1-knot "
         "steps, 0-1022, 1023='not available' -- needs /10) or already "
         "rescaled to knots (0.0-102.2, the default). Confirm from the "
         "source data rather than guessing. A warning is logged if the "
         "chosen setting looks implausible given the data, but it is not "
         "auto-corrected.",
)
def to_segment(input_file, output_file, epoch_time, vessel_codes_json, sog_raw_units):
    """
    Generate point-pair line segments from trajectorized point trajectories.
    """
    output_file = output_file or _default_output_path(input_file, "-segments.geoparquet")
    run_segment_generation(input_file, output_file, sog_raw_units, epoch_time, vessel_codes_json)


# Register trajectory commands
cli.add_command(trajectory)
# Register convert commands
cli.add_command(convert)


if __name__ == "__main__":
    cli()
