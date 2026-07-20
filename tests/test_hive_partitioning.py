"""
Regression tests for detect_hive_partitioning (data_loader.py): correctness of
partition-key/type detection, and that it no longer performs a full
input_path.rglob("*") walk of every leaf file (expensive on large hive
datasets with many partitions/files) to find the much shallower partition
directories.
"""
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from ais_shader.data_loader import detect_hive_partitioning


def _make_hive_dataset(root: Path, n_leaf_files_per_partition=5):
    for year in ("2021",):
        for month in ("03", "04"):
            part_dir = root / f"year={year}" / f"month={month}"
            part_dir.mkdir(parents=True)
            table = pa.table({"mmsi": [1, 2], "sog": [1.0, 2.0]})
            for i in range(n_leaf_files_per_partition):
                pq.write_table(table, part_dir / f"part-{i}.parquet")
    return root


def test_detects_partition_keys_and_values(tmp_path):
    _make_hive_dataset(tmp_path)
    partitioning = detect_hive_partitioning(tmp_path)
    assert partitioning is not None
    schema_names = partitioning.schema.names
    assert "year" in schema_names
    assert "month" in schema_names


def test_infers_types_matching_file_schema(tmp_path):
    _make_hive_dataset(tmp_path)
    partitioning = detect_hive_partitioning(tmp_path)
    # "year"/"month" directory values ("2021", "03") are all-digit strings,
    # so with no matching column in the actual file schema (mmsi/sog only),
    # they should be inferred as int64.
    year_field = partitioning.schema.field("year")
    assert pa.types.is_integer(year_field.type)


def test_non_directory_returns_none(tmp_path):
    f = tmp_path / "not_a_dir.txt"
    f.write_text("x")
    assert detect_hive_partitioning(f) is None


def test_non_hive_directory_returns_none(tmp_path):
    (tmp_path / "plainfile.parquet").write_text("not real parquet, doesn't matter for this check")
    assert detect_hive_partitioning(tmp_path) is None


def test_does_not_recurse_into_leaf_files(tmp_path, monkeypatch):
    # The old implementation walked every leaf file via input_path.rglob("*")
    # to find partition-key directories. The fixed version only walks the
    # partition-key directory levels via Path.iterdir(), never Path.rglob().
    _make_hive_dataset(tmp_path, n_leaf_files_per_partition=50)

    original_rglob = Path.rglob

    def _fail_if_called(self, *args, **kwargs):
        raise AssertionError("detect_hive_partitioning should not call Path.rglob()")

    monkeypatch.setattr(Path, "rglob", _fail_if_called)
    try:
        partitioning = detect_hive_partitioning(tmp_path)
    finally:
        monkeypatch.setattr(Path, "rglob", original_rglob)

    assert partitioning is not None
