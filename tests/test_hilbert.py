import numpy as np
from dask_geopandas.hilbert_distance import _encode
from ais_shader.moving_dask.trajectory import encode_3d_hilbert_numpy

def test_hilbert_numpy():
    p = 8
    # Generate random points
    np.random.seed(42)
    coords = np.random.randint(0, 2**p, size=(1000, 3))
    
    expected = []
    for c in coords:
        s_idx = _encode(p, np.array([c[0]], dtype='uint32'), np.array([c[1]], dtype='uint32'))[0]
        expected.append((int(s_idx) << p) | int(c[2]))
    expected = np.array(expected)
    
    actual = encode_3d_hilbert_numpy(coords, p)
    
    assert np.array_equal(expected, actual), "Vectorized Hilbert curve does not match reference!"
    print("Vectorized 3D Hilbert Curve test PASSED successfully!")

if __name__ == "__main__":
    test_hilbert_numpy()
