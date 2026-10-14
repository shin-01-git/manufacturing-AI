"""Tests for the O-9 (a) three-phase simultaneous rule (6.1 / B2)."""

from __future__ import annotations

from config import config
from src.anomaly import _common
from src.anomaly.rules import (
    TYPE_POWER_FACTOR,
    TYPE_VOLTAGE,
    evaluate_row,
    scan_rules,
)
from src.common.io_meta import get_file_metadata

# 19-column header matching the real training CSV.
HEADER = (
    "module(equipment),timestamp,localtime,operation,"
    "voltageR,voltageS,voltageT,voltageRS,voltageST,voltageTR,"
    "currentR,currentS,currentT,activePower,"
    "powerFactorR,powerFactorS,powerFactorT,reactivePowerLagging,accumActiveEnergy\n"
)


def _row(
    module="M1",
    localtime="20241201000000",
    vR=215.0,
    vS=215.0,
    vT=215.0,
    cR=10.0,
    cS=10.0,
    cT=10.0,
    ap=1000.0,
    pfR=90.0,
    pfS=90.0,
    pfT=90.0,
):
    return (
        f"{module},0,{localtime},1,"
        f"{vR},{vS},{vT},371,375,375,"
        f"{cR},{cS},{cT},{ap},"
        f"{pfR},{pfS},{pfT},785,1955004\n"
    )


def _write(path, rows):
    path.write_text(HEADER + "".join(rows), encoding="utf-8")


def _parse_single(**kwargs):
    raw_line = _row(**kwargs)
    cols = HEADER.strip().split(",")
    values = raw_line.strip().split(",")
    record = dict(zip(cols, values))
    columns = _common.resolve_columns(cols)
    return _common.parse_row(record, columns)


def test_three_phase_voltage_all_below_detects():
    parsed = _parse_single(vR=209.0, vS=208.0, vT=200.0)
    hits = evaluate_row(parsed)
    assert any(h.type == TYPE_VOLTAGE for h in hits)


def test_one_phase_voltage_below_does_not_detect():
    # Only one phase below 210 -> no three-phase-simultaneous voltage hit.
    parsed = _parse_single(vR=209.0, vS=215.0, vT=215.0)
    hits = evaluate_row(parsed)
    assert not any(h.type == TYPE_VOLTAGE for h in hits)


def test_two_phase_voltage_below_does_not_detect():
    parsed = _parse_single(vR=209.0, vS=200.0, vT=215.0)
    hits = evaluate_row(parsed)
    assert not any(h.type == TYPE_VOLTAGE for h in hits)


def test_three_phase_power_factor_all_below_detects():
    parsed = _parse_single(pfR=80.0, pfS=84.9, pfT=10.0)
    hits = evaluate_row(parsed)
    assert any(h.type == TYPE_POWER_FACTOR for h in hits)


def test_one_phase_power_factor_below_does_not_detect():
    parsed = _parse_single(pfR=80.0, pfS=90.0, pfT=90.0)
    hits = evaluate_row(parsed)
    assert not any(h.type == TYPE_POWER_FACTOR for h in hits)


def test_normal_row_detects_nothing():
    parsed = _parse_single()
    assert evaluate_row(parsed) == []


def test_thresholds_come_from_config():
    # (b) Thresholds are read from config, not hardcoded in the rule.
    original_v = config.VOLTAGE_THRESHOLD
    try:
        config.VOLTAGE_THRESHOLD = 999.0  # make an otherwise-normal row trip
        parsed = _parse_single(vR=215.0, vS=215.0, vT=215.0)
        hits = evaluate_row(parsed, voltage_threshold=config.VOLTAGE_THRESHOLD)
        assert any(h.type == TYPE_VOLTAGE for h in hits)
    finally:
        config.VOLTAGE_THRESHOLD = original_v


def test_scan_counts_candidates_and_source_unchanged(tmp_path):
    source = tmp_path / "train.csv"
    rows = [
        _row(localtime="20241201000000", vR=209, vS=208, vT=207),  # voltage hit
        _row(localtime="20241201000005", pfR=80, pfS=81, pfT=82),  # pf hit
        _row(localtime="20241201000010"),  # normal
        _row(localtime="20241201000015", vR=209, vS=215, vT=215),  # one phase -> no hit
    ]
    _write(source, rows)
    before = get_file_metadata(source, compute_hash=True)

    result = scan_rules(source)

    after = get_file_metadata(source, compute_hash=True)
    assert result.voltage_candidate_count == 1
    assert result.power_factor_candidate_count == 1
    assert len(result.candidates) == 2
    assert before.sha256 == after.sha256
    assert result.meta["source_immutability"]["unchanged"] is True
    assert result.meta["thresholds_from_config"] is True


def test_max_rows_marks_partial(tmp_path):
    source = tmp_path / "train.csv"
    _write(source, [_row(localtime=f"202412010000{i:02d}") for i in range(5)])
    result = scan_rules(source, max_rows=2)
    assert result.partial is True
