#!/bin/bash
#SBATCH --partition=rome
#SBATCH --nodes=1
#SBATCH --tasks-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=01:00:00
#SBATCH --job-name=ais-passage-analysis
#SBATCH --output=ais_passage_%j.log

echo "Starting passage analysis job..."
date

uv run ais-shader analyze-passage \
    --passage-file /scratch-shared/fbaart/data/euris-export/PassageLine_NL_20260224.geojson \
    --ais-dir /scratch-shared/fbaart/data/ais_data/20260430-2093161291.8-anonymous1-Noordzee_2025_01_TUD.parquet \
    --output-file /scratch-shared/fbaart/data/PassageLine_NL_velocities.geojson

echo "Passage analysis job finished."
date
