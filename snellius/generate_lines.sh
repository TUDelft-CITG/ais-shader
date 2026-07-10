#!/bin/bash
# snellius/generate_lines.sh
# Aggregates point pings into LineStrings/MultiLineStrings GPKG and Parquet matching the Marine Cadastre schema.

set -euo pipefail

USER_NAME=${USER:-fbaart}
DATA_DIR="/scratch-shared/${USER_NAME}/data/rws"
VESSEL_CODES="/home/${USER_NAME}/src/openvts/test-data/vts-vessel-codes-expanded.json"

echo "==> Generating track linestrings from trajectorized points..."
if [ -f "$VESSEL_CODES" ]; then
    echo "Using vessel codes configuration: $VESSEL_CODES"
    uv run ais-shader generate-lines \
        --input-file "$DATA_DIR/trajectorized.parquet" \
        --output-file "$DATA_DIR/trajectorized_lines.geoparquet" \
        --vessel-codes-json "$VESSEL_CODES"
else
    uv run ais-shader generate-lines \
        --input-file "$DATA_DIR/trajectorized.parquet" \
        --output-file "$DATA_DIR/trajectorized_lines.geoparquet"
fi

echo "==> Track linestring generation complete!"
