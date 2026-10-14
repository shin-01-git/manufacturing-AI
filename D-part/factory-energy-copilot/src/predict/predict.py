"""Predict the 505 May hourly points and convert to bill/carbon (5.3).

The predictor loads ``preprocessor_state.json`` and ``model.joblib``, builds the
505-point hourly grid (2025-05-10 00:00 .. 2025-05-31 00:00 inclusive, 1-hour
step), aligns calendar features to the stored ``feature_columns`` order, and
writes ``predictions.csv`` with ``id, pred_pow, pred_bill, pred_carbon``.

Conversions use the single config coefficients (NFR-8):
``pred_bill = pred_pow * BILL_RATE`` and ``pred_carbon = pred_pow * CARBON_RATE``.

Loading the preprocessor state is mandatory: a missing/invalid state makes the
prediction fail (no silent reconstruction).  The Validation data path is never
referenced (NFR-4).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
import argparse
import csv
import logging
import os
from pathlib import Path
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

import joblib
import numpy as np

from config import config
from src.preprocess.features import make_calendar_features
from src.preprocess.state import load_preprocessor_state

KST = ZoneInfo("Asia/Seoul")
PREDICT_VERSION = "1.0.0"

PRED_START = datetime(2025, 5, 10, 0, 0, tzinfo=KST)
PRED_END = datetime(2025, 5, 31, 0, 0, tzinfo=KST)  # inclusive
PRED_COUNT = 505
_HOUR = timedelta(hours=1)


def prediction_grid() -> list[datetime]:
    """Return the 505 KST hourly timestamps for the May forecast window."""

    grid: list[datetime] = []
    current = PRED_START
    while current <= PRED_END:
        grid.append(current)
        current += _HOUR
    if len(grid) != PRED_COUNT:
        raise ValueError(f"expected {PRED_COUNT} grid points, built {len(grid)}")
    return grid


def build_feature_matrix(
    grid: Sequence[datetime],
    feature_columns: Sequence[str],
) -> np.ndarray:
    """Build the design matrix from calendar features in the stored order."""

    matrix = np.empty((len(grid), len(feature_columns)), dtype=np.float64)
    for i, moment in enumerate(grid):
        calendar = make_calendar_features(moment)
        for j, name in enumerate(feature_columns):
            if name not in calendar:
                raise KeyError(f"feature '{name}' is not derivable from the calendar grid")
            matrix[i, j] = float(calendar[name])
    return matrix


def convert_bill(pred_pow: float) -> float:
    """Bill conversion using the single config coefficient (NFR-8)."""

    return pred_pow * config.BILL_RATE


def convert_carbon(pred_pow: float) -> float:
    """Carbon conversion using the single config coefficient (NFR-8)."""

    return pred_pow * config.CARBON_RATE


def predict_rows(model: Any, grid: Sequence[datetime], feature_columns: Sequence[str]) -> list[dict[str, Any]]:
    """Produce the prediction rows (id, pred_pow, pred_bill, pred_carbon)."""

    matrix = build_feature_matrix(grid, feature_columns)
    pred_pow = np.asarray(model.predict(matrix), dtype=np.float64)
    rows: list[dict[str, Any]] = []
    for moment, value in zip(grid, pred_pow):
        power = float(value)
        rows.append(
            {
                "id": moment,
                "pred_pow": power,
                "pred_bill": convert_bill(power),
                "pred_carbon": convert_carbon(power),
            }
        )
    return rows


def _format_id(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S")


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
                record = dict(row)
                record["id"] = _format_id(record["id"]) if isinstance(record["id"], datetime) else record["id"]
                writer.writerow(record)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _configure_logger(artifacts_dir: Path) -> logging.Logger:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("factory_energy_copilot.predict")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = logging.FileHandler(artifacts_dir / "predict.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger


def predict(
    *,
    state_path: str | Path = config.ARTIFACTS_DIR / "preprocessor_state.json",
    model_path: str | Path | None = None,
    output_path: str | Path = config.PREDICTIONS_CSV,
) -> list[dict[str, Any]]:
    """Load state + model, predict the 505-point grid, write predictions.csv.

    Raises:
        FileNotFoundError: when the preprocessor state is missing (fail, do not
            reconstruct) or when the model file is missing.
    """

    output_file = Path(output_path)
    logger = _configure_logger(output_file.parent)

    state = load_preprocessor_state(state_path)  # mandatory; raises if absent
    feature_columns = list(state["feature_columns"])
    resolved_model_path = Path(model_path) if model_path is not None else Path(state["model_path"])
    if not resolved_model_path.exists():
        raise FileNotFoundError(f"model artifact not found: {resolved_model_path}")

    model = joblib.load(resolved_model_path)
    grid = prediction_grid()
    rows = predict_rows(model, grid, feature_columns)

    _atomic_write_csv(output_file, rows, ("id", "pred_pow", "pred_bill", "pred_carbon"))
    logger.info(
        '{"event": "predict_finished", "rows": %d, "bill_rate": %s, "carbon_rate": %s}'
        % (len(rows), config.BILL_RATE, config.CARBON_RATE)
    )
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Predict 505 May points and convert bill/carbon")
    parser.add_argument("--state", type=Path, default=config.ARTIFACTS_DIR / "preprocessor_state.json")
    parser.add_argument("--model", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=config.PREDICTIONS_CSV)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    predict(state_path=args.state, model_path=args.model, output_path=args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
