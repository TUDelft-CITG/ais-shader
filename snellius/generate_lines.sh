#!/bin/bash
# snellius/generate_lines.sh
# Aggregates point pings into LineStrings/MultiLineStrings GPKG and Parquet matching the Marine Cadastre schema.

set -euo pipefail

echo "==> Generating track linestrings from trajectorized points..."
uv run ais-shader generate-lines \
    --input-file /scratch-shared/fbaart/data/rws/trajectorized.parquet \
    --output-gpkg /scratch-shared/fbaart/data/rws/trajectorized_lines.gpkg \
    --output-parquet /scratch-shared/fbaart/data/rws/trajectorized_lines.geoparquet

echo "==> Track linestring generation complete!"
