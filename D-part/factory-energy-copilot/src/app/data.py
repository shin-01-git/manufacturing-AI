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

import functools
import json
from pathlib import Path
from typing import Any

from config import config
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
                        "actual_pow": float(r["actual_pow"]) / config.WH_PER_KWH,
                        "pred_pow": float(r["pred_pow"]) / config.WH_PER_KWH,
                        "pred_low": float(r["pred_low"]) / config.WH_PER_KWH if r.get("pred_low") else None,
                        "pred_high": float(r["pred_high"]) / config.WH_PER_KWH if r.get("pred_high") else None,
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
    mh = bundle["modules_hourly"]
    if "frame" in mh and "hourly_pow" in mh["frame"].columns:  # Wh → kWh (교안 단위)
        mh["frame"] = mh["frame"].assign(hourly_pow=mh["frame"]["hourly_pow"] / config.WH_PER_KWH)
    bundle["quality"] = _load_parquet_frame(QUALITY_NAME, artifacts_dir)
    bundle["selection"] = _load_json(SELECTION_NAME, artifacts_dir)
    bundle["source_meta"] = _load_json("source_meta.json", artifacts_dir)
    bundle["aggregate_run"] = _load_json("aggregate_run.json", artifacts_dir)
    # 탐지 시간대 패널(3-뷰)이 쓰는 anomaly_hourly DataFrame. trend(dict, points 리스트)는
    # 기존 ui_logic.heatmap_matrix/charts.anomaly_timeline/page_logic.timeline_points가
    # 여전히 쓰므로 그대로 둔다 — 이 키는 그 옆에 추가되는 별도 DataFrame 뷰다.
    bundle["anomaly_hourly_frame"] = _load_parquet_frame(
        tools_mod.ANOMALY_HOURLY_NAME, artifacts_dir
    )
    # 6.5만 건 이벤트를 매 rerun마다 훑지 않도록 기간은 로드 시 한 번만 계산한다.
    bundle["events_period"] = ui_logic.events_period(bundle["events"])
    # B-part 선택 모델 예측(있으면 홈 예측 그래프·KPI가 이것을 쓴다). 없으면 predictions.csv.
    bundle["forecast_bpart"] = tools_mod.get_forecast_summary(
        artifacts_dir=artifacts_dir, name=tools_mod.BPART_FORECAST_NAME
    )
    bundle["forecast_bpart_meta"] = _load_json(tools_mod.BPART_META_NAME, artifacts_dir)
    return bundle


_SIGNATURE_FILES = (
    "predictions.csv", "forecast_bpart.csv", "forecast_bpart_meta.json", "evaluation.json", "module_status.csv", "anomaly_events.parquet",
    "anomaly_hourly.parquet", ACTUAL_VS_PRED_NAME, HOURLY_MODULES_NAME, REBUILD_META_NAME,
    SELECTION_NAME,
)


def _artifacts_signature(artifacts_dir: Path | None) -> tuple:
    """(파일명, 수정시각, 크기) 묶음. artifacts가 다시 생성되면 값이 바뀌어 캐시가 갱신된다."""

    sig = []
    for name in _SIGNATURE_FILES:
        try:
            p = tools_mod.resolve_artifact_path(name, artifacts_dir=artifacts_dir)
            st_ = p.stat()
            sig.append((name, st_.st_mtime_ns, st_.st_size))
        except Exception:  # noqa: BLE001 - 없거나 막힌 파일은 '없음'으로 서명
            sig.append((name, None, None))
    return tuple(sig)


@functools.lru_cache(maxsize=1)
def _cached_loader():
    """모듈 수준에서 한 번만 만드는 캐시 함수.

    cache_data → cache_resource 로 변경한 이유: cache_data는 rerun마다 결과를 pickle로
    복사해 돌려준다. 번들에는 이상 이벤트 6.5만 건(dict 목록)과 parquet DataFrame이 들어
    있어 클릭할 때마다 이 복사(수백 ms)가 반복된다. cache_resource는 같은 객체를 그대로
    돌려주므로 복사 비용이 없다. **대신 호출부는 번들을 수정하면 안 된다(읽기 전용).**
    """

    import streamlit as st

    @st.cache_resource(show_spinner="artifacts 불러오는 중…", max_entries=2)
    def _cached(dir_str: str | None, signature: tuple) -> dict[str, Any]:  # noqa: ARG001
        root = Path(dir_str) if dir_str else None
        return load_bundle(root)

    return _cached


def cached_bundle(artifacts_dir: Path | None = None) -> dict[str, Any]:
    """Streamlit-cached bundle loader (A-6). Falls back to a plain call when
    Streamlit is unavailable (e.g. unit tests). Read-only: never mutate the result."""

    try:
        import streamlit  # noqa: F401
    except ModuleNotFoundError:  # pragma: no cover - streamlit always present in app
        return load_bundle(artifacts_dir)

    return _cached_loader()(
        str(artifacts_dir) if artifacts_dir else None, _artifacts_signature(artifacts_dir)
    )