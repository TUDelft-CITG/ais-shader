import logging
import sys
from pathlib import Path
import click
import tomllib

# Import from src modules
from .renderer import run_rendering
from .postprocessing import run_post_processing
from .preprocessing import run_preprocessing, run_wkb_conversion, run_ndjson_conversion, run_csv_conversion, run_linestring_generation, run_segment_generation
from .analysis import run_passage_analysis

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

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
def preprocess(input_file, output_file, partitions, scheduler):
    """
    Preprocess AIS data (GeoParquet/GPKG -> Reproject -> Spatial Partition).
    """
    run_preprocessing(input_file, output_file, partitions, scheduler)

@cli.command()
@click.option(
    "--input-file",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to input WKB Parquet file.",
)
@click.option(
    "--output-file",
    type=click.Path(path_type=Path),
    required=True,
    help="Path to output GeoParquet file.",
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
    Convert a WKB-based Parquet file to a standard GeoParquet file.
    """
    run_wkb_conversion(input_file, output_file, partitions, scheduler)


@cli.command()
@click.option(
    "--input-file",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to input NDJSON file.",
)
@click.option(
    "--output-file",
    type=click.Path(path_type=Path),
    required=True,
    help="Path to output GeoParquet file.",
)
@click.option(
    "--scheduler",
    type=str,
    default=None,
    help="Address of the Dask scheduler. If None, starts a local cluster.",
)
def convert_ndjson(input_file, output_file, scheduler):
    """
    Convert an NDJSON file to a standard flat GeoParquet file using Dask Bag.
    """
    run_ndjson_conversion(input_file, output_file, scheduler)


@cli.command()
@click.option(
    "--input-file",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to input CSV (or zipped CSV) file.",
)
@click.option(
    "--output-file",
    type=click.Path(path_type=Path),
    required=True,
    help="Path to output GeoParquet file.",
)
@click.option(
    "--scheduler",
    type=str,
    default=None,
    help="Address of the Dask scheduler. If None, starts a local cluster.",
)
def convert_csv(input_file, output_file, scheduler):
    """
    Convert a CSV (or zipped CSV) file to a standard flat GeoParquet file.
    """
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



@trajectory.command(name="compute")
@click.option(
    "--input-file",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to input raw AIS Parquet directory/file.",
)
@click.option(
    "--output-file",
    type=click.Path(path_type=Path),
    required=True,
    help="Path to output trajectorized Parquet directory.",
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
    import dask_geopandas
    from dask.distributed import Client
    import pandas as pd
    from .moving_dask.trajectory import trajectorize_dataframe
    
    if scheduler:
        client = Client(scheduler)
    else:
        client = Client()
        
    try:
        logger.info(f"Reading input from {input_file}...")
        ddf = dask_geopandas.read_parquet(input_file)

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
            def normalize_to_epoch(df):
                if len(df) == 0:
                    return df
                if 'trip_id' in df.columns:
                    start_times = df.groupby('trip_id')['base_date_time'].transform('min')
                    offsets = df['base_date_time'] - start_times
                    tz = df['base_date_time'].dt.tz
                    epoch_base = pd.Timestamp('1970-01-01 00:00:00', tz=tz)
                    df['base_date_time'] = epoch_base + offsets
                return df
            res_ddf = res_ddf.map_partitions(normalize_to_epoch, meta=res_ddf._meta)

        logger.info(f"Saving trajectorized dataset to {output_file}...")
        res_ddf = res_ddf.reset_index(drop=True)
        res_ddf.to_parquet(output_file)
        logger.info("Trajectorization complete!")
    finally:
        client.close()


@trajectory.command(name="to-linestring")
@click.option(
    "--input-file",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to trajectorized point parquet file.",
)
@click.option(
    "--output-file",
    type=click.Path(path_type=Path),
    required=True,
    help="Path to output GeoParquet file.",
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
    run_linestring_generation(input_file, output_file, vessel_codes_json)


@trajectory.command(name="to-segment")
@click.option(
    "--input-file",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to trajectorized point parquet file.",
)
@click.option(
    "--output-file",
    type=click.Path(path_type=Path),
    required=True,
    help="Path to output segments GeoParquet file.",
)
@click.option(
    "--epoch-time",
    is_flag=True,
    default=False,
    help="Represent segment start/end timestamps as epoch-relative times.",
)
def to_segment(input_file, output_file, epoch_time):
    """
    Generate point-pair line segments from trajectorized point trajectories.
    """
    run_segment_generation(input_file, output_file, epoch_time)


# Register trajectory commands
cli.add_command(trajectory)
# Expose subcommands at the root level under their old names for backward compatibility:
cli.add_command(compute, name="trajectorize")
cli.add_command(to_linestring, name="generate-lines")
cli.add_command(to_segment, name="generate-segments")


if __name__ == "__main__":
    cli()
