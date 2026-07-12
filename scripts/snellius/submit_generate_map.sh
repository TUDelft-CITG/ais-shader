#!/bin/bash
#SBATCH --partition=staging
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=60G
#SBATCH --time=00:30:00
#SBATCH --job-name=generate-us-map
#SBATCH --output=generate_us_map_%j.log

# Load modules
module load 2025 CGAL/6.0.1-GCCcore-14.2.0 Boost/1.88.0-GCC-14.2.0 GMP/6.3.0-GCCcore-14.2.0 MPFR/4.2.2-GCCcore-14.2.0

echo "Starting map generation on full dataset..."
.venv/bin/python docs/images/generate_us_hilbert_map.py
echo "Map generation complete!"
