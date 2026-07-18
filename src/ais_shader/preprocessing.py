import logging
from pathlib import Path
import dask.dataframe as dd
import dask_geopandas
import geopandas as gpd
import pandas as pd
from dask.distributed import Client
from .data_loader import detect_hive_partitioning

logger = logging.getLogger(__name__)

def convert_to_gdf(df: pd.DataFrame) -> gpd.GeoDataFrame:
    """Convert WKB to GeoDataFrame, preserving all non-Shape columns."""
    if "Shape" not in df.columns:
        raise KeyError("Required column 'Shape' (WKB format) not found in DataFrame.")
    df_copy = df.copy()
    gs = gpd.GeoSeries.from_wkb(df_copy.pop("Shape"))
    gdf = gpd.GeoDataFrame(df_copy, geometry=gs, crs="EPSG:4269")
    return gdf

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
    df_meta_copy = ddf._meta.copy()
    if "Shape" in df_meta_copy.columns:
        df_meta_copy = df_meta_copy.drop(columns=["Shape"])
    meta_gdf = gpd.GeoDataFrame(df_meta_copy, geometry=gpd.GeoSeries([], dtype="object"), crs="EPSG:4269")
    ddf_geo = ddf.map_partitions(convert_to_gdf, meta=meta_gdf)
    ddf_geo = dask_geopandas.from_dask_dataframe(ddf_geo, geometry="geometry")

    logger.info(f"Saving converted GeoParquet to {output_file}...")
    ddf_geo.to_parquet(output_file)
    logger.info("Done!")


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


def flatten_row(row):
    track_id = row.get('track_id')
    timestamp = row.get('timestamp')
    
    longitude = to_float(unwrap_field(row.get('longitude')))
    latitude = to_float(unwrap_field(row.get('latitude')))
    
    cog = to_float(unwrap_field(row.get('cog')))
    sog = to_float(unwrap_field(row.get('sog')))
    heading = to_float(unwrap_field(row.get('heading')))
    beam = to_float(unwrap_field(row.get('beam')))
    length = to_float(unwrap_field(row.get('length')))
    draught = to_float(unwrap_field(row.get('draught')))
    
    status = unwrap_field(row.get('status'))
    if status is not None:
        status = str(status)
        
    shiptypeAIS = unwrap_field(row.get('shiptypeAIS'))
    if shiptypeAIS is not None:
        shiptypeAIS = str(shiptypeAIS)
        
    return {
        'mmsi': track_id,
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


def run_ndjson_conversion(input_file: Path, output_file: Path, scheduler: str):
    """
    Convert NDJSON AIS data to standard flat GeoParquet.
    """
    import dask.bag as db
    import json
    
    if scheduler:
        logger.info(f"Connecting to Dask scheduler at {scheduler}...")
        client = Client(scheduler)
    else:
        logger.info("Starting Local Dask Client...")
        client = Client()
        
    logger.info(f"Dashboard: {client.dashboard_link}")
    
    try:
        logger.info(f"Reading NDJSON from {input_file} using Dask Bag...")
        bag = db.read_text(str(input_file)).map(json.loads).map(flatten_row)
        
        # Meta schema for mapping to dataframe
        meta = {
            'mmsi': 'object',
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
        
        logger.info("Converting Dask Bag to DataFrame...")
        ddf = bag.to_dataframe(meta=meta)
        
        # Convert to GeoDataFrame
        logger.info("Converting DataFrame to GeoDataFrame with Point geometry...")
        def make_points(df):
            df['base_date_time'] = pd.to_datetime(df['base_date_time'], utc=True).dt.tz_localize(None)
            
            # Map standard AIS missing coordinate sentinels (91.0 / 181.0) to NaN
            # This generates POINT EMPTY geometries without discarding any raw rows
            import numpy as np
            df.loc[df['latitude'] == 91.0, 'latitude'] = np.nan
            df.loc[df['longitude'] == 181.0, 'longitude'] = np.nan
            
            geometry = gpd.points_from_xy(df['longitude'], df['latitude'])
            return gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")
            
        meta_gdf = gpd.GeoDataFrame(
            ddf._meta.assign(base_date_time=pd.to_datetime([])),
            geometry=gpd.GeoSeries([], dtype="object"),
            crs="EPSG:4326"
        )
        
        ddf_geo = ddf.map_partitions(make_points, meta=meta_gdf)
        ddf_geo = dask_geopandas.from_dask_dataframe(ddf_geo, geometry="geometry")
        
        # Make sure directory exists
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Saving GeoParquet to {output_file}...")
        ddf_geo.to_parquet(output_file)
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
            import numpy as np
            df.loc[df['latitude'] == 91.0, 'latitude'] = np.nan
            df.loc[df['longitude'] == 181.0, 'longitude'] = np.nan
            
            geometry = gpd.points_from_xy(df['longitude'], df['latitude'])
            return gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")
            
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
        
        ddf_geo = df.map_partitions(make_points, meta=meta_gdf)
        ddf_geo = dask_geopandas.from_dask_dataframe(ddf_geo, geometry="geometry")
        
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Saving GeoParquet to {output_file}...")
        ddf_geo.to_parquet(output_file)
        logger.info("CSV conversion complete!")
        
    finally:
        client.close()


def run_linestring_generation(input_file: Path, output_file: Path, vessel_codes_json: Path = None):
    """
    Aggregate point pings from trajectorized parquet into LineString GeoParquet with optional vessel codes config.
    """
    from shapely.geometry import LineString, MultiLineString
    import numpy as np
    
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
    
    vessel_mapping = {}
    if vessel_codes_json and Path(vessel_codes_json).exists():
        import json
        try:
            logger.info(f"Loading vessel codes mapping from: {vessel_codes_json}...")
            with open(vessel_codes_json, "r") as f:
                data = json.load(f)
                for item in data:
                    code = item.get("vessel_code")
                    group = item.get("vessel_group")
                    if code is not None and group is not None:
                        try:
                            vessel_mapping[int(float(code))] = group
                        except (ValueError, TypeError):
                            if isinstance(code, str) and " to " in code:
                                parts = code.split(" to ")
                                if len(parts) == 2:
                                    try:
                                        start = int(float(parts[0]))
                                        end = int(float(parts[1]))
                                        for c in range(start, end + 1):
                                            vessel_mapping[c] = group
                                    except Exception:
                                        pass
        except Exception as e:
            logger.warning(f"Failed to load vessel codes JSON: {e}")

    def get_vessel_group(shiptype):
        if not shiptype:
            return "Other"
        try:
            code = int(float(shiptype))
        except (ValueError, TypeError):
            return "Other"
            
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
            
    df_attrs['VesselGroup'] = df_attrs['VesselType'].apply(get_vessel_group)
    
    gdf_lines = gpd.GeoDataFrame(df_attrs, geometry=geoms, crs="EPSG:4326")
    
    output_file.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Saving GeoParquet to: {output_file}...")
    gdf_lines.to_parquet(output_file)
    
    logger.info("Line generation complete!")


def run_segment_generation(input_file: Path, output_file: Path, epoch_time: bool = False):
    """
    Generate point-pair line segments from trajectorized point dataset,
    with option to use epoch-normalized timestamps.
    """
    from shapely.geometry import LineString
    import shapely
    import numpy as np
    
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
    
    df_segments = pd.DataFrame({
        'MMSI': p1[vessel_col].values,
        'trip_id': p1['trip_id'].values,
        'VesselType': p1['shiptypeAIS'].values if 'shiptypeAIS' in p1.columns else np.nan,
        'Length': p1['length'].values if 'length' in p1.columns else np.nan,
        'Width': p1['beam'].values if 'beam' in p1.columns else np.nan,
        'Draft': p1['draught'].values if 'draught' in p1.columns else np.nan,
        'segment_start_time': p1[time_col].values,
        'segment_end_time': p2[time_col].values,
        'speed_mps': p1['speed_mps'].values if 'speed_mps' in p1.columns else np.nan,
        'acceleration_mps2': p1['acceleration_mps2'].values if 'acceleration_mps2' in p1.columns else np.nan,
    })
    
    df_segments['segment_duration_s'] = (df_segments['segment_end_time'] - df_segments['segment_start_time']).dt.total_seconds()
    gdf_segments = gpd.GeoDataFrame(df_segments, geometry=geoms, crs="EPSG:4326")
    
    output_file.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Saving segments GeoParquet to: {output_file}...")
    gdf_segments.to_parquet(output_file)
    logger.info("Segment generation complete!")

