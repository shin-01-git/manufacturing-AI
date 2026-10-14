"""Dashboard artifacts loader (steering A-6): artifacts-only, cached once.

Wraps the existing pure logic (``ui_logic.load_all``) and the artifacts-only
tools (``agent/tools.py``) so the Streamlit app reads every artifact exactly
once per run via ``@st.cache_data``. Two extra read-only artifacts that the five
tools don't cover — ``anomaly_rebuild_meta.json`` (method_counts / overlap for
the method-comparison tables) and ``evaluation.json``'s ``selected`` baseline —
are read here through ``tools_mod.resolve_artifact_path`` so the artifacts-only
path contract (S7, C1) still holds. No raw-CSV path is ever imported.

Missing artifacts never raise: each loader returns an ``error`` dict (tools) or
``None`` so the tabs render empty-state cards instead of crashing (A-6).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.agent import tools as tools_mod
from src.app import ui_logic

REBUILD_META_NAME = "anomaly_rebuild_meta.json"
ACTUAL_VS_PRED_NAME = "actual_vs_pred.csv"
HOURLY_MODULES_NAME = "hourly_from_full.parquet"
QUALITY_NAME = "module_quality_hourly.parquet"
SELECTION_NAME = "model_selection.json"


def _load_rebuild_meta(artifacts_dir: Path | None) -> dict[str, Any] | None:
    """Read method_counts / method_overlap from anomaly_rebuild_meta.json.

    Returns ``None`` when the file is missing or malformed (caller shows an empty
    state). Path stays inside the artifacts root via ``resolve_artifact_path``.
    """

    try:
        path = tools_mod.resolve_artifact_path(REBUILD_META_NAME, artifacts_dir=artifacts_dir)
    except tools_mod.PathSecurityError:
        return None
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _missing(name: str) -> dict[str, Any]:
    return {"error": {"code": "missing_artifact", "message": name}}


def _load_actual_vs_pred(artifacts_dir: Path | None) -> dict[str, Any]:
    """Read ``actual_vs_pred.csv`` (evaluation output) -> ``{"rows": [...]}``.

    Written by the evaluate step; the dashboard never reads the raw answer CSV (S7).
    """

    import csv

    try:
        path = tools_mod.resolve_artifact_path(ACTUAL_VS_PRED_NAME, artifacts_dir=artifacts_dir)
    except tools_mod.PathSecurityError:
        return _missing(ACTUAL_VS_PRED_NAME)
    if not path.exists():
        return _missing(ACTUAL_VS_PRED_NAME)
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = []
            for r in csv.DictReader(handle):
                rows.append(
                    {
                        "id": r["id"],
                        "actual_pow": float(r["actual_pow"]),
                        "pred_pow": float(r["pred_pow"]),
                        "pred_low": float(r["pred_low"]) if r.get("pred_low") else None,
                        "pred_high": float(r["pred_high"]) if r.get("pred_high") else None,
                    }
                )
    except (OSError, KeyError, ValueError):
        return _missing(ACTUAL_VS_PRED_NAME)
    return {"rows": rows}


def _load_parquet_frame(name: str, artifacts_dir: Path | None) -> dict[str, Any]:
    """Read an artifact parquet into ``{"frame": DataFrame}`` (hour -> naive KST)."""

    try:
        path = tools_mod.resolve_artifact_path(name, artifacts_dir=artifacts_dir)
    except tools_mod.PathSecurityError:
        return _missing(name)
    if not path.exists():
        return _missing(name)
    try:
        import pandas as pd

        frame = pd.read_parquet(path)
        if "hour" in frame.columns:
            hour = pd.to_datetime(frame["hour"])
            if getattr(hour.dt, "tz", None) is not None:
                hour = hour.dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
            frame = frame.assign(hour=hour)
    except Exception:  # noqa: BLE001 - unreadable file -> error state, details to log only
        return _missing(name)
    return {"frame": frame}


def _load_json(name: str, artifacts_dir: Path | None) -> dict[str, Any]:
    try:
        path = tools_mod.resolve_artifact_path(name, artifacts_dir=artifacts_dir)
    except tools_mod.PathSecurityError:
        return _missing(name)
    if not path.exists():
        return _missing(name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _missing(name)
    return data if isinstance(data, dict) else _missing(name)


def load_bundle(artifacts_dir: Path | None = None) -> dict[str, Any]:
    """Load every artifact the dashboard uses (artifacts-only, no raise).

    Pure wrapper so it is testable without Streamlit; the app wraps the call in
    ``@st.cache_data``.
    """

    bundle = ui_logic.load_all(artifacts_dir=artifacts_dir)
    bundle["rebuild_meta"] = _load_rebuild_meta(artifacts_dir)
    bundle["actual_vs_pred"] = _load_actual_vs_pred(artifacts_dir)
    bundle["modules_hourly"] = _load_parquet_frame(HOURLY_MODULES_NAME, artifacts_dir)
    bundle["quality"] = _load_parquet_frame(QUALITY_NAME, artifacts_dir)
    bundle["selection"] = _load_json(SELECTION_NAME, artifacts_dir)
    bundle["source_meta"] = _load_json("source_meta.json", artifacts_dir)
    bundle["aggregate_run"] = _load_json("aggregate_run.json", artifacts_dir)
    return bundle


def cached_bundle(artifacts_dir: Path | None = None) -> dict[str, Any]:
    """Streamlit-cached bundle loader (@st.cache_data, A-6). Falls back to a plain
    call when Streamlit's cache is unavailable (e.g. unit tests)."""

    try:
        import streamlit as st
    except ModuleNotFoundError:  # pragma: no cover - streamlit always present in app
        return load_bundle(artifacts_dir)

    @st.cache_data(show_spinner=False)
    def _cached(dir_str: str | None) -> dict[str, Any]:
        root = Path(dir_str) if dir_str else None
        return load_bundle(root)

    return _cached(str(artifacts_dir) if artifacts_dir else None)
