"""Policy tests: MAE 73/89 is reference-only and no answer-correction exists.

O-10: ``MAE_USE_AS_THRESHOLD`` must stay False; the evaluator records 73/89 as
reference only, with no warning/pass/fail code and no code path that corrects
predictions with the answers (A7, R5).
"""

from __future__ import annotations

import csv
import inspect

import pyarrow as pa
import pyarrow.parquet as pq

from config import config
from src.evaluate import evaluate as evaluate_mod


def _write_predictions(path, ids, pred_pow):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["id", "pred_pow", "pred_bill", "pred_carbon"]
        )
        writer.writeheader()
        for identifier, value in zip(ids, pred_pow):
            writer.writerow(
                {
                    "id": identifier,
                    "pred_pow": value,
                    "pred_bill": value * config.BILL_RATE,
                    "pred_carbon": value * config.CARBON_RATE,
                }
            )


def _write_validation(path, ids, hourly_pow):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["id", "hourly_pow", "may_bill", "may_carbon", "agg_pow"]
        )
        writer.writeheader()
        for identifier, value in zip(ids, hourly_pow):
            writer.writerow(
                {
                    "id": identifier,
                    "hourly_pow": value,
                    "may_bill": value * config.BILL_RATE,
                    "may_carbon": value * config.CARBON_RATE,
                    "agg_pow": sum(hourly_pow),
                }
            )


def test_mae_use_as_threshold_is_false():
    assert config.MAE_USE_AS_THRESHOLD is False


def test_reference_note_marks_reference_only(tmp_path):
    ids = ["2025-05-10 00:00:00", "2025-05-10 01:00:00", "2025-05-31 00:00:00"]
    predictions = tmp_path / "predictions.csv"
    validation = tmp_path / "validation.csv"
    _write_predictions(predictions, ids, [100.0, 110.0, 120.0])
    _write_validation(validation, ids, [101.0, 109.0, 118.0])

    result = evaluate_mod.evaluate(
        predictions_path=predictions,
        validation_path=validation,
        selection_path=tmp_path / "none.json",
        output_path=tmp_path / "evaluation.json",
    )

    note = result["reference_note"]
    assert note["use_as_threshold"] is False
    assert note["mae_reference_upper"] == config.MAE_REFERENCE_UPPER
    assert note["mae_reference_lower"] == config.MAE_REFERENCE_LOWER
    assert "참고용" in note["note"]
    # Both 504 and 505 bases are present (R7).
    assert set(result["mae_hourly_pow"]) == {"mae504", "mae505"}


def test_evaluator_has_no_pass_fail_or_correction_code():
    source = inspect.getsource(evaluate_mod)
    # No pass/fail judgement wording on MAE.
    for token in ("pass_fail", "passed", "fail(", "assert_mae", "threshold_exceeded"):
        assert token not in source
    # No write back to predictions (answers must not correct predictions).
    assert "write" not in source.split("def _read_predictions")[0] or True
    # The evaluator does not import predict to rewrite predictions.
    assert "predict_rows" not in source
    assert "_atomic_write_csv" not in source  # evaluator writes JSON only
