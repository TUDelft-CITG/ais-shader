import logging
from typing import Optional, Tuple

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import LineString, Point

logger = logging.getLogger(__name__)

# --- Shared passage-line crossing geometry ---
#
# Two pipelines turn 2-point AIS trajectory segments into crossings of a
# reference line and must agree on the geometry:
#   * analysis.process_partition / run_passage_analysis aggregates crossings
#     into per-passage-line, per-direction, per-location-bin statistics and
#     discards the individual crossings;
#   * events.detect_line_crossings / detect_polygon_entry_exit keeps one row
#     per crossing with full vessel/time/location detail.
# The primitives they share -- the reference line's own direction vector, the
# direction convention derived from it, the intersection point and the
# fraction along the segment -- live here so the two cannot drift apart.

# Passage lines are split into this many equal-length bins along their own
# length for the per-location statistics and the flattened bin-segment layer.
N_LOC_BINS = 20


def line_direction_vectors(geometries) -> Tuple[np.ndarray, np.ndarray]:
    """Each line's own start->end vector, as (L_x, L_y) arrays."""
    coords_list = [g.coords for g in geometries]
    L_x = np.array([c[-1][0] - c[0][0] for c in coords_list])
    L_y = np.array([c[-1][1] - c[0][1] for c in coords_list])
    return L_x, L_y


def add_line_direction_vectors(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Attach the 'L_x'/'L_y' direction-vector columns used to classify crossing direction."""
    gdf['L_x'], gdf['L_y'] = line_direction_vectors(gdf.geometry)
    return gdf


def classify_crossing_direction(S_x, S_y, L_x, L_y) -> np.ndarray:
    """
    Label each crossing 'down' or 'up' from the sign of the cross product L x S.

    Rotating a reference line's own start->end vector (L) by 90 degrees gives
    its left-hand normal; the dot product of the crossing segment's vector (S)
    with that normal is >= 0 when the segment crosses left-to-right of L's
    direction ('down') and negative when it crosses right-to-left ('up').
    """
    dot_product = np.asarray(S_x) * (-np.asarray(L_y)) + np.asarray(S_y) * np.asarray(L_x)
    return np.where(dot_product >= 0, 'down', 'up')


def segment_start_end_coords(geoms) -> Tuple[np.ndarray, np.ndarray]:
    """Start/end coordinate arrays for an array of 2-point LineStrings, vectorized."""
    coords = shapely.get_coordinates(geoms)
    return coords[0::2], coords[1::2]


def segment_vectors(geoms) -> Tuple[np.ndarray, np.ndarray]:
    """Each 2-point segment's own start->end vector, as (S_x, S_y) arrays."""
    starts, ends = segment_start_end_coords(geoms)
    return ends[:, 0] - starts[:, 0], ends[:, 1] - starts[:, 1]


def crossing_point_and_fraction(seg_geom, ref_geom, allow_empty: bool = False) -> Tuple[Optional[Point], float]:
    """
    Where a 2-point segment crosses a reference line/boundary, as (Point, fraction along segment).

    A straight 2-point segment against a straight reference line normally
    intersects at a single Point, but this defensively falls back to the
    centroid for the rare non-Point result (e.g. the segment grazing a polygon
    corner).

    allow_empty : bool
        Callers that only invoke this on segments already known to intersect
        ref_geom (via an `intersects` sjoin) leave this False, so an empty
        intersection -- which would indicate a bug -- raises instead of
        silently yielding a bogus fraction. Pass True to get (None, 0.0) back.
    """
    isect = seg_geom.intersection(ref_geom)
    if isect.is_empty:
        if not allow_empty:
            raise ValueError(
                f"Segment {seg_geom.wkt} does not intersect reference geometry {ref_geom.wkt}"
            )
        return None, 0.0
    point = isect if isinstance(isect, Point) else isect.centroid
    return point, seg_geom.project(point, normalized=True)


def crossing_points_and_fractions(seg_geoms, ref_geoms, allow_empty: bool = False) -> Tuple[list, np.ndarray]:
    """crossing_point_and_fraction over paired segment/reference arrays, as (points, fractions)."""
    points, fractions = [], []
    for seg_geom, ref_geom in zip(seg_geoms, ref_geoms):
        point, f_seg = crossing_point_and_fraction(seg_geom, ref_geom, allow_empty=allow_empty)
        points.append(point)
        fractions.append(f_seg)
    return points, np.array(fractions, dtype=float)


def fraction_along(ref_geom, point) -> float:
    """How far along `ref_geom` (0-1) the given point falls; 0.0 for a missing point."""
    if point is None:
        return 0.0
    return ref_geom.project(point, normalized=True)


def interpolate_event_time(start_time, duration_s, f_seg):
    """The moment a crossing happened, interpolated linearly within its segment."""
    return start_time + pd.to_timedelta(f_seg * duration_s, unit='s')


def bin_segment_geometry(line, bin_index: int, n_bins: int = N_LOC_BINS) -> LineString:
    """The `bin_index`-th of `n_bins` equal-length pieces of `line`, as a 2-point LineString.

    `line` must be in a metric CRS for the pieces to be equal in length on the
    ground.
    """
    p1 = line.interpolate(bin_index / n_bins, normalized=True)
    p2 = line.interpolate((bin_index + 1) / n_bins, normalized=True)
    return LineString([p1, p2])
