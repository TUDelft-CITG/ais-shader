import functools
import json
import logging
import shutil
import zipfile
from pathlib import Path
import dask.bag as db
import dask.dataframe as dd
import dask_geopandas
import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from dask.distributed import Client
from shapely.geometry import LineString
from tqdm.auto import tqdm
from .archive import is_encrypted_zip, find_zip_password, iter_zip_member_lines
from .data_loader import detect_hive_partitioning
from .moving_dask.trajectory import filter_speed_outliers, DEFAULT_OUTLIER_V_MAX, DEFAULT_OUTLIER_D_MIN

logger = logging.getLogger(__name__)

def convert_to_gdf(df: pd.DataFrame) -> gpd.GeoDataFrame:
    """Convert WKB to GeoDataFrame, preserving all non-Shape columns."""
    if "Shape" not in df.columns:
        raise KeyError("Required column 'Shape' (WKB format) not found in DataFrame.")
    df_copy = df.copy()
    gs = gpd.GeoSeries.from_wkb(df_copy.pop("Shape"))
    gdf = gpd.GeoDataFrame(df_copy, geometry=gs, crs="EPSG:4269")
    return gdf

VESSEL_CODE_RANGE_SEPARATOR = " to "
"""Range syntax accepted for a 'vessel_code' string in the vessel-codes JSON,
e.g. "70 to 79" -- expanded to individual int keys 70, 71, ..., 79. Any string
containing this separator is parsed as a range and must be exactly two
"start to end" numbers; any other string is treated as a plain string-label
key (see build_vessel_mapping)."""


def build_vessel_mapping(vessel_codes_json: Path = None) -> dict:
    """
    Build a {AIS ship type code or label: vessel group name} mapping from an
    optional JSON file: a list of {"vessel_code": ..., "vessel_group": ...}
    entries, where vessel_code is exactly one of:
      - a numeric AIS code, e.g. 80 (int or float-as-string)
      - a numeric range using VESSEL_CODE_RANGE_SEPARATOR, e.g. "70 to 79"
        (expanded to individual int keys)
      - a string label, e.g. "Tanker" (for datasets like the Danish AIS
        Denmark open data whose 'Ship type' column is already decoded to a
        string rather than a numeric code) -- stored as a lowercased string
        key

    Any entry that doesn't meet this format raises immediately -- a
    vessel-codes file the caller explicitly supplied is expected to be
    well-formed, so a malformed entry is a configuration error to surface,
    not something to skip past silently.
    """
    vessel_mapping = {}
    if not vessel_codes_json:
        return vessel_mapping

    vessel_codes_json = Path(vessel_codes_json)
    if not vessel_codes_json.exists():
        raise FileNotFoundError(f"Vessel codes JSON file not found: {vessel_codes_json}")

    logger.info(f"Loading vessel codes mapping from: {vessel_codes_json}...")
    with open(vessel_codes_json, "r") as f:
        data = json.load(f)

    for item in data:
        code = item.get("vessel_code")
        group = item.get("vessel_group")
        if code is None or group is None:
            raise ValueError(f"Malformed vessel code entry (needs 'vessel_code' and 'vessel_group'): {item}")

        try:
            vessel_mapping[int(float(code))] = group
            continue
        except (ValueError, TypeError):
            pass

        if isinstance(code, str) and VESSEL_CODE_RANGE_SEPARATOR in code:
            parts = code.split(VESSEL_CODE_RANGE_SEPARATOR)
            if len(parts) != 2:
                raise ValueError(
                    f"'vessel_code' range must be exactly 'start{VESSEL_CODE_RANGE_SEPARATOR}end': {code!r}"
                )
            start, end = int(float(parts[0])), int(float(parts[1]))
            for c in range(start, end + 1):
                vessel_mapping[c] = group
        elif isinstance(code, str):
            vessel_mapping[code.strip().lower()] = group
        else:
            raise ValueError(f"Unrecognized 'vessel_code' format: {code!r}")

    return vessel_mapping

def get_vessel_group(shiptype, vessel_mapping: dict) -> str:
    """
    Recode an AIS ship type into a Marine Cadastre-style vessel group.

    Handles both raw numeric AIS ship type codes (e.g. RWS datasets) and
    datasets that already provide a decoded string label (e.g.
    the Danish AIS Denmark open data's 'Ship type' column, which arrives as
    values like "Tanker" or "HSC" rather than a numeric code) -- string labels
    are looked up in vessel_mapping (see build_vessel_mapping), supplied via
    --vessel-codes-json, since there's no universal string vocabulary to bake in.
    """
    if not shiptype:
        return "Other"
    try:
        code = int(float(shiptype))
    except (ValueError, TypeError):
        return vessel_mapping.get(str(shiptype).strip().lower(), "Other")

    if code in vessel_mapping:
        return vessel_mapping[code]

    if code == 30:
        return "Fishing"
    elif code in [31, 32, 52]:
        return "Tug"
    elif code == 35:
        return "Military"
    elif code in [36, 37]:
        return "Pleasure Craft/Sailing"
    elif 60 <= code <= 69:
        return "Passenger"
    elif 70 <= code <= 79:
        return "Cargo"
    elif 80 <= code <= 89:
        return "Tanker"
    else:
        return "Other"

SOG_NOT_AVAILABLE_KNOTS = 102.3
SOG_CAP_KNOTS = 102.2


def clean_sog(sog_raw, raw_units: bool) -> np.ndarray:
    """
    Clean an AIS SOG (speed over ground) column.

    `raw_units` must be supplied explicitly by the caller (config/CLI flag),
    never inferred from the data's magnitude: AIS SOG is transmitted in raw
    0.1-knot steps (0-1022, with 1023 meaning "not available"), but some
    upstream converters already rescale it to knots (0.0-102.2) before it
    reaches this function, and the two encodings are not reliably
    distinguishable from a single column's value range alone (e.g. RWS's own
    feed arrives already in knots, topping out at 102.3). Pass
    raw_units=True only when you have confirmed the source column is still
    in raw AIS units and needs dividing by 10.
    """
    sog = np.asarray(sog_raw, dtype=float).copy()
    if raw_units:
        sog = sog / 10.0
    invalid_mask = sog >= SOG_NOT_AVAILABLE_KNOTS
    _warn_if_sog_units_implausible(sog, raw_units, invalid_mask)
    cap_mask = ~invalid_mask & np.isclose(sog, SOG_CAP_KNOTS, atol=0.05)
    sog[invalid_mask] = np.nan
    sog[cap_mask] = SOG_CAP_KNOTS
    return sog


def _warn_if_sog_units_implausible(sog_after_scaling, raw_units: bool, invalid_mask) -> None:
    """
    Sanity-check (warn only, never auto-correct) that the declared raw_units
    matches what the data looks like. This can't prove the flag is right --
    only flag values implausible enough to suggest it's wrong.
    """
    finite = sog_after_scaling[np.isfinite(sog_after_scaling)]
    if finite.size == 0:
        return

    invalid_fraction = invalid_mask.sum() / sog_after_scaling.size
    if invalid_fraction > 0.01:
        logger.warning(
            f"clean_sog: {invalid_fraction:.1%} of values exceeded the max valid "
            f"SOG ({SOG_NOT_AVAILABLE_KNOTS} knots) after applying raw_units="
            f"{raw_units} and were dropped as 'not available'. If this fraction "
            "looks too high, the source column's units may not match --sog-raw-units."
        )

    if raw_units:
        valid = finite[finite < SOG_NOT_AVAILABLE_KNOTS]
        if valid.size and np.nanmax(valid) < 5.0:
            logger.warning(
                "clean_sog: raw_units=True was declared, but after dividing by "
                f"10 the max resulting speed is only {np.nanmax(valid):.2f} knots. "
                "The source column may already have been in knots -- check "
                "--sog-raw-units."
            )


def strip_tz_and_epoch_seconds(time_series: pd.Series) -> np.ndarray:
    """
    Convert a datetime Series to epoch seconds (float64), tolerating tz-aware
    input. `.values.astype('datetime64[s]')` on a tz-aware column returns an
    object array of Timestamps (not a numpy datetime64 array), and the
    subsequent astype raises -- so tz must be dropped first, matching
    add_hilbert_index's handling in moving_dask/trajectory.py.
    """
    if hasattr(time_series.dt, "tz") and time_series.dt.tz is not None:
        time_series = time_series.dt.tz_localize(None)
    return time_series.values.astype('datetime64[s]').astype('float64')


def normalize_to_epoch(df: pd.DataFrame, time_col: str = 'base_date_time') -> pd.DataFrame:
    """Normalizes timestamps in a vessel trajectory dataframe to be epoch-relative (starting at 1970-01-01)."""
    if len(df) == 0:
        return df
    if 'trip_id' in df.columns:
        start_times = df.groupby('trip_id')[time_col].transform('min')
        offsets = df[time_col] - start_times
        tz = df[time_col].dt.tz
        epoch_base = pd.Timestamp('1970-01-01 00:00:00', tz=tz)
        df[time_col] = epoch_base + offsets
    return df


def run_preprocessing(input_file: Path, output_file: Path, partitions: int, scheduler: str, spatial_index: bool = True):
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
        read_kwargs = {"categories": []}
        partitioning = detect_hive_partitioning(Path(input_file))
        if partitioning is not None:
            read_kwargs["dataset"] = {"partitioning": partitioning}
        ddf_geo = dask_geopandas.read_parquet(input_file, gather_spatial_partitions=False, **read_kwargs)
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

    if spatial_index:
        # Persist to ensure data is available for spatial partitioning calculation
        ddf_geo = ddf_geo.persist()

        # Calculate Spatial Partitions
        logger.info("Calculating spatial partitions...")
        ddf_geo.calculate_spatial_partitions()

        if ddf_geo.spatial_partitions is None:
             logger.warning("Spatial partitions not set after call!")
    else:
        logger.info("Skipping spatial partition calculation (--no-spatial-index).")

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
    df_meta_copy = ddf._meta.copy()
    if "Shape" in df_meta_copy.columns:
        df_meta_copy = df_meta_copy.drop(columns=["Shape"])
    meta_gdf = gpd.GeoDataFrame(df_meta_copy, geometry=gpd.GeoSeries([], dtype="object"), crs="EPSG:4269")
    ddf_geo = ddf.map_partitions(convert_to_gdf, meta=meta_gdf)
    ddf_geo = dask_geopandas.from_dask_dataframe(ddf_geo, geometry="geometry")

    logger.info(f"Saving converted GeoParquet to {output_file}...")
    ddf_geo.to_parquet(output_file)
    logger.info("Done!")


def add_date_partitions(df: pd.DataFrame, time_col: str = "base_date_time") -> pd.DataFrame:
    """Add year/month/day string columns derived from time_col, for Hive-style partitioning on write."""
    df["year"] = df[time_col].dt.strftime("%Y")
    df["month"] = df[time_col].dt.strftime("%m")
    df["day"] = df[time_col].dt.strftime("%d")
    return df


def make_points(df: pd.DataFrame) -> gpd.GeoDataFrame:
    """Flat AIS DataFrame -> GeoDataFrame with Point geometry and year/month/day columns."""
    df['base_date_time'] = pd.to_datetime(df['base_date_time'], utc=True, format='ISO8601').dt.tz_localize(None)

    # Map standard AIS missing coordinate sentinels (91.0 / 181.0) to NaN
    # This generates POINT EMPTY geometries without discarding any raw rows
    df.loc[df['latitude'] == 91.0, 'latitude'] = np.nan
    df.loc[df['longitude'] == 181.0, 'longitude'] = np.nan

    geometry = gpd.points_from_xy(df['longitude'], df['latitude'])
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")
    return add_date_partitions(gdf)


def _write_gdf_partitioned(gdf: gpd.GeoDataFrame, output_file: Path, batch_idx: int) -> None:
    """Append one batch to a year=/month=/day= hive-partitioned GeoParquet directory."""
    for (year, month, day), group in gdf.groupby(["year", "month", "day"], sort=False):
        partition_dir = output_file / f"year={year}" / f"month={month}" / f"day={day}"
        partition_dir.mkdir(parents=True, exist_ok=True)
        group.drop(columns=["year", "month", "day"]).to_parquet(
            partition_dir / f"part-{batch_idx}.parquet"
        )


NDJSON_STREAM_BATCH_SIZE = 250_000
"""Rows per batch in run_ndjson_conversion_streaming. Sized to keep peak
memory (batch of dicts + DataFrame + GeoDataFrame all resident at once)
comfortably above ~300MB per batch, measured against realistic
"aggregated"-profile rows (the heaviest, with imo/name/callsign columns)."""


def _is_aggregated_profile(row: dict) -> bool:
    """True if row carries the "aggregated" profile's data.identification block (see flatten_row)."""
    return isinstance(row.get('data'), dict) and isinstance(row['data'].get('identification'), dict)


def _flush_batch(batch: list, output_file: Path, batch_idx: int, progress: tqdm) -> int:
    """Write one batch to its hive partitions and report progress. Returns the next batch index."""
    _write_gdf_partitioned(make_points(pd.DataFrame(batch)), output_file, batch_idx)
    progress.set_postfix(batches_written=batch_idx + 1)
    return batch_idx + 1


def run_ndjson_conversion_streaming(input_file: Path, output_file: Path, password: str = None) -> None:
    """
    Convert NDJSON AIS data from a zip archive to GeoParquet without Dask.

    A zip's compressed (and possibly AES-encrypted) stream can't be split
    across Dask partitions, so `dask.bag.read_text` used to fall back to
    reading the whole decrypted archive into a single partition/worker's
    memory. This streams it through `7z e -so` (see iter_zip_member_lines)
    and writes fixed-size batches straight to the partitioned GeoParquet
    output, so memory use stays bounded by NDJSON_STREAM_BATCH_SIZE rather
    than the archive size, without ever materializing the decrypted archive
    on disk either. Relies on input rows being ordered by time, so each
    batch only ever touches a small, contiguous set of day partitions.
    """
    output_file.mkdir(parents=True, exist_ok=True)

    batch = []
    include_identification = None
    batch_idx = 0

    with tqdm(desc="Streaming NDJSON via 7z", unit=" rows", unit_scale=True) as progress:
        for line in iter_zip_member_lines(input_file, password=password):
            row = json.loads(line)
            if include_identification is None:
                include_identification = _is_aggregated_profile(row)

            batch.append(flatten_row(row, include_identification=include_identification))
            progress.update(1)

            if len(batch) >= NDJSON_STREAM_BATCH_SIZE:
                batch_idx = _flush_batch(batch, output_file, batch_idx, progress)
                batch = []

        if batch:
            batch_idx = _flush_batch(batch, output_file, batch_idx, progress)
            batch_idx += 1
            progress.set_postfix(batches_written=batch_idx)


def unwrap_field(obj):
    if obj is None:
        return None
    if isinstance(obj, dict):
        if 'value' in obj:
            return obj['value']
        if 'code' in obj:
            return obj['code']
        return None
    return obj


def to_float(val):
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _dig(row, path):
    """Follow a dotted path of dict keys (e.g. 'data.casco.to_bow') and
    return the unwrapped {"value"|"code": ...} leaf, or None."""
    node = row
    for key in path.split('.'):
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return unwrap_field(node)


def _first(row, *paths):
    """First non-None value among candidate paths, in priority order.

    RWS delivers ndjson in (at least) two profiles: a flat one, where AIS
    fields are top-level siblings of track_id/timestamp, and an "aggregated"
    one, where they're nested under a top-level "data" key (and, for a few
    fields, nested further still). Listing both paths here is what lets
    flatten_row support either profile transparently.
    """
    for path in paths:
        value = _dig(row, path)
        if value is not None:
            return value
    return None


def flatten_row(row, include_identification: bool = False):
    """Flatten a single AIS ndjson record.

    include_identification: also extract imo/name/callsign from
    data.identification. Only meaningful for the "aggregated" profile, which
    is the only one that carries this block -- callers should detect the
    profile once per file and pass this consistently for every row, so the
    resulting dask bag has a uniform schema.
    """
    # Prefer the real AIS MMSI (present in the "aggregated" profile);
    # fall back to track_id for the flat profile, which has no mmsi field.
    mmsi = _first(row, 'mmsi', 'track_id')
    track_id = row.get('track_id')
    timestamp = row.get('timestamp')

    longitude = to_float(_first(row, 'longitude', 'data.longitude'))
    latitude = to_float(_first(row, 'latitude', 'data.latitude'))

    cog = to_float(_first(row, 'cog', 'data.cog'))
    sog = to_float(_first(row, 'sog', 'data.sog'))
    heading = to_float(_first(row, 'heading', 'data.heading'))

    beam = to_float(_first(row, 'beam', 'data.beam'))
    if beam is None:
        to_port = to_float(_dig(row, 'data.casco.to_port'))
        to_starboard = to_float(_dig(row, 'data.casco.to_starboard'))
        if to_port is not None and to_starboard is not None:
            beam = to_port + to_starboard

    length = to_float(_first(row, 'length', 'data.length'))
    if length is None:
        to_bow = to_float(_dig(row, 'data.casco.to_bow'))
        to_stern = to_float(_dig(row, 'data.casco.to_stern'))
        if to_bow is not None and to_stern is not None:
            length = to_bow + to_stern

    draught = to_float(_first(row, 'draught', 'data.draught', 'data.casco.draught'))

    status = _first(row, 'status', 'data.status')
    if status is not None:
        status = str(status)

    shiptypeAIS = _first(row, 'shiptypeAIS', 'data.shiptypeAIS', 'data.identification.shiptypeAIS')
    if shiptypeAIS is not None:
        shiptypeAIS = str(shiptypeAIS)

    result = {
        'mmsi': mmsi,
        'track_id': track_id,
        'base_date_time': timestamp,
        'longitude': longitude,
        'latitude': latitude,
        'cog': cog,
        'sog': sog,
        'heading': heading,
        'beam': beam,
        'length': length,
        'draught': draught,
        'status': status,
        'shiptypeAIS': shiptypeAIS
    }

    if include_identification:
        imo = _first(row, 'imo', 'data.identification.imo')
        if imo is not None:
            imo = str(imo)

        name = _first(row, 'name', 'data.identification.name')
        if isinstance(name, str):
            name = name.strip()

        callsign = _first(row, 'callsign', 'data.identification.callsign')
        if isinstance(callsign, str):
            callsign = callsign.strip()

        result['imo'] = imo
        result['name'] = name
        result['callsign'] = callsign

    return result


def _require_7z(input_file: Path) -> None:
    if shutil.which("7z") is None:
        raise RuntimeError(
            f"'{input_file}' is a zip archive, but the '7z' command-line tool "
            "is required to read it and was not found on PATH. Install p7zip "
            "(e.g. `brew install p7zip` or `apt install p7zip-full`) and retry."
        )


def _convert_zip_ndjson(input_file: Path, output_file: Path) -> None:
    """Route a zip (encrypted or plain) NDJSON input through the 7z-streaming path.

    A zip's compressed (and possibly AES-encrypted) stream can't be split
    across Dask partitions, so handing it to dask.bag.read_text forces the
    whole decrypted archive into one partition/worker's memory. Stream it
    through the `7z` CLI instead (see run_ndjson_conversion_streaming) --
    no Dask cluster needed here.
    """
    _require_7z(input_file)
    if is_encrypted_zip(input_file):
        logger.info(f"Detected AES-encrypted zip archive at {input_file}, streaming via 7z...")
        password = find_zip_password(input_file)
    else:
        logger.info(f"Detected zip archive at {input_file}, streaming via 7z...")
        password = None
    run_ndjson_conversion_streaming(input_file, output_file, password=password)


def run_ndjson_conversion(input_file: Path, output_file: Path, scheduler: str):
    """
    Convert NDJSON AIS data to standard flat GeoParquet.
    """
    if is_encrypted_zip(input_file) or zipfile.is_zipfile(input_file):
        _convert_zip_ndjson(input_file, output_file)
        return

    if scheduler:
        logger.info(f"Connecting to Dask scheduler at {scheduler}...")
        client = Client(scheduler)
    else:
        logger.info("Starting Local Dask Client...")
        client = Client()

    logger.info(f"Dashboard: {client.dashboard_link}")

    try:
        logger.info(f"Reading NDJSON from {input_file} using Dask Bag...")
        text_bag = db.read_text(str(input_file), compression="infer", blocksize="64MB")

        # Peek at the first record to detect the "aggregated" profile
        # (nested under "data", carrying an identification block), so the
        # dataframe schema stays uniform across every row in this file.
        first_record = json.loads(text_bag.take(1)[0])
        include_identification = isinstance(first_record.get('data'), dict) and isinstance(
            first_record['data'].get('identification'), dict
        )

        bag = text_bag.map(json.loads).map(
            functools.partial(flatten_row, include_identification=include_identification)
        )

        # Meta schema for mapping to dataframe
        meta = {
            'mmsi': 'object',
            'track_id': 'object',
            'base_date_time': 'object',
            'longitude': 'float64',
            'latitude': 'float64',
            'cog': 'float64',
            'sog': 'float64',
            'heading': 'float64',
            'beam': 'float64',
            'length': 'float64',
            'draught': 'float64',
            'status': 'object',
            'shiptypeAIS': 'object'
        }
        if include_identification:
            meta['imo'] = 'object'
            meta['name'] = 'object'
            meta['callsign'] = 'object'

        logger.info("Converting Dask Bag to DataFrame...")
        ddf = bag.to_dataframe(meta=meta)

        # Convert to GeoDataFrame
        logger.info("Converting DataFrame to GeoDataFrame with Point geometry...")
        meta_gdf = gpd.GeoDataFrame(
            ddf._meta.assign(base_date_time=pd.to_datetime([])),
            geometry=gpd.GeoSeries([], dtype="object"),
            crs="EPSG:4326"
        )
        meta_gdf = add_date_partitions(meta_gdf)

        ddf_geo = ddf.map_partitions(make_points, meta=meta_gdf)
        ddf_geo = dask_geopandas.from_dask_dataframe(ddf_geo, geometry="geometry")

        # Make sure directory exists
        output_file.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"Saving GeoParquet to {output_file}, partitioned by year/month/day...")
        ddf_geo.to_parquet(output_file, partition_on=["year", "month", "day"])
        logger.info("NDJSON conversion complete!")

    finally:
        client.close()


def run_csv_conversion(input_file: Path, output_file: Path, scheduler: str):
    """
    Convert CSV AIS data (standard or zipped) to standard flat GeoParquet.
    """
    if scheduler:
        logger.info(f"Connecting to Dask scheduler at {scheduler}...")
        client = Client(scheduler)
    else:
        logger.info("Starting Local Dask Client...")
        client = Client()
        
    try:
        logger.info(f"Reading CSV from {input_file} using Dask DataFrame...")
        # Note: # Timestamp has a leading hash sign in standard Danish AIS CSV files.
        # See specification at: http://aisdata.ais.dk/!_README_information_CSV_files.txt
        needed_src_cols = [
            '# Timestamp', 'MMSI', 'Latitude', 'Longitude', 'SOG', 'COG', 
            'Heading', 'Width', 'Length', 'Draught', 'Navigational status', 'Ship type'
        ]
        df = dd.read_csv(
            input_file,
            compression="zip" if input_file.suffix == ".zip" else None,
            blocksize=None if input_file.suffix == ".zip" else "64MB",
            usecols=needed_src_cols,
            dtype={
                '# Timestamp': 'object',
                'MMSI': 'int64',
                'Latitude': 'float64',
                'Longitude': 'float64',
                'SOG': 'float64',
                'COG': 'float64',
                'Heading': 'float64',
                'Width': 'float64',
                'Length': 'float64',
                'Draught': 'float64',
                'Navigational status': 'object',
                'Ship type': 'object'
            }
        )
        
        df = df.rename(columns={
            '# Timestamp': 'base_date_time',
            'MMSI': 'mmsi',
            'Latitude': 'latitude',
            'Longitude': 'longitude',
            'SOG': 'sog',
            'COG': 'cog',
            'Heading': 'heading',
            'Width': 'beam',
            'Length': 'length',
            'Draught': 'draught',
            'Navigational status': 'status',
            'Ship type': 'shiptypeAIS'
        })
        
        needed_cols = [
            'mmsi', 'base_date_time', 'longitude', 'latitude', 'cog', 'sog', 
            'heading', 'beam', 'length', 'draught', 'status', 'shiptypeAIS'
        ]
        df = df[needed_cols]
        
        logger.info("Converting DataFrame to GeoDataFrame with Point geometry...")
        def make_points(df):
            df['base_date_time'] = pd.to_datetime(df['base_date_time'], format="%d/%m/%Y %H:%M:%S", errors='coerce')
            
            # Map standard AIS missing coordinate sentinels (91.0 / 181.0) to NaN
            # This generates POINT EMPTY geometries without discarding any raw rows
            df.loc[df['latitude'] == 91.0, 'latitude'] = np.nan
            df.loc[df['longitude'] == 181.0, 'longitude'] = np.nan
            
            geometry = gpd.points_from_xy(df['longitude'], df['latitude'])
            gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")
            return add_date_partitions(gdf)

        meta_df = pd.DataFrame(columns=needed_cols)
        meta_df = meta_df.astype({
            'mmsi': 'int64',
            'base_date_time': 'datetime64[ns]',
            'longitude': 'float64',
            'latitude': 'float64',
            'cog': 'float64',
            'sog': 'float64',
            'heading': 'float64',
            'beam': 'float64',
            'length': 'float64',
            'draught': 'float64',
            'status': 'object',
            'shiptypeAIS': 'object'
        })
        meta_gdf = gpd.GeoDataFrame(meta_df, geometry=gpd.GeoSeries([], dtype="object"), crs="EPSG:4326")
        meta_gdf = add_date_partitions(meta_gdf)

        ddf_geo = df.map_partitions(make_points, meta=meta_gdf)
        ddf_geo = dask_geopandas.from_dask_dataframe(ddf_geo, geometry="geometry")

        output_file.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"Saving GeoParquet to {output_file}, partitioned by year/month/day...")
        ddf_geo.to_parquet(output_file, partition_on=["year", "month", "day"])
        logger.info("CSV conversion complete!")
        
    finally:
        client.close()


def run_linestring_generation(input_file: Path, output_file: Path, vessel_codes_json: Path = None):
    """
    Aggregate point pings from trajectorized parquet into LineString GeoParquet with optional vessel codes config.
    """
    logger.info(f"Loading trajectorized points from {input_file}...")
    gdf = gpd.read_parquet(input_file)
    
    # Dynamically detect time and vessel columns
    time_col = 'base_date_time' if 'base_date_time' in gdf.columns else ('timestamp' if 'timestamp' in gdf.columns else None)
    vessel_col = 'mmsi' if 'mmsi' in gdf.columns else ('track_id' if 'track_id' in gdf.columns else None)
    if not time_col or not vessel_col:
        raise KeyError(f"Could not find time column or vessel ID column in dataset schema. Available columns: {list(gdf.columns)}")

    logger.info("Sorting data chronologically per trip...")
    gdf = gdf.sort_values(by=['trip_id', time_col])
    
    logger.info("Aggregating points to linestrings...")
    grouped = gdf.groupby('trip_id')
    
    mmsis = grouped[vessel_col].first()
    start_times = grouped[time_col].min()
    end_times = grouped[time_col].max()
    vessel_types = grouped['shiptypeAIS'].first() if 'shiptypeAIS' in gdf.columns else grouped.get_group(list(grouped.groups.keys())[0]).iloc[0].get('shiptypeAIS', np.nan)
    lengths = grouped['length'].first() if 'length' in gdf.columns else np.nan
    widths = grouped['beam'].first() if 'beam' in gdf.columns else np.nan
    drafts = grouped['draught'].first() if 'draught' in gdf.columns else np.nan
    
    geoms = []
    valid_trip_ids = []
    
    for trip_id, group in grouped:
        if len(group) < 2:
            continue
            
        coords = np.column_stack((group['longitude'].values, group['latitude'].values))
        # Remove consecutive duplicate coordinates
        mask = np.ones(len(coords), dtype=bool)
        mask[1:] = np.any(coords[1:] != coords[:-1], axis=1)
        clean_coords = coords[mask]
        
        if len(clean_coords) < 2:
            continue
            
        line = LineString(clean_coords)
        geoms.append(line)
        valid_trip_ids.append(trip_id)
        
    logger.info(f"Created {len(geoms)} valid linestrings out of {len(grouped)} trips.")
    
    df_attrs = pd.DataFrame({
        'MMSI': mmsis.loc[valid_trip_ids].values,
        'TrackStartTime': start_times.loc[valid_trip_ids].values,
        'TrackEndTime': end_times.loc[valid_trip_ids].values,
        'VesselType': vessel_types.loc[valid_trip_ids].values if hasattr(vessel_types, 'loc') else [vessel_types]*len(valid_trip_ids),
        'Length': lengths.loc[valid_trip_ids].values if hasattr(lengths, 'loc') else [lengths]*len(valid_trip_ids),
        'Width': widths.loc[valid_trip_ids].values if hasattr(widths, 'loc') else [widths]*len(valid_trip_ids),
        'Draft': drafts.loc[valid_trip_ids].values if hasattr(drafts, 'loc') else [drafts]*len(valid_trip_ids),
    }, index=valid_trip_ids)
    
    durations = (df_attrs['TrackEndTime'] - df_attrs['TrackStartTime']).dt.total_seconds() / 60.0
    df_attrs['DurationMinutes'] = durations.round().astype(int)
    
    vessel_mapping = build_vessel_mapping(vessel_codes_json)
    df_attrs['VesselGroup'] = df_attrs['VesselType'].apply(lambda st: get_vessel_group(st, vessel_mapping))
    
    gdf_lines = gpd.GeoDataFrame(df_attrs, geometry=geoms, crs="EPSG:4326")
    
    output_file.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Saving GeoParquet to: {output_file}...")
    gdf_lines.to_parquet(output_file)
    
    logger.info("Line generation complete!")


def run_outlier_filtering(input_file: Path, output_file: Path, v_max: float = None, d_min: float = None):
    """
    Drop speed-implausible position outliers per vessel from a preprocessed point
    dataset, using a vectorized re-implementation of movingpandas.OutlierCleaner
    with a joint speed-and-distance gate: a point is dropped only if its jump from
    the last kept point exceeds both v_max (m/s) and d_min (meters).
    """
    v_max = DEFAULT_OUTLIER_V_MAX if v_max is None else v_max
    d_min = DEFAULT_OUTLIER_D_MIN if d_min is None else d_min

    logger.info(f"Loading points from {input_file}...")
    gdf = gpd.read_parquet(input_file)

    time_col = 'base_date_time' if 'base_date_time' in gdf.columns else ('timestamp' if 'timestamp' in gdf.columns else None)
    vessel_col = 'mmsi' if 'mmsi' in gdf.columns else ('track_id' if 'track_id' in gdf.columns else None)
    if not time_col or not vessel_col:
        raise KeyError(f"Could not find time column or vessel ID column in dataset schema. Available columns: {list(gdf.columns)}")

    logger.info("Sorting data chronologically per vessel...")
    gdf = gdf.sort_values(by=[vessel_col, time_col]).reset_index(drop=True)

    lon_all = gdf['longitude'].values
    lat_all = gdf['latitude'].values
    t_all = strip_tz_and_epoch_seconds(gdf[time_col])
    keep_mask = np.ones(len(gdf), dtype=bool)

    logger.info(f"Detecting speed-implausible position outliers per vessel (v_max={v_max} m/s, d_min={d_min} m)...")
    for _, group in gdf.groupby(vessel_col, sort=False):
        pos = group.index.values
        if len(pos) < 2:
            continue
        lon, lat, t = lon_all[pos], lat_all[pos], t_all[pos]
        mask = filter_speed_outliers(lon, lat, t, v_max=v_max, d_min=d_min)
        if not mask.all():
            keep_mask[pos[~mask]] = False

    n_dropped = int((~keep_mask).sum())
    logger.info(f"Dropped {n_dropped} outlier point(s) out of {len(gdf)}.")
    gdf_clean = gdf[keep_mask]

    output_file.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Saving cleaned points to {output_file}...")
    gdf_clean.to_parquet(output_file)
    logger.info("Outlier filtering complete!")


def run_segment_generation(input_file: Path, output_file: Path, sog_raw_units: bool, epoch_time: bool = False, vessel_codes_json: Path = None):
    """
    Generate point-pair line segments from trajectorized point dataset,
    with option to use epoch-normalized timestamps.
    """
    logger.info(f"Loading trajectorized points from {input_file}...")
    gdf = gpd.read_parquet(input_file)
    
    # Dynamically detect time and vessel columns
    time_col = 'base_date_time' if 'base_date_time' in gdf.columns else ('timestamp' if 'timestamp' in gdf.columns else None)
    vessel_col = 'mmsi' if 'mmsi' in gdf.columns else ('track_id' if 'track_id' in gdf.columns else None)
    if not time_col or not vessel_col:
        raise KeyError(f"Could not find time column or vessel ID column in dataset schema. Available columns: {list(gdf.columns)}")

    logger.info("Sorting data chronologically per trip...")
    gdf = gdf.sort_values(by=['trip_id', time_col])
    
    if epoch_time:
        logger.info("Calculating trip start times and epoch-normalized timestamps...")
        gdf = normalize_to_epoch(gdf, time_col)
        
    # Decompose into 2-point Line Segments (Pairs)
    logger.info("Generating point-pair line segments...")
    shifted = gdf.groupby('trip_id').shift(-1)
    mask = shifted[time_col].notna()
    p1 = gdf[mask]
    p2 = shifted[mask]
    
    x1 = p1['longitude'].values
    y1 = p1['latitude'].values
    x2 = p2['longitude'].values
    y2 = p2['latitude'].values

    coords = np.column_stack([x1, y1, x2, y2]).reshape(-1, 2, 2)
    logger.info("Creating LineString geometries...")
    geoms = shapely.linestrings(coords)

    if 'shiptypeAIS' not in p1.columns:
        raise KeyError(
            "Expected a 'shiptypeAIS' column for vessel-group classification; "
            f"not found in dataset schema. Available columns: {list(p1.columns)}"
        )
    vessel_mapping = build_vessel_mapping(vessel_codes_json)
    vessel_groups = pd.Series(p1['shiptypeAIS'].values).apply(lambda st: get_vessel_group(st, vessel_mapping)).values

    df_segments = pd.DataFrame({
        'MMSI': p1[vessel_col].values,
        'trip_id': p1['trip_id'].values,
        'VesselType': p1['shiptypeAIS'].values,
        'VesselGroup': vessel_groups,
        'Length': p1['length'].values if 'length' in p1.columns else np.nan,
        'Width': p1['beam'].values if 'beam' in p1.columns else np.nan,
        'Draft': p1['draught'].values if 'draught' in p1.columns else np.nan,
        'segment_start_time': p1[time_col].values,
        'segment_end_time': p2[time_col].values,
        'speed_mps': p1['speed_mps'].values if 'speed_mps' in p1.columns else np.nan,
        'acceleration_mps2': p1['acceleration_mps2'].values if 'acceleration_mps2' in p1.columns else np.nan,
    })

    # Extract and clean SOG variable
    if 'sog' not in p1.columns:
        raise KeyError(f"Expected a 'sog' column in the trajectorized dataset. Available columns: {list(p1.columns)}")
    df_segments['sog'] = clean_sog(p1['sog'].values, raw_units=sog_raw_units)

    df_segments['segment_duration_s'] = (df_segments['segment_end_time'] - df_segments['segment_start_time']).dt.total_seconds()
    gdf_segments = gpd.GeoDataFrame(df_segments, geometry=geoms, crs="EPSG:4326")
    
    output_file.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Saving segments GeoParquet to: {output_file}...")
    gdf_segments.to_parquet(output_file)
    logger.info("Segment generation complete!")

