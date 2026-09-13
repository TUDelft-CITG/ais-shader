#!/bin/bash
#SBATCH --partition=rome
#SBATCH --nodes=1
#SBATCH --tasks-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=60G
#SBATCH --time=01:00:00
#SBATCH --job-name=ais-encounters-1h
#SBATCH --output=ais_encounters_1h_%j.log

set -euo pipefail

echo "==> Job ID: ${SLURM_JOB_ID:-local}"
echo "==> Running on host: $(hostname)"
echo "==> Start time: $(date -u '+%Y-%m-%d %H:%M:%SZ')"

# Load required C++ / CGAL / Boost modules for convex hull extension
echo "==> Loading modules..."
module load 2025 CGAL/6.0.1-GCCcore-14.2.0 Boost/1.88.0-GCC-14.2.0 GMP/6.3.0-GCCcore-14.2.0 MPFR/4.2.2-GCCcore-14.2.0

USER_NAME=${USER:-fbaart}
INPUT_SEGS="/scratch-shared/${USER_NAME}/data/mississippi_3h/mississippi_3h_segments_utm15n.geoparquet"
FAIRWAY_CENTERLINE="/scratch-shared/${USER_NAME}/data/marine-cadastre/mississippi_fairway_centerline_utm15n.geoparquet"
OUTPUT_DIR="/scratch-shared/${USER_NAME}/data/mississippi_1h"

mkdir -p "$OUTPUT_DIR"

ENCOUNTERS_PARQUET="$OUTPUT_DIR/mississippi_1h_encounters.geoparquet"
TIMESERIES_PARQUET="$OUTPUT_DIR/mississippi_1h_timeseries.geoparquet"
STATIONARY_PARQUET="$OUTPUT_DIR/mississippi_1h_stationary.geoparquet"

# Start Dask Distributed Scheduler and Workers on the node
SCHEDULER_HOST=$(hostname)
SCHEDULER_URL="tcp://${SCHEDULER_HOST}:8786"

echo "==> Starting Dask Scheduler at ${SCHEDULER_URL}..."
uv run dask-scheduler --host "${SCHEDULER_HOST}" --port 8786 --dashboard-address :8787 &
SCHEDULER_PID=$!

trap "kill \$(jobs -p) 2>/dev/null || true" EXIT

sleep 5

echo "=========================================================================="
echo " DASK DASHBOARD ACTIVE"
echo " Run from your local terminal to forward (using local port 8788):"
echo "   ssh -N -L 8788:${SCHEDULER_HOST}:8787 snellius"
echo " Then browse: http://localhost:8788/status"
echo "=========================================================================="

echo "==> Starting Dask Workers (4 workers x 8 threads, 12GB memory each)..."
uv run dask-worker "${SCHEDULER_URL}" \
    --nthreads 8 \
    --nworkers 4 \
    --memory-limit 12GB \
    --no-dashboard &

sleep 10

echo "==> Running encounter detection for 1-hour time slice (2026-03-31 00:00:00 to 01:00:00 UTC)..."
uv run ais-shader events encounters "$INPUT_SEGS" \
    -o "$ENCOUNTERS_PARQUET" \
    --start-time "2026-03-31 00:00:00" \
    --end-time "2026-03-31 01:00:00" \
    --fairway-markers "$FAIRWAY_CENTERLINE" \
    --metric-crs "EPSG:32615" \
    --scheduler "${SCHEDULER_URL}" \
    --time-bin-minutes 15.0 \
    --timeseries-file "$TIMESERIES_PARQUET" \
    --timeseries-step 30.0 \
    --stationary-file "$STATIONARY_PARQUET" \
    --exclude-stationary both \
    --max-distance 100.0 \
    --merge-gap-minutes 10.0

echo "==> Encounter detection completed successfully!"
echo "==> Synchronizing QGIS styles..."
uv run python scripts/generate_qgis_styles.py

echo "==> Output artifacts:"
ls -lh "$OUTPUT_DIR"
echo "==> End time: $(date -u '+%Y-%m-%d %H:%M:%SZ')"
