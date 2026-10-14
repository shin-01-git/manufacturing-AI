"""Preprocessor state artifact: save and load the training preprocessing basis.

The preprocessor state pins the training-time preprocessing basis (feature
column order, categorical maps, missing fills, optional scaling, target name,
model path, and run/version metadata) so the Predictor and Evaluator apply the
exact same transformation at prediction time (design: preprocessor_state.json).

Everything here is a pure function over plain Python/JSON values.  No source
CSV is read and the Validation data path is never referenced (NFR-4).  State
is stored as JSON metadata, not a pickle (version compatibility / security).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any

STATE_SCHEMA_VERSION = 1

# Required top-level keys of the preprocessor_state.json schema (design Data Models).
_REQUIRED_KEYS = (
    "feature_columns",
    "categorical_maps",
    "missing_fill",
    "scaling",
    "target_name",
    "model_path",
    "code_config_version",
)


def build_preprocessor_state(
    *,
    feature_columns: Sequence[str],
    categorical_maps: Mapping[str, Any] | None = None,
    missing_fill: Mapping[str, Any] | None = None,
    scaling: Mapping[str, Any] | None = None,
    target_name: str,
    model_path: str | Path,
    code_config_version: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a preprocessor_state dict that matches the design schema.

    Args:
        feature_columns: Ordered feature column names used for training. The
            prediction input is aligned to this exact order.
        categorical_maps: Encoding maps for categorical features (may be empty).
        missing_fill: Per-feature missing-value fill (training basis).
        scaling: Per-feature scaling parameters, or ``None`` when unused
            (tree models and the plain calendar linear model do not need it).
        target_name: Target column name (``target_hourly_pow``).
        model_path: Path to the connected model artifact (``model.joblib``).
        code_config_version: Code/config version, generated_at, input path,
            and random seed (NFR-7). ``generated_at`` is filled if absent.

    Returns:
        A JSON-serializable state dict.
    """

    if not feature_columns:
        raise ValueError("feature_columns must be a non-empty ordered sequence")

    version_meta: dict[str, Any] = dict(code_config_version or {})
    version_meta.setdefault("generated_at", datetime.now(timezone.utc).isoformat())

    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "feature_columns": list(feature_columns),
        "categorical_maps": dict(categorical_maps or {}),
        "missing_fill": dict(missing_fill or {}),
        "scaling": (dict(scaling) if scaling is not None else None),
        "target_name": target_name,
        "model_path": str(model_path),
        "code_config_version": version_meta,
    }


def validate_preprocessor_state(state: Mapping[str, Any]) -> None:
    """Raise ``ValueError`` when the state is missing a required key or malformed."""

    if not isinstance(state, Mapping):
        raise ValueError("preprocessor_state must be a mapping")
    missing = [key for key in _REQUIRED_KEYS if key not in state]
    if missing:
        raise ValueError(f"preprocessor_state missing required keys: {missing}")
    if not isinstance(state["feature_columns"], (list, tuple)) or not state["feature_columns"]:
        raise ValueError("feature_columns must be a non-empty list")
    if not isinstance(state["target_name"], str) or not state["target_name"]:
        raise ValueError("target_name must be a non-empty string")


def _json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Not JSON serializable: {type(value)!r}")


def save_preprocessor_state(path: str | Path, state: Mapping[str, Any]) -> Path:
    """Atomically write the preprocessor state as JSON. Returns the path."""

    validate_preprocessor_state(state)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(dict(state), temporary, ensure_ascii=False, indent=2, default=_json_default)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return target


def load_preprocessor_state(path: str | Path) -> dict[str, Any]:
    """Load and validate a preprocessor state JSON file.

    Raises:
        FileNotFoundError: when the state file does not exist (Predictor must
            fail prediction rather than reconstruct state).
        ValueError: when the file is not a valid state object.
    """

    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"preprocessor_state not found: {source}")
    with source.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    validate_preprocessor_state(value)
    return value
