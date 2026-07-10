// pybind11 wrapper around CGAL::convex_hull_2 for 2D point sets.
#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <vector>
#include <cmath>

#include <CGAL/Exact_predicates_inexact_constructions_kernel.h>
#include <CGAL/convex_hull_2.h>

namespace py = pybind11;
using K = CGAL::Exact_predicates_inexact_constructions_kernel;
using Point_2 = K::Point_2;

// Compute planar Shoelace area of a polygon directly in C++
double shoelace_area(const std::vector<Point_2>& hull) {
    if (hull.size() < 3) return 0.0;
    double sum = 0.0;
    size_t n = hull.size();
    for (size_t i = 0; i < n; ++i) {
        size_t next = (i + 1) % n;
        sum += CGAL::to_double(hull[i].x()) * CGAL::to_double(hull[next].y()) - 
               CGAL::to_double(hull[next].x()) * CGAL::to_double(hull[i].y());
    }
    return 0.5 * std::abs(sum);
}

// Compute the 2D convex hull of an (n, 2) array of planar points.
py::array_t<double> convex_hull_2(py::array_t<double, py::array::c_style | py::array::forcecast> xy) {
    auto buf = xy.request();
    if (buf.ndim != 2 || buf.shape[1] != 2) {
        throw std::runtime_error("expected an (n, 2) array");
    }
    const double* ptr = static_cast<double*>(buf.ptr);
    const ssize_t n = buf.shape[0];

    std::vector<Point_2> points;
    points.reserve(n);
    for (ssize_t i = 0; i < n; ++i) {
        points.emplace_back(ptr[2 * i], ptr[2 * i + 1]);
    }

    std::vector<Point_2> hull;
    {
        py::gil_scoped_release release;
        CGAL::convex_hull_2(points.begin(), points.end(), std::back_inserter(hull));
    }

    auto result = py::array_t<double>({(ssize_t)hull.size(), (ssize_t)2});
    auto rbuf = result.request();
    double* rptr = static_cast<double*>(rbuf.ptr);
    for (size_t i = 0; i < hull.size(); ++i) {
        rptr[2 * i] = CGAL::to_double(hull[i].x());
        rptr[2 * i + 1] = CGAL::to_double(hull[i].y());
    }
    return result;
}

// Compute the area of the 2D convex hull directly in C++
double convex_hull_area_2(py::array_t<double, py::array::c_style | py::array::forcecast> xy) {
    auto buf = xy.request();
    if (buf.ndim != 2 || buf.shape[1] != 2) {
        throw std::runtime_error("expected an (n, 2) array");
    }
    const double* ptr = static_cast<double*>(buf.ptr);
    const ssize_t n = buf.shape[0];
    if (n < 3) return 0.0;

    std::vector<Point_2> points;
    points.reserve(n);
    for (ssize_t i = 0; i < n; ++i) {
        points.emplace_back(ptr[2 * i], ptr[2 * i + 1]);
    }

    std::vector<Point_2> hull;
    {
        py::gil_scoped_release release;
        CGAL::convex_hull_2(points.begin(), points.end(), std::back_inserter(hull));
    }

    return shoelace_area(hull);
}

// Compute rolling convex hull area for an entire track directly in C++
py::array_t<double> rolling_convex_hull_area_2(
    py::array_t<double, py::array::c_style | py::array::forcecast> xy,
    py::array_t<ssize_t, py::array::c_style | py::array::forcecast> starts
) {
    auto xy_buf = xy.request();
    auto starts_buf = starts.request();

    if (xy_buf.ndim != 2 || xy_buf.shape[1] != 2) {
        throw std::runtime_error("expected an (n, 2) array for xy");
    }
    if (starts_buf.ndim != 1) {
        throw std::runtime_error("expected a 1D array for starts");
    }

    const double* xy_ptr = static_cast<double*>(xy_buf.ptr);
    const ssize_t* starts_ptr = static_cast<ssize_t*>(starts_buf.ptr);
    const ssize_t n = xy_buf.shape[0];

    if (starts_buf.shape[0] != n) {
        throw std::runtime_error("size of starts must match size of xy");
    }

    // Check bounds before releasing GIL to prevent python crash
    for (ssize_t i = 0; i < n; ++i) {
        if (starts_ptr[i] < 0 || starts_ptr[i] > i) {
            throw std::runtime_error("Invalid start index: starts[" + std::to_string(i) + "] = " + std::to_string(starts_ptr[i]) + " is out of bounds [0, " + std::to_string(i) + "].");
        }
    }

    auto result = py::array_t<double>(n);
    auto result_buf = result.request();
    double* res_ptr = static_cast<double*>(result_buf.ptr);

    {
        py::gil_scoped_release release;
        std::vector<Point_2> points;
        std::vector<Point_2> hull;
        for (ssize_t i = 0; i < n; ++i) {
            ssize_t j = starts_ptr[i];
            ssize_t count = i - j + 1;
            if (count < 3) {
                res_ptr[i] = 0.0;
                continue;
            }

            points.clear();
            points.reserve(count);
            for (ssize_t k = j; k <= i; ++k) {
                points.emplace_back(xy_ptr[2 * k], xy_ptr[2 * k + 1]);
            }

            hull.clear();
            CGAL::convex_hull_2(points.begin(), points.end(), std::back_inserter(hull));
            res_ptr[i] = shoelace_area(hull);
        }
    }

    return result;
}

PYBIND11_MODULE(_cgal_hull, m) {
    m.doc() = "CGAL::convex_hull_2 wrapper. Expects planar (e.g. locally projected "
               "meter) coordinates, not lon/lat degrees.";
    m.def("convex_hull_2", &convex_hull_2,
          "Compute the 2D convex hull of an (n,2) planar point array");
    m.def("convex_hull_area_2", &convex_hull_area_2,
          "Compute the area of the 2D convex hull of an (n,2) planar point array directly in C++");
    m.def("rolling_convex_hull_area_2", &rolling_convex_hull_area_2,
          "Compute rolling convex hull areas for an entire track directly in C++");
}
