#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <dataset_dir> [years...]" >&2
    echo "  dataset_dir: path to a single RWS *.7z.geoparquet dataset directory (year=/month= partitioned)." >&2
    echo "               Its parent directory is used as the base dir for config_multiband.toml, intermediate/, and maps/." >&2
    echo "  years: optional list of years to process (default: all years found in dataset_dir)" >&2
    exit 1
fi

DATASET_DIR="$1"
shift
YEARS=("$@")

BASE_DIR="$(dirname "$DATASET_DIR")"
INTERMEDIATE_DIR="$BASE_DIR/intermediate"
MAPS_DIR="$BASE_DIR/maps"
ZOOM=15
VESSEL_ID_COL="${VESSEL_ID_COL:-mmsi}"
TIME_COL="${TIME_COL:-base_date_time}"
TAG_PREFIX="$(basename "$BASE_DIR")"

mkdir -p "$INTERMEDIATE_DIR" "$MAPS_DIR"

CONFIG_PATH="$BASE_DIR/config_multiband.toml"
if [ ! -f "$CONFIG_PATH" ]; then
    echo "Missing config file: $CONFIG_PATH" >&2
    exit 1
fi

process_month() {
    local year="$1"
    local month="$2"
    local tag="${TAG_PREFIX}_${year}_${month}"

    echo "=== Processing ${year}-${month} ==="

    # Stage 1: Preprocess Points
    # --no-spatial-index: this is a small, regional single-area dataset, so
    # dask-geopandas spatial partitioning (useful for pruning partitions across
    # a country-scale extent) is pure overhead here.
    uv run ais-shader preprocess \
        "$DATASET_DIR/year=$year/month=$month" \
        --output-file "$INTERMEDIATE_DIR/${tag}_preprocessed.geoparquet" \
        --no-spatial-index

    # Stage 2: Filter Speed/Position Outliers
    uv run ais-shader trajectory filter-outliers \
        "$INTERMEDIATE_DIR/${tag}_preprocessed.geoparquet" \
        --config-file "$CONFIG_PATH" \
        --output-file "$INTERMEDIATE_DIR/${tag}_cleaned.geoparquet"

    # Stage 3: Trajectorize
    # --exclude-moored: RWS river traffic includes many permanently moored
    # vessels whose GPS jitter (position noise while stationary) otherwise
    # gets split into thousands of spurious short trips.
    uv run ais-shader trajectory compute \
        "$INTERMEDIATE_DIR/${tag}_cleaned.geoparquet" \
        --vessel-id-col "$VESSEL_ID_COL" \
        --time-col "$TIME_COL" \
        --partition-method vessel \
        --gap-threshold-hours 0.1666666 \
        --exclude-moored \
        --output-file "$INTERMEDIATE_DIR/${tag}_trajectorized.geoparquet"

    # Stage 4: Generate Segments
    # --sog-knots: confirmed from RWS data (sog tops out at 102.3, in 0.1-knot
    # steps of real vessel speeds) that this feed is already in knots, not
    # raw AIS units needing /10.
    uv run ais-shader trajectory to-segment \
        "$INTERMEDIATE_DIR/${tag}_trajectorized.geoparquet" \
        --output-file "$INTERMEDIATE_DIR/${tag}_segments.geoparquet" \
        --sog-knots

    # Stage 4b: Generate full trajectory LineStrings (one line per voyage)
    uv run ais-shader trajectory to-linestring \
        "$INTERMEDIATE_DIR/${tag}_trajectorized.geoparquet" \
        --output-file "$INTERMEDIATE_DIR/${tag}_lines.geoparquet"

    # Stage 5: Preprocess Segments
    uv run ais-shader preprocess \
        "$INTERMEDIATE_DIR/${tag}_segments.geoparquet" \
        --output-file "$INTERMEDIATE_DIR/${tag}_segments_preprocessed.geoparquet" \
        --no-spatial-index

    # Stage 6: Render Multi-Band Tiles
    local render_run_dir="$MAPS_DIR/render_run_${tag}"
    rm -rf "$render_run_dir"
    uv run ais-shader render \
        --config-file "$CONFIG_PATH" \
        --input-file "$INTERMEDIATE_DIR/${tag}_segments_preprocessed.geoparquet" \
        --output-dir "$render_run_dir"

    local actual_run_dir
    actual_run_dir=$(ls -d "$render_run_dir"/run_* | head -1)

    # Stage 7: Postprocess Multi-Band Pyramid
    uv run ais-shader postprocess \
        --config-file "$CONFIG_PATH" \
        --run-dir "$actual_run_dir" \
        --base-zoom "$ZOOM" \
        --cogs

    echo "=== Done ${year}-${month} ==="
}

if [ "${#YEARS[@]}" -eq 0 ]; then
    YEARS=()
    for y in "$DATASET_DIR"/year=*; do
        [ -d "$y" ] || continue
        YEARS+=("$(basename "$y" | cut -d= -f2)")
    done
fi

for year in "${YEARS[@]}"; do
    months=$(ls -d "$DATASET_DIR/year=$year"/month=* | xargs -n1 basename | cut -d= -f2 | sort)
    for month in $months; do
        process_month "$year" "$month"
    done
done

echo "All months processed successfully."
