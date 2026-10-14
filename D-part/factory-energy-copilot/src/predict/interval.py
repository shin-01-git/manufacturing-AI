"""Prediction intervals from time-ordered validation residuals (5.4).

The lower/upper residual quantiles come from the model's time-ordered
*training* validation window (the last 3 weeks of the training span), never
from the May answer file (NFR-4).  The quantile levels are the provisional
config constants (O-7): ``PRED_INTERVAL_LOW_Q`` / ``PRED_INTERVAL_HIGH_Q``.

Order invariant (Property 6): every row satisfies
``0 <= pred_low <= pred_pow <= pred_high`` regardless of the quantile values,
because

    pred_low  = max(0, pred_pow - lower_residual_quantile_magnitude)
    pred_high = max(pred_low, pred_pow + upper_residual_quantile_magnitude)

When ``PRED_INTERVAL_PROVISIONAL`` is true the provisional status is written to
the log and interval metadata.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
import argparse
import csv
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any

import numpy as np

from config import config

INTERVAL_VERSION = "1.0.0"


def residual_quantile_magnitudes(
    residuals: Sequence[float],
    *,
    low_q: float = config.PRED_INTERVAL_LOW_Q,
    high_q: float = config.PRED_INTERVAL_HIGH_Q,
) -> tuple[float, float]:
    """Return non-negative lower/upper residual magnitudes for the interval.

    ``residuals`` are ``actual - predicted`` on the validation window.  The
    lower magnitude is the absolute value of the ``low_q`` residual quantile
    and the upper magnitude is the absolute value of the ``high_q`` quantile.
    Both are clamped to be non-negative so the order invariant holds for any
    quantile configuration.
    """

    if len(residuals) == 0:
        return 0.0, 0.0
    array = np.asarray(residuals, dtype=np.float64)
    lower = float(np.quantile(array, low_q))
    upper = float(np.quantile(array, high_q))
    return abs(lower), abs(upper)


def apply_interval(
    pred_pow: float,
    lower_magnitude: float,
    upper_magnitude: float,
) -> tuple[float, float]:
    """Compute (pred_low, pred_high) enforcing ``0 <= low <= pow <= high``."""

    low_mag = abs(lower_magnitude)
    high_mag = abs(upper_magnitude)
    pred_low = max(0.0, pred_pow - low_mag)
    pred_high = max(pred_low, pred_pow + high_mag)
    return pred_low, pred_high


def add_intervals(
    rows: Sequence[dict[str, Any]],
    residuals: Sequence[float],
    *,
    low_q: float = config.PRED_INTERVAL_LOW_Q,
    high_q: float = config.PRED_INTERVAL_HIGH_Q,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Add pred_low/pred_high columns to prediction rows.

    Returns the augmented rows and interval metadata (quantiles, magnitudes,
    provisional flag).
    """

    lower_magnitude, upper_magnitude = residual_quantile_magnitudes(
        residuals, low_q=low_q, high_q=high_q
    )
    augmented: list[dict[str, Any]] = []
    for row in rows:
        pred_pow = float(row["pred_pow"])
        pred_low, pred_high = apply_interval(pred_pow, lower_magnitude, upper_magnitude)
        new_row = dict(row)
        new_row["pred_low"] = pred_low
        new_row["pred_high"] = pred_high
        augmented.append(new_row)

    metadata = {
        "interval_version": INTERVAL_VERSION,
        "low_quantile": low_q,
        "high_quantile": high_q,
        "lower_residual_magnitude": lower_magnitude,
        "upper_residual_magnitude": upper_magnitude,
        "residual_count": len(residuals),
        "provisional": bool(config.PRED_INTERVAL_PROVISIONAL),
        "provisional_note": (
            "임시/잠정 분위수 (O-7)" if config.PRED_INTERVAL_PROVISIONAL else None
        ),
        "formula": "pred_low=max(0, pred_pow-lower); pred_high=max(pred_low, pred_pow+upper)",
    }
    return augmented, metadata


def _read_predictions(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = [dict(row) for row in reader]
    for row in rows:
        row["pred_pow"] = float(row["pred_pow"])
        row["pred_bill"] = float(row["pred_bill"])
        row["pred_carbon"] = float(row["pred_carbon"])
    return rows


def _atomic_write_csv(path: Path, rows: Sequence[dict[str, Any]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            writer = csv.DictWriter(temporary, fieldnames=list(fieldnames))
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


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


def _configure_logger(artifacts_dir: Path) -> logging.Logger:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("factory_energy_copilot.interval")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = logging.FileHandler(artifacts_dir / "interval.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger


def _validation_residuals(
    features_path: Path,
    *,
    seed: int,
) -> list[float]:
    """Compute actual-minus-predicted residuals on the time-ordered split."""

    import pyarrow.parquet as pq

    from src.train.train import (
        CALENDAR_FEATURES,
        TARGET_NAME,
        build_matrix,
        time_order_split,
        _candidate_models,
    )

    rows = pq.read_table(features_path).to_pylist()
    train_rows, validation_rows, _ = time_order_split(rows)
    feature_columns = list(CALENDAR_FEATURES)
    x_train = build_matrix(train_rows, feature_columns)
    y_train = np.array([float(r[TARGET_NAME]) for r in train_rows], dtype=np.float64)
    x_valid = build_matrix(validation_rows, feature_columns)
    y_valid = np.array([float(r[TARGET_NAME]) for r in validation_rows], dtype=np.float64)

    # Use the tree candidate's residuals as a representative interval basis.
    model = _candidate_models(seed)["random_forest"]
    model.fit(x_train, y_train)
    preds = np.asarray(model.predict(x_valid), dtype=np.float64)
    return list(y_valid - preds)


def add_intervals_to_file(
    *,
    predictions_path: str | Path = config.PREDICTIONS_CSV,
    features_path: str | Path = config.ARTIFACTS_DIR / "features.parquet",
    interval_meta_path: str | Path | None = None,
    residuals: Sequence[float] | None = None,
    seed: int = config.RANDOM_SEED,
) -> dict[str, Any]:
    """Read predictions.csv, add intervals, and write back with metadata."""

    predictions_file = Path(predictions_path)
    features_file = Path(features_path)
    meta_file = (
        Path(interval_meta_path)
        if interval_meta_path is not None
        else predictions_file.parent / "interval_run.json"
    )
    logger = _configure_logger(predictions_file.parent)

    rows = _read_predictions(predictions_file)
    if residuals is None:
        residuals = _validation_residuals(features_file, seed=seed)
    augmented, metadata = add_intervals(rows, residuals)
    metadata["generated_at"] = datetime.now(timezone.utc).isoformat()
    metadata["predictions_path"] = str(predictions_file)

    _atomic_write_csv(
        predictions_file,
        augmented,
        ("id", "pred_pow", "pred_bill", "pred_carbon", "pred_low", "pred_high"),
    )
    _atomic_write_json(meta_file, metadata)

    status = "임시/잠정" if metadata["provisional"] else "확정"
    logger.info(
        json.dumps(
            {
                "event": "interval_finished",
                "rows": len(augmented),
                "low_quantile": metadata["low_quantile"],
                "high_quantile": metadata["high_quantile"],
                "status": status,
            },
            ensure_ascii=False,
        )
    )
    return metadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Add prediction intervals to predictions.csv")
    parser.add_argument("--predictions", type=Path, default=config.PREDICTIONS_CSV)
    parser.add_argument("--features", type=Path, default=config.ARTIFACTS_DIR / "features.parquet")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    add_intervals_to_file(predictions_path=args.predictions, features_path=args.features)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
