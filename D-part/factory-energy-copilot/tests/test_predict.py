"""Tests for the 505-point predictor and interval augmentation (5.3, 5.4)."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from config import config
from src.predict import interval as interval_mod
from src.predict import predict as predict_mod

KST = ZoneInfo("Asia/Seoul")


def _read_csv(path):
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def test_prediction_grid_has_505_hourly_points():
    grid = predict_mod.prediction_grid()
    assert len(grid) == predict_mod.PRED_COUNT == 505
    assert grid[0] == datetime(2025, 5, 10, 0, tzinfo=KST)
    assert grid[-1] == datetime(2025, 5, 31, 0, tzinfo=KST)
    diffs = {grid[i + 1] - grid[i] for i in range(len(grid) - 1)}
    assert diffs == {timedelta(hours=1)}


def test_predict_writes_505_rows_with_conversions(trained_artifacts):
    output = trained_artifacts["tmp"] / "predictions.csv"
    rows = predict_mod.predict(
        state_path=trained_artifacts["state"],
        model_path=trained_artifacts["model"],
        output_path=output,
    )
    assert len(rows) == 505

    written = _read_csv(output)
    assert len(written) == 505
    assert list(written[0]) == ["id", "pred_pow", "pred_bill", "pred_carbon"]
    for row in written:
        pow_value = float(row["pred_pow"])
        assert abs(float(row["pred_bill"]) - pow_value * config.BILL_RATE) < 1e-6
        assert abs(float(row["pred_carbon"]) - pow_value * config.CARBON_RATE) < 1e-6
    assert written[0]["id"] == "2025-05-10 00:00:00"
    assert written[-1]["id"] == "2025-05-31 00:00:00"


def test_predict_fails_when_state_missing(trained_artifacts):
    with pytest.raises(FileNotFoundError):
        predict_mod.predict(
            state_path=trained_artifacts["tmp"] / "missing_state.json",
            model_path=trained_artifacts["model"],
            output_path=trained_artifacts["tmp"] / "out.csv",
        )


def test_add_intervals_enforces_order_and_zero_floor():
    rows = [
        {"id": "a", "pred_pow": 0.0, "pred_bill": 0.0, "pred_carbon": 0.0},
        {"id": "b", "pred_pow": 100.0, "pred_bill": 0.0, "pred_carbon": 0.0},
    ]
    residuals = [-50.0, -10.0, 0.0, 10.0, 50.0]
    augmented, meta = interval_mod.add_intervals(rows, residuals)

    for row in augmented:
        assert 0.0 <= row["pred_low"] <= row["pred_pow"] <= row["pred_high"]
    assert meta["provisional"] == bool(config.PRED_INTERVAL_PROVISIONAL)
    assert meta["low_quantile"] == config.PRED_INTERVAL_LOW_Q
    assert meta["high_quantile"] == config.PRED_INTERVAL_HIGH_Q


def test_interval_file_adds_columns(trained_artifacts):
    output = trained_artifacts["tmp"] / "predictions.csv"
    predict_mod.predict(
        state_path=trained_artifacts["state"],
        model_path=trained_artifacts["model"],
        output_path=output,
    )
    # Supply explicit residuals to avoid any model refit coupling.
    meta = interval_mod.add_intervals_to_file(
        predictions_path=output,
        features_path=trained_artifacts["features"],
        interval_meta_path=trained_artifacts["tmp"] / "interval_run.json",
        residuals=[-20.0, -5.0, 0.0, 5.0, 20.0],
    )

    written = _read_csv(output)
    assert list(written[0]) == ["id", "pred_pow", "pred_bill", "pred_carbon", "pred_low", "pred_high"]
    for row in written:
        low, pow_v, high = float(row["pred_low"]), float(row["pred_pow"]), float(row["pred_high"])
        assert 0.0 <= low <= pow_v <= high
    assert meta["residual_count"] == 5


def test_provisional_flag_recorded_in_metadata():
    rows = [{"id": "a", "pred_pow": 10.0, "pred_bill": 0.0, "pred_carbon": 0.0}]
    _, meta = interval_mod.add_intervals(rows, [1.0, 2.0, 3.0])
    if config.PRED_INTERVAL_PROVISIONAL:
        assert meta["provisional"] is True
        assert "잠정" in meta["provisional_note"]
