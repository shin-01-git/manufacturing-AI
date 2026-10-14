"""Small-file tests for streaming CSV exploration; no production CSV is read."""

import json

from src.aggregate.explore import explore_sources
from src.common.streaming_csv import scan_csv


HEADER = "module(equipment),localtime,activePower\n"
ROWS = (
    "M1,20250101000000,1\n"
    "M1,20250101000005,2\n"
    "M1,20250101000010,3\n"
    "M2,20250101000100,4\n"
)


def test_streaming_scan_returns_header_samples_and_physical_count(tmp_path):
    path = tmp_path / "small.csv"
    path.write_text(HEADER + ROWS, encoding="utf-8")

    result = scan_csv(path, sample_size=2, tail_size=2)

    assert result.header == ["module(equipment)", "localtime", "activePower"]
    assert result.column_count == 3
    assert result.delimiter == ","
    assert result.csv_record_count == 4
    assert result.physical_row_count == 4
    assert result.partial is False
    assert result.head_samples[0]["localtime"] == "20250101000000"
    assert result.tail_samples[-1]["activePower"] == "4"


def test_max_rows_is_partial_and_keeps_bounded_tail(tmp_path):
    path = tmp_path / "small.csv"
    path.write_text(HEADER + ROWS, encoding="utf-8")

    result = scan_csv(path, max_rows=2, sample_size=5, tail_size=1)

    assert result.csv_record_count == 2
    assert result.partial is True
    assert len(result.tail_samples) == 1
    assert result.tail_samples[0]["activePower"] == "2"


def test_explore_writes_source_metadata_and_distribution(tmp_path):
    training = tmp_path / "training.csv"
    validation = tmp_path / "validation.csv"
    artifacts = tmp_path / "artifacts"
    training.write_text(HEADER + ROWS, encoding="utf-8")
    validation.write_text(
        "id,hourly_pow,may_bill,may_carbon,agg_pow\n"
        "2025-05-10 00:00:00,1,180,0.424,1\n",
        encoding="utf-8",
    )

    explore_sources(
        training,
        validation,
        artifacts_dir=artifacts,
        max_rows=2,
        include_distribution=True,
    )

    source = json.loads((artifacts / "source_meta.json").read_text(encoding="utf-8"))
    distribution = json.loads((artifacts / "explore_dist.json").read_text(encoding="utf-8"))
    assert source["source_immutability"]["unchanged"] is True
    assert source["files"][0]["column_count"] == 3
    assert source["files"][0]["partial"] is True
    assert distribution["scan"]["single_sequential_pass"] is True
    assert distribution["scan"]["partial"] is True
    assert distribution["discovered_modules"] == ["M1"]
    assert distribution["n_720_summary"]["incomplete_module_hour_count"] == 1
