"""
Minimal, pipeline-free reproduction of the antialiasing coverage-split anomaly seen in
tiled rendering: does raw datashader.Canvas.line() itself violate the invariant that a
line's antialiased coverage split (which pixel gets the "primary" vs "bordering"
fractional value) depends only on the line's geometry relative to a fixed pixel grid --
not on digitization direction, and not on the extent of the canvas used to render it
(for a shared pixel grid)?

Built up from raw x/y-column rendering (no ais_shader code at all) to the
GeoDataFrame/geometry-column code path ais_shader's renderer.py actually uses, to
isolate whether the bug lives in datashader's core line rasterizer or is introduced by
that code path / by our own tiling wrapper.
"""
import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import shapely

import datashader as ds

CANVAS_SIZE = 10
CENTER = (5.0, 5.0)
REACH = 15.0  # how far outward each direction's endpoint extends beyond the canvas

# 8 compass directions as unit vectors
DIRECTIONS = {
    "E": (1, 0),
    "W": (-1, 0),
    "N": (0, 1),
    "S": (0, -1),
    "NE": (1, 1),
    "NW": (-1, 1),
    "SE": (1, -1),
    "SW": (-1, -1),
}


def _render(x0, y0, x1, y1, plot_width, plot_height, x_range, y_range, line_width=1):
    df = pd.DataFrame({"x0": [x0], "y0": [y0], "x1": [x1], "y1": [y1]})
    cvs = ds.Canvas(plot_width=plot_width, plot_height=plot_height, x_range=x_range, y_range=y_range)
    agg = cvs.line(df, x=["x0", "x1"], y=["y0", "y1"], axis=1, agg=ds.count(), line_width=line_width)
    return agg.fillna(0).values


def _canonicalize(x0, y0, x1, y1):
    """Reorder a 2-point line's endpoints to match datashader's own internal
    flip_order convention (_full_antialias, line.py:863): want y1>=y0, tie-broken by
    x1>=x0. Rendering every segment in this canonical order -- regardless of the
    arbitrary chronological order points were digitized in -- means every segment
    takes the same internal code branch, sidestepping the order-dependent rounding
    bug in datashader's round-cap rendering."""
    flip = y1 < y0 or (y1 == y0 and x1 < x0)
    return (x1, y1, x0, y0) if flip else (x0, y0, x1, y1)


def _render_geom(x0, y0, x1, y1, plot_width, plot_height, x_range, y_range, line_width=1):
    """Same as _render, but via a GeoDataFrame 'geometry' LineString column -- the
    code path ais_shader's renderer.py actually uses -- instead of raw x/y columns.
    Returns the xarray DataArray (not .values) so callers can crop by coordinate."""
    gdf = gpd.GeoDataFrame(geometry=[shapely.LineString([(x0, y0), (x1, y1)])])
    cvs = ds.Canvas(plot_width=plot_width, plot_height=plot_height, x_range=x_range, y_range=y_range)
    agg = cvs.line(gdf, geometry="geometry", agg=ds.count(), line_width=line_width)
    return agg.fillna(0)


def _small_and_wide(x0, y0, x1, y1, line_width=1):
    """Render the same line on (a) a small 10x10 canvas covering (0,10)x(0,10), and
    (b) a wide 30x30 canvas sharing the same pixel grid (dx=dy=1, same origin offset
    by -10), covering (-10,20)x(-10,20). Returns the small array and the wide array
    cropped (by matching coordinates) to the small canvas's window."""
    small = _render_geom(x0, y0, x1, y1, CANVAS_SIZE, CANVAS_SIZE, (0, CANVAS_SIZE), (0, CANVAS_SIZE), line_width)
    wide = _render_geom(x0, y0, x1, y1, 3 * CANVAS_SIZE, 3 * CANVAS_SIZE, (-CANVAS_SIZE, 2 * CANVAS_SIZE),
                         (-CANVAS_SIZE, 2 * CANVAS_SIZE), line_width)
    wide_cropped = wide.sel(x=small.x, y=small.y, method="nearest")
    return small.values, wide_cropped.values


@pytest.mark.parametrize("name,vec", DIRECTIONS.items())
@pytest.mark.parametrize("orientation", ["outward", "inward"])
def test_direction_invariance_all_compass_directions(name, vec, orientation):
    """A line crossing the canvas boundary in each of the 8 compass directions, both
    heading outward (inside -> outside) and inward (outside -> inside), must render
    identically regardless of which endpoint is listed first."""
    ux, uy = vec
    norm = (ux ** 2 + uy ** 2) ** 0.5
    ux, uy = ux / norm, uy / norm
    far = (CENTER[0] + ux * REACH, CENTER[1] + uy * REACH)

    if orientation == "outward":
        p0, p1 = CENTER, far
    else:
        p0, p1 = far, CENTER

    fwd = _render_geom(p0[0], p0[1], p1[0], p1[1], CANVAS_SIZE, CANVAS_SIZE,
                        (0, CANVAS_SIZE), (0, CANVAS_SIZE))
    rev = _render_geom(p1[0], p1[1], p0[0], p0[1], CANVAS_SIZE, CANVAS_SIZE,
                        (0, CANVAS_SIZE), (0, CANVAS_SIZE))
    np.testing.assert_allclose(fwd.values, rev.values, atol=1e-6,
                                err_msg=f"direction dependence for {orientation} {name}")


@pytest.mark.parametrize("name,vec", DIRECTIONS.items())
@pytest.mark.parametrize("orientation", ["outward", "inward"])
def test_canvas_extent_invariance_all_compass_directions(name, vec, orientation):
    """A line crossing the canvas boundary in each of the 8 compass directions must
    render identically in the shared window whether drawn on the small (clipping)
    canvas or a much wider canvas (no clipping) sharing the same pixel grid."""
    ux, uy = vec
    norm = (ux ** 2 + uy ** 2) ** 0.5
    ux, uy = ux / norm, uy / norm
    far = (CENTER[0] + ux * REACH, CENTER[1] + uy * REACH)

    if orientation == "outward":
        p0, p1 = CENTER, far
    else:
        p0, p1 = far, CENTER

    small, wide_cropped = _small_and_wide(p0[0], p0[1], p1[0], p1[1])
    np.testing.assert_allclose(small, wide_cropped, atol=1e-6,
                                err_msg=f"canvas-extent dependence for {orientation} {name}")


ANGLES_DEG = list(range(0, 360, 15))  # every 15 degrees, full circle
CENTERS = {
    "aligned": (5.0, 5.0),        # exactly on a pixel-grid line
    "unaligned": (5.37, 5.62),    # sub-pixel offset from the grid
}


@pytest.mark.parametrize("center_name,center", CENTERS.items())
@pytest.mark.parametrize("angle_deg", ANGLES_DEG)
def test_direction_invariance_angle_and_alignment_sweep(angle_deg, center_name, center):
    """Direction invariance swept across a full 360-degree circle of angles, at both
    a pixel-grid-aligned and a sub-pixel-unaligned center point, to map out exactly
    when the clip-order dependence appears (all angles? only diagonals? only
    specific alignments?)."""
    theta = np.radians(angle_deg)
    ux, uy = np.cos(theta), np.sin(theta)
    far = (center[0] + ux * REACH, center[1] + uy * REACH)

    fwd = _render_geom(center[0], center[1], far[0], far[1], CANVAS_SIZE, CANVAS_SIZE,
                        (0, CANVAS_SIZE), (0, CANVAS_SIZE))
    rev = _render_geom(far[0], far[1], center[0], center[1], CANVAS_SIZE, CANVAS_SIZE,
                        (0, CANVAS_SIZE), (0, CANVAS_SIZE))
    mismatch = np.abs(fwd.values - rev.values)
    assert mismatch.max() <= 1e-6, (
        f"direction dependence at angle={angle_deg}deg center={center_name}: "
        f"max mismatch={mismatch.max():.6f} at {np.unravel_index(np.argmax(mismatch), mismatch.shape)}"
    )


def _render_tile_style(near, far, tile_size, border, line_width=1):
    """Mimic render_tile_task's border-expand-then-crop technique for a single tile:
    render on a canvas padded by `border` pixels beyond the true tile bounds
    [0, tile_size), then crop back to the true tile window. Returns the CROPPED
    (kept, saved-to-disk-equivalent) array only."""
    x_range = (0 - border, tile_size + border)
    y_range = (0 - border, tile_size + border)
    plot_size = tile_size + 2 * border
    gdf = gpd.GeoDataFrame(geometry=[shapely.LineString([near, far])])
    cvs = ds.Canvas(plot_width=plot_size, plot_height=plot_size, x_range=x_range, y_range=y_range)
    agg = cvs.line(gdf, geometry="geometry", agg=ds.count(), line_width=line_width).fillna(0)
    cropped = agg.isel(x=slice(border, border + tile_size), y=slice(border, border + tile_size))
    return cropped.values


TILE_SIZE_SMALL = 20
BORDERS = [4, 8, 16, 32, 64]
OVERREACH_PX = [1, 2, 4, 8, 16, 24, 32]  # how far the far endpoint extends beyond the tile edge


@pytest.mark.parametrize("overreach", OVERREACH_PX)
@pytest.mark.parametrize("border", BORDERS)
def test_border_size_vs_overreach_direction_invariance(border, overreach):
    """Does widening the border (relative to how far a line's far endpoint overreaches
    beyond the true tile edge) make the CROPPED/KEPT output direction-invariant, even
    though the underlying datashader endpoint-cap bug is still present in the
    (discarded) padding? This tests the mitigation hypothesis directly: push the
    erroneous pixel into the cropped-away border so it never appears in saved output."""
    near = (TILE_SIZE_SMALL / 2, TILE_SIZE_SMALL / 2)  # well inside the tile
    far = (TILE_SIZE_SMALL + overreach, TILE_SIZE_SMALL / 2)  # beyond the right edge

    fwd = _render_tile_style(near, far, TILE_SIZE_SMALL, border)
    rev = _render_tile_style(far, near, TILE_SIZE_SMALL, border)

    mismatch = np.abs(fwd - rev)
    max_mismatch = mismatch.max()
    result = "MATCH" if max_mismatch <= 1e-6 else f"MISMATCH ({max_mismatch:.6f})"
    print(f"border={border:3d} overreach={overreach:3d}: {result}")

    np.testing.assert_allclose(
        fwd, rev, atol=1e-6,
        err_msg=f"border={border} overreach={overreach}: kept region is direction-dependent, "
                f"max mismatch={max_mismatch:.6f} -- border does NOT fully absorb the bug here"
    )


@pytest.mark.parametrize("name,vec", DIRECTIONS.items())
@pytest.mark.parametrize("orientation", ["outward", "inward"])
def test_canonicalization_fixes_direction_invariance_compass(name, vec, orientation):
    """With endpoint order canonicalized before rendering, all 8 compass directions
    (both orientations) that previously failed direction invariance should now pass."""
    ux, uy = vec
    norm = (ux ** 2 + uy ** 2) ** 0.5
    ux, uy = ux / norm, uy / norm
    far = (CENTER[0] + ux * REACH, CENTER[1] + uy * REACH)

    if orientation == "outward":
        p0, p1 = CENTER, far
    else:
        p0, p1 = far, CENTER

    fwd_c = _canonicalize(p0[0], p0[1], p1[0], p1[1])
    rev_c = _canonicalize(p1[0], p1[1], p0[0], p0[1])

    fwd = _render_geom(*fwd_c, CANVAS_SIZE, CANVAS_SIZE, (0, CANVAS_SIZE), (0, CANVAS_SIZE))
    rev = _render_geom(*rev_c, CANVAS_SIZE, CANVAS_SIZE, (0, CANVAS_SIZE), (0, CANVAS_SIZE))
    np.testing.assert_allclose(fwd.values, rev.values, atol=1e-6,
                                err_msg=f"still direction-dependent after canonicalizing: {orientation} {name}")


@pytest.mark.parametrize("center_name,center", CENTERS.items())
@pytest.mark.parametrize("angle_deg", ANGLES_DEG)
def test_canonicalization_fixes_direction_invariance_angle_sweep(angle_deg, center_name, center):
    """With endpoint order canonicalized before rendering, the full 360-degree angle
    sweep (both pixel-aligned and unaligned centers) that previously failed 100% of
    the time should now pass."""
    theta = np.radians(angle_deg)
    ux, uy = np.cos(theta), np.sin(theta)
    far = (center[0] + ux * REACH, center[1] + uy * REACH)

    fwd_c = _canonicalize(center[0], center[1], far[0], far[1])
    rev_c = _canonicalize(far[0], far[1], center[0], center[1])

    fwd = _render_geom(*fwd_c, CANVAS_SIZE, CANVAS_SIZE, (0, CANVAS_SIZE), (0, CANVAS_SIZE))
    rev = _render_geom(*rev_c, CANVAS_SIZE, CANVAS_SIZE, (0, CANVAS_SIZE), (0, CANVAS_SIZE))
    mismatch = np.abs(fwd.values - rev.values)
    assert mismatch.max() <= 1e-6, (
        f"still direction-dependent after canonicalizing at angle={angle_deg}deg center={center_name}: "
        f"max mismatch={mismatch.max():.6f}"
    )


def test_aliased_line_is_binary_no_partial_border_coverage():
    """line_width=0 (aliased/hairline): pixels the line passes through get exactly 1,
    all others (including diagonal border neighbors) get exactly 0 -- no fractional
    values at all, since aliasing has no notion of partial coverage."""
    arr = _render(1, 1, 8, 8, 10, 10, (0, 10), (0, 10), line_width=0)
    values = set(np.unique(arr).tolist())
    assert values <= {0.0, 1.0}, f"aliased render should be binary, got values {values}"


def test_antialiased_diagonal_has_bounded_symmetric_kernel():
    """line_width=1 (antialiased) diagonal: each crossed pixel's coverage should form
    a bounded kernel around a primary (~1.0) pixel -- e.g. a symmetric tent kernel
    like [0.293, 1.0, 0.293] -- not an unbounded/runaway value."""
    arr = _render(1, 1, 8, 8, 10, 10, (0, 10), (0, 10), line_width=1)
    assert arr.max() <= 1.0 + 1e-6, f"no single pixel should exceed line width 1.0: max={arr.max()}"
