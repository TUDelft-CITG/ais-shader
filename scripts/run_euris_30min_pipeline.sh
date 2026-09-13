#!/bin/bash
# run_euris_30min_pipeline.sh
#
# Crawls 30 minutes of live AIS vessel data from EURIS WebSocket API
# and runs encounter detection on the Amsterdam-Rijnkanaal and Lek waterways,
# exporting all results to a master GeoPackage (.gpkg).

set -euo pipefail

echo "================================================================================"
echo "Starting 30-Minute EURIS Ingestion and Encounter Pipeline"
echo "Time: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "================================================================================"

# 1. Load system environment modules for CGAL and C++ extensions
module load 2025 CGAL/6.0.1-GCCcore-14.2.0 Boost/1.88.0-GCC-14.2.0 GMP/6.3.0-GCCcore-14.2.0 MPFR/4.2.2-GCCcore-14.2.0

# 2. Run live crawl for 30 minutes (polling every 10 seconds)
echo "==> Crawling EURIS AIS stream for 30 minutes..."
uv run python scripts/crawl_euris.py --duration-minutes 30 --interval-seconds 10

# 3. Find the freshly created geoparquet dataset
LATEST_PARQUET=$(ls -t /scratch-shared/fbaart/data/euris_crawl/euris_crawl_*.geoparquet | head -n 1)
TAG=$(basename "$LATEST_PARQUET" .geoparquet | sed 's/euris_crawl_//')
OUTPUT_GPKG="/scratch-shared/fbaart/data/euris_crawl/euris_encounters_${TAG}.gpkg"
MASTER_GPKG="/scratch-shared/fbaart/data/euris_crawl/euris_encounters.gpkg"

echo "==> Fresh crawl file: $LATEST_PARQUET"
echo "==> Running encounter detection on Amsterdam-Rijnkanaal corridor..."

# 4. Run encounter detection and generate multi-layer GeoPackage
uv run python examples/detect_euris_encounters.py \
    --input-file "$LATEST_PARQUET" \
    --output-gpkg "$OUTPUT_GPKG" \
    --fairway-id 15384 \
    --river-name "Amsterdam-Rijnkanaal" \
    --metric-crs "EPSG:28992" \
    --max-distance 100.0 \
    --corridor-width 300.0 \
    --timeseries-step 15.0

# Copy/symlink as latest master
cp "$OUTPUT_GPKG" "$MASTER_GPKG"

echo "================================================================================"
echo "Pipeline Completed Successfully!"
echo "Timestamp Tag: $TAG"
echo "Raw Points GPKG: /scratch-shared/fbaart/data/euris_crawl/euris_crawl_${TAG}.gpkg"
echo "Encounters GPKG: $OUTPUT_GPKG"
echo "Master Symlink/Copy: $MASTER_GPKG"
echo "================================================================================"
