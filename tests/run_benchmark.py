#!/usr/bin/env python
import argparse
from benchmark import run_benchmark_suite

def main():
    parser = argparse.ArgumentParser(description="Run Dask trajectorize benchmark sweeps logging to MLflow.")
    parser.add_argument("--dataset-path", type=str, required=True, help="Path to sample Parquet file/directory to benchmark.")
    parser.add_argument("--mlflow-tracking-uri", type=str, default="sqlite:///mlflow.db", help="MLflow tracking URI.")
    parser.add_argument("--vessel-id-col", type=str, default="mmsi", help="Vessel ID column name.")
    parser.add_argument("--time-col", type=str, default="base_date_time", help="Time column name.")
    parser.add_argument("--x-col", type=str, default="longitude", help="Longitude column name.")
    parser.add_argument("--y-col", type=str, default="latitude", help="Latitude column name.")
    parser.add_argument("--runs-limit", type=int, default=0, help="Limit number of runs in the sweep.")
    parser.add_argument("--scheduler", type=str, default=None, help="Dask scheduler URL.")
    parser.add_argument("--tmp-dir", type=str, default=None, help="Dask temporary cache directory.")
    
    args = parser.parse_args()
    
    run_benchmark_suite(
        dataset_path=args.dataset_path,
        mlflow_tracking_uri=args.mlflow_tracking_uri,
        vessel_id_col=args.vessel_id_col,
        time_col=args.time_col,
        x_col=args.x_col,
        y_col=args.y_col,
        runs_limit=args.runs_limit,
        scheduler=args.scheduler,
        tmp_dir=args.tmp_dir
    )

if __name__ == "__main__":
    main()
