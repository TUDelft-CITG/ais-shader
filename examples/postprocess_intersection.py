#!/usr/bin/env python
"""
examples/postprocess_intersection.py

An example post-processing script demonstrating how to intersect vessel tracks
with custom GeoJSON LineString gates (e.g., 'south' and 'east') and append
binary indicator columns (sails_through_south, sails_through_east) to all the
point and segment epoch-normalized datasets.

It also fetches separation zones (seperatiezones) and shipping lanes (scheepvaart)
from Rijkswaterstaat FeatureServer REST APIs to check if vessels touch or cross them.
"""

import geopandas as gpd
import pandas as pd
from pathlib import Path
import json

def main():
    # Define directories and input/output paths
    data_dir = Path('/scratch-shared/fbaart/data/rws')
    tracks_path = data_dir / 'trajectorized_lines.geoparquet'
    if not tracks_path.exists():
        tracks_path = data_dir / 'trajectorized_lines.parquet'
    
    epoch_points_path = data_dir / 'trajectorized_epochs.geoparquet'
    rt_segments_path = data_dir / 'trajectorized_segments.geoparquet'
    epoch_segments_path = data_dir / 'trajectorized_segments_epochs.geoparquet'
    
    # Custom GeoJSON gates
    geojson_gates = {
        "type": "FeatureCollection",
        "name": "intersections",
        "crs": { "type": "name", "properties": { "name": "urn:ogc:def:crs:OGC:1.3:CRS84" } },
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "south"},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [3.025637436535185, 53.768120305925372],
                        [3.204382396885693, 53.673200878028922]
                    ]
                }
            },
            {
                "type": "Feature",
                "properties": {"name": "east"},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [3.536664694973178, 54.006502787483484],
                        [3.561872317586711, 53.885793186988273]
                    ]
                }
            }
        ]
    }
    
    print("Loading custom GeoJSON gates...")
    gates_gdf = gpd.GeoDataFrame.from_features(geojson_gates, crs="EPSG:4326")
    gate_dict = {row['name']: row['geometry'] for _, row in gates_gdf.iterrows()}
    
    # Fetch separation zones (seperatiezones) from Rijkswaterstaat REST FeatureServer
    print("Fetching separation zones (seperatiezones) from Rijkswaterstaat REST API...")
    sep_url = 'https://geo.rijkswaterstaat.nl/arcgis/rest/services/GDR/nwp_structuurvisiekaart_noordzee/FeatureServer/16/query?where=1%3D1&outFields=*&f=geojson'
    sep_gdf = gpd.read_file(sep_url)
    sep_union = sep_gdf.geometry.unary_union
    
    # Fetch shipping lanes (scheepvaart) from Rijkswaterstaat REST FeatureServer
    print("Fetching shipping lanes (scheepvaart) from Rijkswaterstaat REST API...")
    lanes_url = 'https://geo.rijkswaterstaat.nl/arcgis/rest/services/GDR/nwp_structuurvisiekaart_noordzee/FeatureServer/17/query?where=1%3D1&outFields=*&f=geojson'
    lanes_gdf = gpd.read_file(lanes_url)
    lanes_union = lanes_gdf.geometry.unary_union
    
    print(f"Loading track trajectories from {tracks_path}...")
    if not tracks_path.exists():
        print(f"Error: {tracks_path} does not exist. Run track linestring generation first.")
        return
    tracks_gdf = gpd.read_parquet(tracks_path)
    
    # Calculate intersections per trip
    print("Calculating intersections for each trip track...")
    intersection_results = pd.DataFrame(index=tracks_gdf.index)
    
    # 1. Custom Gates
    for gate_name, gate_geom in gate_dict.items():
        col_name = f'sails_through_{gate_name}'
        print(f"  Checking intersections for gate: '{gate_name}'...")
        intersects = tracks_gdf.geometry.intersects(gate_geom)
        intersection_results[col_name] = intersects.astype(int)
        
    # 2. Seperatiezones (touches_seperatiezones)
    print("  Checking intersections for separation zones (seperatiezones)...")
    sep_intersects = tracks_gdf.geometry.intersects(sep_union)
    intersection_results['touches_seperatiezones'] = sep_intersects.astype(int)
    
    # 3. Scheepvaart (crosses_scheepvaart)
    print("  Checking intersections for shipping lanes (scheepvaart)...")
    lanes_intersects = tracks_gdf.geometry.intersects(lanes_union)
    intersection_results['crosses_scheepvaart'] = lanes_intersects.astype(int)
    
    print("\nIntersection statistics per trip:")
    for col in intersection_results.columns:
        print(f"Column: {col}")
        print(intersection_results[col].value_counts().to_string())
        print()
        
    # Apply indicator columns to each target output file
    target_files = [
        (epoch_points_path, "parquet"),
        (rt_segments_path, "parquet"),
        (epoch_segments_path, "parquet"),
        (data_dir / 'trajectorized_lines.geoparquet', "parquet"),
        (data_dir / 'trajectorized_lines.parquet', "parquet"),
        (data_dir / 'trajectorized_lines.gpkg', "gpkg")
    ]
    
    for file_path, file_type in target_files:
        if not file_path.exists():
            print(f"Warning: {file_path} not found. Skipping.")
            continue
            
        print(f"Updating dataset: {file_path}...")
        if file_type == "parquet":
            gdf = gpd.read_parquet(file_path)
        else:
            gdf = gpd.read_file(file_path, layer="vessel_tracks")
            
        # Add the indicator columns to the dataset rows mapping by trip_id/index
        for col_name in intersection_results.columns:
            if 'trip_id' in gdf.columns:
                gdf[col_name] = gdf['trip_id'].map(intersection_results[col_name]).fillna(0).astype(int)
            elif 'index' in gdf.columns:
                gdf[col_name] = gdf['index'].map(intersection_results[col_name]).fillna(0).astype(int)
            else:
                gdf[col_name] = gdf.index.map(intersection_results[col_name]).fillna(0).astype(int)
            
        print(f"Saving updated dataset back to {file_path}...")
        if file_type == "parquet":
            gdf.to_parquet(file_path)
        else:
            gdf.to_file(file_path, driver="GPKG", layer="vessel_tracks")
        print("  Done!")
        
    print("\nAll datasets successfully updated with gate, seperatiezones, and scheepvaart flags!")

if __name__ == "__main__":
    main()
