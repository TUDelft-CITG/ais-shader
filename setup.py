from setuptools import setup
from pybind11.setup_helpers import Pybind11Extension, build_ext
import sys

import os

include_dirs = []
library_dirs = []

# 1. Respect standard compilation environment variables if defined
if "CPATH" in os.environ:
    include_dirs.extend(p for p in os.environ["CPATH"].split(os.pathsep) if p)
if "LIBRARY_PATH" in os.environ:
    library_dirs.extend(p for p in os.environ["LIBRARY_PATH"].split(os.pathsep) if p)

# 2. Add common search paths on macOS and Linux if they exist
from pathlib import Path
common_prefixes = [Path("/opt/homebrew"), Path("/opt/local"), Path("/usr/local")]
for prefix in common_prefixes:
    inc_path = prefix / "include"
    lib_path = prefix / "lib"
    if inc_path.is_dir() and str(inc_path) not in include_dirs:
        include_dirs.append(str(inc_path))
    if lib_path.is_dir() and str(lib_path) not in library_dirs:
        library_dirs.append(str(lib_path))

# 3. Dynamic lookup for Macports versioned Boost subdirectory (if present)
if sys.platform == "darwin":
    from pathlib import Path
    macports_boost = Path("/opt/local/libexec/boost")
    if macports_boost.exists():
        for path in macports_boost.iterdir():
            if path.is_dir():
                inc_dir = str(path / "include")
                lib_dir = str(path / "lib")
                if inc_dir not in include_dirs:
                    include_dirs.append(inc_dir)
                if lib_dir not in library_dirs:
                    library_dirs.append(lib_dir)

ext_modules = [
    Pybind11Extension(
        "ais_shader._cgal_hull",
        ["src/ais_shader/_cgal_hull.cpp"],
        libraries=["gmp", "mpfr"],
        include_dirs=include_dirs,
        library_dirs=library_dirs,
        extra_compile_args=["-O3", "-std=c++17"],
    ),
]

setup(
    ext_modules=ext_modules,
    cmdclass={"build_ext": build_ext},
)
