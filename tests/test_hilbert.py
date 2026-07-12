import numpy as np

from ais_shader.moving_dask.trajectory import encode_3d_hilbert_numpy

def encode_2d_hilbert_reference_single(x, y, p):
    coords = [x, y]
    m = 1 << (p - 1)
    q = m
    while q > 1:
        p_val = q - 1
        if (coords[0] & q) > 0:
            coords[0] ^= p_val
        else:
            t = (coords[0] ^ coords[1]) & p_val
            coords[0] ^= t
            coords[1] ^= t
        q >>= 1
        
    coords[1] ^= coords[0]
    
    h_int = 0
    for bit in range(p - 1, -1, -1):
        bx = (coords[0] >> bit) & 1
        by = (coords[1] >> bit) & 1
        h_int = (h_int << 2) | (by << 1) | bx
    return h_int

def encode_3d_hilbert_reference_single(x, y, z, p):
    spatial_index = encode_2d_hilbert_reference_single(x, y, p)
    return (spatial_index << p) | z

def test_hilbert_numpy():
    p = 8
    # Generate random points
    np.random.seed(42)
    coords = np.random.randint(0, 2**p, size=(1000, 3))
    
    expected = np.array([encode_3d_hilbert_reference_single(c[0], c[1], c[2], p) for c in coords])
    actual = encode_3d_hilbert_numpy(coords, p)
    
    assert np.array_equal(expected, actual), "Vectorized Hilbert curve does not match reference!"
    print("Vectorized 3D Hilbert Curve test PASSED successfully!")

if __name__ == "__main__":
    test_hilbert_numpy()
