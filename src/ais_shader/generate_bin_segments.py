import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString
from pathlib import Path

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate flattened bin segments from passage velocities.")
    parser.add_argument("--input-file", type=str, default="data/PassageLine_NL_velocities.geojson", help="Path to input velocities GeoJSON.")
    parser.add_argument("--output-file", type=str, default="data/PassageLine_NL_bin_segments.geojson", help="Path to output GeoJSON.")
    args = parser.parse_args()
    
    input_path = Path(args.input_file)
    output_path = Path(args.output_file)
    
    print(f"Loading velocities GeoJSON from {input_path}...")
    gdf = gpd.read_file(input_path)
    
    # We will build a list of records for the new flattened layer
    records = []
    
    # Speed labels to sum for verification or lookup if needed
    directions = ['up', 'down']
    
    for _, row in gdf.iterrows():
        passage_id = row['PassageId']
        geom = row['geometry']
        
        # Check if geometry is valid LineString
        if not isinstance(geom, LineString):
            # If MultiLineString or other, take the longest part or centroid, but passage lines are LineStrings
            if geom.geom_type == 'MultiLineString':
                geom = max(geom.geoms, key=lambda g: g.length)
            else:
                continue
                
        # We need normalized interpolation, so we interpolate on EPSG:3857 for accurate metric splitting
        # Reproject line temporarily to EPSG:3857 to do equal length splits
        geom_3857 = gpd.GeoSeries([geom], crs="EPSG:4326").to_crs("EPSG:3857").iloc[0]
        
        for i in range(20):
            # Get start and end points of the 1/20th segment in EPSG:3857
            p1_3857 = geom_3857.interpolate(i / 20.0, normalized=True)
            p2_3857 = geom_3857.interpolate((i + 1) / 20.0, normalized=True)
            segment_3857 = LineString([p1_3857, p2_3857])
            
            # Reproject segment back to EPSG:4326
            segment_4326 = gpd.GeoSeries([segment_3857], crs="EPSG:3857").to_crs("EPSG:4326").iloc[0]
            
            for d in directions:
                freq_col = f"loc_bin_{i}_{d}"
                median_col = f"median_speed_loc_{i}_{d}"
                
                freq = int(row.get(freq_col, 0))
                median_speed = float(row.get(median_col, 0.0))
                
                # Default to 0.0 if nan
                if pd.isna(median_speed):
                    median_speed = 0.0
                    
                records.append({
                    'PassageId': passage_id,
                    'BinIndex': i,
                    'Direction': d,
                    'Frequency': freq,
                    'MedianSpeed': median_speed,
                    'geometry': segment_4326
                })
                
    output_gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")
    
    # Filter out entries where Frequency is 0 to keep dataset clean
    output_gdf = output_gdf[output_gdf['Frequency'] > 0].copy()
    
    print(f"Saving {len(output_gdf)} bin segments to {output_path}...")
    output_gdf.to_file(output_path, driver="GeoJSON")
    print("Done!")

if __name__ == "__main__":
    main()
