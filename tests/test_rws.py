import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, MultiLineString, Point

from ais_shader.rws import (
    build_rws_fairway,
    fetch_rws_fairway_sections,
    fetch_rws_kilometer_markers,
)


def test_build_rws_fairway_from_mock_gdf():
    # Construct 3 consecutive fairway sections in RD New (EPSG:28992)
    sec1 = LineString([(100000, 400000), (105000, 400000)])
    sec2 = LineString([(105000, 400000), (110000, 400000)])
    sec3 = LineString([(110000, 400000), (115000, 400000)])

    gdf = gpd.GeoDataFrame({
        "objectid": [1, 2, 3],
        "fairwayid": [15384, 15384, 15384],
        "fairway_name": ["Amsterdam-Rijnkanaal", "Amsterdam-Rijnkanaal", "Amsterdam-Rijnkanaal"],
        "routekmbegin": [10.0, 15.0, 20.0],
        "routekmend": [15.0, 20.0, 25.0],
        "geometry": [sec1, sec2, sec3],
    }, crs="EPSG:28992")

    axis = build_rws_fairway(data=gdf, metric_crs="EPSG:28992")
    assert axis.fairway_name == "Amsterdam-Rijnkanaal"
    assert np.isclose(axis.length_m, 15000.0, atol=0.1)
    assert np.isclose(axis.chainage_start_m, 10000.0, atol=0.1)

    # Test point projection along the fairway
    pts = gpd.GeoDataFrame({
        "geometry": [Point(102000, 400050), Point(112000, 399970)]
    }, crs="EPSG:28992")

    ann = axis.annotate_points(pts)
    assert np.isclose(ann["chainage_m"].iloc[0], 12000.0, atol=0.1)
    assert np.isclose(ann["cross_track_m"].iloc[0], 50.0, atol=0.1)
    assert np.isclose(ann["chainage_m"].iloc[1], 22000.0, atol=0.1)
    assert np.isclose(ann["cross_track_m"].iloc[1], -30.0, atol=0.1)


def test_build_rws_fairway_reverses_direction_if_inverted():
    # If sections are oriented from 100k to 110k, but linemerge happened to orient them backwards
    sec1 = LineString([(100000, 400000), (105000, 400000)])
    sec2 = LineString([(105000, 400000), (110000, 400000)])

    gdf = gpd.GeoDataFrame({
        "objectid": [1, 2],
        "fairwayid": [15384, 15384],
        "fairway_name": ["Amsterdam-Rijnkanaal", "Amsterdam-Rijnkanaal"],
        "routekmbegin": [0.0, 5.0],
        "routekmend": [5.0, 10.0],
        # Pass reversed list to linemerge to test orientation normalization
        "geometry": [sec2, sec1],
    }, crs="EPSG:28992")

    axis = build_rws_fairway(data=gdf, metric_crs="EPSG:28992")
    coords = np.array(axis.centerline_geom.coords)
    assert np.isclose(coords[0][0], 100000, atol=1.0)
    assert np.isclose(coords[-1][0], 110000, atol=1.0)


def test_build_rws_fairway_missing_crs():
    sec1 = LineString([(100000, 400000), (105000, 400000)])
    gdf = gpd.GeoDataFrame({
        "fairwayid": [15384],
        "fairway_name": ["Amsterdam-Rijnkanaal"],
        "routekmbegin": [0.0],
        "geometry": [sec1],
    })
    gdf.crs = None
    with pytest.raises(ValueError, match="Input data has no CRS set"):
        build_rws_fairway(data=gdf)


def test_build_rws_fairway_missing_routekmbegin():
    sec1 = LineString([(100000, 400000), (105000, 400000)])
    gdf = gpd.GeoDataFrame({
        "fairwayid": [15384],
        "fairway_name": ["Amsterdam-Rijnkanaal"],
        "geometry": [sec1],
    }, crs="EPSG:28992")
    with pytest.raises(KeyError, match="routekmbegin"):
        build_rws_fairway(data=gdf)


def test_build_rws_fairway_disjoint_geometry():
    # Two disconnected line segments
    sec1 = LineString([(100000, 400000), (105000, 400000)])
    sec2 = LineString([(110000, 400000), (115000, 400000)])  # 5km gap
    gdf = gpd.GeoDataFrame({
        "fairwayid": [15384, 15384],
        "fairway_name": ["Amsterdam-Rijnkanaal", "Amsterdam-Rijnkanaal"],
        "routekmbegin": [0.0, 10.0],
        "geometry": [sec1, sec2],
    }, crs="EPSG:28992")
    with pytest.raises(ValueError, match="do not form a single continuous line"):
        build_rws_fairway(data=gdf)


def test_build_rws_fairway_no_filters():
    with pytest.raises(ValueError, match="Must specify at least one of data, fairway_id, river_name, or bbox"):
        build_rws_fairway()


def test_fetch_rws_fairway_sections_no_filters():
    with pytest.raises(ValueError, match="Must specify at least one of fairway_id, name, or bbox"):
        fetch_rws_fairway_sections()


def test_fetch_rws_kilometer_markers_no_filters():
    with pytest.raises(ValueError, match="Must specify at least one of fairway_id or bbox"):
        fetch_rws_kilometer_markers()
