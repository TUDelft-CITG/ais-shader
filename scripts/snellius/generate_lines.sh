#!/bin/bash
# snellius/generate_lines.sh
# Aggregates point pings into LineStrings/MultiLineStrings GPKG and Parquet matching the Marine Cadastre schema.

set -euo pipefail

USER_NAME=${USER:-fbaart}
DATA_DIR="/scratch-shared/${USER_NAME}/data/rws"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VESSEL_CODES="$REPO_DIR/resources/vessel_groups.json"

echo "==> Generating track linestrings from trajectorized points..."
if [ -f "$VESSEL_CODES" ]; then
    echo "Using vessel codes configuration: $VESSEL_CODES"
    uv run ais-shader trajectory to-linestring \
        --input-file "$DATA_DIR/trajectorized.parquet" \
        --output-file "$DATA_DIR/trajectorized_lines.geoparquet" \
        --vessel-codes-json "$VESSEL_CODES"
else
    uv run ais-shader trajectory to-linestring \
        --input-file "$DATA_DIR/trajectorized.parquet" \
        --output-file "$DATA_DIR/trajectorized_lines.geoparquet"
fi

echo "==> Track linestring generation complete!"
