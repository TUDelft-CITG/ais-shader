import os
from setuptools import setup
from pybind11.setup_helpers import Pybind11Extension, build_ext

# Read environment variables set by Snellius module load, or use standard fallbacks
cgal_root = os.environ.get("EBROOTCGAL", "/usr/local")
boost_root = os.environ.get("EBROOTBOOST", "/usr/local")
gmp_root = os.environ.get("EBROOTGMP", "/usr/local")
mpfr_root = os.environ.get("EBROOTMPFR", "/usr/local")

include_dirs = [
    os.path.join(cgal_root, "include"),
    os.path.join(boost_root, "include"),
    os.path.join(gmp_root, "include"),
    os.path.join(mpfr_root, "include"),
]

library_dirs = [
    os.path.join(gmp_root, "lib"),
    os.path.join(mpfr_root, "lib"),
]

ext_modules = [
    Pybind11Extension(
        "ais_shader._cgal_hull",
        ["src/ais_shader/_cgal_hull.cpp"],
        include_dirs=include_dirs,
        library_dirs=library_dirs,
        libraries=["gmp", "mpfr"],
        extra_compile_args=["-O3", "-std=c++17"],
    ),
]

setup(
    ext_modules=ext_modules,
    cmdclass={"build_ext": build_ext},
)
