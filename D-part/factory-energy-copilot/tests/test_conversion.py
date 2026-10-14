"""Conversion coefficient tests: bill = pow * 180, carbon = pow * 0.424."""

from __future__ import annotations

from config import config
from src.predict import predict as predict_mod


def test_config_coefficients_are_single_source():
    assert config.BILL_RATE == 180.0
    assert config.CARBON_RATE == 0.424


def test_bill_and_carbon_use_config_coefficients():
    for pow_value in (0.0, 1.0, 123.456, 39072.258992385100):
        assert predict_mod.convert_bill(pow_value) == pow_value * config.BILL_RATE
        assert predict_mod.convert_carbon(pow_value) == pow_value * config.CARBON_RATE


def test_predict_rows_apply_conversions():
    class _Model:
        def predict(self, matrix):
            return [10.0 for _ in matrix]

    grid = predict_mod.prediction_grid()
    rows = predict_mod.predict_rows(_Model(), grid, ["weekday"])
    for row in rows:
        assert row["pred_bill"] == row["pred_pow"] * config.BILL_RATE
        assert row["pred_carbon"] == row["pred_pow"] * config.CARBON_RATE
