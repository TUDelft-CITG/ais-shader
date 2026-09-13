"""
fairway.py

Fairway-aligned spatio-temporal coordinate modeling for inland waterways.
Metric-only (SI): along-channel chainage (m/km), cross-channel lateral offset (m),
and along/cross channel velocities (m/s).

Provides the FairwayAxis class for projecting vessel trajectories, segments,
and encounters onto a 1D fairway centerline (Frenet-Serret frame: along-channel
chainage s in meters, and cross-channel lateral offset n in meters).

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
import pyproj
import shapely
from shapely.geometry import LineString
from scipy.spatial import cKDTree

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
    Continuous 1D metric fairway centerline and Frenet-Serret coordinate reference system.

    Parameters
    ----------
    centerline_geom : LineString
        Dense, continuous fairway centerline geometry in metric coordinates.
    metric_crs : str
        The metric Projected Coordinate Reference System (e.g. 'EPSG:26915', 'EPSG:32615', 'EPSG:3857').
    fairway_name : str, optional
        Name of the fairway / waterway (e.g. 'MISSISSIPPI-LO', 'Waal', 'Rhine').
    chainage_start_m : float, optional
        Starting metric chainage (in meters) for the beginning vertex of the centerline (default: 0.0).
    river_name : str, optional
        Backwards-compatible alias for fairway_name.
    """

    def __init__(
        self,
        centerline_geom: LineString,
        metric_crs: str = "EPSG:3857",
        fairway_name: Optional[str] = None,
        chainage_start_m: float = 0.0,
        river_name: Optional[str] = None,
        **kwargs,
    ):
        if not isinstance(centerline_geom, LineString):
            raise TypeError("centerline_geom must be a shapely.geometry.LineString.")

        self.centerline_geom = centerline_geom
        self.metric_crs = metric_crs
        self.fairway_name = fairway_name or river_name or "fairway"
        self.river_name = self.fairway_name
        self.chainage_start_m = float(chainage_start_m)
        self.length_m = float(centerline_geom.length)

        # Build cKDTree on centerline vertices for O(log N) nearest segment lookup
        coords = shapely.get_coordinates(centerline_geom)
        if len(coords) < 2:
            raise ValueError("centerline_geom must contain at least 2 vertices.")
        self._coords = coords
        self._p1 = coords[:-1]
        self._p2 = coords[1:]
        self._v = self._p2 - self._p1
        self._v_lens_sq = np.sum(self._v**2, axis=1)
        self._v_lens = np.sqrt(self._v_lens_sq)
        self._cum_s = np.empty(len(coords), dtype=np.float64)
        self._cum_s[0] = 0.0
        self._cum_s[1:] = np.cumsum(self._v_lens)
        self._kdtree = cKDTree(coords)

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "FairwayAxis":
        """
        Load a preprocessed metric FairwayAxis directly from a GeoParquet or GeoPackage/GeoJSON file.
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Fairway file not found at {p}")
        if p.suffix in [".parquet", ".geoparquet"]:
            gdf = gpd.read_parquet(p)
        else:
            gdf = gpd.read_file(p)
        if gdf.empty:
            raise ValueError(f"Fairway file at {p} is empty.")

        geom = gdf.geometry.iloc[0]
        if geom.geom_type == "MultiLineString":
            raise ValueError(f"Fairway geometry at {p} must be a continuous LineString, got MultiLineString.")
        if not gdf.crs:
            raise ValueError(f"Fairway file at {p} must have a defined Coordinate Reference System (CRS).")
        metric_crs = f"EPSG:{gdf.crs.to_epsg()}" if gdf.crs.to_epsg() else str(gdf.crs)

        fairway_name = None
        for col in ["fairway_name", "river_name", "name"]:
            if col in gdf.columns:
                fairway_name = str(gdf[col].iloc[0])
                break

        chainage_start_m = 0.0
        if "chainage_start_m" in gdf.columns:
            chainage_start_m = float(gdf["chainage_start_m"].iloc[0])

        return cls(
            centerline_geom=geom,
            metric_crs=metric_crs,
            fairway_name=fairway_name,
            chainage_start_m=chainage_start_m,
        )

    @classmethod
    def load(
        cls,
        data: Union[str, Path, gpd.GeoDataFrame],
        river_name: str = "MISSISSIPPI-LO",
        metric_crs: Optional[str] = None,
    ) -> "FairwayAxis":
        """
        Load FairwayAxis from a preprocessed centerline file or delegate to USACE provider if point markers.
        """
        if isinstance(data, (str, Path)):
            p = Path(data)
            if not p.exists():
                raise FileNotFoundError(f"Fairway file not found at {p}")
            if p.suffix in [".parquet", ".geoparquet"]:
                gdf = gpd.read_parquet(p)
            else:
                gdf = gpd.read_file(p)
        elif isinstance(data, gpd.GeoDataFrame):
            gdf = data.copy()
        else:
            raise TypeError("data must be a filepath or GeoDataFrame.")

        if not gdf.empty and gdf.geometry.iloc[0].geom_type in ["LineString", "MultiLineString"]:
            geom = gdf.geometry.iloc[0]
            if geom.geom_type == "MultiLineString":
                geom = max(geom.geoms, key=lambda g: g.length)
            metric_crs = f"EPSG:{gdf.crs.to_epsg()}" if (gdf.crs and gdf.crs.to_epsg()) else (str(gdf.crs) if gdf.crs else (metric_crs or "EPSG:32615"))
            fairway_name = str(gdf["fairway_name"].iloc[0]) if "fairway_name" in gdf.columns else (str(gdf["river_name"].iloc[0]) if "river_name" in gdf.columns else river_name)
            chainage_start_m = float(gdf["chainage_start_m"].iloc[0]) if "chainage_start_m" in gdf.columns else 0.0
            return cls(
                centerline_geom=geom,
                metric_crs=metric_crs,
                fairway_name=fairway_name,
                chainage_start_m=chainage_start_m,
            )
        else:
            from .usace import build_usace_fairway
            return build_usace_fairway(gdf, river_name=river_name, metric_crs=metric_crs)

    @classmethod
    def from_mile_markers(cls, *args, **kwargs) -> "FairwayAxis":
        """Backwards compatibility wrapper delegating to usace.build_usace_fairway."""
        from .usace import build_usace_fairway
        return build_usace_fairway(*args, **kwargs)

    def to_geodataframe(
        self,
        crs: Optional[str] = None,
        clip_bbox: Optional[Tuple[float, float, float, float]] = None,
    ) -> gpd.GeoDataFrame:
        """
        Export the fairway centerline as a GeoDataFrame in the desired metric CRS.
        """
        target_crs = crs or self.metric_crs
        line_geom = self.centerline_geom
        if clip_bbox is not None:
            bbox_poly = shapely.box(*clip_bbox)
            bbox_metric = gpd.GeoSeries([bbox_poly], crs=target_crs).to_crs(self.metric_crs).iloc[0]
            isect = shapely.intersection(line_geom, bbox_metric)
            if not shapely.is_empty(isect):
                line_geom = isect

        gdf = gpd.GeoDataFrame({
            "fairway_name": [self.fairway_name],
            "river_name": [self.fairway_name],
            "length_m": [round(line_geom.length, 1)],
            "length_km": [round(line_geom.length / 1000.0, 3)],
            "chainage_start_m": [round(self.chainage_start_m, 1)],
            "chainage_end_m": [round(self.chainage_start_m + line_geom.length, 1)],
            "geometry": [line_geom],
        }, crs=self.metric_crs)

        if target_crs != self.metric_crs:
            gdf = gdf.to_crs(target_crs)
        return gdf

    def to_station_points(self, step_m: float = 1000.0, crs: Optional[str] = None) -> gpd.GeoDataFrame:
        """
        Generate regular station points along the continuous centerline at fixed metric intervals (e.g. 1000m).
        """
        target_crs = crs or self.metric_crs
        s_vals = np.arange(0.0, self.length_m, step_m)
        pts = [shapely.line_interpolate_point(self.centerline_geom, s) for s in s_vals]
        gdf = gpd.GeoDataFrame({
            "fairway_name": self.fairway_name,
            "chainage_m": np.round(self.chainage_start_m + s_vals, 1),
            "chainage_km": np.round((self.chainage_start_m + s_vals) / 1000.0, 3),
            "geometry": pts,
        }, crs=self.metric_crs)
        return gdf.to_crs(target_crs) if target_crs != self.metric_crs else gdf

    def project_geometries(self, geoms_metric: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Vectorized projection of metric Point geometries onto the fairway axis
        using a cKDTree spatial index for O(log N) vertex lookup and candidate segment projection.

        Returns
        -------
        chainage_m : np.ndarray
            Metric distance along the fairway centerline (meters).
        cross_track_m : np.ndarray
            Signed lateral offset perpendicular to fairway centerline (meters, positive=starboard, negative=port).
        """
        if len(geoms_metric) == 0:
            return np.array([]), np.array([])

        if isinstance(geoms_metric, np.ndarray) and geoms_metric.ndim == 2 and geoms_metric.shape[1] == 2:
            pts_xy = geoms_metric
        else:
            pts_xy = shapely.get_coordinates(geoms_metric)
        M = len(pts_xy)
        n_segs = len(self._p1)

        k = min(4, len(self._coords))
        _, v_idx = self._kdtree.query(pts_xy, k=k, workers=1)
        if k == 1:
            v_idx = v_idx[:, None]

        cand_segs = np.empty((M, 2 * k), dtype=np.int64)
        cand_segs[:, 0::2] = np.clip(v_idx - 1, 0, n_segs - 1)
        cand_segs[:, 1::2] = np.clip(v_idx, 0, n_segs - 1)

        A = self._coords[cand_segs]
        B = self._coords[cand_segs + 1]
        T = B - A
        T_len_sq = np.sum(T**2, axis=2)
        safe_T_len_sq = np.where(T_len_sq > 0, T_len_sq, 1.0)
        V = pts_xy[:, None, :] - A
        u = np.clip((V[:, :, 0] * T[:, :, 0] + V[:, :, 1] * T[:, :, 1]) / safe_T_len_sq, 0.0, 1.0)
        P_proj = A + u[:, :, None] * T
        diff = pts_xy[:, None, :] - P_proj
        dist_sq = diff[:, :, 0]**2 + diff[:, :, 1]**2
        best_k = np.argmin(dist_sq, axis=1)
        ar = np.arange(M)

        best_seg = cand_segs[ar, best_k]
        best_u = u[ar, best_k]
        best_T = T[ar, best_k]
        best_V = V[ar, best_k]
        best_dist = np.sqrt(dist_sq[ar, best_k])

        s_meters = self._cum_s[best_seg] + best_u * self._v_lens[best_seg]
        chainage_m = self.chainage_start_m + s_meters

        cross_prod = best_T[:, 0] * best_V[:, 1] - best_T[:, 1] * best_V[:, 0]
        sign = np.where(cross_prod >= 0, 1.0, -1.0)
        cross_track_m = best_dist * sign

        return chainage_m, cross_track_m

    def annotate_points(self, gdf_points: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """
        Annotate an AIS points GeoDataFrame with metric chainage_m, chainage_km, and cross_track_m.
        """
        if gdf_points.empty:
            df = gdf_points.copy()
            df["chainage_m"] = []
            df["chainage_km"] = []
            df["cross_track_m"] = []
            return df

        if gdf_points.crs and pyproj.CRS.from_user_input(gdf_points.crs) != pyproj.CRS.from_user_input(self.metric_crs):
            raise ValueError(
                f"Input GeoDataFrame CRS ({gdf_points.crs}) does not match FairwayAxis metric CRS ({self.metric_crs}). "
                f"Please project points to {self.metric_crs} before calling FairwayAxis."
            )

        pts_metric = gdf_points
        geoms = pts_metric.geometry.values
        chainage_m, cross_track_m = self.project_geometries(geoms)

        df = gdf_points.copy()
        df["chainage_m"] = np.round(chainage_m, 1)
        df["chainage_km"] = np.round(chainage_m / 1000.0, 3)
        df["cross_track_m"] = np.round(cross_track_m, 1)
        return df

    def annotate_segments(self, gdf_segments: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """
        Annotate trajectory segments with fairway kinematics (metric only):
        - fairway_start_m, fairway_end_m (chainage in meters)
        - fairway_start_km, fairway_end_km (chainage in kilometers)
        - fairway_speed_mps (along-channel velocity ds/dt in m/s)
        - cross_speed_mps (cross-channel velocity dn/dt in m/s)
        - fairway_direction ('upbound', 'downbound', 'crossing', 'stationary')
        """
        if gdf_segments.empty:
            df = gdf_segments.copy()
            for col in [
                "fairway_start_m",
                "fairway_end_m",
                "fairway_start_km",
                "fairway_end_km",
                "fairway_speed_mps",
                "cross_speed_mps",
                "fairway_direction",
            ]:
                df[col] = []
            return df

        if gdf_segments.crs and pyproj.CRS.from_user_input(gdf_segments.crs) != pyproj.CRS.from_user_input(self.metric_crs):
            raise ValueError(
                f"Input GeoDataFrame CRS ({gdf_segments.crs}) does not match FairwayAxis metric CRS ({self.metric_crs}). "
                f"Please project segments to {self.metric_crs} before calling FairwayAxis."
            )

        seg_metric = gdf_segments
        durations = gdf_segments["segment_duration_s"].values

        coords = shapely.get_coordinates(seg_metric.geometry.values)
        s1, n1 = self.project_geometries(coords[0::2])
        s2, n2 = self.project_geometries(coords[1::2])

        safe_dur = np.where(durations > 1e-3, durations, 1.0)
        v_s = (s2 - s1) / safe_dur
        v_n = (n2 - n1) / safe_dur

        # Vectorized direction classification
        is_stationary = (durations <= 1e-3) | ((np.abs(v_s) < 0.25) & (np.abs(v_n) < 0.25))
        is_crossing = (~is_stationary) & (np.abs(v_n) > 1.5 * np.maximum(np.abs(v_s), 0.1)) & (np.abs(v_n) > 0.5)
        is_upbound = (~is_stationary) & (~is_crossing) & (v_s > 0)

        # Segments far from the fairway centerline are outside the fairway corridor
        max_dist = np.maximum(np.abs(n1), np.abs(n2))
        is_outside = max_dist > 3000.0

        directions = np.where(
            is_outside,
            "outside_fairway",
            np.where(
                is_stationary,
                "stationary",
                np.where(is_crossing, "crossing", np.where(is_upbound, "upbound", "downbound")),
            ),
        )

        df = gdf_segments.copy()
        df["fairway_start_m"] = np.round(s1, 1)
        df["fairway_end_m"] = np.round(s2, 1)
        df["fairway_start_km"] = np.round(s1 / 1000.0, 3)
        df["fairway_end_km"] = np.round(s2 / 1000.0, 3)
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
        heading1: Optional[float] = None,
        heading2: Optional[float] = None,
    ) -> str:
        """
        Classify encounter using fairway-aligned coordinates.

        Eliminates heading divergence in river bends:
        - Opposite directions ('upbound' vs 'downbound') -> 'head-on'
        - Same direction ('upbound' vs 'upbound' or 'downbound' vs 'downbound'):
            - Along-fairway order flip (ds_start * ds_end < 0) -> 'overtaking'
            - No order flip -> 'parallel_sailing'
        - Either vessel outside fairway corridor -> fall back to relative heading/kinematics
        - Either vessel moving across fairway ('crossing') -> 'crossing' (unless relative heading <= 45°)
        - Both stationary -> 'stationary'
        """
        if dir1 == "stationary" and dir2 == "stationary":
            return "stationary"

        # If either vessel is outside the fairway corridor, use standard kinematic relative heading
        if dir1 == "outside_fairway" or dir2 == "outside_fairway":
            if heading1 is not None and heading2 is not None:
                diff = (heading1 - heading2) % 360.0
                rel_angle = min(diff, 360.0 - diff)
                if rel_angle <= 45.0:
                    order_flipped = (ds_start * ds_end < -1e-3)
                    has_speed_diff = (abs(dv_along) >= 0.5) if dv_along is not None else False
                    if order_flipped or (has_speed_diff and (ds_start * ds_end <= 0.0 and abs(ds_start - ds_end) > 1.0)):
                        return "overtaking"
                    elif abs(dv_along or 0.0) > 1.0 and abs(ds_start) > abs(ds_end):
                        return "overtaking"
                    return "parallel_sailing"
                elif rel_angle >= 135.0:
                    return "head-on"
                else:
                    return "crossing"
            return "crossing"

        # Vessels steering in the same general direction (rel_angle <= 45°) cannot be crossing each other
        if heading1 is not None and heading2 is not None:
            diff = (heading1 - heading2) % 360.0
            rel_angle = min(diff, 360.0 - diff)
            if rel_angle <= 45.0:
                order_flipped = (ds_start * ds_end < -1e-3)
                has_speed_diff = (abs(dv_along) >= 0.5) if dv_along is not None else False
                if order_flipped or (has_speed_diff and (ds_start * ds_end <= 0.0 and abs(ds_start - ds_end) > 1.0)):
                    return "overtaking"
                elif abs(dv_along or 0.0) > 1.0 and abs(ds_start) > abs(ds_end):
                    return "overtaking"
                else:
                    return "parallel_sailing"

        if dir1 == "crossing" or dir2 == "crossing":
            return "crossing"

        if (dir1 == "upbound" and dir2 == "downbound") or (dir1 == "downbound" and dir2 == "upbound"):
            return "head-on"

        if (dir1 == "upbound" and dir2 == "upbound") or (dir1 == "downbound" and dir2 == "downbound"):
            if ds_start * ds_end < -1e-3:
                return "overtaking"
            elif abs(dv_along) > 1.0 and abs(ds_start) > abs(ds_end):
                return "overtaking"
            else:
                return "parallel_sailing"

        if dir1 == "stationary" or dir2 == "stationary":
            return "parallel_sailing"

        return "crossing"
