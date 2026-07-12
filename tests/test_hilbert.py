import numpy as np
from hilbertcurve.hilbertcurve import HilbertCurve

from ais_shader.moving_dask.trajectory import encode_3d_hilbert_numpy


def test_hilbert_numpy():
    p = 8
    hc = HilbertCurve(p, 3)
    # Generate random points
    np.random.seed(42)
    coords = np.random.randint(0, 2**p, size=(1000, 3))
    
    expected = np.array(hc.distances_from_points(coords.tolist()))
    actual = encode_3d_hilbert_numpy(coords, p)
    
    assert np.array_equal(expected, actual), "Vectorized Hilbert curve does not match original!"
    print("Vectorized 3D Hilbert Curve test PASSED successfully!")

if __name__ == "__main__":
    test_hilbert_numpy()
