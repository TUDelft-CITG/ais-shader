"""
Controlled, minimal reproduction of the tile-boundary "seam" artifact seen in rendered
transit_count/sog/speed_mps bands: a single synthetic line segment crossing a tile edge,
rendered from different directions, checked against a seamless (single wide canvas)
reference render using the exact production render_tile_task code path.
"""
import geopandas as gpd
import morecantile
import numpy as np
import pytest
import shapely
import xarray as xr

import datashader as ds
from ais_shader.renderer import render_tile_task, _canonicalize_line_direction

TILE_SIZE = 128
BORDER = 8
LINE_WIDTH = 1
ZOOM = 10


def _adjacent_tiles():
    tms = morecantile.tms.get("WebMercatorQuad")
    t0 = morecantile.commons.Tile(x=100, y=100, z=ZOOM)
    t1 = morecantile.commons.Tile(x=101, y=100, z=ZOOM)
    b0, b1 = tms.xy_bounds(t0), tms.xy_bounds(t1)
    dx = (b0.right - b0.left) / TILE_SIZE
    dy = (b0.top - b0.bottom) / TILE_SIZE
    return t0, t1, b0, b1, dx, dy


def _config():
    return {
        "visualization": {
            "tile_size": TILE_SIZE,
            "line_width": LINE_WIDTH,
            "bands": ["transit_count"],
        }
    }


def _line_gdf(p0, p1):
    return gpd.GeoDataFrame(
        {"sog": [0.0], "speed_mps": [0.0]},
        geometry=[shapely.LineString([p0, p1])],
        crs="EPSG:3857",
    )


def _render_two_tiles(gdf, tmp_path):
    t0, t1, b0, b1, dx, dy = _adjacent_tiles()
    zarr_dir = tmp_path / "zarr"
    zarr_dir.mkdir(parents=True, exist_ok=True)
    render_tile_task(gdf.copy(), t0, zarr_dir, _config())
    render_tile_task(gdf.copy(), t1, zarr_dir, _config())

    def _load(tile):
        path = zarr_dir / f"tile_{tile.z}_{tile.x}_{tile.y}.zarr"
        if not path.exists():
            return np.zeros((TILE_SIZE, TILE_SIZE))
        return xr.open_zarr(path)["metrics"].sel(band="transit_count").values

    return _load(t0), _load(t1)


def _seamless_reference(gdf):
    """Render the same geometry on one wide canvas spanning both tiles, using the same
    border-expand + crop technique as render_tile_task, as ground truth to compare the
    tiled (per-tile) render against."""
    t0, t1, b0, b1, dx, dy = _adjacent_tiles()
    width = 2 * TILE_SIZE + 2 * BORDER
    height = TILE_SIZE + 2 * BORDER
    cvs = ds.Canvas(
        plot_width=width,
        plot_height=height,
        x_range=(b0.left - BORDER * dx, b1.right + BORDER * dx),
        y_range=(b0.bottom - BORDER * dy, b0.top + BORDER * dy),
    )
    gdf = gdf.copy()
    gdf['geometry'] = gdf.geometry.apply(_canonicalize_line_direction)
    agg = cvs.line(gdf, geometry="geometry", agg=ds.count(), line_width=LINE_WIDTH).fillna(0)
    agg = agg.isel(x=slice(BORDER, BORDER + 2 * TILE_SIZE), y=slice(BORDER, BORDER + TILE_SIZE))
    arr = agg.values[::-1]  # flip so row 0 = top, matching raster convention
    return arr[:, :TILE_SIZE], arr[:, TILE_SIZE:]


def _endpoints(case):
    t0, t1, b0, b1, dx, dy = _adjacent_tiles()
    boundary_x = b0.right
    mid_y = (b0.bottom + b0.top) / 2
    span = 20
    if case == "horizontal":
        p0 = (boundary_x - span * dx, mid_y)
        p1 = (boundary_x + span * dx, mid_y)
    elif case == "diagonal_ne":
        p0 = (boundary_x - span * dx, mid_y - span * dy)
        p1 = (boundary_x + span * dx, mid_y + span * dy)
    elif case == "diagonal_se":
        p0 = (boundary_x - span * dx, mid_y + span * dy)
        p1 = (boundary_x + span * dx, mid_y - span * dy)
    elif case == "vertical_on_boundary":
        p0 = (boundary_x, mid_y - span * dy)
        p1 = (boundary_x, mid_y + span * dy)
    else:
        raise ValueError(case)
    return p0, p1


CASES = ["horizontal", "diagonal_ne", "diagonal_se", "vertical_on_boundary"]


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reversed"])
def test_line_crossing_boundary_matches_seamless_render(tmp_path, case, reverse):
    """A line crossing the tile boundary, rendered as two independent tiles and
    stitched, should match a single seamless render of the same region -- regardless
    of which endpoint is listed first (line digitization direction)."""
    p0, p1 = _endpoints(case)
    if reverse:
        p0, p1 = p1, p0
    gdf = _line_gdf(p0, p1)

    tile0, tile1 = _render_two_tiles(gdf, tmp_path)
    ref0, ref1 = _seamless_reference(gdf)

    stitched_tiled = np.concatenate([tile0, tile1], axis=1)
    stitched_ref = np.concatenate([ref0, ref1], axis=1)

    np.testing.assert_allclose(stitched_tiled, stitched_ref, atol=1e-5)


@pytest.mark.parametrize("case", CASES)
def test_line_direction_does_not_change_tiled_render(tmp_path, case):
    """Digitization direction (which endpoint is 'start' vs 'end') must not affect the
    rendered transit_count: it's the same physical line either way."""
    p0, p1 = _endpoints(case)
    gdf_fwd = _line_gdf(p0, p1)
    gdf_rev = _line_gdf(p1, p0)

    fwd0, fwd1 = _render_two_tiles(gdf_fwd, tmp_path / "fwd")
    rev0, rev1 = _render_two_tiles(gdf_rev, tmp_path / "rev")

    stitched_fwd = np.concatenate([fwd0, fwd1], axis=1)
    stitched_rev = np.concatenate([rev0, rev1], axis=1)

    np.testing.assert_allclose(stitched_fwd, stitched_rev, atol=1e-5)
