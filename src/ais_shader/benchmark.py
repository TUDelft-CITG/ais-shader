import logging
import time
import os
import threading
import psutil
import pandas as pd
import dask.dataframe as dd
from dask.distributed import Client
import mlflow

from .moving_dask.trajectory import trajectorize_dataframe

logger = logging.getLogger(__name__)

class ClusterMemoryTracker:
    """Tracks peak memory of the client process and all Dask workers on the cluster."""
    def __init__(self, client=None, interval=0.2):
        self.client = client
        self.interval = interval
        self.peak_memory = 0
        self.stop_event = threading.Event()
        self.process = psutil.Process(os.getpid())
        self.thread = threading.Thread(target=self._track)

    def start(self):
        self.thread.start()

    def _track(self):
        while not self.stop_event.is_set():
            try:
                # 1. Local driver process memory
                mem = self.process.memory_info().rss
                for child in self.process.children(recursive=True):
                    mem += child.memory_info().rss
                
                # 2. Dask workers memory
                if self.client:
                    try:
                        workers = self.client.scheduler_info().get('workers', {})
                        worker_mem = sum(w.get('memory', 0) for w in workers.values())
                        mem += worker_mem
                    except Exception:
                        pass
                
                if mem > self.peak_memory:
                    self.peak_memory = mem
            except Exception:
                pass
            time.sleep(self.interval)

    def stop(self):
        self.stop_event.set()
        self.thread.join()
        return self.peak_memory

def run_strategy_1_groupby_apply(ddf, vessel_id_col, time_col, x_col, y_col):
    """Strategy 1: Direct Dask groupby-apply without pre-shuffling."""
    meta = ddf._meta.copy()
    meta[time_col] = pd.to_datetime(meta[time_col])
    meta['time_diff_s'] = pd.Series(dtype='float64')
    meta['rolling_area_m2'] = pd.Series(dtype='float64')
    meta['trip_id'] = pd.Series(dtype='str')
    meta['speed_mps'] = pd.Series(dtype='float64')
    meta['acceleration_mps2'] = pd.Series(dtype='float64')
    meta['turn_rate_from_cog'] = pd.Series(dtype='float64')
    meta['turn_rate_from_heading'] = pd.Series(dtype='float64')

    from .moving_dask.trajectory import process_single_vessel_partition
    # We apply directly on groupby
    # For Dask groupby apply, the applied function receives a pandas DataFrame for a single group (vessel)
    def process_group(df):
        return process_single_vessel_partition(
            df=df,
            vessel_id_col=vessel_id_col,
            time_col=time_col,
            x_col=x_col,
            y_col=y_col,
            gap_threshold_seconds=3600.0,
            stop_duration_min=20.0,
            stop_radius_m=50.0
        )
        
    result = ddf.groupby(vessel_id_col, group_keys=False).apply(process_group, meta=meta)
    return result

def run_strategy_3_set_index_map(ddf, vessel_id_col, time_col, x_col, y_col):
    """Strategy 3: Dask set_index followed by map_partitions."""
    ddf_indexed = ddf.set_index(vessel_id_col)
    
    # Meta schema
    meta = ddf_indexed._meta.copy()
    meta[time_col] = pd.to_datetime(meta[time_col])
    meta['time_diff_s'] = pd.Series(dtype='float64')
    meta['rolling_area_m2'] = pd.Series(dtype='float64')
    meta['trip_id'] = pd.Series(dtype='str')
    meta['speed_mps'] = pd.Series(dtype='float64')
    meta['acceleration_mps2'] = pd.Series(dtype='float64')
    meta['turn_rate_from_cog'] = pd.Series(dtype='float64')
    meta['turn_rate_from_heading'] = pd.Series(dtype='float64')

    from .moving_dask.trajectory import process_single_vessel_partition
    # We pass local operation that processes groups
    # Note that the index of df inside the partition mapper is vessel_id_col
    def local_op_indexed(df):
        if df.empty:
            return df
        # Reset index to make vessel_id_col a column again for the processing function,
        # then set it back to maintain index metadata consistency
        df_reset = df.reset_index()
        processed = process_single_vessel_partition(
            df=df_reset,
            vessel_id_col=vessel_id_col,
            time_col=time_col,
            x_col=x_col,
            y_col=y_col,
            gap_threshold_seconds=3600.0,
            stop_duration_min=20.0,
            stop_radius_m=50.0
        )
        if processed.empty:
            return processed
        return processed.set_index(vessel_id_col)

    result = ddf_indexed.map_partitions(local_op_indexed, meta=meta)
    return result

def run_benchmark_suite(
    dataset_path: str,
    mlflow_tracking_uri: str,
    vessel_id_col: str,
    time_col: str,
    x_col: str,
    y_col: str,
    runs_limit: int,
    scheduler: str = None,
    tmp_dir: str = None
):
    """Runs a parameter sweep of sorting/grouping strategies and logs them to MLflow."""
    # Set MLflow tracking URI
    mlflow.set_tracking_uri(mlflow_tracking_uri)
    mlflow.set_experiment("MovingDask_Benchmarking")

    # Set Dask temporary directory to fast scratch disk if available
    if not tmp_dir:
        import tempfile
        tmp_dir = os.getenv("TMPDIR", tempfile.gettempdir())
        
    try:
        os.makedirs(tmp_dir, exist_ok=True)
        import dask
        dask.config.set({"temporary-directory": str(tmp_dir)})
        logger.info(f"Dask temporary directory set to: {tmp_dir}")
    except Exception as e:
        logger.warning(f"Could not set Dask temporary directory to {tmp_dir}: {e}")

    # Connect to Dask
    if scheduler:
        logger.info(f"Connecting to Dask scheduler at {scheduler}...")
        client = Client(scheduler)
    else:
        logger.info("Starting Local Dask Client...")
        client = Client()
    
    logger.info(f"Dask Dashboard: {client.dashboard_link}")

    try:
        # Load sample data
        logger.info(f"Loading dataset: {dataset_path}...")
        ddf = dd.read_parquet(dataset_path)
        if ddf.npartitions < 32:
            logger.info(f"Dataset has only {ddf.npartitions} partitions. Repartitioning to 32 partitions to enable cluster parallelization...")
            ddf = ddf.repartition(npartitions=32)
        num_partitions = ddf.npartitions
        logger.info(f"Dataset partitions: {num_partitions}")

        # Count total rows to log
        row_count = len(ddf)
        logger.info(f"Dataset row count: {row_count}")

        # Define configurations to test
        configs = [
            {"strategy": "Strategy 2 (Shuffle + Map)", "shuffle_backend": "tasks"},
            {"strategy": "Strategy 2 (Shuffle + Map)", "shuffle_backend": "p2p"},
            {"strategy": "Strategy 2 (Shuffle + Map)", "shuffle_backend": "disk"},
            {"strategy": "Strategy 3 (Set Index + Map)", "shuffle_backend": "set_index"},
            {"strategy": "Strategy 1 (Direct Groupby-Apply)", "shuffle_backend": "groupby_apply"},
        ]

        run_count = 0
        for config in configs:
            if runs_limit > 0 and run_count >= runs_limit:
                logger.info("Reached runs limit, stopping sweep.")
                break

            strategy = config["strategy"]
            backend = config["shuffle_backend"]
            
            logger.info(f"\n--- Running: {strategy} [Backend: {backend}] ---")
            
            # Start memory tracking
            tracker = ClusterMemoryTracker(client=client)
            tracker.start()
            start_time = time.time()
            
            try:
                # Select the computation based on strategy
                if backend == "groupby_apply":
                    res_ddf = run_strategy_1_groupby_apply(ddf, vessel_id_col, time_col, x_col, y_col)
                elif backend == "set_index":
                    res_ddf = run_strategy_3_set_index_map(ddf, vessel_id_col, time_col, x_col, y_col)
                else:
                    # Strategy 2 (Shuffle + MapPartitions)
                    res_ddf = trajectorize_dataframe(
                        ddf=ddf,
                        vessel_id_col=vessel_id_col,
                        time_col=time_col,
                        x_col=x_col,
                        y_col=y_col,
                        shuffle_backend=backend
                    )
                
                # Execute computation
                computed_df = res_ddf.compute()
                elapsed = time.time() - start_time
                peak_mem = tracker.stop()
                
                # Log to MLflow
                with mlflow.start_run(run_name=f"{strategy} ({backend})"):
                    mlflow.log_param("dataset_path", dataset_path)
                    mlflow.log_param("vessel_id_col", vessel_id_col)
                    mlflow.log_param("time_col", time_col)
                    mlflow.log_param("strategy", strategy)
                    mlflow.log_param("shuffle_backend", backend)
                    mlflow.log_param("num_partitions", num_partitions)
                    mlflow.log_param("row_count", row_count)
                    
                    mlflow.log_metric("elapsed_time_seconds", elapsed)
                    mlflow.log_metric("peak_memory_mb", peak_mem / (1024**2))
                    mlflow.log_metric("throughput_rows_per_sec", row_count / elapsed)
                    
                logger.info(f"Completed: {strategy} in {elapsed:.2f}s (Peak Memory: {peak_mem / (1024**2):.2f} MB)")
                run_count += 1
                
            except Exception as e:
                tracker.stop()
                logger.error(f"Execution failed for {strategy} with backend {backend}: {e}")
                with mlflow.start_run(run_name=f"{strategy} ({backend}) - FAILED"):
                    mlflow.log_param("strategy", strategy)
                    mlflow.log_param("shuffle_backend", backend)
                    mlflow.log_param("status", "FAILED")
                    mlflow.log_param("error", str(e))
                
    finally:
        client.close()
