"""
Regression test: renderer.py used to only apply the configured line_width to
the transit_count band; sog/speed_mps bands and value_column-based
aggregations (mean/max) always rendered at datashader's default (effectively
single-pixel/aliased) line width, producing inconsistent stroke widths across
bands of the same tile.
"""
import tempfile
from pathlib import Path

import geopandas as gpd
import morecantile
import shapely
import xarray as xr

from ais_shader.renderer import render_tile_task


def _diagonal_line_gdf():
    tms = morecantile.tms.get("WebMercatorQuad")
    tile = morecantile.commons.Tile(x=536, y=343, z=10)
    bbox = tms.xy_bounds(tile)
    cx, cy = (bbox.left + bbox.right) / 2, (bbox.bottom + bbox.top) / 2
    span = 400
    line = shapely.LineString([(cx - span, cy - span), (cx + span, cy + span)])
    return gpd.GeoDataFrame({"sog": [10.0], "speed_mps": [5.0]}, geometry=[line], crs="EPSG:3857"), tile


def _render(config):
    gdf, tile = _diagonal_line_gdf()
    with tempfile.TemporaryDirectory() as tmp:
        zarr_dir = Path(tmp)
        render_tile_task(gdf.copy(), tile, zarr_dir, config)
        path = zarr_dir / f"tile_{tile.z}_{tile.x}_{tile.y}.zarr"
        ds = xr.open_zarr(path)
        data_var_names = [v for v in ds.data_vars if v != "spatial_ref"]
        var_name = data_var_names[0]
        return ds[var_name].load()


def _nonzero_pixel_count(da, band):
    return int((da.sel(band=band).values != 0).sum())


def test_sog_band_footprint_matches_transit_count_footprint():
    config = {"visualization": {"tile_size": 256, "line_width": 4, "bands": ["transit_count", "sog"]}}
    da = _render(config)
    count_px = _nonzero_pixel_count(da, "transit_count")
    sog_px = _nonzero_pixel_count(da, "sog")
    assert count_px > 0
    assert sog_px == count_px


def test_wider_line_width_widens_sog_band_footprint_too():
    thin = _render({"visualization": {"tile_size": 256, "line_width": 1, "bands": ["transit_count", "sog"]}})
    thick = _render({"visualization": {"tile_size": 256, "line_width": 9, "bands": ["transit_count", "sog"]}})
    thin_sog_px = _nonzero_pixel_count(thin, "sog")
    thick_sog_px = _nonzero_pixel_count(thick, "sog")
    assert thick_sog_px > thin_sog_px * 2


def test_value_column_mean_aggregation_respects_line_width():
    config_thin = {"visualization": {"tile_size": 256, "line_width": 1, "value_column": "sog", "aggregation": "mean"}}
    config_thick = {"visualization": {"tile_size": 256, "line_width": 9, "value_column": "sog", "aggregation": "mean"}}
    thin = _render(config_thin)
    thick = _render(config_thick)
    thin_px = int((thin.values != 0).sum())
    thick_px = int((thick.values != 0).sum())
    assert thick_px > thin_px * 2
