"""Example tests for the three forecasting baselines (A2)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.train import baselines

KST = ZoneInfo("Asia/Seoul")


def _history(hours: int, start: datetime, step_value: float = 1.0) -> list[dict]:
    return [
        {"hour": start + timedelta(hours=i), "target": float(i) * step_value}
        for i in range(hours)
    ]


def test_baseline_names_are_the_three_models():
    assert baselines.BASELINE_NAMES == (
        "global_mean_repeat",
        "weekday_hour_mean",
        "previous_week_same_hour",
    )


def test_global_mean_repeat_is_constant_mean():
    start = datetime(2025, 4, 1, 0, tzinfo=KST)
    history = _history(10, start)
    targets = [start + timedelta(hours=100 + i) for i in range(5)]

    preds = baselines.global_mean_repeat(history, targets)

    expected_mean = sum(range(10)) / 10
    assert len(preds) == 5
    assert all(abs(p - expected_mean) < 1e-9 for p in preds)


def test_weekday_hour_mean_matches_same_weekday_hour():
    start = datetime(2025, 4, 1, 0, tzinfo=KST)
    # Two weeks of hourly data so each (weekday, hour) has two samples.
    history = []
    for day in range(14):
        for hour in range(24):
            moment = start + timedelta(days=day, hours=hour)
            history.append({"hour": moment, "target": float(hour)})

    target = start + timedelta(days=30, hours=7)
    preds = baselines.weekday_hour_mean(history, [target])

    assert abs(preds[0] - 7.0) < 1e-9


def test_previous_week_same_hour_uses_exact_prior_week_value():
    start = datetime(2025, 4, 1, 0, tzinfo=KST)
    history = _history(24 * 10, start)
    # Pick a target whose prior-week timestamp exists in history.
    target = start + timedelta(days=7, hours=5)
    prior_value = next(
        row["target"] for row in history if row["hour"] == target - timedelta(days=7)
    )

    preds = baselines.previous_week_same_hour(history, [target])

    assert abs(preds[0] - prior_value) < 1e-9


def test_run_baselines_returns_all_three_with_matching_length():
    start = datetime(2025, 4, 1, 0, tzinfo=KST)
    history = _history(50, start)
    targets = [start + timedelta(hours=60 + i) for i in range(4)]

    result = baselines.run_baselines(history, targets)

    assert set(result) == set(baselines.BASELINE_NAMES)
    assert all(len(preds) == len(targets) for preds in result.values())


def test_mean_absolute_error_is_non_negative_and_zero_on_match():
    assert baselines.mean_absolute_error([1.0, 2.0], [1.0, 2.0]) == 0.0
    assert baselines.mean_absolute_error([1.0, 3.0], [2.0, 2.0]) == 1.0
