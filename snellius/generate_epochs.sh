#!/bin/bash
# snellius/generate_epochs.sh
# Generates point and segment trajectories (real-time and epoch-normalized versions).

set -euo pipefail

echo "==> Loading modules..."
module load 2025 CGAL/6.0.1-GCCcore-14.2.0 Boost/1.88.0-GCC-14.2.0 GMP/6.3.0-GCCcore-14.2.0 MPFR/4.2.2-GCCcore-14.2.0

echo "==> Running epoch and segment generation..."
uv run ais-shader generate-epochs \
    --input-file /scratch-shared/fbaart/data/rws/trajectorized.parquet \
    --output-dir /scratch-shared/fbaart/data/rws

echo "==> Epoch and segment generation complete!"
