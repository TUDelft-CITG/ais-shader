import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

from ais_shader.fairway import FairwayAxis, get_utm_crs_for_lon_lat
from ais_shader.usace import build_usace_fairway


def test_fairway_axis_mock():
    # Straight channel along X axis from X=0 to X=10,000 (10 km)
    centerline = LineString([(0, 0), (10000, 0)])
    axis = FairwayAxis(centerline, metric_crs="EPSG:3857", chainage_start_m=1000.0)

    # Test point projection
    pts = gpd.GeoDataFrame({
        "geometry": [Point(1500.0, 50.0), Point(3000.0, -30.0)]
    }, crs="EPSG:3857")

    ann_pts = axis.annotate_points(pts)
    assert np.isclose(ann_pts["chainage_m"].iloc[0], 2500.0, atol=0.1)
    assert np.isclose(ann_pts["chainage_km"].iloc[0], 2.5, atol=0.001)
    assert np.isclose(ann_pts["cross_track_m"].iloc[0], 50.0, atol=0.1)
    assert np.isclose(ann_pts["cross_track_m"].iloc[1], -30.0, atol=0.1)


def test_fairway_segment_kinematics():
    centerline = LineString([(0, 0), (10000, 0)])
    axis = FairwayAxis(centerline, metric_crs="EPSG:3857", chainage_start_m=1000.0)

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
    assert np.isclose(ann_segs["fairway_start_m"].iloc[0], 1000.0, atol=0.1)
    assert np.isclose(ann_segs["fairway_end_m"].iloc[0], 1100.0, atol=0.1)
    assert np.isclose(ann_segs["fairway_start_km"].iloc[0], 1.0, atol=0.001)
    assert np.isclose(ann_segs["fairway_end_km"].iloc[0], 1.1, atol=0.001)


def test_fairway_crs_mismatch_raises_value_error():
    centerline = LineString([(0, 0), (10000, 0)])
    axis = FairwayAxis(centerline, metric_crs="EPSG:3857")

    # GeoDataFrame in geographic CRS (EPSG:4326) passed to metric FairwayAxis (EPSG:3857)
    segments_geo = gpd.GeoDataFrame({
        "MMSI": ["111"],
        "trip_id": [1],
        "segment_duration_s": [20.0],
        "geometry": [LineString([(-90.0, 30.0), (-90.01, 30.01)])]
    }, crs="EPSG:4326")

    with pytest.raises(ValueError, match="does not match FairwayAxis metric CRS"):
        axis.annotate_segments(segments_geo)

    points_geo = gpd.GeoDataFrame({
        "geometry": [Point(-90.0, 30.0)]
    }, crs="EPSG:4326")

    with pytest.raises(ValueError, match="does not match FairwayAxis metric CRS"):
        axis.annotate_points(points_geo)


def test_fairway_encounter_classification_in_bend():
    # Centerline forms a 90-degree river bend: (0,0) -> (1000,0) -> (1000, 1000)
    centerline = LineString([(0, 0), (1000, 0), (1000, 1000)])
    axis = FairwayAxis(centerline, metric_crs="EPSG:3857")

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


def test_fairway_axis_save_and_load_roundtrip(tmp_path):
    centerline = LineString([(0, 0), (5000, 0), (10000, 5000)])
    axis = FairwayAxis(
        centerline,
        metric_crs="EPSG:32615",
        fairway_name="TEST-RIVER",
        chainage_start_m=50000.0,
    )

    out_file = tmp_path / "test_centerline.geoparquet"
    gdf = axis.to_geodataframe()
    gdf.to_parquet(out_file)

    loaded_from_file = FairwayAxis.from_file(out_file)
    assert loaded_from_file.fairway_name == "TEST-RIVER"
    assert loaded_from_file.metric_crs == "EPSG:32615"
    assert loaded_from_file.chainage_start_m == 50000.0
    assert np.isclose(loaded_from_file.length_m, axis.length_m)

    loaded_via_load = FairwayAxis.load(out_file)
    assert loaded_via_load.fairway_name == "TEST-RIVER"
    assert loaded_via_load.metric_crs == "EPSG:32615"
    assert np.isclose(loaded_via_load.length_m, axis.length_m)


def test_usace_builder_mock():
    # Mock USACE River Mile Markers GeoDataFrame
    markers_gdf = gpd.GeoDataFrame({
        "RIVER_NAME": ["MOCK-RIVER"] * 5,
        "MILE": [10.0, 11.0, 12.0, 13.0, 14.0],
        "geometry": [
            Point(0.0, 0.0),
            Point(1609.344, 0.0),
            Point(3218.688, 0.0),
            Point(4828.032, 0.0),
            Point(6437.376, 0.0),
        ]
    }, crs="EPSG:3857")

    axis = build_usace_fairway(markers_gdf, river_name="MOCK-RIVER", metric_crs="EPSG:3857")
    assert isinstance(axis, FairwayAxis)
    assert axis.fairway_name == "MOCK-RIVER"
    assert np.isclose(axis.chainage_start_m, 10.0 * 1609.344)
    assert np.isclose(axis.length_m, 6437.376, atol=50.0)


def test_fairway_encounter_outside_fairway_corridor():
    # Centerline along x-axis
    centerline = LineString([(0, 0), (10000, 0)])
    axis = FairwayAxis(centerline, metric_crs="EPSG:3857")

    # Both vessels far outside corridor (Morgan City style, rel_angle ~6 deg)
    enc = axis.classify_fairway_encounter(
        dir1="outside_fairway",
        dir2="outside_fairway",
        speed1=1.4,
        speed2=3.5,
        ds_start=50.0,
        ds_end=-50.0,
        dv_along=-2.1,
        heading1=100.0,
        heading2=106.0,
    )
    assert enc == "overtaking"

