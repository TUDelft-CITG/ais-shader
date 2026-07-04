#!/bin/bash
# compile_and_test.sh
# Loads modules, compiles the CGAL python extension, and runs the test suite.
# Keeps the user's interactive shell clean of module states.

set -euo pipefail

echo "==> Loading modules..."
module load 2025 CGAL/6.0.1-GCCcore-14.2.0 Boost/1.88.0-GCC-14.2.0 GMP/6.3.0-GCCcore-14.2.0 MPFR/4.2.2-GCCcore-14.2.0

echo "==> Compiling _cgal_hull.cpp..."
EXT_SUFFIX=$(uv run python -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))")
g++ -O3 -Wall -shared -std=c++17 -fPIC \
    $(uv run python -m pybind11 --includes) \
    -I$EBROOTCGAL/include \
    -I$EBROOTBOOST/include \
    -I$EBROOTGMP/include \
    -I$EBROOTMPFR/include \
    -L$EBROOTGMP/lib \
    -L$EBROOTMPFR/lib \
    -lgmp -lmpfr \
    src/ais_shader/_cgal_hull.cpp \
    -o src/ais_shader/_cgal_hull${EXT_SUFFIX}

echo "==> Running pytests..."
uv run pytest tests/test_moving_dask.py

echo "==> Running micro-benchmarks..."
uv run python /home/fbaart/.gemini/antigravity-cli/brain/e6d12f55-d77d-4260-b648-4c438aac1e89/scratch/test_cgal_hull.py

echo "==> Build and verification complete!"
