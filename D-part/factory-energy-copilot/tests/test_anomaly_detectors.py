"""Tests for the statistical + unsupervised detectors and method matching (6.2)."""

from __future__ import annotations

import random

from config import config
from src.anomaly.detectors import (
    DetectionAggregator,
    MemoryEventSink,
    Reservoir,
    run_detectors,
)

HEADER = (
    "module(equipment),timestamp,localtime,operation,"
    "voltageR,voltageS,voltageT,voltageRS,voltageST,voltageTR,"
    "currentR,currentS,currentT,activePower,"
    "powerFactorR,powerFactorS,powerFactorT,reactivePowerLagging,accumActiveEnergy\n"
)


def _row(localtime, module="M1", vR=215.0, vS=215.0, vT=215.0, ap=1000.0, pfR=90.0, pfS=90.0, pfT=90.0):
    return (
        f"{module},0,{localtime},1,"
        f"{vR},{vS},{vT},371,375,375,"
        f"10,10,10,{ap},"
        f"{pfR},{pfS},{pfT},785,1955004\n"
    )


def _write(path, rows):
    path.write_text(HEADER + "".join(rows), encoding="utf-8")


def test_reservoir_is_bounded_and_deterministic():
    r1 = Reservoir(10, seed=config.RANDOM_SEED)
    r2 = Reservoir(10, seed=config.RANDOM_SEED)
    for i in range(1000):
        r1.offer([float(i)])
        r2.offer([float(i)])
    assert len(r1.samples) == 10
    assert r1.seen == 1000
    # Same seed -> identical reservoir contents (reproducibility, NFR-7).
    assert r1.samples == r2.samples


def test_three_methods_matched_on_same_row(tmp_path):
    source = tmp_path / "train.csv"
    # Many normal rows to establish a per-module baseline, plus a clear outlier.
    rows = [_row(f"202412010{i // 60:02d}{i % 60:02d}00", ap=1000.0 + (i % 3)) for i in range(200)]
    # Outlier row: rule hit (3-phase low voltage) + huge activePower spike.
    rows.append(_row("20241201030000", vR=200.0, vS=201.0, vT=199.0, ap=500000.0))
    _write(source, rows)

    result = run_detectors(source, reservoir_size=100)

    assert result.events, "expected at least the outlier event"
    outlier = [e for e in result.events if e.localtime.hour == 3]
    assert outlier, "the spike row should be detected"
    event = outlier[0]
    # Every event carries all three method flags (matched on the same 5s row).
    assert set(event.detected_by) == {"rule", "statistical", "unsupervised"}
    assert event.detected_by["rule"] is True  # 3-phase voltage rule fired
    assert result.meta["passes"] == 2
    assert result.meta["isolation_forest_trained"] is True


def test_detector_result_is_reproducible(tmp_path):
    source = tmp_path / "train.csv"
    rows = [_row(f"202412010{i // 60:02d}{i % 60:02d}00", ap=1000.0 + random.Random(i).random()) for i in range(150)]
    rows.append(_row("20241201030000", ap=999999.0))
    _write(source, rows)

    r1 = run_detectors(source, reservoir_size=50)
    r2 = run_detectors(source, reservoir_size=50)
    keys1 = sorted(e.key() for e in r1.events)
    keys2 = sorted(e.key() for e in r2.events)
    assert keys1 == keys2


def test_events_stream_to_sink_not_memory(tmp_path):
    """collect_events=False keeps no event list; the sink receives every event."""

    source = tmp_path / "train.csv"
    rows = [_row(f"202412010{i // 60:02d}{i % 60:02d}00", ap=1000.0 + (i % 3)) for i in range(200)]
    rows.append(_row("20241201030000", vR=200.0, vS=201.0, vT=199.0, ap=500000.0))
    _write(source, rows)

    sink = MemoryEventSink()
    result = run_detectors(source, reservoir_size=100, sink=sink, collect_events=False)

    # No in-memory event list is retained on the full-scan path.
    assert result.events is None
    assert result.events_in_memory is False
    assert result.meta["events_in_memory"] is False
    # The sink saw exactly the detected events the aggregator counted.
    assert len(sink.events) == result.aggregator.total_detected > 0


def test_aggregates_match_full_event_enumeration(tmp_path):
    """Hourly / module / method aggregates equal a full re-count of the events."""

    source = tmp_path / "train.csv"
    rows = [_row(f"202412010{i // 60:02d}{i % 60:02d}00", module="M1", ap=1000.0 + (i % 3)) for i in range(120)]
    rows += [_row(f"202412011{i // 60:02d}{i % 60:02d}00", module="M2", ap=900.0 + (i % 3)) for i in range(80)]
    rows.append(_row("20241201030000", module="M1", vR=200.0, vS=201.0, vT=199.0, ap=500000.0))
    rows.append(_row("20241201030500", module="M2", vR=200.0, vS=201.0, vT=199.0, ap=400000.0))
    _write(source, rows)

    sink = MemoryEventSink()
    result = run_detectors(source, reservoir_size=100, sink=sink)
    agg = result.aggregator

    # Rebuild the same aggregates from the captured events and compare.
    expected = DetectionAggregator()
    for event in sink.events:
        expected.add_event(event)

    assert agg.hourly == expected.hourly
    assert agg.module_anomaly_count == expected.module_anomaly_count
    assert agg.module_last_seen == expected.module_last_seen
    assert agg.method_counts == expected.method_counts
    assert agg.overlap_counts == expected.overlap_counts
    assert agg.total_detected == len(sink.events)
    # Denominator rows were folded into pass 2 (3-pass -> 2-pass).
    assert agg.module_rows["M1"] >= 1 and agg.module_rows["M2"] >= 1


def test_scan_is_two_pass_only(tmp_path):
    """The detector scans the source exactly twice (no extra denominator pass)."""

    source = tmp_path / "train.csv"
    _write(source, [_row(f"202412010000{i:02d}", ap=1000.0 + i) for i in range(30)])
    result = run_detectors(source, reservoir_size=20)
    assert result.meta["passes"] == 2


def test_source_unchanged_after_two_passes(tmp_path):
    from src.common.io_meta import get_file_metadata

    source = tmp_path / "train.csv"
    _write(source, [_row(f"202412010000{i:02d}", ap=1000.0 + i) for i in range(30)])
    before = get_file_metadata(source, compute_hash=True)
    result = run_detectors(source, reservoir_size=20)
    after = get_file_metadata(source, compute_hash=True)
    assert before.sha256 == after.sha256
    assert result.meta["source_immutability"]["unchanged"] is True
