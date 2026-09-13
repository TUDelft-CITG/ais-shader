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


def test_noaa_csv_conversion(tmp_path):
    csv_file = tmp_path / "noaa_test.csv"
    parquet_file = tmp_path / "noaa_test.parquet"

    content = (
        "mmsi,base_date_time,longitude,latitude,sog,cog,heading,vessel_name,imo,call_sign,vessel_type,status,length,width,draft,cargo,transceiver\n"
        "366996430,2026-03-31 00:00:00,-82.55641,38.40362,0.1,295.2,100,CAPT JEFF IRBY,,WDK7088,31,12,20,8,3.3,,A\n"
        "368435310,2026-03-31 00:00:00,-80.11748,40.51168,5.0,180.0,180,DANI Z,,WDQ5513,52,0,15,6,,52,A\n"
    )

    with open(csv_file, "w") as f:
        f.write(content)

    run_csv_conversion(csv_file, parquet_file, scheduler=None)

    assert parquet_file.exists()
    gdf = gpd.read_parquet(parquet_file)

    assert len(gdf) == 2
    assert "mmsi" in gdf.columns
    assert "geometry" in gdf.columns
    assert "shiptypeAIS" in gdf.columns
    assert "beam" in gdf.columns
    assert "length" in gdf.columns
    assert "draught" in gdf.columns
    assert gdf.crs == "EPSG:4326"

    row1 = gdf.iloc[0]
    assert row1["mmsi"] == 366996430
    assert abs(row1["longitude"] - (-82.55641)) < 1e-5
    assert abs(row1["latitude"] - 38.40362) < 1e-5
    assert row1["sog"] == 0.1
    assert row1["cog"] == 295.2
    assert row1["beam"] == 8.0
    assert row1["length"] == 20.0
    assert row1["draught"] == 3.3
    assert int(row1["shiptypeAIS"]) == 31


def test_noaa_csv_conversion_with_bbox(tmp_path):
    csv_file = tmp_path / "noaa_bbox.csv"
    parquet_file = tmp_path / "noaa_bbox.parquet"

    # Row 1 is in Ohio (lon -82.5, lat 38.4)
    # Row 2 is in Pennsylvania (lon -80.1, lat 40.5)
    content = (
        "mmsi,base_date_time,longitude,latitude,sog,cog,heading,vessel_name,imo,call_sign,vessel_type,status,length,width,draft,cargo,transceiver\n"
        "366996430,2026-03-31 00:00:00,-82.55641,38.40362,0.1,295.2,100,CAPT JEFF IRBY,,WDK7088,31,12,20,8,3.3,,A\n"
        "368435310,2026-03-31 00:00:00,-80.11748,40.51168,5.0,180.0,180,DANI Z,,WDQ5513,52,0,15,6,,52,A\n"
    )

    with open(csv_file, "w") as f:
        f.write(content)

    # Filter with bbox covering only Row 1 (-83 to -81, 38 to 39)
    run_csv_conversion(csv_file, parquet_file, scheduler=None, bbox="-83.0,38.0,-81.0,39.0")

    assert parquet_file.exists()
    gdf = gpd.read_parquet(parquet_file)
    assert len(gdf) == 1
    assert gdf.iloc[0]["mmsi"] == 366996430

    # Test invalid bbox fails fast
    with pytest.raises(ValueError, match="Invalid bbox format"):
        run_csv_conversion(csv_file, tmp_path / "fail.parquet", scheduler=None, bbox="-83.0,38.0")


