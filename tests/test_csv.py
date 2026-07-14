import pytest
import pandas as pd
import geopandas as gpd
from pathlib import Path
from ais_shader.preprocessing import run_csv_conversion

def test_csv_conversion(tmp_path):
    csv_file = tmp_path / "test.csv"
    parquet_file = tmp_path / "test.parquet"
    
    content = (
        "# Timestamp,Type of mobile,MMSI,Latitude,Longitude,Navigational status,ROT,SOG,COG,Heading,IMO,Callsign,Name,Ship type,Cargo type,Width,Length,Type of position fixing device,Draught,Destination,ETA,Data source type,A,B,C,D\n"
        "11/07/2026 00:00:00,Class A,219000431,54.654183,11.350683,Under way using engine,-1.1,0.0,9.3,31,Unknown,Unknown,,Undefined,,,,Undefined,,Unknown,,AIS,,,,\n"
        "11/07/2026 00:00:01,Class A,305411000,57.874073,9.711152,Under way using engine,0.0,15.9,265.0,265,Unknown,Unknown,,Undefined,,,,Undefined,,Unknown,,AIS,,,,\n"
    )
    
    with open(csv_file, "w") as f:
        f.write(content)
        
    run_csv_conversion(csv_file, parquet_file, scheduler=None)
    
    assert parquet_file.exists()
    gdf = gpd.read_parquet(parquet_file)
    
    assert len(gdf) == 2
    assert "mmsi" in gdf.columns
    assert "geometry" in gdf.columns
    assert gdf.crs == "EPSG:4326"
    
    row1 = gdf.iloc[0]
    assert row1["mmsi"] == 219000431
    assert row1["longitude"] == 11.350683
    assert row1["latitude"] == 54.654183
    assert row1["cog"] == 9.3
    assert row1["sog"] == 0.0
    assert row1["heading"] == 31.0
