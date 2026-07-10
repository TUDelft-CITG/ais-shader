#!/bin/bash
# snellius/process_rws.sh
# Processes the RWS NDJSON dataset: converts to GeoParquet, preprocesses (reprojects and partitions), and trajectorizes.

set -euo pipefail

echo "==> Loading modules..."
module load 2025 CGAL/6.0.1-GCCcore-14.2.0 Boost/1.88.0-GCC-14.2.0 GMP/6.3.0-GCCcore-14.2.0 MPFR/4.2.2-GCCcore-14.2.0

USER_NAME=${USER:-fbaart}
DATA_DIR="/scratch-shared/${USER_NAME}/data/rws"
INPUT_FILE="/scratch-shared/${USER_NAME}/data/ais_data/20260704-2098787588.3-anonymous1-Parken_67.ndjson"

echo "==> Step 1: Converting NDJSON to flat GeoParquet..."
uv run ais-shader convert-ndjson \
    --input-file "$INPUT_FILE" \
    --output-file "$DATA_DIR/flat.parquet"

echo "==> Step 2: Preprocessing GeoParquet (reproject to EPSG:3857 and spatially partition)..."
uv run ais-shader preprocess \
    --input-file "$DATA_DIR/flat.parquet" \
    --output-file "$DATA_DIR/processed.parquet"

echo "==> Step 3: Trajectorizing preprocessed dataset (voyage segmentation & features)..."
uv run ais-shader trajectorize \
    --input-file "$DATA_DIR/processed.parquet" \
    --output-file "$DATA_DIR/trajectorized.parquet" \
    --coords-are-degrees \
    --gap-threshold-hours 0.333333

echo "==> All steps completed successfully!"
