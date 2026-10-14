"""Hypothesis property tests for prediction structure and conversion."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from hypothesis import given, settings
from hypothesis import strategies as st

from config import config
from src.predict import predict as predict_mod

KST = ZoneInfo("Asia/Seoul")


class _GridModel:
    """A stub model that returns the supplied per-row predictions in order."""

    def __init__(self, values):
        self._values = list(values)

    def predict(self, matrix):
        # One prediction per grid row; length is validated by the caller.
        return self._values[: len(matrix)]


# Feature: factory-energy-copilot, Property 3: 예측 산출물 구조 불변식
@settings(max_examples=100, deadline=None)
@given(
    seed_values=st.lists(
        st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=10,
    )
)
def test_property_prediction_structure(seed_values):
    """id grid is exactly 505 hourly points 2025-05-10 00:00..2025-05-31 00:00."""

    grid = predict_mod.prediction_grid()
    # Build a deterministic value per row from the generated seed values.
    values = [seed_values[i % len(seed_values)] for i in range(len(grid))]
    model = _GridModel(values)
    rows = predict_mod.predict_rows(model, grid, ["weekday", "hour_of_day"])

    ids = [row["id"] for row in rows]
    assert len(ids) == 505
    assert len(set(ids)) == 505  # no duplicates
    assert ids[0] == datetime(2025, 5, 10, 0, tzinfo=KST)
    assert ids[-1] == datetime(2025, 5, 31, 0, tzinfo=KST)
    # 1-hour spacing with no gaps.
    for earlier, later in zip(ids, ids[1:]):
        assert later - earlier == timedelta(hours=1)


# Feature: factory-energy-copilot, Property 4: 요금·탄소 선형 환산 불변식
@settings(max_examples=100, deadline=None)
@given(pow_value=st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False))
def test_property_linear_conversion(pow_value):
    """bill = pow * 180 and carbon = pow * 0.424 from the single config source."""

    bill = predict_mod.convert_bill(pow_value)
    carbon = predict_mod.convert_carbon(pow_value)

    tolerance = 1e-6 * max(1.0, abs(pow_value))
    assert abs(bill - pow_value * config.BILL_RATE) <= tolerance
    assert abs(carbon - pow_value * config.CARBON_RATE) <= tolerance
    assert config.BILL_RATE == 180.0
    assert config.CARBON_RATE == 0.424
