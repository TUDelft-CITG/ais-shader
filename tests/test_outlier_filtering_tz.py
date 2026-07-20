"""
Regression test: run_outlier_filtering used to crash on tz-aware timestamps.

`gdf[time_col].values.astype('datetime64[s]')` returns an object array of
Timestamps (not numpy datetime64) when the column is tz-aware, and the
subsequent `.astype('float64')` raises. add_hilbert_index (moving_dask/
trajectory.py) already handled this by stripping tz first; run_outlier_filtering
did not.
"""
import geopandas as gpd
import pandas as pd
import pytest

from ais_shader.preprocessing import run_outlier_filtering, strip_tz_and_epoch_seconds


def _make_points_gdf(tz):
    times = pd.date_range("2024-01-01", periods=5, freq="min", tz=tz)
    lons = [4.500, 4.501, 4.502, 4.503, 4.504]
    lats = [51.900, 51.900, 51.900, 51.900, 51.900]
    return gpd.GeoDataFrame(
        {
            "mmsi": [1] * 5,
            "base_date_time": times,
            "longitude": lons,
            "latitude": lats,
        },
        geometry=gpd.points_from_xy(lons, lats),
        crs="EPSG:4326",
    )


def test_strip_tz_and_epoch_seconds_handles_tz_aware():
    series = pd.Series(pd.date_range("2024-01-01", periods=3, freq="min", tz="UTC"))
    result = strip_tz_and_epoch_seconds(series)
    assert result[1] - result[0] == pytest.approx(60.0)


def test_strip_tz_and_epoch_seconds_handles_naive():
    series = pd.Series(pd.date_range("2024-01-01", periods=3, freq="min"))
    result = strip_tz_and_epoch_seconds(series)
    assert result[1] - result[0] == pytest.approx(60.0)


def test_run_outlier_filtering_does_not_crash_on_tz_aware_input(tmp_path):
    gdf = _make_points_gdf(tz="UTC")
    input_file = tmp_path / "points.geoparquet"
    output_file = tmp_path / "cleaned.geoparquet"
    gdf.to_parquet(input_file)

    run_outlier_filtering(input_file, output_file)

    result = gpd.read_parquet(output_file)
    assert len(result) == 5  # no genuine outliers in this fixture


def test_run_outlier_filtering_matches_naive_input(tmp_path):
    gdf_tz = _make_points_gdf(tz="UTC")
    gdf_naive = _make_points_gdf(tz=None)

    for name, gdf in [("tz", gdf_tz), ("naive", gdf_naive)]:
        input_file = tmp_path / f"points_{name}.geoparquet"
        output_file = tmp_path / f"cleaned_{name}.geoparquet"
        gdf.to_parquet(input_file)
        run_outlier_filtering(input_file, output_file)

    tz_result = gpd.read_parquet(tmp_path / "cleaned_tz.geoparquet")
    naive_result = gpd.read_parquet(tmp_path / "cleaned_naive.geoparquet")
    assert len(tz_result) == len(naive_result)
