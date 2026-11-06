"""
Flatten a passage-line velocities layer (the wide ``loc_bin_{i}_{direction}``
output of ``analysis.run_passage_analysis``) into one row per
(passage line, location bin, direction).

``run_passage_analysis`` already writes this layer directly as
``<stem>_bin_segments.geojson``; this script exists to regenerate it from an
existing velocities file without re-running the analysis. Both paths build the
bin geometries with ``crossings.bin_segment_geometry`` so the two layers are
guaranteed to line up.
"""

import argparse
import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString

from .crossings import N_LOC_BINS, bin_segment_geometry
from .vector_io import CRS_LONLAT, CRS_METRIC, read_vector, write_vector

logger = logging.getLogger(__name__)

DIRECTIONS = ['up', 'down']


def _longest_linestring(geom):
    """The passage line as a single LineString, or None if it isn't line-like."""
    if isinstance(geom, LineString):
        return geom
    if geom is not None and geom.geom_type == 'MultiLineString':
        return max(geom.geoms, key=lambda g: g.length)
    return None


def flatten_bin_segments(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Expand a wide velocities layer into one bin-segment row per bin and direction."""
    records = []
    for _, row in gdf.iterrows():
        geom = _longest_linestring(row['geometry'])
        if geom is None:
            continue
        # Bins must be split in a metric CRS to come out equal in length.
        geom_metric = gpd.GeoSeries([geom], crs=CRS_LONLAT).to_crs(CRS_METRIC).iloc[0]

        for i in range(N_LOC_BINS):
            segment_metric = bin_segment_geometry(geom_metric, i)
            segment_lonlat = gpd.GeoSeries([segment_metric], crs=CRS_METRIC).to_crs(CRS_LONLAT).iloc[0]

            for d in DIRECTIONS:
                median_speed = float(row.get(f"median_speed_loc_{i}_{d}", 0.0))
                records.append({
                    'PassageId': row['PassageId'],
                    'BinIndex': i,
                    'Direction': d,
                    'Frequency': int(row.get(f"loc_bin_{i}_{d}", 0)),
                    'MedianSpeed': 0.0 if pd.isna(median_speed) else median_speed,
                    'geometry': segment_lonlat,
                })

    out_gdf = gpd.GeoDataFrame(records, crs=CRS_LONLAT)
    # Drop empty bins to keep the layer small.
    return out_gdf[out_gdf['Frequency'] > 0].copy()


def main():
    parser = argparse.ArgumentParser(description="Generate flattened bin segments from passage velocities.")
    parser.add_argument("--input-file", type=str, default="data/PassageLine_NL_velocities.geojson",
                        help="Path to input velocities file (GeoJSON/GPKG/GeoParquet).")
    parser.add_argument("--output-file", type=str, default="data/PassageLine_NL_bin_segments.geojson",
                        help="Path to the output bin-segments file.")
    args = parser.parse_args()

    input_path = Path(args.input_file)
    output_path = Path(args.output_file)

    logger.info(f"Loading velocities from {input_path}...")
    out_gdf = flatten_bin_segments(read_vector(input_path))

    logger.info(f"Saving {len(out_gdf)} bin segments to {output_path}...")
    write_vector(out_gdf, output_path)
    logger.info("Done!")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    main()
