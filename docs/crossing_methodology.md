# Passage Line Crossing & Interpolation Methodology

This document details the mathematical and algorithmic approach used by `ais-shader` to calculate vessel crossing events along passage lines (gates).

## 1. Trajectory Construction (Segment Vectors)
Instead of snapping individual, raw AIS points directly to the nearest passage lines, the pipeline reconstructs continuous vessel movement. 
1. AIS points are grouped by `track_id` and sorted chronologically.
2. For any two consecutive pings $P_{\text{start}} = (x_1, y_1)$ at time $t_1$ and $P_{\text{end}} = (x_2, y_2)$ at time $t_2$, a segment vector $\vec{S}$ is constructed if the time gap $\Delta t = t_2 - t_1$ is within the allowed threshold (default: 2 hours).

$$\vec{S} = \begin{pmatrix} x_2 - x_1 \\ y_2 - y_1 \end{pmatrix}$$

## 2. Spatial Intersection
Using Shapely's high-performance vectorized operations, we find segment geometries that intersect the passage line $\vec{L}$ (defined by endpoints $L_{\text{start}}$ and $L_{\text{end}}$).

For each intersection, the exact crossing coordinate $P_{\text{cross}} = (x_{\text{cross}}, y_{\text{cross}})$ is calculated:

$$P_{\text{cross}} = \vec{S} \cap \vec{L}$$

## 3. Lateral Position Calculation (`loc_fraction`)
To map where the ship crossed relative to the width of the passage gate, the crossing point $P_{\text{cross}}$ is projected onto the passage line geometry. The resulting coordinate is normalized to a fraction representing its position along the gate from start to end:

$$f_{\text{lateral}} = \frac{\| P_{\text{cross}} - L_{\text{start}} \|}{\| L_{\text{end}} - L_{\text{start}} \|}$$

*   $f_{\text{lateral}} = 0.0$ represents a crossing exactly at the start of the passage line (typically the left bank).
*   $f_{\text{lateral}} = 1.0$ represents a crossing exactly at the end of the passage line (typically the right bank).

## 4. Speed Linear Interpolation
Because AIS reports are logged periodically, a vessel rarely pings exactly on the passage line. To estimate the crossing speed ($sog_{\text{cross}}$), we perform linear interpolation based on the distance fraction ($f_{\text{segment}}$) of the crossing point along the vessel segment $\vec{S}$:

$$f_{\text{segment}} = \frac{\| P_{\text{cross}} - P_{\text{start}} \|}{\| P_{\text{end}} - P_{\text{start}} \|}$$

$$sog_{\text{cross}} = sog_{\text{start}} + f_{\text{segment}} \times (sog_{\text{end}} - sog_{\text{start}})$$

## 5. Travel Direction Classification
Vessel directions are separated into `up` (upstream) and `down` (downstream). 
We define the passage line orientation vector as $\vec{L} = (L_x, L_y)$. Its normal vector (pointing downstream) is defined as:

$$\vec{N} = \begin{pmatrix} -L_y \\ L_x \end{pmatrix}$$

We calculate the dot product between the vessel segment vector $\vec{S}$ and the normal vector $\vec{N}$:

$$\text{dot} = S_x \cdot (-L_y) + S_y \cdot L_x$$

*   **`down` (downstream)**: $\text{dot} \ge 0$ (vessel moves in the general direction of the normal vector).
*   **`up` (upstream)**: $\text{dot} < 0$ (vessel moves against the direction of the normal vector).

## 6. Lateral Profile Binning (20 Bins)
The normalized lateral coordinate $f_{\text{lateral}} \in [0, 1)$ is divided into 20 equal-width bins (width $= 0.05$ each). The crossing event is assigned to a bin index using the floor function:

$$\text{BinIndex} = \lfloor f_{\text{lateral}} \times 20 \rfloor$$

### Example:
For a vessel crossing at $f_{\text{lateral}} = 0.37$:

$$\text{BinIndex} = \lfloor 0.37 \times 20 \rfloor = \lfloor 7.4 \rfloor = 7$$

This corresponds to the 8th segment along the passage line profile (interval $[0.35, 0.40)$), placing it in Bin 7.
