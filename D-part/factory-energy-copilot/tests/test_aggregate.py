"""Small-file tests for the streaming hourly aggregator."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq

from config import config
from src.aggregate.aggregate import aggregate_csv
from src.common.io_meta import get_file_metadata


HEADER = "module(equipment),timestamp,localtime,activePower\n"


def _write_csv(path, rows: list[str]) -> None:
    path.write_text(HEADER + "".join(rows), encoding="utf-8")


def _read_rows(path):
    return pq.read_table(path).to_pylist()


def test_aggregate_sums_by_module_and_hour_with_count(tmp_path):
    source = tmp_path / "training.csv"
    output = tmp_path / "hourly_from_full.parquet"
    rows = [f"M1,ignored,202501010000{i:02d},{i + 1}\n" for i in range(3)]
    _write_csv(source, rows)

    # Use the production complete-hour setting for a focused one-group test.
    original_complete = config.SAMPLES_PER_HOUR_COMPLETE
    config.SAMPLES_PER_HOUR_COMPLETE = 3
    try:
        metadata = aggregate_csv(source, output, chunk_log_interval=2)
    finally:
        config.SAMPLES_PER_HOUR_COMPLETE = original_complete

    result = _read_rows(output)
    assert len(result) == 1
    assert result[0]["module"] == "M1"
    assert result[0]["hour"] == datetime(2025, 1, 1, tzinfo=ZoneInfo("Asia/Seoul"))
    assert result[0]["n"] == 3
    assert result[0]["hourly_pow"] == (1 + 2 + 3) * 5 / 3600
    assert metadata["total_group_count"] == 1


def test_n_720_filter_and_excluded_group_count(tmp_path):
    source = tmp_path / "training.csv"
    output = tmp_path / "hourly.parquet"
    rows = [f"M1,,202501010000{i:02d},{1}\n" for i in range(3)]
    rows.extend(f"M1,,202501010100{i:02d},{2}\n" for i in range(2))
    _write_csv(source, rows)

    original_complete = config.SAMPLES_PER_HOUR_COMPLETE
    config.SAMPLES_PER_HOUR_COMPLETE = 3
    try:
        metadata = aggregate_csv(source, output)
    finally:
        config.SAMPLES_PER_HOUR_COMPLETE = original_complete

    assert len(_read_rows(output)) == 1
    assert metadata["total_group_count"] == 2
    assert metadata["complete_group_count"] == 1
    assert metadata["excluded_incomplete_group_count"] == 1


def test_max_rows_marks_partial_and_records_limit(tmp_path):
    source = tmp_path / "training.csv"
    output = tmp_path / "hourly.parquet"
    _write_csv(source, [f"M1,,202501010000{i:02d},1\n" for i in range(4)])

    original_complete = config.SAMPLES_PER_HOUR_COMPLETE
    config.SAMPLES_PER_HOUR_COMPLETE = 3
    try:
        metadata = aggregate_csv(source, output, max_rows=3)
    finally:
        config.SAMPLES_PER_HOUR_COMPLETE = original_complete

    run = json.loads((tmp_path / "aggregate_run.json").read_text(encoding="utf-8"))
    assert metadata["partial"] is True
    assert run["partial"] is True
    assert run["rows_limit"] == 3
    assert run["input_scan_partial"] is True


def test_malformed_and_non_numeric_rows_are_recorded(tmp_path):
    source = tmp_path / "training.csv"
    output = tmp_path / "hourly.parquet"
    _write_csv(
        source,
        [
            "M1,,20250101000000,not-a-number\n",
            "M1,only-two-fields\n",
            "M1,,20250101000005,1\n",
        ],
    )

    metadata = aggregate_csv(source, output)

    assert metadata["error_count"] == 2
    assert metadata["malformed_row_error_count"] == 1
    assert metadata["non_numeric_row_count"] == 1
    assert len(metadata["error_samples"]) == 2
    assert metadata["valid_rows"] == 2


def test_source_metadata_is_unchanged_and_output_is_separate(tmp_path):
    source = tmp_path / "training.csv"
    output = tmp_path / "artifacts" / "hourly.parquet"
    _write_csv(source, ["M1,,20250101000000,1\n"])
    before = get_file_metadata(source, compute_hash=True)

    aggregate_csv(source, output)

    after = get_file_metadata(source, compute_hash=True)
    assert before.length == after.length
    assert before.last_write_time_ns == after.last_write_time_ns
    assert before.sha256 == after.sha256
    run = json.loads((output.parent / "aggregate_run.json").read_text(encoding="utf-8"))
    assert run["source_immutability"]["unchanged"] is True
    assert source != output


def test_five_second_formula_and_kst_hour_bucket(tmp_path):
    source = tmp_path / "training.csv"
    output = tmp_path / "hourly.parquet"
    rows = [
        "M1,,20250101235959,10\n",
        "M1,,20250101235955,10\n",
        "M1,,20250102000000,20\n",
        "M1,,20250102000005,30\n",
    ]
    _write_csv(source, rows)

    original_complete = config.SAMPLES_PER_HOUR_COMPLETE
    config.SAMPLES_PER_HOUR_COMPLETE = 2
    try:
        metadata = aggregate_csv(source, output)
    finally:
        config.SAMPLES_PER_HOUR_COMPLETE = original_complete

    result = _read_rows(output)
    assert [row["n"] for row in result] == [2, 2]
    assert result[0]["hour"].hour == 23
    assert result[1]["hour"].hour == 0
    assert result[1]["hourly_pow"] == (20 + 30) * config.SECONDS_PER_SAMPLE / 3600
    assert metadata["time_basis"] == "localtime (KST)"
    assert metadata["timestamp_column_used"] is False
    assert metadata["seconds_per_sample"] == 5
