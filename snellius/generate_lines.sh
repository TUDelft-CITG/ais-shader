#!/bin/bash
# snellius/generate_lines.sh
# Aggregates point pings into LineStrings/MultiLineStrings GPKG and Parquet matching the Marine Cadastre schema.

set -euo pipefail

USER_NAME=${USER:-fbaart}
DATA_DIR="/scratch-shared/${USER_NAME}/data/rws"

echo "==> Generating track linestrings from trajectorized points..."
uv run ais-shader generate-lines \
    --input-file "$DATA_DIR/trajectorized.parquet" \
    --output-gpkg "$DATA_DIR/trajectorized_lines.gpkg" \
    --output-parquet "$DATA_DIR/trajectorized_lines.geoparquet"

echo "==> Track linestring generation complete!"
