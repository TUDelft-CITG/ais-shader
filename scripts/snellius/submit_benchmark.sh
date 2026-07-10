#!/bin/bash
#SBATCH --partition=rome
#SBATCH --nodes=2
#SBATCH --tasks-per-node=1
#SBATCH --cpus-per-task=128
#SBATCH --mem=120G
#SBATCH --time=04:00:00
#SBATCH --job-name=ais-benchmark
#SBATCH --output=ais_benchmark_%j.log

# Load required modules for CGAL C++ convex hull extension
module load 2025 CGAL/6.0.1-GCCcore-14.2.0 Boost/1.88.0-GCC-14.2.0 GMP/6.3.0-GCCcore-14.2.0 MPFR/4.2.2-GCCcore-14.2.0

# 1. Start the Dask Scheduler on the master node
SCHEDULER_HOST=$(hostname)
SCHEDULER_URL="tcp://${SCHEDULER_HOST}:8786"

echo "Starting Dask Scheduler at ${SCHEDULER_URL}..."
uv run dask-scheduler --host ${SCHEDULER_HOST} --port 8786 --dashboard-address :8787 &
SCHEDULER_PID=$!
sleep 5

# 2. Start Dask Workers on all nodes using srun
# We start 4 worker processes per node, each running 32 threads (totalling 128 cores per node)
# Each worker is allocated memory-limit of 28GB (totalling 112GB per node)
echo "Starting Dask Workers across allocated nodes..."
srun uv run dask-worker ${SCHEDULER_URL} \
    --nthreads 32 \
    --nworkers 4 \
    --memory-limit 28GB \
    --no-dashboard &
sleep 10

# 3. Run the MLflow benchmark sweep script
echo "Launching MLflow benchmark sweep..."
uv run python tests/run_benchmark.py \
    --scheduler ${SCHEDULER_URL} \
    --dataset-path /projects/prjs2131/data/marine-cadastre/ais_2025_12/ais-2025-12-01.parquet \
    --mlflow-tracking-uri sqlite:///mlflow.db

# 4. Clean up Dask cluster
echo "Tearing down Dask cluster..."
kill ${SCHEDULER_PID}
wait

echo "Benchmarking complete!"
