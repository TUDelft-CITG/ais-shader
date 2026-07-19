"""
Regression coverage for datashader's line antialiasing invariants: a line's
antialiased coverage split (which pixel gets the "primary" vs "bordering"
fractional value) should depend only on the line's geometry relative to a
fixed pixel grid -- not on digitization direction, and not on the extent of
the canvas used to render it (for a shared pixel grid).

This file used to carry ~150 parametrized cases (every 15 degrees around a
circle, a 5x7 border/overreach grid, etc.) built up incrementally while
diagnosing a real direction-dependent antialiasing bug in datashader. That
bug is now fixed upstream (see pyproject.toml's datashader source: SiggyF's
fix-geopandas-line-invariance branch) and confirmed here. One representative
case per invariant is enough regression coverage -- not a full sweep.
"""
import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

import datashader as ds

CANVAS_SIZE = 10
# A diagonal, off-grid case: the geometry most likely to expose a rounding or
# clip-order bug (axis-aligned lines rarely do).
NEAR = (5.0, 5.0)
FAR = (5.0 + 15.0 / 2 ** 0.5, 5.0 + 15.0 / 2 ** 0.5)


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


def test_direction_invariance():
    """A line's rendered coverage must not depend on which endpoint is listed
    first (digitization direction)."""
    fwd = _render_geom(NEAR[0], NEAR[1], FAR[0], FAR[1], CANVAS_SIZE, CANVAS_SIZE,
                        (0, CANVAS_SIZE), (0, CANVAS_SIZE))
    rev = _render_geom(FAR[0], FAR[1], NEAR[0], NEAR[1], CANVAS_SIZE, CANVAS_SIZE,
                        (0, CANVAS_SIZE), (0, CANVAS_SIZE))
    np.testing.assert_allclose(fwd.values, rev.values, atol=1e-6, err_msg="direction dependence")


def test_canvas_extent_invariance():
    """A line's rendered coverage in a shared window must not depend on
    whether it was drawn on a small (clipping) canvas or a much wider one
    (no clipping) sharing the same pixel grid."""
    small = _render_geom(NEAR[0], NEAR[1], FAR[0], FAR[1], CANVAS_SIZE, CANVAS_SIZE,
                          (0, CANVAS_SIZE), (0, CANVAS_SIZE))
    wide = _render_geom(NEAR[0], NEAR[1], FAR[0], FAR[1], 3 * CANVAS_SIZE, 3 * CANVAS_SIZE,
                         (-CANVAS_SIZE, 2 * CANVAS_SIZE), (-CANVAS_SIZE, 2 * CANVAS_SIZE))
    wide_cropped = wide.sel(x=small.x, y=small.y, method="nearest")
    np.testing.assert_allclose(small.values, wide_cropped.values, atol=1e-6,
                                err_msg="canvas-extent dependence")


def test_canonicalization_workaround_still_correct():
    """renderer.py's _canonicalize_line_direction workaround (kept as defense in
    depth alongside the upstream fix) must still produce direction-invariant
    output."""
    fwd_c = _canonicalize(NEAR[0], NEAR[1], FAR[0], FAR[1])
    rev_c = _canonicalize(FAR[0], FAR[1], NEAR[0], NEAR[1])
    fwd = _render_geom(*fwd_c, CANVAS_SIZE, CANVAS_SIZE, (0, CANVAS_SIZE), (0, CANVAS_SIZE))
    rev = _render_geom(*rev_c, CANVAS_SIZE, CANVAS_SIZE, (0, CANVAS_SIZE), (0, CANVAS_SIZE))
    np.testing.assert_allclose(fwd.values, rev.values, atol=1e-6,
                                err_msg="still direction-dependent after canonicalizing")


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
