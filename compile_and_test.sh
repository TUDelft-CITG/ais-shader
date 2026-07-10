#!/bin/bash
# compile_and_test.sh
# Loads modules, compiles the CGAL python extension, and runs the test suite.
# Keeps the user's interactive shell clean of module states.

set -euo pipefail

echo "==> Loading modules..."
module load 2025 CGAL/6.0.1-GCCcore-14.2.0 Boost/1.88.0-GCC-14.2.0 GMP/6.3.0-GCCcore-14.2.0 MPFR/4.2.2-GCCcore-14.2.0

echo "==> Building C++ extensions..."
uv pip install -e .

echo "==> Running pytests..."
uv run pytest tests/

echo "==> Build and verification complete!"
