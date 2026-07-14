from setuptools import setup
from pybind11.setup_helpers import Pybind11Extension, build_ext
import sys

include_dirs = []
library_dirs = []

if sys.platform == "darwin":
    # Support Macports
    include_dirs.append("/opt/local/include")
    library_dirs.append("/opt/local/lib")
    
    # Dynamically find Macports versioned Boost if present
    from pathlib import Path
    macports_boost = Path("/opt/local/libexec/boost")
    if macports_boost.exists():
        for path in macports_boost.iterdir():
            if path.is_dir():
                include_dirs.append(str(path / "include"))
                library_dirs.append(str(path / "lib"))

    # Support Homebrew
    include_dirs.append("/opt/homebrew/include")
    library_dirs.append("/opt/homebrew/lib")

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
