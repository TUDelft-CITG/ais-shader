import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path
import sys
from mpl_toolkits.mplot3d import Axes3D

sys.path.append(str(Path(__file__).resolve().parents[2] / 'src'))
from ais_shader.moving_dask.trajectory import encode_3d_hilbert_numpy

def main():
    p = 3  # Order 3 gives 8x8x8 = 512 cells
    grid_size = 2**p
    
    # Generate all coordinates in an 8x8x8 grid
    x, y, z = np.meshgrid(np.arange(grid_size), np.arange(grid_size), np.arange(grid_size), indexing='ij')
    coords = np.column_stack((x.ravel(), y.ravel(), z.ravel()))
    
    # Encode with our 3D Hilbert implementation
    indices = encode_3d_hilbert_numpy(coords, p)
    
    # Sort coordinates by their Hilbert index to trace the curve path
    sorted_idx = np.argsort(indices)
    pts = coords[sorted_idx]
    
    # Create 3D figure
    fig = plt.figure(figsize=(10, 8), dpi=150)
    ax = fig.add_subplot(111, projection='3d')
    
    # Set background style
    fig.patch.set_facecolor('#0f172a')  # Slate-900 background
    ax.set_facecolor('#0f172a')
    
    # Style the axes
    ax.xaxis.set_pane_color((0.09, 0.13, 0.22, 1.0)) # Slate-800-like
    ax.yaxis.set_pane_color((0.09, 0.13, 0.22, 1.0))
    ax.zaxis.set_pane_color((0.09, 0.13, 0.22, 1.0))
    ax.grid(True, color='#1e293b', linestyle='--', linewidth=0.5)
    
    # Change tick and label colors
    ax.tick_params(colors='#94a3b8', labelsize=8)
    ax.set_xlabel('X (Space)', color='#94a3b8', fontsize=10, labelpad=5)
    ax.set_ylabel('Y (Space)', color='#94a3b8', fontsize=10, labelpad=5)
    ax.set_zlabel('T (Time)', color='#94a3b8', fontsize=10, labelpad=5)
    
    # Draw the 3D Hilbert Curve colored by its index (spatiotemporal partitions)
    # We can draw it as a line that changes color along the path
    cmap = plt.get_cmap('plasma')
    colors = cmap(np.linspace(0, 1, len(pts) - 1))
    
    for i in range(len(pts) - 1):
        ax.plot(pts[i:i+2, 0], pts[i:i+2, 1], pts[i:i+2, 2], color=colors[i], linewidth=1.5, alpha=0.8)
        
    # Highlight specific spatiotemporal partitions (e.g. split into 4 partitions)
    # Draw bounding boxes for these partitions to show how they group in space-time!
    n_partitions = 4
    colors_parts = ['#38bdf8', '#fb7185', '#34d399', '#fbbf24']
    pts_per_part = len(pts) // n_partitions
    
    for part_idx in range(n_partitions):
        part_pts = pts[part_idx*pts_per_part : (part_idx+1)*pts_per_part]
        
        # Bounding box of this partition
        min_coords = part_pts.min(axis=0) - 0.1
        max_coords = part_pts.max(axis=0) + 0.1
        
        # Draw 3D wireframe box
        for s, e in [
            (min_coords, max_coords),
        ]:
            # X edges
            ax.plot([min_coords[0], max_coords[0]], [min_coords[1], min_coords[1]], [min_coords[2], min_coords[2]], color=colors_parts[part_idx], linestyle='--', linewidth=1.0, alpha=0.6)
            ax.plot([min_coords[0], max_coords[0]], [max_coords[1], max_coords[1]], [min_coords[2], min_coords[2]], color=colors_parts[part_idx], linestyle='--', linewidth=1.0, alpha=0.6)
            ax.plot([min_coords[0], max_coords[0]], [min_coords[1], min_coords[1]], [max_coords[2], max_coords[2]], color=colors_parts[part_idx], linestyle='--', linewidth=1.0, alpha=0.6)
            ax.plot([min_coords[0], max_coords[0]], [max_coords[1], max_coords[1]], [max_coords[2], max_coords[2]], color=colors_parts[part_idx], linestyle='--', linewidth=1.0, alpha=0.6)
            
            # Y edges
            ax.plot([min_coords[0], min_coords[0]], [min_coords[1], max_coords[1]], [min_coords[2], min_coords[2]], color=colors_parts[part_idx], linestyle='--', linewidth=1.0, alpha=0.6)
            ax.plot([max_coords[0], max_coords[0]], [min_coords[1], max_coords[1]], [min_coords[2], min_coords[2]], color=colors_parts[part_idx], linestyle='--', linewidth=1.0, alpha=0.6)
            ax.plot([min_coords[0], min_coords[0]], [min_coords[1], max_coords[1]], [max_coords[2], max_coords[2]], color=colors_parts[part_idx], linestyle='--', linewidth=1.0, alpha=0.6)
            ax.plot([max_coords[0], max_coords[0]], [min_coords[1], max_coords[1]], [max_coords[2], max_coords[2]], color=colors_parts[part_idx], linestyle='--', linewidth=1.0, alpha=0.6)
            
            # Z edges
            ax.plot([min_coords[0], min_coords[0]], [min_coords[1], min_coords[1]], [min_coords[2], max_coords[2]], color=colors_parts[part_idx], linestyle='--', linewidth=1.0, alpha=0.6)
            ax.plot([max_coords[0], max_coords[0]], [min_coords[1], min_coords[1]], [min_coords[2], max_coords[2]], color=colors_parts[part_idx], linestyle='--', linewidth=1.0, alpha=0.6)
            ax.plot([min_coords[0], min_coords[0]], [max_coords[1], max_coords[1]], [min_coords[2], max_coords[2]], color=colors_parts[part_idx], linestyle='--', linewidth=1.0, alpha=0.6)
            ax.plot([max_coords[0], max_coords[0]], [max_coords[1], max_coords[1]], [min_coords[2], max_coords[2]], color=colors_parts[part_idx], linestyle='--', linewidth=1.0, alpha=0.6)
            
        # Draw legend proxy
        ax.plot([], [], [], color=colors_parts[part_idx], label=f'Spatio-Temporal Partition {part_idx+1}')

    ax.set_title('3D Spatio-Temporal Hilbert Curve Partitioning (X, Y, T)', color='white', fontsize=14, pad=15, weight='bold')
    
    # Legend
    legend = ax.legend(facecolor='#0f172a', edgecolor='#1e293b', labelcolor='white', loc='upper right')
    if legend:
        legend.get_frame().set_alpha(0.8)
        
    # Save the output image
    out_dir = Path(__file__).resolve().parent
    out_path = out_dir / 'hilbert_spaces.png'
    plt.savefig(out_path, facecolor=fig.get_facecolor(), edgecolor='none', bbox_inches='tight')
    plt.close()
    print(f"Successfully generated 3D Hilbert partitioning visualization at: {out_path}")

if __name__ == '__main__':
    main()
