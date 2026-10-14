"""Schema/path, status label, module-cap, impact, and no-leak tests (6.4/6.5)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq

from config import config
from src.anomaly import artifacts as artifacts_mod
from src.anomaly import compare as compare_mod
from src.anomaly import detectors as detectors_mod
from src.anomaly import rules as rules_mod
from src.anomaly.artifacts import (
    STATUS_UNDECIDED,
    build_hourly_rows,
    build_module_status,
    default_tmp_paths,
    module_cap_check,
    write_artifacts,
)
from src.anomaly.artifacts import (
    ParquetEventSink,
    build_hourly_rows_from_aggregator,
    build_module_status_from_aggregator,
    write_aggregate_artifacts,
)
from src.anomaly.detectors import DetectionAggregator, DetectionEvent
from src.anomaly.impact import NO_IMPACT_LABEL, UNMEASURED_LABEL, assess_impact

KST = ZoneInfo("Asia/Seoul")

SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "anomaly"


def _event(module, hour, detected_by, rule_types=None, score=0.1):
    return DetectionEvent(
        module=module,
        localtime=datetime(2024, 12, 1, hour, tzinfo=KST),
        detected_by=detected_by,
        rule_types=rule_types or [],
        value=5.0,
        normal_range=">= 210 V (all 3 phases)",
        statistical_z=4.2,
        unsupervised_score=score,
    )


def test_artifacts_schema_and_paths(tmp_path):
    events = [
        _event("M1", 0, {"rule": True, "statistical": True, "unsupervised": False}, ["voltage_low_3phase"]),
        _event("M1", 1, {"rule": False, "statistical": True, "unsupervised": True}),
        _event("M2", 0, {"rule": True, "statistical": False, "unsupervised": False}, ["power_factor_low_3phase"]),
    ]
    module_rows = {"M1": 100, "M2": 50}
    paths = default_tmp_paths(tmp_path)
    write_artifacts(events, module_rows, paths)

    assert paths.events.exists()
    assert paths.hourly.exists()
    assert paths.module_status.exists()

    events_cols = pq.read_table(paths.events).column_names
    assert events_cols == ["module", "localtime", "type", "value", "normal_range", "detected_by", "score"]

    hourly_cols = pq.read_table(paths.hourly).column_names
    assert hourly_cols == ["hour", "module", "type", "count"]

    header = paths.module_status.read_text(encoding="utf-8").splitlines()[0]
    assert header == "module,anomaly_count,anomaly_ratio,last_seen,status"


def test_parquet_event_sink_streams_schema_in_batches(tmp_path):
    """The streaming sink writes anomaly_events.parquet with the design schema."""

    events = [
        _event("M1", 0, {"rule": True, "statistical": True, "unsupervised": False}, ["voltage_low_3phase"]),
        _event("M1", 1, {"rule": False, "statistical": True, "unsupervised": True}),
        _event("M2", 0, {"rule": True, "statistical": False, "unsupervised": False}, ["power_factor_low_3phase"]),
    ]
    out = tmp_path / "anomaly_events.parquet"
    # Small batch size forces multiple appended batches (bounded memory path).
    sink = ParquetEventSink(out, batch_size=2)
    for event in events:
        sink.emit(event)
    sink.close()

    table = pq.read_table(out)
    assert table.column_names == ["module", "localtime", "type", "value", "normal_range", "detected_by", "score"]
    assert table.num_rows == len(events) == sink.rows_written


def test_parquet_event_sink_writes_empty_file_with_schema(tmp_path):
    """Zero detected events still yields a schema-correct empty parquet file."""

    out = tmp_path / "anomaly_events.parquet"
    sink = ParquetEventSink(out)
    sink.close()
    table = pq.read_table(out)
    assert table.num_rows == 0
    assert table.column_names == ["module", "localtime", "type", "value", "normal_range", "detected_by", "score"]


def test_aggregate_artifacts_match_event_based_build(tmp_path):
    """hourly/module_status from the aggregator equal the in-memory event build."""

    events = [
        _event("M1", 0, {"rule": True, "statistical": True, "unsupervised": False}, ["voltage_low_3phase"]),
        _event("M1", 1, {"rule": False, "statistical": True, "unsupervised": True}),
        _event("M2", 0, {"rule": True, "statistical": False, "unsupervised": False}, ["power_factor_low_3phase"]),
    ]
    module_rows = {"M1": 100, "M2": 50}

    aggregator = DetectionAggregator()
    for _ in range(module_rows["M1"]):
        aggregator.count_row("M1")
    for _ in range(module_rows["M2"]):
        aggregator.count_row("M2")
    for event in events:
        aggregator.add_event(event)

    hourly = build_hourly_rows_from_aggregator(aggregator)
    status = build_module_status_from_aggregator(aggregator)
    assert hourly == build_hourly_rows(events)
    assert status == build_module_status(events, module_rows)

    # The aggregate writer produces the same files/schema as write_artifacts.
    paths = default_tmp_paths(tmp_path)
    sink = ParquetEventSink(paths.events)
    for event in events:
        sink.emit(event)
    sink.close()
    write_aggregate_artifacts(aggregator, paths, events_written=sink.rows_written)

    assert paths.events.exists() and paths.hourly.exists() and paths.module_status.exists()
    assert pq.read_table(paths.hourly).column_names == ["hour", "module", "type", "count"]
    header = paths.module_status.read_text(encoding="utf-8").splitlines()[0]
    assert header == "module,anomaly_count,anomaly_ratio,last_seen,status"


def test_module_status_is_undecided_label(tmp_path):
    events = [_event("M1", 0, {"rule": True, "statistical": False, "unsupervised": False}, ["voltage_low_3phase"])]
    rows = build_module_status(events, {"M1": 10})
    assert rows
    assert all(row["status"] == STATUS_UNDECIDED for row in rows)
    assert rows[0]["anomaly_count"] == 1
    assert 0.0 <= rows[0]["anomaly_ratio"] <= 1.0


def test_module_cap_warns_when_not_13():
    # Fewer than 13 discovered -> warning recorded (O-5).
    check = module_cap_check(["M1", "M2"])
    assert check["expected_cap"] == config.MODULE_COUNT_EXPECTED == 13
    assert check["matches_expected"] is False
    assert check["warnings"]

    # Exactly 13 -> no warning.
    check13 = module_cap_check([f"M{i}" for i in range(13)])
    assert check13["matches_expected"] is True
    assert check13["warnings"] == []

    # More than 13 -> over-cap warning.
    check14 = module_cap_check([f"M{i}" for i in range(14)])
    assert check14["within_cap"] is False
    assert check14["warnings"]


def test_refuses_to_overwrite_shared_artifacts(tmp_path, monkeypatch):
    # Point the shared events path at an existing file and confirm the guard.
    shared = tmp_path / "anomaly_events.parquet"
    shared.write_text("existing", encoding="utf-8")
    monkeypatch.setattr(config, "ANOMALY_EVENTS", shared)
    paths = artifacts_mod.ArtifactPaths(
        events=shared,
        hourly=tmp_path / "h.parquet",
        module_status=tmp_path / "s.csv",
        run_meta=tmp_path / "m.json",
    )
    try:
        write_artifacts([], {}, paths)
        assert False, "expected overwrite guard to raise"
    except RuntimeError as exc:
        assert "Refusing to overwrite" in str(exc)


def test_impact_unmeasured_shows_no_number():
    result = assess_impact(None, measurable=False)
    assert result.label == UNMEASURED_LABEL
    assert result.bill_impact is None
    assert result.carbon_impact is None
    assert result.to_dict()["o2_approved"] is False


def test_impact_noise_range_shows_no_number():
    result = assess_impact(5.0, measurable=True, is_noise=True)
    assert result.label == NO_IMPACT_LABEL
    assert result.bill_impact is None
    assert result.energy_delta is None


def test_impact_measurable_uses_config_rates():
    result = assess_impact(10.0, measurable=True)
    assert result.bill_impact == 10.0 * config.BILL_RATE
    assert result.carbon_impact == 10.0 * config.CARBON_RATE
    assert result.to_dict()["provisional"] is True


def test_anomaly_modules_do_not_reference_validation_csv():
    # No-leak static check (NFR-4/S2): anomaly source must not name the
    # validation ground-truth file or its config constant.
    for name in ("rules.py", "detectors.py", "compare.py", "artifacts.py", "impact.py", "_common.py"):
        text = (SRC_DIR / name).read_text(encoding="utf-8")
        assert "VALIDATION_CSV" not in text, f"{name} references VALIDATION_CSV"
        assert "검증용" not in text, f"{name} references the validation file name"
        assert "rtu_ground_truth" not in text, f"{name} references the ground-truth file"
