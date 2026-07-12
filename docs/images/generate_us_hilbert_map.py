import pyarrow.parquet as pq
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from pathlib import Path
import sys

# Import encode_3d_hilbert_numpy directly from the trajectory module
sys.path.append('/home/fbaart/src/ais-shader/src')
from ais_shader.moving_dask.trajectory import encode_3d_hilbert_numpy

def main():
    dataset_path = '/projects/prjs2131/data/marine-cadastre/ais_2025_12'
    
    # 1. Read first file of the dataset to get actual coordinates
    print("Reading sample coordinates from US AIS dataset...")
    pfile = list(Path(dataset_path).glob("*.parquet"))[0]
    df = pq.read_table(pfile, columns=['longitude', 'latitude', 'base_date_time']).to_pandas()
    
    # Clean up empty values and sample 20,000 points
    df = df.dropna(subset=['longitude', 'latitude', 'base_date_time'])
    df = df[(df['longitude'] >= -125) & (df['longitude'] <= -70) & (df['latitude'] >= 24) & (df['latitude'] <= 48)]
    df = df.sample(n=min(20000, len(df)), random_state=42)
    
    # 2. Get global bounds
    x_min, x_max = -125.0, -70.0
    y_min, y_max = 24.0, 48.0
    df['base_date_time'] = pd.to_datetime(df['base_date_time'])
    t_min = df['base_date_time'].min()
    t_max = df['base_date_time'].max()
    t_min_epoch = t_min.timestamp()
    t_max_epoch = t_max.timestamp()
    
    # 3. Compute 3D Hilbert Coordinates & Index
    p = 6  # Order 6 provides good spatial granularity (64x64 grid)
    grid_size = (1 << p) - 1
    
    xs = df['longitude'].values
    ys = df['latitude'].values
    ts = df['base_date_time'].values.view('int64') // 10**9
    
    xd = x_max - x_min
    yd = y_max - y_min
    td = t_max_epoch - t_min_epoch if t_max_epoch != t_min_epoch else 1.0
    
    x_int = np.clip((xs - x_min) / xd * grid_size, 0, grid_size).astype(np.int64)
    y_int = np.clip((ys - y_min) / yd * grid_size, 0, grid_size).astype(np.int64)
    t_int = np.clip((ts - t_min_epoch) / td * grid_size, 0, grid_size).astype(np.int64)
    
    coords = np.column_stack((x_int, y_int, t_int))
    df['hilbert_index'] = encode_3d_hilbert_numpy(coords, p)
    
    # 4. Partition points using quantiles (32 partitions)
    n_partitions = 32
    quantiles = np.linspace(0, 1, n_partitions + 1)
    divisions = list(df['hilbert_index'].quantile(quantiles))
    divisions = sorted(list(set(divisions)))
    
    # Assign partition labels
    df['partition'] = pd.cut(df['hilbert_index'], bins=divisions, labels=False, include_lowest=True)
    df = df.dropna(subset=['partition'])
    df['partition'] = df['partition'].astype(int)
    
    # 5. Create Map Plot
    fig, ax = plt.subplots(figsize=(14, 8), dpi=150)
    fig.patch.set_facecolor('#0f172a')  # Slate-900 background
    ax.set_facecolor('#0f172a')
    
    # Group partitions by their temporal centroids into 3 phases:
    # Phase 1: Early Dec (Blue/Cyan), Phase 2: Mid Dec (Purple/Magenta), Phase 3: Late Dec (Yellow/Green)
    part_times = []
    for part_id in range(n_partitions):
        part_df = df[df['partition'] == part_id]
        if not part_df.empty:
            mean_time = part_df['base_date_time'].mean()
            part_times.append((part_id, mean_time))
            
    # Sort partitions temporally
    part_times.sort(key=lambda x: x[1])
    sorted_parts = [x[0] for x in part_times]
    
    # Divide into 3 temporal bands
    n_valid = len(sorted_parts)
    band_size = n_valid // 3
    
    # Plot partitions
    for rank, part_id in enumerate(sorted_parts):
        part_df = df[df['partition'] == part_id]
        if part_df.empty:
            continue
            
        # Determine temporal group and color palette
        if rank < band_size:
            # Early Phase (Cyan/Teal)
            color = plt.get_cmap('cool')(rank / band_size * 0.4)
            group_label = "Early Dec (T-Band 1)"
        elif rank < 2 * band_size:
            # Mid Phase (Magenta/Purple)
            color = plt.get_cmap('plasma')(0.3 + (rank - band_size) / band_size * 0.4)
            group_label = "Mid Dec (T-Band 2)"
        else:
            # Late Phase (Orange/Yellow/Green)
            color = plt.get_cmap('spring')((rank - 2*band_size) / (n_valid - 2*band_size) * 0.5 + 0.3)
            group_label = "Late Dec (T-Band 3)"
            
        # Plot points of this partition (small dots)
        ax.scatter(part_df['longitude'], part_df['latitude'], 
                   color=color, s=1.5, alpha=0.3, 
                   label=group_label if f"printed_{group_label}" not in locals() else "")
        locals()[f"printed_{group_label}"] = True
        
        # Compute spatial bounding box
        px_min, px_max = part_df['longitude'].min(), part_df['longitude'].max()
        py_min, py_max = part_df['latitude'].min(), part_df['latitude'].max()
        
        # Draw bounding box (overlapping translucent grids)
        rect = Rectangle((px_min, py_min), px_max - px_min, py_max - py_min,
                         linewidth=1.0, edgecolor=color, facecolor='none', alpha=0.4, linestyle='-')
        ax.add_patch(rect)
        
        # Draw label at partition centroid
        cx = part_df['longitude'].mean()
        cy = part_df['latitude'].mean()
        ax.text(cx, cy, f"P{part_id}", color='white', fontsize=7, weight='bold',
                bbox=dict(facecolor=color, edgecolor='none', boxstyle='round,pad=0.15', alpha=0.7))
                
    # Labels & Title
    ax.set_title('3D Spatio-Temporal Partitioning over US Coastline (32 Partitions, 3 Temporal Bands)', color='white', fontsize=14, weight='bold', pad=15)
    ax.set_xlabel('Longitude (Degrees)', color='#94a3b8')
    ax.set_ylabel('Latitude (Degrees)', color='#94a3b8')
    
    ax.set_xlim(-125, -70)
    ax.set_ylim(24, 48)
    
    # Grid and Style
    ax.grid(True, color='#1e293b', linestyle='--', linewidth=0.5)
    ax.tick_params(colors='#94a3b8', labelsize=9)
    
    # Legend
    # Filter unique legend items
    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    legend = ax.legend(by_label.values(), by_label.keys(), facecolor='#0f172a', edgecolor='#1e293b', labelcolor='white', loc='lower left', prop={'size': 9})
    if legend:
        legend.get_frame().set_alpha(0.8)
        
    # Save the output image
    out_dir = Path('/home/fbaart/src/ais-shader/docs/images')
    out_path = out_dir / 'us_hilbert_spaces.png'
    plt.savefig(out_path, facecolor=fig.get_facecolor(), edgecolor='none', bbox_inches='tight')
    plt.close()
    print(f"Successfully generated 3D overlapping US partitioning visualization at: {out_path}")

if __name__ == '__main__':
    main()
