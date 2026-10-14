"""Tests for training, model selection, and preprocessor state (5.2)."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import math
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from config import config
from src.preprocess import features as features_mod
from src.preprocess import state as state_mod
from src.train import train as train_mod

KST = ZoneInfo("Asia/Seoul")


def _write_features(path, n_hours: int = 24 * 60) -> None:
    """Write a small synthetic features.parquet (60 days hourly)."""

    start = datetime(2025, 2, 1, 0, tzinfo=KST)
    records = []
    for i in range(n_hours):
        hour = start + timedelta(hours=i)
        calendar = features_mod.make_calendar_features(hour)
        # Smooth target: daily + weekly structure, deterministic.
        target = 100.0 + 10.0 * math.sin(i / 24.0) + 5.0 * calendar["weekday"]
        records.append({"hour": hour, "target_hourly_pow": target, **calendar})

    table = pa.table(
        {
            "hour": pa.array([r["hour"] for r in records], type=pa.timestamp("us", tz="Asia/Seoul")),
            "target_hourly_pow": pa.array([r["target_hourly_pow"] for r in records], type=pa.float64()),
            "weekday": pa.array([r["weekday"] for r in records], type=pa.int8()),
            "hour_of_day": pa.array([r["hour_of_day"] for r in records], type=pa.int8()),
            "day_of_month": pa.array([r["day_of_month"] for r in records], type=pa.int8()),
            "week_of_year": pa.array([r["week_of_year"] for r in records], type=pa.int16()),
            "is_weekend": pa.array([r["is_weekend"] for r in records], type=pa.bool_()),
            "month": pa.array([r["month"] for r in records], type=pa.int8()),
            "day_of_year": pa.array([r["day_of_year"] for r in records], type=pa.int16()),
        }
    )
    pq.write_table(table, path)


def test_assert_no_forbidden_features_rejects_leaked_feature():
    with pytest.raises(ValueError, match="forbidden"):
        train_mod.assert_no_forbidden_features(["weekday", "voltageR"])

    # Allowed calendar features pass without raising.
    train_mod.assert_no_forbidden_features(list(train_mod.CALENDAR_FEATURES))


def test_time_order_split_holds_out_last_weeks():
    start = datetime(2025, 2, 1, 0, tzinfo=KST)
    rows = [
        {"hour": start + timedelta(hours=i), "target_hourly_pow": float(i)}
        for i in range(24 * 40)
    ]
    train_rows, valid_rows, split_time = train_mod.time_order_split(rows, weeks=3)

    assert all(r["hour"] < split_time for r in train_rows)
    assert all(r["hour"] >= split_time for r in valid_rows)
    assert split_time == rows[-1]["hour"] - timedelta(weeks=3)


def test_train_writes_model_state_and_selection(tmp_path):
    features_path = tmp_path / "features.parquet"
    model_path = tmp_path / "model.joblib"
    state_path = tmp_path / "preprocessor_state.json"
    selection_path = tmp_path / "model_selection.json"
    _write_features(features_path)

    meta = train_mod.train(
        features_path,
        model_path=model_path,
        state_path=state_path,
        selection_path=selection_path,
    )

    assert model_path.exists()
    assert state_path.exists()
    assert selection_path.exists()

    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    assert selection["selected_model"] in {"calendar_linear_regression", "random_forest"}
    assert "split_time" in selection
    assert "selection_score_validation_mae" in selection
    assert selection["refit_scope"] == "full training span"
    # Comparison table has the three baselines plus two candidates.
    models = {entry["model"] for entry in selection["baseline_comparison"]}
    assert {"global_mean_repeat", "weekday_hour_mean", "previous_week_same_hour"} <= models
    assert meta["selected_model"] == selection["selected_model"]


def test_preprocessor_state_schema_round_trips(tmp_path):
    state = state_mod.build_preprocessor_state(
        feature_columns=["weekday", "hour_of_day"],
        categorical_maps={},
        missing_fill={},
        scaling=None,
        target_name="target_hourly_pow",
        model_path=str(tmp_path / "model.joblib"),
        code_config_version={"random_seed": config.RANDOM_SEED},
    )
    path = tmp_path / "preprocessor_state.json"
    state_mod.save_preprocessor_state(path, state)
    loaded = state_mod.load_preprocessor_state(path)

    assert loaded["feature_columns"] == ["weekday", "hour_of_day"]
    assert loaded["target_name"] == "target_hourly_pow"
    assert loaded["scaling"] is None
    assert loaded["code_config_version"]["random_seed"] == config.RANDOM_SEED


def test_load_preprocessor_state_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        state_mod.load_preprocessor_state(tmp_path / "nope.json")


def test_training_is_reproducible_with_fixed_seed(tmp_path):
    features_path = tmp_path / "features.parquet"
    _write_features(features_path)
    rows = pq.read_table(features_path).to_pylist()

    first = train_mod.select_and_train(rows, seed=config.RANDOM_SEED)
    second = train_mod.select_and_train(rows, seed=config.RANDOM_SEED)

    assert first["selected_model"] == second["selected_model"]
    assert first["candidate_scores"] == second["candidate_scores"]
