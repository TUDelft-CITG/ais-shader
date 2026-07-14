#!/bin/bash
# snellius/generate_epochs.sh
# Generates point and segment trajectories (real-time and epoch-normalized versions).

set -euo pipefail

echo "==> Loading modules..."
module load 2025 CGAL/6.0.1-GCCcore-14.2.0 Boost/1.88.0-GCC-14.2.0 GMP/6.3.0-GCCcore-14.2.0 MPFR/4.2.2-GCCcore-14.2.0

USER_NAME=${USER:-fbaart}
DATA_DIR="/scratch-shared/${USER_NAME}/data/rws"

# Generate epoch-normalized points
uv run ais-shader trajectory compute \
    --input-file "$DATA_DIR/processed.parquet" \
    --output-file "$DATA_DIR/trajectorized_epochs.geoparquet" \
    --epoch-time

# Generate epoch-normalized segment-pairs
uv run ais-shader trajectory to-segment \
    --input-file "$DATA_DIR/trajectorized_epochs.geoparquet" \
    --output-file "$DATA_DIR/trajectorized_segments_epochs.geoparquet" \
    --epoch-time

echo "==> Epoch and segment generation complete!"
