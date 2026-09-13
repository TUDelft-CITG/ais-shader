"""
rws.py

Ingestion and preprocessing routines for Rijkswaterstaat (RWS) Fairway Information Services
(FIS / VNDS - Vaarweg Netwerk Data Services) from the Rijkswaterstaat ArcGIS REST API:
https://geo.rijkswaterstaat.nl/arcgis/rest/services/GDR/fis_vnds/MapServer

Layers utilized:
- Layer 58: vaarwegvak (fairway links / sections with geometries and route kilometrierung)
- Layer 55: vaarwegen (fairway master metadata with official names and VIN codes)
- Layer 11: kilometermarkering (official hectometer and kilometer markers along fairways)

Converts Dutch national fairway geometries into standard metric FairwayAxis centerlines
(defaulting to Amersfoort / RD New - EPSG:28992).
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, MultiLineString
from shapely.ops import linemerge

from .fairway import FairwayAxis

logger = logging.getLogger(__name__)

RWS_FIS_MAPSERVER_BASE = "https://geo.rijkswaterstaat.nl/arcgis/rest/services/GDR/fis_vnds/MapServer"
LAYER_VAARWEGVAK = 58
LAYER_VAARWEGEN = 55
LAYER_KILOMETERMARKERING = 11


def query_rws_arcgis_layer(
    layer_id: int,
    where: str = "1=1",
    bbox: Optional[Tuple[float, float, float, float]] = None,
    out_fields: str = "*",
    return_geometry: bool = True,
    out_crs: Optional[str] = "EPSG:4326",
) -> gpd.GeoDataFrame:
    """
    Query an ArcGIS Feature Layer from Rijkswaterstaat FIS VNDS and return a GeoDataFrame.

    Parameters
    ----------
    layer_id : int
        Layer index in MapServer (e.g. 58 for vaarwegvak, 55 for vaarwegen, 11 for kilometermarkering).
    where : str
        SQL WHERE clause (default '1=1').
    bbox : tuple of float, optional
        Bounding box (min_lon, min_lat, max_lon, max_lat) in EPSG:4326.
    out_fields : str
        Comma-separated list of field names or '*' for all fields.
    return_geometry : bool
        Whether to return feature geometries.
    out_crs : str, optional
        Target Coordinate Reference System (e.g. 'EPSG:28992' or 'EPSG:4326').
    """
    params = {
        "where": where,
        "outFields": out_fields,
        "returnGeometry": "true" if return_geometry else "false",
        "f": "geojson" if return_geometry else "json",
        "inSR": "4326",
        "outSR": "4326",
    }

    if bbox is not None:
        min_lon, min_lat, max_lon, max_lat = bbox
        params["geometry"] = f"{min_lon},{min_lat},{max_lon},{max_lat}"
        params["geometryType"] = "esriGeometryEnvelope"
        params["spatialRel"] = "esriSpatialRelIntersects"

    query_str = urllib.parse.urlencode(params)
    url = f"{RWS_FIS_MAPSERVER_BASE}/{layer_id}/query?{query_str}"
    logger.info(f"Querying RWS MapServer layer {layer_id}: {url[:100]}...")

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ais-shader/rws (https://github.com/TUDelft-CITG/ais-shader)"},
    )

    with urllib.request.urlopen(req) as resp:
        content = resp.read().decode("utf-8")

    data = json.loads(content)
    if isinstance(data, dict) and "error" in data:
        err = data["error"]
        raise RuntimeError(
            f"ArcGIS MapServer layer {layer_id} returned error {err.get('code')}: {err.get('message')} ({err.get('details', [])})"
        )

    if not return_geometry:
        records = [feat["attributes"] for feat in data.get("features", [])]
        return pd.DataFrame(records)

    gdf = gpd.read_file(content)
    if gdf.crs is None:
        raise ValueError(f"ArcGIS layer {layer_id} returned GeoDataFrame without a CRS.")
    if out_crs and gdf.crs.to_string() != out_crs:
        gdf = gdf.to_crs(out_crs)
    return gdf


def fetch_rws_fairways_metadata(
    where: str = "1=1",
    bbox: Optional[Tuple[float, float, float, float]] = None,
) -> pd.DataFrame:
    """
    Fetch metadata from RWS layer 55 (vaarwegen), mapping fairwayid/id to official name,
    vincode, and CEMT class.
    """
    return query_rws_arcgis_layer(
        layer_id=LAYER_VAARWEGEN,
        where=where,
        bbox=bbox,
        out_fields="id,name,vincode,fairwaynumber,cemtclass,routekmbegin,routekmend",
        return_geometry=False,
    )


def fetch_rws_fairway_sections(
    fairway_id: Optional[Union[int, List[int]]] = None,
    name: Optional[str] = None,
    bbox: Optional[Tuple[float, float, float, float]] = None,
    metric_crs: str = "EPSG:28992",
    enrich_metadata: bool = True,
) -> gpd.GeoDataFrame:
    """
    Fetch fairway sections (vaarwegvakken, layer 58) from Rijkswaterstaat FIS VNDS.

    Parameters
    ----------
    fairway_id : int or list of int, optional
        Filter by RWS fairway ID (e.g. 15384 for Amsterdam-Rijnkanaal, 27861 for Lek, 33192 for Waal).
    name : str, optional
        Search by waterway name (e.g. 'Amsterdam-Rijnkanaal', 'Lek', 'Waal').
    bbox : tuple of float, optional
        (min_lon, min_lat, max_lon, max_lat) in WGS84 degrees.
    metric_crs : str
        Target metric CRS (default: 'EPSG:28992' - Amersfoort / RD New).
    enrich_metadata : bool
        Whether to join layer 55 (vaarwegen) to add official names and VIN codes.
    """
    if fairway_id is None and name is None and bbox is None:
        raise ValueError("Must specify at least one of fairway_id, name, or bbox.")

    where_parts = []
    if fairway_id is not None:
        if isinstance(fairway_id, (list, tuple, set)):
            ids_str = ",".join(str(i) for i in fairway_id)
            where_parts.append(f"fairwayid IN ({ids_str})")
        else:
            where_parts.append(f"fairwayid = {fairway_id}")

    where_clause = " AND ".join(where_parts) if where_parts else "1=1"

    # If searching by name and fairway_id wasn't directly given, resolve name via layer 55 first
    if name is not None and fairway_id is None:
        logger.info(f"Resolving fairway name '{name}' via RWS layer 55...")
        meta_matches = query_rws_arcgis_layer(
            layer_id=LAYER_VAARWEGEN,
            where=f"name LIKE '%{name}%'",
            bbox=bbox,
            out_fields="id,name,vincode",
            return_geometry=False,
        )
        if meta_matches.empty:
            raise ValueError(f"No RWS fairway found matching name '{name}'.")
        matched_ids = meta_matches["id"].dropna().astype(int).unique().tolist()
        logger.info(f"Matched {len(matched_ids)} fairway IDs for '{name}': {matched_ids}")
        ids_str = ",".join(str(i) for i in matched_ids)
        where_clause = f"fairwayid IN ({ids_str})"

    gdf = query_rws_arcgis_layer(
        layer_id=LAYER_VAARWEGVAK,
        where=where_clause,
        bbox=bbox,
        out_fields="*",
        return_geometry=True,
        out_crs=metric_crs,
    )

    if enrich_metadata and not gdf.empty:
        meta_df = fetch_rws_fairways_metadata(bbox=bbox)
        if not meta_df.empty:
            id_to_name: Dict[int, str] = dict(zip(meta_df["id"], meta_df["name"]))
            id_to_vin: Dict[int, str] = dict(zip(meta_df["id"], meta_df["vincode"]))
            id_to_cemt: Dict[int, str] = dict(zip(meta_df["id"], meta_df["cemtclass"]))

            gdf["fairway_name"] = gdf["fairwayid"].map(id_to_name)
            gdf["vincode"] = gdf["fairwayid"].map(id_to_vin)
            gdf["cemtclass"] = gdf["fairwayid"].map(id_to_cemt)

    return gdf


def fetch_rws_kilometer_markers(
    fairway_id: Optional[Union[int, List[int]]] = None,
    bbox: Optional[Tuple[float, float, float, float]] = None,
    metric_crs: str = "EPSG:28992",
) -> gpd.GeoDataFrame:
    """
    Fetch official hectometer and kilometer markers (layer 11) from Rijkswaterstaat FIS VNDS.
    """
    if fairway_id is None and bbox is None:
        raise ValueError("Must specify at least one of fairway_id or bbox.")

    where_parts = []
    if fairway_id is not None:
        if isinstance(fairway_id, (list, tuple, set)):
            ids_str = ",".join(str(i) for i in fairway_id)
            where_parts.append(f"fairwayid IN ({ids_str})")
        else:
            where_parts.append(f"fairwayid = '{fairway_id}'")

    where_clause = " AND ".join(where_parts) if where_parts else "1=1"
    return query_rws_arcgis_layer(
        layer_id=LAYER_KILOMETERMARKERING,
        where=where_clause,
        bbox=bbox,
        out_fields="*",
        return_geometry=True,
        out_crs=metric_crs,
    )


def build_rws_fairway(
    data: Optional[Union[str, Path, gpd.GeoDataFrame]] = None,
    fairway_id: Optional[Union[int, List[int]]] = None,
    river_name: Optional[str] = None,
    bbox: Optional[Tuple[float, float, float, float]] = None,
    metric_crs: str = "EPSG:28992",
) -> FairwayAxis:
    """
    Construct a continuous metric FairwayAxis for a Dutch inland waterway from RWS FIS VNDS.

    Parameters
    ----------
    data : str, Path, or GeoDataFrame, optional
        Pre-fetched or local fairway sections GeoDataFrame/file. If None, queries RWS MapServer 58 live.
    fairway_id : int or list of int, optional
        RWS fairway ID (e.g. 15384 for Amsterdam-Rijnkanaal, 27861 for Lek, 33192 for Waal).
    river_name : str, optional
        Fairway name to query or assign (e.g. 'Amsterdam-Rijnkanaal', 'Lek', 'Waal').
    bbox : tuple of float, optional
        (min_lon, min_lat, max_lon, max_lat) bounding box.
    metric_crs : str
        Metric CRS (default: 'EPSG:28992' - RD New).
    """
    if data is not None:
        if isinstance(data, (str, Path)):
            gdf = gpd.read_file(data)
        elif isinstance(data, gpd.GeoDataFrame):
            gdf = data.copy()
        else:
            raise TypeError("data must be a filepath or GeoDataFrame.")
        if gdf.crs is None:
            raise ValueError("Input data has no CRS set.")
        if gdf.crs.to_string() != metric_crs:
            gdf = gdf.to_crs(metric_crs)
    else:
        if fairway_id is None and river_name is None and bbox is None:
            raise ValueError("Must specify at least one of data, fairway_id, river_name, or bbox.")
        gdf = fetch_rws_fairway_sections(
            fairway_id=fairway_id,
            name=river_name,
            bbox=bbox,
            metric_crs=metric_crs,
        )

    if gdf.empty:
        raise ValueError(f"No fairway sections found for fairway_id={fairway_id}, river_name={river_name}.")

    if "routekmbegin" not in gdf.columns:
        raise KeyError("Fairway sections must contain 'routekmbegin' column for chainage ordering and orientation.")

    # Sort sections by route kilometrierung
    gdf = gdf.sort_values("routekmbegin").reset_index(drop=True)

    geoms = gdf.geometry.tolist()
    merged = linemerge(geoms)

    if merged.is_empty:
        raise ValueError("Failed to merge fairway section geometries.")

    if isinstance(merged, MultiLineString):
        raise ValueError(
            f"Fairway section geometries do not form a single continuous line (linemerge produced {len(merged.geoms)} disjoint parts). "
            "Verify that input fairway sections are contiguous."
        )
    elif isinstance(merged, LineString):
        line_geom = merged
    else:
        raise TypeError(f"Unexpected merged geometry type: {type(merged)}")

    # Ensure orientation matches routekmbegin progression
    if "routekmbegin" in gdf.columns and len(gdf) > 1:
        first_sec = gdf.iloc[0]
        last_sec = gdf.iloc[-1]
        p_first = first_sec.geometry.interpolate(0.0)
        p_last = last_sec.geometry.interpolate(last_sec.geometry.length)

        start_pt = LineString([line_geom.coords[0], line_geom.coords[1]]).interpolate(0.0)
        end_pt = LineString([line_geom.coords[-2], line_geom.coords[-1]]).interpolate(1.0)

        dist_direct = start_pt.distance(p_first) + end_pt.distance(p_last)
        dist_flipped = start_pt.distance(p_last) + end_pt.distance(p_first)

        if dist_flipped < dist_direct:
            logger.info("Reversing fairway centerline vertices to match increasing route kilometrierung.")
            line_geom = LineString(line_geom.coords[::-1])

    display_name = river_name
    if not display_name:
        if "fairway_name" in gdf.columns and pd.notna(gdf["fairway_name"].iloc[0]):
            display_name = str(gdf["fairway_name"].iloc[0])
        elif "name" in gdf.columns and pd.notna(gdf["name"].iloc[0]):
            display_name = str(gdf["name"].iloc[0])
        else:
            display_name = f"RWS_Fairway_{fairway_id or 'unknown'}"

    chainage_start_m = 0.0
    if "routekmbegin" in gdf.columns:
        chainage_start_m = float(gdf["routekmbegin"].min()) * 1000.0

    return FairwayAxis(
        centerline_geom=line_geom,
        metric_crs=metric_crs,
        fairway_name=display_name,
        chainage_start_m=chainage_start_m,
    )
