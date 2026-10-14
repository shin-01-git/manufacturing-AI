"""Train and select the May forecasting model (5.2).

Pipeline
--------
1. Load the training feature table (``features.parquet`` schema: ``hour``,
   ``target_hourly_pow`` plus calendar columns).
2. Candidate models: a calendar-feature linear regression, then one tree model
   (RandomForest or GradientBoosting).
3. Time-ordered validation: the last 3 weeks of the training span are held out
   and model selection uses only time-ordered validation MAE.  The May answer
   file is never referenced (NFR-4, S2); only the Evaluator reads it.
4. Refit the selected model on the full training span.
5. Save ``model.joblib``, ``preprocessor_state.json`` and
   ``model_selection.json`` (split time, selection scores, refit time,
   comparison table).

Forbidden-feature guard (A3): May-unavailable voltage/current/powerFactor and
2025-04-30..2025-05-10 lag columns must not enter the feature set.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
import argparse
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any

import joblib
import numpy as np
import pyarrow.parquet as pq
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression

from config import config
from src.preprocess.features import get_forbidden_features
from src.preprocess.state import build_preprocessor_state, save_preprocessor_state
from src.train.baselines import mean_absolute_error, run_baselines

TRAIN_VERSION = "1.0.0"
TARGET_NAME = "target_hourly_pow"
VALIDATION_WEEKS = 3

# Candidate feature columns derived from the KST calendar.  All are available
# at prediction time; none is a May sensor value or a recent lag.
CALENDAR_FEATURES = (
    "weekday",
    "hour_of_day",
    "day_of_month",
    "week_of_year",
    "is_weekend",
    "month",
    "day_of_year",
)


def assert_no_forbidden_features(feature_columns: Sequence[str]) -> None:
    """Raise ``ValueError`` if any forbidden feature leaked into the set (A3)."""

    forbidden = get_forbidden_features(feature_columns)
    if forbidden:
        raise ValueError(f"forbidden features present in feature set: {forbidden}")


def build_matrix(
    rows: Sequence[dict[str, Any]],
    feature_columns: Sequence[str],
) -> np.ndarray:
    """Build a numeric design matrix in the exact feature-column order."""

    matrix = np.empty((len(rows), len(feature_columns)), dtype=np.float64)
    for i, row in enumerate(rows):
        for j, name in enumerate(feature_columns):
            matrix[i, j] = float(row[name])
    return matrix


def time_order_split(
    rows: Sequence[dict[str, Any]],
    *,
    weeks: int = VALIDATION_WEEKS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], datetime]:
    """Split chronologically: last ``weeks`` weeks become validation.

    Returns (train_rows, validation_rows, split_time).  The split time is the
    first validation hour.  Rows are sorted by ``hour`` first.
    """

    ordered = sorted(rows, key=lambda row: row["hour"])
    if len(ordered) < 2:
        raise ValueError("need at least two rows to form a time-ordered split")
    last_hour = ordered[-1]["hour"]
    split_time = last_hour - timedelta(weeks=weeks)
    train_rows = [row for row in ordered if row["hour"] < split_time]
    validation_rows = [row for row in ordered if row["hour"] >= split_time]
    # Guarantee a non-empty split even for short synthetic spans.
    if not train_rows or not validation_rows:
        cut = max(1, int(len(ordered) * 0.75))
        train_rows = ordered[:cut]
        validation_rows = ordered[cut:]
        split_time = validation_rows[0]["hour"] if validation_rows else last_hour
    return train_rows, validation_rows, split_time


def _candidate_models(seed: int) -> dict[str, Any]:
    return {
        "calendar_linear_regression": LinearRegression(),
        "random_forest": RandomForestRegressor(
            n_estimators=200,
            random_state=seed,
            n_jobs=1,
        ),
    }


def _fit_predict(model: Any, x_train, y_train, x_eval):
    model.fit(x_train, y_train)
    return model.predict(x_eval)


def select_and_train(
    rows: Sequence[dict[str, Any]],
    *,
    feature_columns: Sequence[str] = CALENDAR_FEATURES,
    seed: int = config.RANDOM_SEED,
    weeks: int = VALIDATION_WEEKS,
) -> dict[str, Any]:
    """Select a model by time-ordered validation MAE, then refit on all rows.

    Returns a result dict with the chosen model object, the comparison table,
    split metadata, and the baseline comparison (for the model-selection log).
    """

    feature_columns = list(feature_columns)
    assert_no_forbidden_features(feature_columns)

    ordered = sorted(rows, key=lambda row: row["hour"])
    train_rows, validation_rows, split_time = time_order_split(ordered, weeks=weeks)

    def targets(subset):
        return np.array([float(row[TARGET_NAME]) for row in subset], dtype=np.float64)

    x_train = build_matrix(train_rows, feature_columns)
    y_train = targets(train_rows)
    x_valid = build_matrix(validation_rows, feature_columns)
    y_valid = targets(validation_rows)

    np.random.seed(seed)

    comparison: list[dict[str, Any]] = []

    # Baselines (A2) on the same time-ordered validation window.
    history = [{"hour": row["hour"], "target": float(row[TARGET_NAME])} for row in train_rows]
    valid_hours = [row["hour"] for row in validation_rows]
    baseline_predictions = run_baselines(history, valid_hours)
    for name, preds in baseline_predictions.items():
        comparison.append(
            {
                "model": name,
                "kind": "baseline",
                "validation_mae": mean_absolute_error(preds, list(y_valid)),
            }
        )

    # Candidate learned models.
    candidate_scores: dict[str, float] = {}
    for name, model in _candidate_models(seed).items():
        preds = _fit_predict(model, x_train, y_train, x_valid)
        mae = mean_absolute_error(list(preds), list(y_valid))
        candidate_scores[name] = mae
        comparison.append({"model": name, "kind": "candidate", "validation_mae": mae})

    selected_name = min(candidate_scores, key=candidate_scores.__getitem__)

    # Refit the selected model on the full training span.
    x_full = build_matrix(ordered, feature_columns)
    y_full = targets(ordered)
    final_model = _candidate_models(seed)[selected_name]
    final_model.fit(x_full, y_full)

    return {
        "model": final_model,
        "selected_model": selected_name,
        "feature_columns": feature_columns,
        "split_time": split_time,
        "selection_score": candidate_scores[selected_name],
        "candidate_scores": candidate_scores,
        "baseline_comparison": comparison,
        "train_row_count": len(train_rows),
        "validation_row_count": len(validation_rows),
        "total_row_count": len(ordered),
        "seed": seed,
    }


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
    logger = logging.getLogger("factory_energy_copilot.train")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = logging.FileHandler(artifacts_dir / "train.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger


def train(
    features_path: str | Path = config.ARTIFACTS_DIR / "features.parquet",
    *,
    model_path: str | Path = config.ARTIFACTS_DIR / "model.joblib",
    state_path: str | Path = config.ARTIFACTS_DIR / "preprocessor_state.json",
    selection_path: str | Path = config.ARTIFACTS_DIR / "model_selection.json",
    feature_columns: Sequence[str] = CALENDAR_FEATURES,
    seed: int = config.RANDOM_SEED,
) -> dict[str, Any]:
    """Train, select, refit, and persist model + state + selection artifacts."""

    features_file = Path(features_path)
    model_file = Path(model_path)
    state_file = Path(state_path)
    selection_file = Path(selection_path)
    logger = _configure_logger(model_file.parent)

    rows = pq.read_table(features_file).to_pylist()
    result = select_and_train(rows, feature_columns=feature_columns, seed=seed)

    model_file.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(result["model"], model_file)

    code_config_version = {
        "train_version": TRAIN_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_path": str(features_file),
        "random_seed": seed,
        "time_basis": "localtime (KST)",
    }
    state = build_preprocessor_state(
        feature_columns=result["feature_columns"],
        categorical_maps={},
        missing_fill={},
        scaling=None,
        target_name=TARGET_NAME,
        model_path=str(model_file),
        code_config_version=code_config_version,
    )
    save_preprocessor_state(state_file, state)

    selection_meta = {
        "schema_version": 1,
        "train_version": TRAIN_VERSION,
        "generated_at": code_config_version["generated_at"],
        "input_path": str(features_file),
        "random_seed": seed,
        "split_time": result["split_time"].isoformat(),
        "validation_weeks": VALIDATION_WEEKS,
        "selected_model": result["selected_model"],
        "selection_score_validation_mae": result["selection_score"],
        "candidate_scores": result["candidate_scores"],
        "refit_scope": "full training span",
        "refit_row_count": result["total_row_count"],
        "train_row_count": result["train_row_count"],
        "validation_row_count": result["validation_row_count"],
        "baseline_comparison": result["baseline_comparison"],
        "model_path": str(model_file),
        "state_path": str(state_file),
    }
    _atomic_write_json(selection_file, selection_meta)

    logger.info(
        json.dumps(
            {
                "event": "train_finished",
                "selected_model": result["selected_model"],
                "split_time": result["split_time"].isoformat(),
                "selection_score_validation_mae": result["selection_score"],
                "refit_row_count": result["total_row_count"],
                "random_seed": seed,
            },
            ensure_ascii=False,
        )
    )
    return selection_meta


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train and select the May forecasting model")
    parser.add_argument("--features", type=Path, default=config.ARTIFACTS_DIR / "features.parquet")
    parser.add_argument("--model", type=Path, default=config.ARTIFACTS_DIR / "model.joblib")
    parser.add_argument("--state", type=Path, default=config.ARTIFACTS_DIR / "preprocessor_state.json")
    parser.add_argument("--selection", type=Path, default=config.ARTIFACTS_DIR / "model_selection.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    train(args.features, model_path=args.model, state_path=args.state, selection_path=args.selection)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
