import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

from ais_shader.fairway import FairwayAxis
from ais_shader.events import detect_encounters


def test_fairway_axis_mock():
    # Straight channel along X axis from X=0 to X=10,000 (10 km)
    centerline = LineString([(0, 0), (10000, 0)])
    axis = FairwayAxis(centerline, metric_crs="EPSG:3857", mile_start=100.0, mile_scale=1.0 / 1609.344)

    # Test point projection
    pts = gpd.GeoDataFrame({
        "geometry": [Point(1609.344, 50.0), Point(3218.688, -30.0)]
    }, crs="EPSG:3857")

    ann_pts = axis.annotate_points(pts)
    assert np.isclose(ann_pts["river_mile"].iloc[0], 101.0, atol=0.05)
    assert np.isclose(ann_pts["river_mile"].iloc[1], 102.0, atol=0.05)
    assert np.isclose(ann_pts["cross_track_m"].iloc[0], 50.0, atol=1.0)
    assert np.isclose(ann_pts["cross_track_m"].iloc[1], -30.0, atol=1.0)


def test_fairway_segment_kinematics():
    centerline = LineString([(0, 0), (10000, 0)])
    axis = FairwayAxis(centerline, metric_crs="EPSG:3857", mile_start=100.0, mile_scale=1.0 / 1609.344)

    # Segment 1: Upbound along X axis (0 -> 100 in 20s = 5 m/s)
    # Segment 2: Downbound along X axis (500 -> 400 in 20s = -5 m/s)
    # Segment 3: Crossing channel across Y axis (100, -100 -> 100, 100 in 20s = 10 m/s cross)
    segments = gpd.GeoDataFrame({
        "MMSI": ["111", "222", "333"],
        "trip_id": [1, 2, 3],
        "segment_duration_s": [20.0, 20.0, 20.0],
        "geometry": [
            LineString([(0, 0), (100, 0)]),
            LineString([(500, 0), (400, 0)]),
            LineString([(100, -100), (100, 100)]),
        ]
    }, crs="EPSG:3857")

    ann_segs = axis.annotate_segments(segments)
    assert ann_segs["fairway_direction"].tolist() == ["upbound", "downbound", "crossing"]
    assert np.isclose(ann_segs["fairway_speed_mps"].iloc[0], 5.0, atol=0.1)
    assert np.isclose(ann_segs["fairway_speed_mps"].iloc[1], -5.0, atol=0.1)


def test_fairway_encounter_classification_in_bend():
    # Centerline forms a 90-degree river bend: (0,0) -> (1000,0) -> (1000, 1000)
    centerline = LineString([(0, 0), (1000, 0), (1000, 1000)])
    axis = FairwayAxis(centerline, metric_crs="EPSG:3857", mile_start=0.0, mile_scale=1.0 / 1600.0)

    # In a bend, opposing vessels have compass headings diverging by 90 degrees (would be 'crossing' in Euclidean!)
    # But along fairway, one is 'upbound' and one is 'downbound'
    enc_type = axis.classify_fairway_encounter(
        dir1="upbound", dir2="downbound",
        speed1=5.0, speed2=5.0,
        ds_start=-100.0, ds_end=100.0, dv_along=0.0
    )
    assert enc_type == "head-on"

    # Same direction with along-track order flip -> overtaking
    enc_overtaking = axis.classify_fairway_encounter(
        dir1="upbound", dir2="upbound",
        speed1=8.0, speed2=4.0,
        ds_start=-50.0, ds_end=50.0, dv_along=4.0
    )
    assert enc_overtaking == "overtaking"

    # Same direction without order flip -> parallel sailing
    enc_parallel = axis.classify_fairway_encounter(
        dir1="upbound", dir2="upbound",
        speed1=5.0, speed2=5.0,
        ds_start=50.0, ds_end=50.0, dv_along=0.0
    )
    assert enc_parallel == "parallel_sailing"


def test_get_utm_crs_for_lon_lat():
    from ais_shader.fairway import get_utm_crs_for_lon_lat

    # Normal locations
    assert get_utm_crs_for_lon_lat(-90.0, 30.0) == "EPSG:32616"  # Mississippi, Northern hemisphere
    assert get_utm_crs_for_lon_lat(0.0, 51.0) == "EPSG:32631"    # Greenwich, Northern hemisphere
    assert get_utm_crs_for_lon_lat(0.0, -20.0) == "EPSG:32731"   # Southern hemisphere

    # Extreme / boundary longitudes
    assert get_utm_crs_for_lon_lat(180.0, 10.0) == "EPSG:32660"  # Valid zone 60 at +180 boundary
    assert get_utm_crs_for_lon_lat(-180.0, 10.0) == "EPSG:32601" # Zone 1 at -180 boundary
    assert get_utm_crs_for_lon_lat(179.9, -10.0) == "EPSG:32760"
    assert get_utm_crs_for_lon_lat(-179.9, -10.0) == "EPSG:32701"

    # Out of range coordinates must fail fast and early
    with pytest.raises(ValueError, match="Longitude.*out of valid range"):
        get_utm_crs_for_lon_lat(181.0, 0.0)
    with pytest.raises(ValueError, match="Longitude.*out of valid range"):
        get_utm_crs_for_lon_lat(-181.0, 0.0)
    with pytest.raises(ValueError, match="Latitude.*out of valid range"):
        get_utm_crs_for_lon_lat(0.0, 95.0)
    with pytest.raises(ValueError, match="Latitude.*out of valid range"):
        get_utm_crs_for_lon_lat(0.0, -95.0)

