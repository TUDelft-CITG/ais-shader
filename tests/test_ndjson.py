import json
import pytest
import pandas as pd
import geopandas as gpd
from pathlib import Path
from ais_shader.preprocessing import run_ndjson_conversion

def test_ndjson_conversion(tmp_path):
    # Create a small dummy NDJSON file
    ndjson_file = tmp_path / "test.ndjson"
    parquet_file = tmp_path / "test.parquet"
    
    records = [
        {
            "id": "1",
            "track_id": "vessel-1",
            "timestamp": "2026-07-05T12:00:00Z",
            "longitude": {"value": 4.5},
            "latitude": {"value": 52.1},
            "cog": {"value": 180.0},
            "sog": {"value": 12.5},
            "heading": {"code": "179"},
            "beam": {"value": 15.0},
            "length": {"value": 100.0},
            "draught": {"value": 5.5},
            "status": {"code": "3"},
            "shiptypeAIS": {"code": "70"}
        },
        {
            "id": "2",
            "track_id": "vessel-2",
            "timestamp": "2026-07-05T12:05:00Z",
            "longitude": {"value": 4.6},
            "latitude": {"value": 52.2},
            "cog": {"code": "1200"},
            "sog": None,
            "heading": None,
            "beam": None,
            "length": None,
            "draught": None,
            "status": None,
            "shiptypeAIS": None
        }
    ]
    
    with open(ndjson_file, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
            
    # Run the conversion (None scheduler to run locally)
    run_ndjson_conversion(ndjson_file, parquet_file, scheduler=None)
    
    # Read back and verify
    assert parquet_file.exists()
    gdf = gpd.read_parquet(parquet_file)
    
    assert len(gdf) == 2
    assert "mmsi" in gdf.columns
    assert "geometry" in gdf.columns
    assert gdf.crs == "EPSG:4326"
    
    # Row 1 check
    row1 = gdf.iloc[0]
    assert row1["mmsi"] == "vessel-1"
    assert row1["longitude"] == 4.5
    assert row1["latitude"] == 52.1
    assert row1["cog"] == 180.0
    assert row1["sog"] == 12.5
    assert row1["heading"] == 179.0
    assert row1["beam"] == 15.0
    assert row1["length"] == 100.0
    assert row1["draught"] == 5.5
    assert row1["status"] == "3"
    assert row1["shiptypeAIS"] == "70"
    
    # Row 2 check
    row2 = gdf.iloc[1]
    assert row2["mmsi"] == "vessel-2"
    assert row2["longitude"] == 4.6
    assert row2["latitude"] == 52.2
    assert row2["cog"] == 1200.0
    assert pd.isna(row2["sog"])
    assert pd.isna(row2["heading"])
