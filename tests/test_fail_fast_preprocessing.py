"""
Regression tests for the "fail fast, don't swallow" review comments on
build_vessel_mapping and run_segment_generation: prior versions silently
skipped malformed vessel-code entries, silently defaulted to "Other" when
'shiptypeAIS' was missing, and (before this session's earlier fix) always
assumed 'sog' was present. These now raise clearly instead.
"""
import json

import geopandas as gpd
import pandas as pd
import pytest
import shapely

from ais_shader.preprocessing import build_vessel_mapping, run_segment_generation


def _write_json(tmp_path, data):
    path = tmp_path / "vessel_codes.json"
    path.write_text(json.dumps(data))
    return path


def test_build_vessel_mapping_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_vessel_mapping(tmp_path / "does_not_exist.json")


def test_build_vessel_mapping_missing_key_raises(tmp_path):
    path = _write_json(tmp_path, [{"vessel_code": 80}])  # missing vessel_group
    with pytest.raises(ValueError, match="vessel_code.*vessel_group"):
        build_vessel_mapping(path)


def test_build_vessel_mapping_malformed_range_raises(tmp_path):
    path = _write_json(tmp_path, [{"vessel_code": "70 to 79 to 89", "vessel_group": "Cargo"}])
    with pytest.raises(ValueError, match="range"):
        build_vessel_mapping(path)


def test_build_vessel_mapping_unrecognized_code_type_raises(tmp_path):
    path = _write_json(tmp_path, [{"vessel_code": [1, 2, 3], "vessel_group": "Cargo"}])
    with pytest.raises(ValueError, match="Unrecognized"):
        build_vessel_mapping(path)


def test_build_vessel_mapping_valid_entries(tmp_path):
    path_data = [
        {"vessel_code": 80, "vessel_group": "Tanker"},
        {"vessel_code": "70 to 72", "vessel_group": "Cargo"},
        {"vessel_code": "HSC", "vessel_group": "Passenger"},
    ]
    path = _write_json(tmp_path, path_data)
    mapping = build_vessel_mapping(path)
    assert mapping[80] == "Tanker"
    assert mapping[70] == mapping[71] == mapping[72] == "Cargo"
    assert mapping["hsc"] == "Passenger"


def test_build_vessel_mapping_no_path_returns_empty():
    assert build_vessel_mapping(None) == {}


def _make_trajectorized_gdf(*, with_shiptype=True, with_sog=True):
    times = pd.date_range("2024-01-01", periods=3, freq="min")
    data = {
        "mmsi": [1, 1, 1],
        "trip_id": ["1_1", "1_1", "1_1"],
        "base_date_time": times,
        "longitude": [4.50, 4.51, 4.52],
        "latitude": [51.9, 51.9, 51.9],
        "speed_mps": [1.0, 2.0, 3.0],
    }
    if with_shiptype:
        data["shiptypeAIS"] = [70, 70, 70]
    if with_sog:
        data["sog"] = [5.0, 6.0, 7.0]
    return gpd.GeoDataFrame(
        data,
        geometry=gpd.points_from_xy(data["longitude"], data["latitude"]),
        crs="EPSG:4326",
    )


def test_run_segment_generation_missing_shiptype_raises(tmp_path):
    gdf = _make_trajectorized_gdf(with_shiptype=False)
    input_file = tmp_path / "trajectorized.geoparquet"
    gdf.to_parquet(input_file)
    with pytest.raises(KeyError, match="shiptypeAIS"):
        run_segment_generation(input_file, tmp_path / "segments.geoparquet", sog_raw_units=False)


def test_run_segment_generation_missing_sog_raises(tmp_path):
    gdf = _make_trajectorized_gdf(with_sog=False)
    input_file = tmp_path / "trajectorized.geoparquet"
    gdf.to_parquet(input_file)
    with pytest.raises(KeyError, match="sog"):
        run_segment_generation(input_file, tmp_path / "segments.geoparquet", sog_raw_units=False)


def test_run_segment_generation_succeeds_with_required_columns(tmp_path):
    gdf = _make_trajectorized_gdf()
    input_file = tmp_path / "trajectorized.geoparquet"
    gdf.to_parquet(input_file)
    output_file = tmp_path / "segments.geoparquet"
    run_segment_generation(input_file, output_file, sog_raw_units=False)
    result = gpd.read_parquet(output_file)
    assert len(result) == 2  # 3 points -> 2 point-pair segments
    assert "VesselGroup" in result.columns
