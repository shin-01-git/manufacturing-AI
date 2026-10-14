"""Small Parquet tests for training feature preprocessing."""

from __future__ import annotations

from datetime import datetime
import inspect
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.preprocess import features

KST = ZoneInfo("Asia/Seoul")


def _write_hourly(path: Path, rows: list[dict]) -> None:
    table = pa.table(
        {
            "module": pa.array([row["module"] for row in rows], type=pa.string()),
            "hour": pa.array([row["hour"] for row in rows], type=pa.timestamp("us", tz="Asia/Seoul")),
            "hourly_pow": pa.array([row["hourly_pow"] for row in rows], type=pa.float64()),
            "n": pa.array([row.get("n", 720) for row in rows], type=pa.int64()),
        }
    )
    pq.write_table(table, path)


def _rows() -> list[dict]:
    hour = datetime(2025, 5, 10, 13, tzinfo=KST)
    return [
        {"module": "M1", "hour": hour, "hourly_pow": 10.0},
        {"module": "M2", "hour": hour, "hourly_pow": 20.5},
        {"module": "M1", "hour": hour.replace(hour=14), "hourly_pow": 11.0},
    ]


def test_module_hourly_power_is_summed(tmp_path):
    source = tmp_path / "hourly.parquet"
    output = tmp_path / "features.parquet"
    _write_hourly(source, _rows())

    features.create_features(source, output)

    result = pq.read_table(output).to_pylist()
    assert [row["target_hourly_pow"] for row in result] == [30.5, 11.0]
    assert set(result[0]) >= {
        "hour",
        "target_hourly_pow",
        "weekday",
        "hour_of_day",
        "day_of_month",
        "week_of_year",
        "is_weekend",
    }


def test_calendar_features_use_kst_values(tmp_path):
    source = tmp_path / "hourly.parquet"
    output = tmp_path / "features.parquet"
    _write_hourly(source, [_rows()[0]])

    features.create_features(source, output)

    row = pq.read_table(output).to_pylist()[0]
    assert row["weekday"] == 5  # Saturday
    assert row["hour_of_day"] == 13
    assert row["day_of_month"] == 10
    assert row["week_of_year"] == 19
    assert row["is_weekend"] is True


def test_forbidden_features_are_removed_and_allowed_features_remain():
    candidates = [
        "weekday",
        "hour_of_day",
        "voltageR",
        "currentT",
        "powerFactorR",
        "recent_lag",
        "target_hourly_pow",
    ]

    assert features.get_forbidden_features(candidates) == [
        "voltageR",
        "currentT",
        "powerFactorR",
        "recent_lag",
    ]
    assert features.select_allowed_features(candidates) == [
        "weekday",
        "hour_of_day",
        "target_hourly_pow",
    ]


def test_partial_input_metadata_is_preserved(tmp_path):
    source = tmp_path / "hourly.parquet"
    output = tmp_path / "features.parquet"
    _write_hourly(source, _rows())
    input_metadata = {"partial": True, "rows_limit": 10000, "source": "aggregate"}
    (tmp_path / "aggregate_run.json").write_text(
        json.dumps(input_metadata), encoding="utf-8"
    )

    metadata = features.create_features(source, output, max_rows=1)
    run = json.loads((tmp_path / "features_run.json").read_text(encoding="utf-8"))

    assert metadata["partial"] is True
    assert metadata["input_partial"] is True
    assert metadata["input_run_metadata"] == input_metadata
    assert run["input_run_metadata"] == input_metadata
    assert run["rows_limit"] == 1


def test_module_limit_records_warning_and_keeps_discovered_modules(tmp_path):
    source = tmp_path / "hourly.parquet"
    output = tmp_path / "features.parquet"
    hour = datetime(2025, 5, 10, 0, tzinfo=KST)
    rows = [
        {"module": f"M{i}", "hour": hour, "hourly_pow": float(i)}
        for i in range(14)
    ]
    _write_hourly(source, rows)

    with pytest.warns(RuntimeWarning, match="exceeding"):
        metadata = features.create_features(source, output)

    result = pq.read_table(output).to_pylist()
    assert metadata["module_count"] == 14
    assert metadata["module_count_exceeds_limit"] is True
    assert metadata["warnings"]
    assert result[0]["target_hourly_pow"] == sum(range(14))


def test_training_features_do_not_reference_validation_path():
    source = inspect.getsource(features)
    assert "Validation_CSV" not in source
    assert "rtu_ground_truth" not in source
    assert "validation_csv" not in source.casefold()
