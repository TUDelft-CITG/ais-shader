import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle
from pathlib import Path

def hilbert_2d(x, y, xi, xj, yi, yj, n):
    if n <= 0:
        return [(x + (xi + yi)/2, y + (xj + yj)/2)]
    
    pts = []
    # Four quadrants
    pts.extend(hilbert_2d(x, y, yi/2, yj/2, xi/2, xj/2, n-1))
    pts.extend(hilbert_2d(x + xi/2, y + xj/2, xi/2, xj/2, yi/2, yj/2, n-1))
    pts.extend(hilbert_2d(x + xi/2 + yi/2, y + xj/2 + yj/2, xi/2, xj/2, yi/2, yj/2, n-1))
    pts.extend(hilbert_2d(x + xi/2 + yi, y + xj/2 + yj, -yi/2, -yj/2, -xi/2, -xj/2, n-1))
    return pts

def main():
    p = 3  # Order 3 gives 8x8 = 64 cells
    grid_size = 2**p
    pts = hilbert_2d(0, 0, 1, 0, 0, 1, p)
    
    # Scale points to fit a grid of size 8x8
    pts = np.array(pts) * grid_size
    
    # Create the figure
    fig, ax = plt.subplots(figsize=(8, 8), dpi=150)
    
    # Set background style
    fig.patch.set_facecolor('#0f172a')  # Slate-900 background
    ax.set_facecolor('#0f172a')
    
    # Plot the grid cells
    cmap = plt.get_cmap('plasma')  # Use plasma colormap for vibrant look
    for idx, (x, y) in enumerate(pts):
        # Determine the lower-left corner of the grid cell
        cx = int(x - 0.5)
        cy = int(y - 0.5)
        
        # Color each cell based on its position along the Hilbert curve
        color = cmap(idx / len(pts))
        rect = Rectangle((cx, cy), 1, 1, linewidth=0.5, edgecolor='#1e293b', facecolor=color, alpha=0.5)
        ax.add_patch(rect)
        
        # Draw the index number in the cell
        ax.text(x, y, str(idx), color='white', ha='center', va='center', fontsize=8, weight='bold', alpha=0.8)
    
    # Plot the Hilbert curve path
    ax.plot(pts[:, 0], pts[:, 1], color='#38bdf8', linewidth=2.5, alpha=0.9, label='Hilbert Curve Path')
    
    # Simulate a vessel track passing through the space-time curve
    np.random.seed(42)
    vessel_x = pts[:, 0] + np.random.normal(0, 0.15, size=len(pts))
    vessel_y = pts[:, 1] + np.random.normal(0, 0.15, size=len(pts))
    ax.scatter(vessel_x, vessel_y, color='#10b981', s=30, edgecolor='white', linewidth=0.5, label='Vessel GPS Pings', zorder=5)
    ax.plot(vessel_x, vessel_y, color='#10b981', linewidth=1, linestyle='--', alpha=0.7, zorder=4)
    
    # Set bounds and styling
    ax.set_xlim(-0.5, grid_size - 0.5)
    ax.set_ylim(-0.5, grid_size - 0.5)
    ax.set_xticks(np.arange(0, grid_size))
    ax.set_yticks(np.arange(0, grid_size))
    ax.grid(True, color='#1e293b', linestyle='-', linewidth=0.5)
    
    # Remove tick labels for a clean UI look
    ax.set_xticklabels([])
    ax.set_yticklabels([])
    
    # Titles and labels
    ax.set_title('3D Spatio-Temporal Partitioning (2D Hilbert Projection)', color='white', fontsize=12, pad=15, weight='bold')
    
    # Legend
    legend = ax.legend(facecolor='#0f172a', edgecolor='#1e293b', labelcolor='white', loc='upper right')
    frame = legend.get_frame()
    frame.set_alpha(0.8)
    
    # Save the output image
    out_dir = Path('/home/fbaart/src/ais-shader/docs/images')
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / 'hilbert_spaces.png'
    plt.savefig(out_path, facecolor=fig.get_facecolor(), edgecolor='none', bbox_inches='tight')
    plt.close()
    print(f"Successfully generated visualization at: {out_path}")

if __name__ == '__main__':
    main()
