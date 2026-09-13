"""
usace.py

Ingestion and preprocessing routines for US Army Corps of Engineers (USACE)
Inland River Mile Markers from the US Marine Cadastre.

Converts US non-SI river mile markers into standard metric FairwayAxis centerlines.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

import geopandas as gpd
import numpy as np
from scipy.interpolate import splev, splprep
from shapely.geometry import LineString

from .fairway import FairwayAxis, get_utm_crs_for_lon_lat

logger = logging.getLogger(__name__)

METERS_PER_STATUTE_MILE = 1609.344


def build_usace_fairway(
    data: Union[str, Path, gpd.GeoDataFrame],
    river_name: str = "MISSISSIPPI-LO",
    metric_crs: Optional[str] = None,
    spline_sample_interval_m: float = 50.0,
    spline_smoothing: float = 5000.0,
) -> FairwayAxis:
    """
    Construct a continuous metric FairwayAxis from USACE River Mile Markers.

    Filters markers by RIVER_NAME, sorts monotonically by MILE, projects to metric_crs (UTM),
    and fits a smooth B-spline sampled at regular metric intervals.
    """
    if isinstance(data, (str, Path)):
        path = str(data)
        logger.info(f"Loading USACE mile markers from {path} (river={river_name})...")
        gdf = gpd.read_file(path)
    elif isinstance(data, gpd.GeoDataFrame):
        gdf = data.copy()
    else:
        raise TypeError("data must be a filepath or GeoDataFrame.")

    if "RIVER_NAME" in gdf.columns:
        gdf_river = gdf[gdf["RIVER_NAME"] == river_name].copy()
        if gdf_river.empty:
            available = gdf["RIVER_NAME"].dropna().unique().tolist()[:10]
            raise ValueError(f"No markers found for river '{river_name}'. Available: {available}")
    else:
        gdf_river = gdf.copy()

    if "MILE" not in gdf_river.columns:
        raise KeyError("USACE mile markers dataset must have a 'MILE' column.")

    gdf_river["MILE"] = gdf_river["MILE"].astype(float)
    gdf_river = gdf_river.sort_values("MILE").drop_duplicates(subset=["MILE"]).reset_index(drop=True)

    if metric_crs is None:
        mean_lon = gdf_river.geometry.x.mean()
        mean_lat = gdf_river.geometry.y.mean()
        metric_crs = get_utm_crs_for_lon_lat(mean_lon, mean_lat)

    logger.info(f"Projecting {len(gdf_river)} USACE markers to metric CRS {metric_crs}...")
    gdf_utm = gdf_river.to_crs(metric_crs)

    x_pts = gdf_utm.geometry.x.values
    y_pts = gdf_utm.geometry.y.values

    if len(x_pts) < 4:
        line_geom = LineString(np.column_stack([x_pts, y_pts]))
    else:
        logger.info(f"Fitting cubic B-spline through {len(x_pts)} markers (smoothing={spline_smoothing})...")
        tck, _ = splprep([x_pts, y_pts], s=spline_smoothing, k=3)
        chord_len = float(np.sum(np.hypot(np.diff(x_pts), np.diff(y_pts))))
        num_eval_pts = max(100, int(np.ceil(chord_len / spline_sample_interval_m)))
        u_fine = np.linspace(0, 1, num_eval_pts)
        x_fine, y_fine = splev(u_fine, tck)
        line_geom = LineString(np.column_stack([x_fine, y_fine]))

    min_mile = float(gdf_river["MILE"].min())
    chainage_start_m = min_mile * METERS_PER_STATUTE_MILE

    return FairwayAxis(
        centerline_geom=line_geom,
        metric_crs=metric_crs,
        fairway_name=river_name,
        chainage_start_m=chainage_start_m,
    )
