"""Three forecasting baselines (A2) as pure functions over training history.

Baselines operate only on training-side history (``hour`` + ``target``) and
target timestamps.  They never read the Validation data (NFR-4): the module
has no reference to the validation path at all.

Baselines
---------
1. ``global_mean_repeat``       -- repeat the training mean for every target.
2. ``weekday_hour_mean``        -- average of the same weekday + hour-of-day.
3. ``previous_week_same_hour``  -- the value exactly 7 days (168 h) earlier.

Each function returns a list of predictions aligned 1:1 with ``targets``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

# Model names recorded in the comparison table (A2).
BASELINE_NAMES = (
    "global_mean_repeat",
    "weekday_hour_mean",
    "previous_week_same_hour",
)

_WEEK = timedelta(days=7)


def _history_pairs(history: Sequence[Mapping[str, Any]]) -> list[tuple[datetime, float]]:
    pairs = [(row["hour"], float(row["target"])) for row in history]
    return pairs


def global_mean_repeat(
    history: Sequence[Mapping[str, Any]],
    targets: Sequence[datetime],
) -> list[float]:
    """Repeat the training-history mean for every target timestamp."""

    pairs = _history_pairs(history)
    if not pairs:
        raise ValueError("history must be non-empty")
    mean = sum(value for _, value in pairs) / len(pairs)
    return [mean for _ in targets]


def weekday_hour_mean(
    history: Sequence[Mapping[str, Any]],
    targets: Sequence[datetime],
) -> list[float]:
    """Average history values sharing the same weekday and hour-of-day.

    Falls back to the overall mean when a (weekday, hour) bucket is empty.
    """

    pairs = _history_pairs(history)
    if not pairs:
        raise ValueError("history must be non-empty")
    overall_mean = sum(value for _, value in pairs) / len(pairs)

    sums: dict[tuple[int, int], float] = {}
    counts: dict[tuple[int, int], int] = {}
    for hour, value in pairs:
        key = (hour.weekday(), hour.hour)
        sums[key] = sums.get(key, 0.0) + value
        counts[key] = counts.get(key, 0) + 1

    predictions: list[float] = []
    for target in targets:
        key = (target.weekday(), target.hour)
        if counts.get(key):
            predictions.append(sums[key] / counts[key])
        else:
            predictions.append(overall_mean)
    return predictions


def previous_week_same_hour(
    history: Sequence[Mapping[str, Any]],
    targets: Sequence[datetime],
) -> list[float]:
    """Use the history value exactly one week earlier (same hour-of-week).

    When the exact prior-week timestamp is absent, fall back to the same
    weekday + hour-of-day mean, then to the overall mean.  This keeps the
    baseline usable across the 2025-04-30 to 2025-05-10 gap without inventing
    disallowed recent lag features for the learned models.
    """

    pairs = _history_pairs(history)
    if not pairs:
        raise ValueError("history must be non-empty")
    by_hour = {hour: value for hour, value in pairs}
    weekday_hour = weekday_hour_mean(history, targets)

    predictions: list[float] = []
    for index, target in enumerate(targets):
        prior = target - _WEEK
        if prior in by_hour:
            predictions.append(by_hour[prior])
        else:
            predictions.append(weekday_hour[index])
    return predictions


BASELINE_FUNCTIONS = {
    "global_mean_repeat": global_mean_repeat,
    "weekday_hour_mean": weekday_hour_mean,
    "previous_week_same_hour": previous_week_same_hour,
}


def run_baselines(
    history: Sequence[Mapping[str, Any]],
    targets: Sequence[datetime],
) -> dict[str, list[float]]:
    """Run all three baselines and return a name -> predictions mapping."""

    return {name: func(history, targets) for name, func in BASELINE_FUNCTIONS.items()}


def mean_absolute_error(predicted: Sequence[float], actual: Sequence[float]) -> float:
    """Mean absolute error over equal-length sequences (non-negative)."""

    if len(predicted) != len(actual):
        raise ValueError("predicted and actual must have equal length")
    if not predicted:
        raise ValueError("inputs must be non-empty")
    return sum(abs(p - a) for p, a in zip(predicted, actual)) / len(predicted)
