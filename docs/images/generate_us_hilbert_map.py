import pyarrow.parquet as pq
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from pathlib import Path

# Import encode_3d_hilbert_numpy directly from the trajectory module
import sys
sys.path.append('/home/fbaart/src/ais-shader/src')
from ais_shader.moving_dask.trajectory import encode_3d_hilbert_numpy

def main():
    dataset_path = '/projects/prjs2131/data/marine-cadastre/ais_2025_12'
    
    # 1. Read first file of the dataset to get actual coordinates
    # We only need a subset of columns to keep it fast
    print("Reading sample coordinates from US AIS dataset...")
    pfile = list(Path(dataset_path).glob("*.parquet"))[0]
    df = pq.read_table(pfile, columns=['longitude', 'latitude', 'base_date_time']).to_pandas()
    
    # Clean up empty values and sample 15,000 points for clear visualization
    df = df.dropna(subset=['longitude', 'latitude', 'base_date_time'])
    # Filter to main US continental area to make the map look nice
    df = df[(df['longitude'] >= -130) & (df['longitude'] <= -65) & (df['latitude'] >= 24) & (df['latitude'] <= 50)]
    df = df.sample(n=min(15000, len(df)), random_state=42)
    
    # 2. Get global bounds from parquet statistics (fast)
    x_min, x_max = -130.0, -65.0
    y_min, y_max = 24.0, 50.0
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
    
    # 4. Partition points using quantiles (simulate 16 partitions)
    n_partitions = 16
    quantiles = np.linspace(0, 1, n_partitions + 1)
    divisions = list(df['hilbert_index'].quantile(quantiles))
    divisions = sorted(list(set(divisions)))
    
    # Assign partition labels
    df['partition'] = pd.cut(df['hilbert_index'], bins=divisions, labels=False, include_lowest=True)
    df = df.dropna(subset=['partition'])
    df['partition'] = df['partition'].astype(int)
    
    # 5. Create Map Plot
    fig, ax = plt.subplots(figsize=(12, 7), dpi=150)
    fig.patch.set_facecolor('#0f172a')  # Slate-900 background
    ax.set_facecolor('#0f172a')
    
    # Get color palette
    cmap = plt.get_cmap('tab20')
    
    # Plot partitions
    for part_id in range(n_partitions):
        part_df = df[df['partition'] == part_id]
        if part_df.empty:
            continue
            
        color = cmap(part_id / n_partitions)
        
        # Plot points of this partition
        ax.scatter(part_df['longitude'], part_df['latitude'], 
                   color=color, s=2, alpha=0.6, 
                   label=f'Partition {part_id}' if part_id < 6 else "") # Avoid legend bloat
        
        # Compute spatial bounding box for this partition
        px_min, px_max = part_df['longitude'].min(), part_df['longitude'].max()
        py_min, py_max = part_df['latitude'].min(), part_df['latitude'].max()
        
        # Draw bounding box for partition
        rect = Rectangle((px_min, py_min), px_max - px_min, py_max - py_min,
                         linewidth=1.2, edgecolor=color, facecolor=color, alpha=0.08, linestyle='-')
        ax.add_patch(rect)
        
        # Draw label at partition centroid
        cx = part_df['longitude'].mean()
        cy = part_df['latitude'].mean()
        ax.text(cx, cy, str(part_id), color='white', fontsize=10, weight='bold',
                bbox=dict(facecolor=color, edgecolor='none', boxstyle='round,pad=0.2', alpha=0.85))
                
    # Labels & Title
    ax.set_title('US Continental AIS Space-Time Partitioning (3D Hilbert)', color='white', fontsize=14, weight='bold', pad=15)
    ax.set_xlabel('Longitude (Degrees)', color='#94a3b8')
    ax.set_ylabel('Latitude (Degrees)', color='#94a3b8')
    
    ax.set_xlim(-130, -65)
    ax.set_ylim(24, 50)
    
    # Grid and Style
    ax.grid(True, color='#1e293b', linestyle='--', linewidth=0.5)
    ax.tick_params(colors='#94a3b8', labelsize=9)
    
    # Show legend
    legend = ax.legend(facecolor='#0f172a', edgecolor='#1e293b', labelcolor='white', loc='lower left', prop={'size': 8})
    if legend:
        legend.get_frame().set_alpha(0.8)
        
    # Save the output image
    out_dir = Path('/home/fbaart/src/ais-shader/docs/images')
    out_path = out_dir / 'us_hilbert_spaces.png'
    plt.savefig(out_path, facecolor=fig.get_facecolor(), edgecolor='none', bbox_inches='tight')
    plt.close()
    print(f"Successfully generated US partitioning visualization at: {out_path}")

if __name__ == '__main__':
    main()
