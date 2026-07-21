#!/bin/bash
#SBATCH --partition=rome
#SBATCH --nodes=2
#SBATCH --tasks-per-node=1
#SBATCH --cpus-per-task=128
#SBATCH --mem=120G
#SBATCH --time=02:00:00
#SBATCH --job-name=ais-trajectorize
#SBATCH --output=ais_trajectorize_%j.log

# Load required modules for CGAL C++ convex hull extension
module load 2025 CGAL/6.0.1-GCCcore-14.2.0 Boost/1.88.0-GCC-14.2.0 GMP/6.3.0-GCCcore-14.2.0 MPFR/4.2.2-GCCcore-14.2.0

# 1. Start the Dask Scheduler on the master node
SCHEDULER_HOST=$(hostname)
SCHEDULER_URL="tcp://${SCHEDULER_HOST}:8786"

echo "Starting Dask Scheduler at ${SCHEDULER_URL}..."
uv run dask-scheduler --host ${SCHEDULER_HOST} --port 8786 --dashboard-address :8787 &
SCHEDULER_PID=$!

# Wait for scheduler to start
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

# Wait for workers to connect
sleep 10

# 3. Run the trajectorize script connecting to the SLURM Dask Scheduler
echo "Launching trajectorize workflow..."
uv run ais-shader trajectory compute /projects/prjs2131/data/marine-cadastre/ais_2025_12 \
    -o /projects/prjs2131/data/marine-cadastre/ais_2025_12_trajectories.parquet \
    --scheduler ${SCHEDULER_URL} \
    --vessel-id-col mmsi \
    --time-col base_date_time \
    --shuffle-backend disk \
    --n-partitions 256 \
    --partition-method spatiotemporal

# 4. Clean up Dask cluster
echo "Tearing down Dask cluster..."
kill ${SCHEDULER_PID}
wait

echo "Trajectorize workflow complete!"
