#!/bin/bash
#SBATCH --partition=rome
#SBATCH --nodes=1
#SBATCH --tasks-per-node=1
#SBATCH --cpus-per-task=128
#SBATCH --mem=220G
#SBATCH --time=02:00:00
#SBATCH --job-name=ais-mississippi-day
#SBATCH --output=ais_mississippi_day_%j.log

set -euo pipefail

# Fail-fast check for cluster environment
echo "==> Job ID: ${SLURM_JOB_ID:-local}"
echo "==> Running on host: $(hostname)"
echo "==> Start time: $(date -u '+%Y-%m-%d %H:%M:%SZ')"

# Load required C++ / CGAL / Boost modules for convex hull extension
echo "==> Loading modules..."
module load 2025 CGAL/6.0.1-GCCcore-14.2.0 Boost/1.88.0-GCC-14.2.0 GMP/6.3.0-GCCcore-14.2.0 MPFR/4.2.2-GCCcore-14.2.0

USER_NAME=${USER:-fbaart}
DATA_DIR="/scratch-shared/${USER_NAME}/data/mississippi_2026_03_31"
RAW_DIR="/scratch-shared/${USER_NAME}/data/noaa_raw"
FAIRWAY_MARKERS="/scratch-shared/${USER_NAME}/data/marine-cadastre/usace_river_mile_markers.gpkg"
NOAA_URL="https://noaaocm.blob.core.windows.net/ais/csv2/csv2026/ais-2026-03-31.csv.zst"

mkdir -p "$DATA_DIR" "$RAW_DIR"

RAW_ZST="$RAW_DIR/ais-2026-03-31.csv.zst"
RAW_CSV="$DATA_DIR/mississippi_raw_day.csv"

# 1. Download and decompress the 1-day NOAA AIS file if not already present
if [ ! -f "$RAW_CSV" ]; then
    if [ ! -f "$RAW_ZST" ]; then
        echo "==> Downloading NOAA 1-day AIS dataset: $NOAA_URL"
        curl -L -o "$RAW_ZST" "$NOAA_URL"
    else
        echo "==> Using cached NOAA archive: $RAW_ZST"
    fi
    echo "==> Decompressing 1-day raw CSV..."
    zstd -dc "$RAW_ZST" > "$RAW_CSV"
fi

# 2. Start Dask Distributed Scheduler and Workers on the node
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

echo "==> Starting Dask Workers (16 workers x 8 threads, 13GB memory each)..."
uv run dask-worker "${SCHEDULER_URL}" \
    --nthreads 8 \
    --nworkers 16 \
    --memory-limit 13GB \
    --no-dashboard &

sleep 10

# 3. Step 1: Convert raw CSV to standard flat GeoParquet with Mississippi BBOX filter
FLAT_PARQUET="$DATA_DIR/mississippi_flat.geoparquet"
if [ ! -e "$FLAT_PARQUET" ]; then
    echo "==> [1/4] Converting CSV to flat GeoParquet with Mississippi corridor bounding box..."
    uv run ais-shader convert csv "$RAW_CSV" \
        -o "$FLAT_PARQUET" \
        --bbox "-91.5,29.0,-89.0,31.0" \
        --scheduler "${SCHEDULER_URL}"
else
    echo "==> [1/4] Flat GeoParquet already exists: $FLAT_PARQUET"
fi

# 4. Step 2: Trajectorize using Dask compute
TRAJ_PARQUET="$DATA_DIR/mississippi_trajectories.parquet"
if [ ! -e "$TRAJ_PARQUET" ]; then
    echo "==> [2/4] Trajectorizing fixes into voyages using Dask..."
    uv run ais-shader trajectory compute "$FLAT_PARQUET" \
        -o "$TRAJ_PARQUET" \
        --scheduler "${SCHEDULER_URL}" \
        --vessel-id-col mmsi \
        --time-col base_date_time \
        --gap-threshold-hours 0.5 \
        --shuffle-backend disk \
        --n-partitions 64 \
        --partition-method spatiotemporal
else
    echo "==> [2/4] Trajectorized dataset already exists: $TRAJ_PARQUET"
fi

# 5. Step 3: Convert trajectories into point-pair segments
SEGS_PARQUET="$DATA_DIR/mississippi_segments.geoparquet"
if [ ! -e "$SEGS_PARQUET" ]; then
    echo "==> [3/4] Generating point-pair trajectory segments..."
    uv run ais-shader trajectory to-segment "$TRAJ_PARQUET" \
        -o "$SEGS_PARQUET"
else
    echo "==> [3/4] Segments already exist: $SEGS_PARQUET"
fi

# Preprocess fairway centerline if markers are present
FAIRWAY_CENTERLINE="/scratch-shared/${USER_NAME}/data/marine-cadastre/mississippi_fairway_centerline_utm15n.geoparquet"
if [ ! -f "$FAIRWAY_CENTERLINE" ] && [ -f "$FAIRWAY_MARKERS" ]; then
    echo "==> [Fairway Preproc] Building metric fairway centerline using build-us-centerline..."
    uv run ais-shader fairway build-us-centerline "$FAIRWAY_MARKERS" \
        -o "$FAIRWAY_CENTERLINE" \
        --river-name "MISSISSIPPI-LO" \
        --metric-crs "EPSG:32615"
fi

# 6. Step 4: Detect encounters and generate dynamic time series connecting lines
ENCOUNTERS_PARQUET="$DATA_DIR/mississippi_encounters.geoparquet"
TIMESERIES_PARQUET="$DATA_DIR/mississippi_encounter_timeseries.geoparquet"
STATIONARY_PARQUET="$DATA_DIR/mississippi_stationary_vessels.geoparquet"
echo "==> [4/4] Detecting vessel encounters and generating dynamic time series..."
FAIRWAY_ARG=""
if [ -f "$FAIRWAY_CENTERLINE" ]; then
    FAIRWAY_ARG="--fairway-markers $FAIRWAY_CENTERLINE --metric-crs EPSG:32615"
elif [ -f "$FAIRWAY_MARKERS" ]; then
    FAIRWAY_ARG="--fairway-markers $FAIRWAY_MARKERS --river-name MISSISSIPPI-LO --metric-crs EPSG:32615"
fi

uv run ais-shader events encounters "$SEGS_PARQUET" \
    -o "$ENCOUNTERS_PARQUET" \
    $FAIRWAY_ARG \
    --scheduler "${SCHEDULER_URL}" \
    --time-bin-minutes 15.0 \
    --timeseries-file "$TIMESERIES_PARQUET" \
    --timeseries-step 30.0 \
    --stationary-file "$STATIONARY_PARQUET" \
    --exclude-stationary both \
    --max-distance 600.0 \
    --merge-gap-minutes 10.0

echo "==> Mississippi 1-day pipeline finished successfully!"
echo "==> End time: $(date -u '+%Y-%m-%d %H:%M:%SZ')"
