"""
Regression test: pyramid coarsening (aggregate_children, postprocessing.py)
used to average sog/speed_mps bands unweighted across 2x2 child pixels. That
is mathematically wrong when the child pixels have very different sample
counts (transit_count) -- a heavily-transited pixel and a barely-transited
pixel should not contribute equally to the coarsened mean.
"""
import numpy as np
import xarray as xr
import pytest

from ais_shader.postprocessing import aggregate_children, _matching_count_band


def test_matching_count_band_total():
    assert _matching_count_band("sog", ["transit_count", "sog"]) == "transit_count"


def test_matching_count_band_per_group():
    assert _matching_count_band("sog__Cargo", ["transit_count__Cargo", "sog__Cargo"]) == "transit_count__Cargo"


def test_matching_count_band_missing_returns_none():
    assert _matching_count_band("sog__Cargo", ["sog__Cargo"]) is None


def test_pyramid_mean_is_weighted_by_transit_count(tmp_path):
    # Two child tiles at the same parent quadrant position aren't possible in
    # aggregate_children's real usage (each quadrant is one child), but the
    # weighting bug is visible within a single child: half its pixels heavily
    # transited at a high sog, half barely transited at a low sog. An
    # unweighted 2x2-block mean would treat both halves equally; a weighted
    # mean should be dominated by the heavily-transited half.
    heavy_count, heavy_sog = 100.0, 20.0
    light_count, light_sog = 1.0, 2.0

    count_arr = np.full((1024, 1024), light_count, dtype="float32")
    sog_arr = np.full((1024, 1024), light_sog, dtype="float32")
    # Make the top-left pixel of a representative 2x2 block heavily transited.
    count_arr[0, 0] = heavy_count
    sog_arr[0, 0] = heavy_sog

    y = np.arange(1024)
    x = np.arange(1024)
    da = xr.concat(
        [
            xr.DataArray(count_arr, dims=["y", "x"], coords={"y": y, "x": x}).expand_dims(band=["transit_count"]),
            xr.DataArray(sog_arr, dims=["y", "x"], coords={"y": y, "x": x}).expand_dims(band=["sog"]),
        ],
        dim="band",
    )
    da.name = "metrics"
    child_path = tmp_path / "tile_10_0_0.zarr"
    da.to_dataset(name="metrics").to_zarr(child_path, mode="w", consolidated=True)

    parent = aggregate_children((9, 0, 0), [child_path], var_name="metrics")

    # cx=cy=0 (both even) places this child in the bottom-left quadrant
    # (y=512:1024, x=0:512) of the 1024x1024 parent -- see the is_right/
    # is_bottom quadrant logic in aggregate_children.
    coarsened_sog_block = parent.sel(band="sog").values[512, 0]
    coarsened_count_block = parent.sel(band="transit_count").values[512, 0]

    expected_count = heavy_count + 3 * light_count
    expected_weighted_sog = (heavy_count * heavy_sog + 3 * light_count * light_sog) / expected_count
    unweighted_sog = (heavy_sog + 3 * light_sog) / 4

    assert coarsened_count_block == pytest.approx(expected_count)
    assert coarsened_sog_block == pytest.approx(expected_weighted_sog, rel=1e-4)
    assert coarsened_sog_block != pytest.approx(unweighted_sog, rel=1e-2)
