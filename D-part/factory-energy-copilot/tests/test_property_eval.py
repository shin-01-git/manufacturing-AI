"""Hypothesis property tests for MAE definition and prediction-interval order."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from src.evaluate import evaluate as evaluate_mod
from src.predict import interval as interval_mod

_finite = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)


# Feature: factory-energy-copilot, Property 5: MAE 정의 일치 불변식
@settings(max_examples=100, deadline=None)
@given(
    data=st.lists(st.tuples(_finite, _finite), min_size=2, max_size=505),
)
def test_property_mae_504_505_definition(data):
    """mae505 is the full absolute-error mean; mae504 drops the last point."""

    predicted = [p for p, _ in data]
    actual = [a for _, a in data]
    result = evaluate_mod.mae_504_505(predicted, actual)

    expected_505 = sum(abs(p - a) for p, a in zip(predicted, actual)) / len(predicted)
    expected_504 = sum(
        abs(p - a) for p, a in zip(predicted[:-1], actual[:-1])
    ) / (len(predicted) - 1)

    assert abs(result["mae505"] - expected_505) <= 1e-6 * max(1.0, abs(expected_505))
    assert abs(result["mae504"] - expected_504) <= 1e-6 * max(1.0, abs(expected_504))
    assert result["mae505"] >= 0.0
    assert result["mae504"] >= 0.0


# Feature: factory-energy-copilot, Property 6: 예측구간 순서 불변식
@settings(max_examples=100, deadline=None)
@given(
    pred_pow=st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
    residuals=st.lists(_finite, min_size=1, max_size=50),
)
def test_property_interval_order(pred_pow, residuals):
    """Every row satisfies 0 <= pred_low <= pred_pow <= pred_high."""

    lower_magnitude, upper_magnitude = interval_mod.residual_quantile_magnitudes(residuals)
    pred_low, pred_high = interval_mod.apply_interval(pred_pow, lower_magnitude, upper_magnitude)

    assert 0.0 <= pred_low <= pred_pow <= pred_high
