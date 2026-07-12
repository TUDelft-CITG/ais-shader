import pyarrow.parquet as pq
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon
from scipy.spatial import ConvexHull
from pathlib import Path
import sys

# Import encode_3d_hilbert_numpy directly from the trajectory module
sys.path.append(str(Path(__file__).resolve().parents[2] / 'src'))
from ais_shader.moving_dask.trajectory import encode_3d_hilbert_numpy

def main():
    dataset_path = '/projects/prjs2131/data/marine-cadastre/ais_2025_12_trajectories.parquet'
    
    # 1. Read a subset of rows from multiple parquet files to cover a wider temporal range (e.g. 15 days)
    print("Reading sample coordinates across multiple US AIS dataset files...")
    files = sorted(list(Path(dataset_path).glob("*.parquet")))
    # Read from all files to cover the entire dataset
    selected_files = files
    dfs = []
    for pfile in selected_files:
        try:
            tbl = pq.read_table(pfile, columns=['longitude', 'latitude', 'base_date_time'])
            df_part = tbl.to_pandas().dropna(subset=['longitude', 'latitude', 'base_date_time'])
            df_part = df_part[(df_part['longitude'] >= -125) & (df_part['longitude'] <= -70) & (df_part['latitude'] >= 24) & (df_part['latitude'] <= 48)]
            if not df_part.empty:
                df_part = df_part.sample(n=min(5000, len(df_part)), random_state=42)
                dfs.append(df_part)
        except Exception as e:
            print(f"Skipping {pfile.name} due to: {e}")
            
    if not dfs:
        raise ValueError("Could not read any coordinates from the dataset files.")
        
    df = pd.concat(dfs, ignore_index=True)
    print(f"Loaded {len(df)} sample coordinates across {len(dfs)} files.")
    
    # 2. Get global bounds
    x_min, x_max = -125.0, -70.0
    y_min, y_max = 24.0, 48.0
    df['base_date_time'] = pd.to_datetime(df['base_date_time'])
    t_min = df['base_date_time'].min()
    t_max = df['base_date_time'].max()
    t_min_epoch = t_min.timestamp()
    t_max_epoch = t_max.timestamp()
    
    # 3. Compute 3D Hilbert Coordinates & Index
    p = 16  # Order 16 matches the main application's spatial granularity
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
    
    # 4. Partition points using quantiles (128 partitions)
    n_partitions = 128
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
    
    # Group partitions by their spatial centroids (mean longitude) to color them spatially
    part_lons = []
    for part_id in range(n_partitions):
        part_df = df[df['partition'] == part_id]
        if not part_df.empty:
            mean_lon = part_df['longitude'].mean()
            part_lons.append((part_id, mean_lon))
            
    # Sort partitions spatially (West to East)
    part_lons.sort(key=lambda x: x[1])
    sorted_parts = [x[0] for x in part_lons]
    
    # Divide into 3 spatial bands
    n_valid = len(sorted_parts)
    band_size = n_valid // 3
    
    # Plot partitions
    printed_labels = set()
    for rank, part_id in enumerate(sorted_parts):
        part_df = df[df['partition'] == part_id]
        if part_df.empty:
            continue
            
        # Determine spatial group and color palette
        if rank < band_size:
            color = plt.get_cmap('cool')(rank / band_size * 0.4)
            group_label = "West Coast / Pacific (Band 1)"
        elif rank < 2 * band_size:
            color = plt.get_cmap('plasma')(0.3 + (rank - band_size) / band_size * 0.4)
            group_label = "Central / Gulf Coast (Band 2)"
        else:
            color = plt.get_cmap('spring')((rank - 2*band_size) / (n_valid - 2*band_size) * 0.5 + 0.3)
            group_label = "East Coast / Atlantic (Band 3)"
            
        # Plot points of this partition (small dots)
        ax.scatter(part_df['longitude'], part_df['latitude'], 
                   color=color, s=1.5, alpha=0.3, 
                   label=group_label if group_label not in printed_labels else "")
        printed_labels.add(group_label)
        
        # Compute spatial convex hull for partition
        points = part_df[['longitude', 'latitude']].values
        if len(points) >= 3:
            hull = ConvexHull(points)
            hull_points = points[hull.vertices]
            poly = Polygon(hull_points, linewidth=1.2, edgecolor=color,
                           facecolor=color, alpha=0.08, linestyle='-')
            ax.add_patch(poly)
        
        # Determine temporal scale (use the absolute min/max days to show the full temporal extent of the partition)
        start_day = int(part_df['base_date_time'].min().day)
        end_day = int(part_df['base_date_time'].max().day)
            
        label_text = f"P{part_id}\n{start_day}d-{end_day}d"
        
        # Draw label at partition centroid
        cx = part_df['longitude'].mean()
        cy = part_df['latitude'].mean()
        ax.text(cx, cy, label_text, color='white', fontsize=6, weight='bold', ha='center', va='center',
                bbox=dict(facecolor=color, edgecolor='none', boxstyle='round,pad=0.15', alpha=0.85))
                
    # Labels & Title
    ax.set_title('3D Spatio-Temporal Partitioning over US Coastline (Convex Hull Partition Hulls + Time Annotations)', color='white', fontsize=14, weight='bold', pad=15)
    ax.set_xlabel('Longitude (Degrees)', color='#94a3b8')
    ax.set_ylabel('Latitude (Degrees)', color='#94a3b8')
    
    ax.set_xlim(-125, -70)
    ax.set_ylim(24, 48)
    
    # Grid and Style
    ax.grid(True, color='#1e293b', linestyle='--', linewidth=0.5)
    ax.tick_params(colors='#94a3b8', labelsize=9)
    
    # Legend
    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    legend = ax.legend(by_label.values(), by_label.keys(), facecolor='#0f172a', edgecolor='#1e293b', labelcolor='white', loc='lower left', prop={'size': 9})
    if legend:
        legend.get_frame().set_alpha(0.8)
        
    # Save the output image
    out_dir = Path(__file__).resolve().parent
    out_path = out_dir / 'us_hilbert_spaces.png'
    plt.savefig(out_path, facecolor=fig.get_facecolor(), edgecolor='none', bbox_inches='tight')
    plt.close()
    print(f"Successfully generated US convex hull partitioning visualization with temporal tags at: {out_path}")

if __name__ == '__main__':
    main()
