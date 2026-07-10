from setuptools import setup
from pybind11.setup_helpers import Pybind11Extension, build_ext

ext_modules = [
    Pybind11Extension(
        "ais_shader._cgal_hull",
        ["src/ais_shader/_cgal_hull.cpp"],
        libraries=["gmp", "mpfr"],
        extra_compile_args=["-O3", "-std=c++17"],
    ),
]

setup(
    ext_modules=ext_modules,
    cmdclass={"build_ext": build_ext},
)
