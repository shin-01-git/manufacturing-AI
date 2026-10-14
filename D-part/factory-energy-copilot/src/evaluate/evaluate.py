"""Evaluate fixed predictions against the May answer file (5.5).

The Evaluator is the only module that reads the Validation answer file, and it
does so **after** ``predictions.csv`` is fixed (A4, A6).  It never corrects the
predictions with the answers (A7, R5): there is no path that writes back to
predictions.csv or adjusts ``pred_*`` using the ground truth.

Metrics (R7): ``mae_hourly_pow``, ``mae_may_bill``, ``mae_may_carbon`` are each
reported on both the 504 basis (dropping the last 2025-05-31 00:00 point) and
the 505 basis.  ``agg_pow_total_error`` is the predicted total vs the agg_pow
total.  ``baseline_comparison`` is carried from model selection when available.

``reference_note`` records the 73/89 interpretation values as reference only
(not a pass/fail threshold).  ``MAE_USE_AS_THRESHOLD`` is False and no code here
warns, passes, or fails on MAE.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
import argparse
import csv
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from config import config

EVALUATE_VERSION = "1.0.0"
LAST_POINT_ID = "2025-05-31 00:00:00"


def mean_absolute_error(predicted: Sequence[float], actual: Sequence[float]) -> float:
    """Mean absolute error over equal-length sequences (non-negative)."""

    if len(predicted) != len(actual):
        raise ValueError("predicted and actual must have equal length")
    if not predicted:
        raise ValueError("inputs must be non-empty")
    return sum(abs(p - a) for p, a in zip(predicted, actual)) / len(predicted)


def mae_504_505(predicted: Sequence[float], actual: Sequence[float]) -> dict[str, float]:
    """Return MAE on both the 505 basis and the 504 basis (drop last point).

    Property 5: ``mae505`` is the full absolute-error mean and ``mae504`` drops
    the last point.  Both are non-negative.
    """

    mae505 = mean_absolute_error(predicted, actual)
    mae504 = mean_absolute_error(predicted[:-1], actual[:-1])
    return {"mae504": mae504, "mae505": mae505}


def _read_predictions(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    return rows


def _read_validation(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    return rows


def _normalize_id(value: str) -> str:
    return value.strip()


def _aligned_columns(
    predictions: Sequence[dict[str, Any]],
    validation: Sequence[dict[str, Any]],
) -> dict[str, list[float]]:
    """Align prediction and validation rows by id and extract paired columns."""

    val_by_id = {_normalize_id(row["id"]): row for row in validation}
    pred_pow: list[float] = []
    pred_bill: list[float] = []
    pred_carbon: list[float] = []
    actual_pow: list[float] = []
    actual_bill: list[float] = []
    actual_carbon: list[float] = []
    agg_pow_values: list[float] = []

    for row in predictions:
        key = _normalize_id(row["id"])
        if key not in val_by_id:
            raise ValueError(f"prediction id has no matching validation row: {key}")
        answer = val_by_id[key]
        pred_pow.append(float(row["pred_pow"]))
        pred_bill.append(float(row["pred_bill"]))
        pred_carbon.append(float(row["pred_carbon"]))
        actual_pow.append(float(answer["hourly_pow"]))
        actual_bill.append(float(answer["may_bill"]))
        actual_carbon.append(float(answer["may_carbon"]))
        agg_pow_values.append(float(answer["agg_pow"]))

    return {
        "pred_pow": pred_pow,
        "pred_bill": pred_bill,
        "pred_carbon": pred_carbon,
        "actual_pow": actual_pow,
        "actual_bill": actual_bill,
        "actual_carbon": actual_carbon,
        "agg_pow": agg_pow_values,
    }


def evaluate_pairs(
    predictions: Sequence[dict[str, Any]],
    validation: Sequence[dict[str, Any]],
    *,
    baseline_comparison: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compute the evaluation payload from fixed predictions + answers."""

    columns = _aligned_columns(predictions, validation)

    # agg_pow is the month-level total repeated on every row (a constant column),
    # not a per-hour value. Use the single constant total as the comparison base;
    # summing the column would multiply the total by the row count. We verify the
    # column is (near-)constant and fall back to its max if not.
    agg_pow_values = columns["agg_pow"]
    agg_pow_total = agg_pow_values[0] if agg_pow_values else 0.0
    agg_pow_is_constant = bool(agg_pow_values) and (
        max(agg_pow_values) - min(agg_pow_values) <= abs(agg_pow_total) * 1e-9 + 1e-6
    )
    predicted_total = sum(columns["pred_pow"])

    result: dict[str, Any] = {
        "schema_version": 1,
        "evaluate_version": EVALUATE_VERSION,
        "row_count": len(columns["pred_pow"]),
        "mae_hourly_pow": mae_504_505(columns["pred_pow"], columns["actual_pow"]),
        "mae_may_bill": mae_504_505(columns["pred_bill"], columns["actual_bill"]),
        "mae_may_carbon": mae_504_505(columns["pred_carbon"], columns["actual_carbon"]),
        "predicted_total_pow": predicted_total,
        "agg_pow_total": agg_pow_total,
        "agg_pow_is_constant": agg_pow_is_constant,
        "agg_pow_total_error": predicted_total - agg_pow_total,
        "agg_pow_total_error_ratio": (
            (predicted_total - agg_pow_total) / agg_pow_total if agg_pow_total else None
        ),
        "baseline_comparison": baseline_comparison or [],
        "reference_note": {
            "mae_reference_upper": config.MAE_REFERENCE_UPPER,
            "mae_reference_lower": config.MAE_REFERENCE_LOWER,
            "use_as_threshold": config.MAE_USE_AS_THRESHOLD,
            "note": "참고용(합격 기준 아님)",
        },
    }
    return result


def _json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Not JSON serializable: {type(value)!r}")


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(value, temporary, ensure_ascii=False, indent=2, default=_json_default)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def evaluate(
    *,
    predictions_path: str | Path = config.PREDICTIONS_CSV,
    validation_path: str | Path = config.VALIDATION_CSV,
    selection_path: str | Path = config.ARTIFACTS_DIR / "model_selection.json",
    output_path: str | Path = config.EVALUATION_JSON,
) -> dict[str, Any]:
    """Read fixed predictions + answers, compute metrics, write evaluation.json.

    Predictions are read first and treated as fixed; the answer file is read
    only afterward.
    """

    predictions_file = Path(predictions_path)
    validation_file = Path(validation_path)
    output_file = Path(output_path)

    if not predictions_file.exists():
        raise FileNotFoundError(f"predictions.csv not found (must be fixed first): {predictions_file}")

    predictions = _read_predictions(predictions_file)  # fixed predictions, read first
    validation = _read_validation(validation_file)      # answers read only after

    baseline_comparison: list[dict[str, Any]] | None = None
    selection_file = Path(selection_path)
    if selection_file.exists():
        with selection_file.open("r", encoding="utf-8") as handle:
            selection = json.load(handle)
        baseline_comparison = selection.get("baseline_comparison")

    result = evaluate_pairs(predictions, validation, baseline_comparison=baseline_comparison)
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    result["predictions_path"] = str(predictions_file)
    result["validation_path"] = str(validation_file)
    _atomic_write_json(output_file, result)
    _write_actual_vs_pred(output_file.parent / ACTUAL_VS_PRED_NAME, predictions, validation)
    return result


ACTUAL_VS_PRED_NAME = "actual_vs_pred.csv"


def _write_actual_vs_pred(
    path: Path,
    predictions: Sequence[dict[str, Any]],
    validation: Sequence[dict[str, Any]],
) -> None:
    """Write the dashboard's actual-vs-forecast artifact (evaluation output only).

    Columns: id, actual_pow, pred_pow, pred_low, pred_high. The dashboard reads
    this artifact (never the raw answer CSV, S7) to draw the actual line and the
    out-of-interval markers. It is derived from fixed predictions + the answers;
    nothing here feeds back into training or prediction.
    """

    val_by_id = {_normalize_id(row["id"]): row for row in validation}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "actual_pow", "pred_pow", "pred_low", "pred_high"])
        for row in predictions:
            key = _normalize_id(row["id"])
            answer = val_by_id.get(key)
            if answer is None:
                continue
            writer.writerow(
                [key, answer["hourly_pow"], row["pred_pow"], row.get("pred_low", ""), row.get("pred_high", "")]
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate fixed predictions against the May answers")
    parser.add_argument("--predictions", type=Path, default=config.PREDICTIONS_CSV)
    parser.add_argument("--validation", type=Path, default=config.VALIDATION_CSV)
    parser.add_argument("--selection", type=Path, default=config.ARTIFACTS_DIR / "model_selection.json")
    parser.add_argument("--output", type=Path, default=config.EVALUATION_JSON)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    evaluate(
        predictions_path=args.predictions,
        validation_path=args.validation,
        selection_path=args.selection,
        output_path=args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
