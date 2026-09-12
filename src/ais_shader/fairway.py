"""
fairway.py

Fairway-aligned spatio-temporal coordinate modeling for inland waterways.

Provides the FairwayAxis class for projecting vessel trajectories, segments,
and encounters onto a 1D fairway centerline (Frenet-Serret frame: along-channel
chainage/river mile s, and cross-channel lateral offset n).

Eliminates river-bend compass heading divergence (the "false crossing" problem)
by classifying encounters according to upbound/downbound fairway direction and
along-fairway order progression.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple, Union

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import LineString, Point, MultiLineString
from scipy.interpolate import splprep, splev

logger = logging.getLogger(__name__)


def get_utm_crs_for_lon_lat(lon: float, lat: float) -> str:
    """Determine the appropriate UTM EPSG CRS string for a given longitude and latitude."""
    if not (-180.0 <= lon <= 180.0):
        raise ValueError(f"Longitude {lon} is out of valid range [-180, 180].")
    if not (-90.0 <= lat <= 90.0):
        raise ValueError(f"Latitude {lat} is out of valid range [-90, 90].")
    if lon == 180.0:
        zone = 60
    else:
        zone = int((lon + 180) / 6) + 1
    if lat >= 0:
        return f"EPSG:{32600 + zone}"
    else:
        return f"EPSG:{32700 + zone}"


class FairwayAxis:
    """
    Continuous 1D fairway centerline and coordinate reference system.

    Parameters
    ----------
    centerline_geom : LineString
        Dense, continuous fairway centerline geometry in metric coordinates.
    metric_crs : str
        The metric Projected Coordinate Reference System (e.g. 'EPSG:26915', 'EPSG:3857').
    source_crs : str
        Original geographic CRS (typically 'EPSG:4326' or 'EPSG:4269').
    river_name : str, optional
        Name of the waterway (e.g. 'MISSISSIPPI-LO').
    mile_start : float
        Starting river mile for the beginning of the centerline line.
    mile_scale : float
        Conversion factor from centerline metric distance (meters) to river miles (default: 1 / 1609.344).
    """

    def __init__(
        self,
        centerline_geom: LineString,
        metric_crs: str = "EPSG:3857",
        source_crs: str = "EPSG:4326",
        river_name: Optional[str] = None,
        mile_start: float = 0.0,
        mile_scale: float = 1.0 / 1609.344,
    ):
        if not isinstance(centerline_geom, LineString):
            raise TypeError("centerline_geom must be a shapely.geometry.LineString.")

        self.centerline_geom = centerline_geom
        self.metric_crs = metric_crs
        self.source_crs = source_crs
        self.river_name = river_name
        self.mile_start = mile_start
        self.mile_scale = mile_scale
        self.length_m = centerline_geom.length

    @classmethod
    def from_mile_markers(
        cls,
        data: Union[str, Path, gpd.GeoDataFrame],
        river_name: str = "MISSISSIPPI-LO",
        metric_crs: Optional[str] = None,
        spline_sample_interval_m: float = 50.0,
        spline_smoothing: float = 5000.0,
    ) -> "FairwayAxis":
        """
        Construct a continuous FairwayAxis from USACE River Mile Markers.

        Sorts markers monotonically by MILE and fits a smooth B-spline to eliminate
        the 1-mile chord error across river meanders and bends.
        """
        if isinstance(data, (str, Path)):
            path = str(data)
            logger.info(f"Loading mile markers from {path} (river={river_name})...")
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
            raise KeyError("Mile markers dataset must have a 'MILE' column.")

        gdf_river["MILE"] = gdf_river["MILE"].astype(float)
        gdf_river = gdf_river.sort_values("MILE").drop_duplicates(subset=["MILE"]).reset_index(drop=True)

        source_crs = str(gdf_river.crs) if gdf_river.crs else "EPSG:4269"

        # Determine metric CRS
        if metric_crs is None:
            mean_lon = gdf_river.geometry.x.mean()
            mean_lat = gdf_river.geometry.y.mean()
            metric_crs = get_utm_crs_for_lon_lat(mean_lon, mean_lat)

        logger.info(f"Projecting {len(gdf_river)} mile markers to {metric_crs}...")
        gdf_utm = gdf_river.to_crs(metric_crs)

        x_pts = gdf_utm.geometry.x.values
        y_pts = gdf_utm.geometry.y.values

        # If too few points for spline, fallback to piecewise linear
        if len(x_pts) < 4:
            line_geom = LineString(np.column_stack([x_pts, y_pts]))
            return cls(
                line_geom,
                metric_crs=metric_crs,
                source_crs=source_crs,
                river_name=river_name,
                mile_start=float(gdf_river["MILE"].min()),
            )

        logger.info(f"Fitting cubic B-spline through {len(x_pts)} markers (smoothing={spline_smoothing})...")
        tck, _ = splprep([x_pts, y_pts], s=spline_smoothing, k=3)

        # Estimate total metric length via chord sum
        chord_len = float(np.sum(np.hypot(np.diff(x_pts), np.diff(y_pts))))
        num_eval_pts = max(100, int(np.ceil(chord_len / spline_sample_interval_m)))

        u_fine = np.linspace(0, 1, num_eval_pts)
        x_fine, y_fine = splev(u_fine, tck)
        spline_geom = LineString(np.column_stack([x_fine, y_fine]))

        min_mile = float(gdf_river["MILE"].min())
        max_mile = float(gdf_river["MILE"].max())
        mile_span = max_mile - min_mile
        mile_scale = mile_span / spline_geom.length if spline_geom.length > 0 else (1.0 / 1609.344)

        logger.info(
            f"FairwayAxis constructed: {spline_geom.length/1000:.1f} km, "
            f"{num_eval_pts} vertices, mile span [{min_mile:.1f} to {max_mile:.1f}]."
        )

        return cls(
            spline_geom,
            metric_crs=metric_crs,
            source_crs=source_crs,
            river_name=river_name,
            mile_start=min_mile,
            mile_scale=mile_scale,
        )

    def to_geodataframe(
        self,
        crs: str = "EPSG:4326",
        clip_bbox: Optional[Tuple[float, float, float, float]] = None,
    ) -> gpd.GeoDataFrame:
        """
        Export the fairway centerline as a GeoDataFrame in the desired CRS.

        Parameters
        ----------
        crs : str
            Target CRS for exported geometry (default: 'EPSG:4326').
        clip_bbox : tuple of (minx, miny, maxx, maxy) in target CRS, optional
            Optional bounding box to clip the exported centerline to the active corridor.
        """
        line_geom = self.centerline_geom
        if clip_bbox is not None:
            bbox_poly = shapely.box(*clip_bbox)
            bbox_metric = gpd.GeoSeries([bbox_poly], crs=crs).to_crs(self.metric_crs).iloc[0]
            isect = shapely.intersection(line_geom, bbox_metric)
            if not shapely.is_empty(isect):
                line_geom = isect

        gdf = gpd.GeoDataFrame({
            "river_name": [self.river_name or "fairway"],
            "length_km": [round(line_geom.length / 1000.0, 2)],
            "mile_start": [self.mile_start],
            "mile_end": [round(self.mile_start + self.length_m * self.mile_scale, 2)],
            "geometry": [line_geom],
        }, crs=self.metric_crs)

        if crs != self.metric_crs:
            gdf = gdf.to_crs(crs)
        return gdf

    def to_mile_points(self, step_miles: float = 1.0, crs: str = "EPSG:4326") -> gpd.GeoDataFrame:
        """
        Generate regular station points along the continuous centerline at fixed river mile intervals.
        """
        total_miles = self.length_m * self.mile_scale
        miles = np.arange(self.mile_start, self.mile_start + total_miles, step_miles)
        s_vals = (miles - self.mile_start) / self.mile_scale
        pts = [shapely.line_interpolate_point(self.centerline_geom, s) for s in s_vals]
        gdf = gpd.GeoDataFrame({
            "river_name": self.river_name or "fairway",
            "river_mile": np.round(miles, 2),
            "geometry": pts,
        }, crs=self.metric_crs)
        return gdf.to_crs(crs) if crs != self.metric_crs else gdf

    def project_geometries(self, geoms_metric: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Vectorized projection of metric Point geometries onto the fairway axis.
        Optimized by clipping the centerline to the bounding box of the input points.

        Returns
        -------
        s_meters : np.ndarray
        river_mile : np.ndarray
        cross_track_m : np.ndarray
        """
        if len(geoms_metric) == 0:
            return np.array([]), np.array([]), np.array([])

        # Clip centerline to points bounding box + 10km buffer for speed
        margin = 10000.0
        x_vals = shapely.get_x(geoms_metric)
        y_vals = shapely.get_y(geoms_metric)
        minx, maxx = float(np.nanmin(x_vals)) - margin, float(np.nanmax(x_vals)) + margin
        miny, maxy = float(np.nanmin(y_vals)) - margin, float(np.nanmax(y_vals)) + margin
        bbox = shapely.box(minx, miny, maxx, maxy)

        isect = shapely.intersection(self.centerline_geom, bbox)
        if shapely.is_empty(isect):
            work_line = self.centerline_geom
            s_offset = 0.0
        elif isect.geom_type == 'MultiLineString':
            work_line = max(isect.geoms, key=lambda g: g.length)
            s_offset = float(shapely.line_locate_point(self.centerline_geom, shapely.get_point(work_line, 0)))
        else:
            work_line = isect
            s_offset = float(shapely.line_locate_point(self.centerline_geom, shapely.get_point(work_line, 0)))

        s_local = shapely.line_locate_point(work_line, geoms_metric)
        s_meters = s_offset + s_local
        river_mile = self.mile_start + s_meters * self.mile_scale
        abs_dist = shapely.distance(work_line, geoms_metric)

        # Signed lateral distance using local tangent along work_line
        work_len = float(work_line.length)
        s_ahead = np.clip(s_local + 1.0, 0.0, work_len)
        s_behind = np.clip(s_local - 1.0, 0.0, work_len)

        pt_ahead = shapely.line_interpolate_point(work_line, s_ahead)
        pt_behind = shapely.line_interpolate_point(work_line, s_behind)
        pt_on_line = shapely.line_interpolate_point(work_line, s_local)

        tx = shapely.get_x(pt_ahead) - shapely.get_x(pt_behind)
        ty = shapely.get_y(pt_ahead) - shapely.get_y(pt_behind)
        vx = shapely.get_x(geoms_metric) - shapely.get_x(pt_on_line)
        vy = shapely.get_y(geoms_metric) - shapely.get_y(pt_on_line)

        cross_prod = tx * vy - ty * vx
        sign = np.where(cross_prod >= 0, 1.0, -1.0)
        cross_track_m = abs_dist * sign

        return s_meters, river_mile, cross_track_m

    def annotate_points(self, gdf_points: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """
        Annotate an AIS points GeoDataFrame with river_mile and cross_track_m.
        """
        if gdf_points.empty:
            df = gdf_points.copy()
            df["river_mile"] = []
            df["cross_track_m"] = []
            return df

        pts_metric = gdf_points.to_crs(self.metric_crs)
        geoms = pts_metric.geometry.values
        _, river_mile, cross_track_m = self.project_geometries(geoms)

        df = gdf_points.copy()
        df["river_mile"] = np.round(river_mile, 2)
        df["cross_track_m"] = np.round(cross_track_m, 1)
        return df

    def annotate_segments(self, gdf_segments: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """
        Annotate trajectory segments with fairway kinematics:
        - fairway_start_mile, fairway_end_mile
        - fairway_speed_mps (along-channel velocity ds/dt in m/s)
        - cross_speed_mps (cross-channel velocity dn/dt in m/s)
        - fairway_direction ('upbound', 'downbound', 'crossing', 'stationary')
        """
        if gdf_segments.empty:
            df = gdf_segments.copy()
            for col in [
                "fairway_start_mile",
                "fairway_end_mile",
                "fairway_speed_mps",
                "cross_speed_mps",
                "fairway_direction",
            ]:
                df[col] = []
            return df

        seg_metric = gdf_segments.to_crs(self.metric_crs)
        durations = gdf_segments["segment_duration_s"].values

        coords = shapely.get_coordinates(seg_metric.geometry.values)
        start_pts = shapely.points(coords[0::2, 0], coords[0::2, 1])
        end_pts = shapely.points(coords[1::2, 0], coords[1::2, 1])

        s1, m1, n1 = self.project_geometries(start_pts)
        s2, m2, n2 = self.project_geometries(end_pts)

        safe_dur = np.where(durations > 1e-3, durations, 1.0)
        v_s = (s2 - s1) / safe_dur
        v_n = (n2 - n1) / safe_dur

        # Vectorized direction classification
        is_stationary = (durations <= 1e-3) | ((np.abs(v_s) < 0.25) & (np.abs(v_n) < 0.25))
        is_crossing = (~is_stationary) & (np.abs(v_n) > 1.5 * np.maximum(np.abs(v_s), 0.1)) & (np.abs(v_n) > 0.5)
        is_upbound = (~is_stationary) & (~is_crossing) & (v_s > 0)

        directions = np.where(
            is_stationary,
            "stationary",
            np.where(is_crossing, "crossing", np.where(is_upbound, "upbound", "downbound")),
        )

        df = gdf_segments.copy()
        df["fairway_start_mile"] = np.round(m1, 2)
        df["fairway_end_mile"] = np.round(m2, 2)
        df["fairway_speed_mps"] = np.round(v_s, 2)
        df["cross_speed_mps"] = np.round(v_n, 2)
        df["fairway_direction"] = directions
        return df

    def classify_fairway_encounter(
        self,
        dir1: str,
        dir2: str,
        speed1: float,
        speed2: float,
        ds_start: float,
        ds_end: float,
        dv_along: float,
    ) -> str:
        """
        Classify encounter using fairway-aligned coordinates.

        Eliminates heading divergence in river bends:
        - Opposite directions ('upbound' vs 'downbound') -> 'head-on'
        - Same direction ('upbound' vs 'upbound' or 'downbound' vs 'downbound'):
            - Along-fairway order flip (ds_start * ds_end < 0) -> 'overtaking'
            - No order flip -> 'parallel_sailing'
        - Either vessel moving across fairway ('crossing') -> 'crossing'
        - Both stationary -> 'stationary'
        """
        if dir1 == "stationary" and dir2 == "stationary":
            return "stationary"

        if dir1 == "crossing" or dir2 == "crossing":
            return "crossing"

        # Opposite fairway traffic (one upbound, one downbound) is unambiguous head-on meeting
        if (dir1 == "upbound" and dir2 == "downbound") or (dir1 == "downbound" and dir2 == "upbound"):
            return "head-on"

        # Same direction along the fairway
        if (dir1 == "upbound" and dir2 == "upbound") or (dir1 == "downbound" and dir2 == "downbound"):
            # Overtaking requires an along-track order flip
            if ds_start * ds_end < -1e-3:
                return "overtaking"
            elif abs(dv_along) > 1.0 and abs(ds_start) > abs(ds_end):
                # Active closing at significant speed difference
                return "overtaking"
            else:
                return "parallel_sailing"

        # Fallback if one is stationary and one is moving along fairway
        if dir1 == "stationary" or dir2 == "stationary":
            return "parallel_sailing"

        return "crossing"
