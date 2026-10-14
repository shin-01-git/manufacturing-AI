"""artifacts-only tool functions for the Agent and Dashboard (8.1 / C1, C2).

The agent and dashboard never touch the 4.9GB raw CSVs. Every number comes from
the small files under ``artifacts/`` (C2, R10). Each tool below:

- resolves its artifact path **inside the artifacts root only** and refuses any
  path that escapes that root (original CSVs included). This is the Property 7
  "artifacts-only path security" invariant (C1, NFR-6).
- returns a plain ``dict`` with a documented output schema, and on error returns
  ``{"error": <code>, "message": <str>}`` instead of raising, so callers (agent
  fallback, UI) can degrade gracefully.

Allowed path contract
---------------------
``resolve_artifact_path(name)`` joins ``name`` under ``config.ARTIFACTS_DIR`` and
verifies the resolved path stays within that root. Absolute paths, ``..``
escapes, and anything pointing at ``data/`` or the raw CSVs are rejected with
``PathSecurityError``. The tools call this with a fixed artifact name, so an
attacker-supplied ``module``/``type`` filter can never redirect a read.

Error response schema (all tools)
--------------------------------
``{"error": "<code>", "message": "<human text>"}`` where ``<code>`` is one of
``path_denied`` (Property 7 violation), ``not_found`` (missing artifact),
``bad_schema`` (artifact missing expected columns), or ``bad_input`` (invalid
filter/argument). Successful calls never contain an ``error`` key.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
import csv
import json

from config import config

# Fixed artifact file names (never user-controlled).
PREDICTIONS_NAME = "predictions.csv"
EVALUATION_NAME = "evaluation.json"
MODULE_STATUS_NAME = "module_status.csv"
ANOMALY_EVENTS_NAME = "anomaly_events.parquet"
ANOMALY_HOURLY_NAME = "anomaly_hourly.parquet"

# "데이터에 없음" sentinel reused by the agent (C2).
NO_DATA = "데이터에 없음"


class PathSecurityError(ValueError):
    """Raised when a requested path escapes the artifacts root (Property 7)."""


def resolve_artifact_path(name: str, *, artifacts_dir: Path | None = None) -> Path:
    """Resolve ``name`` strictly inside the artifacts root.

    Args:
        name: Relative artifact file name (e.g. ``"predictions.csv"``).
        artifacts_dir: Override root (tests pass a tmp dir). Defaults to
            ``config.ARTIFACTS_DIR``.

    Returns:
        The resolved absolute path inside the artifacts root.

    Raises:
        PathSecurityError: when the resolved path is outside the artifacts root
            (absolute paths, ``..`` escapes, or anything under ``data/`` / the
            raw CSVs). This enforces the artifacts-only contract (C1, Property 7).
    """

    root = Path(artifacts_dir) if artifacts_dir is not None else config.ARTIFACTS_DIR
    root_resolved = root.resolve()

    candidate = Path(name)
    # Reject obviously-absolute inputs before joining (defence in depth).
    if candidate.is_absolute() or (candidate.drive != "") or name.startswith(("/", "\\")):
        raise PathSecurityError(f"absolute paths are not allowed: {name!r}")

    resolved = (root_resolved / candidate).resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:  # pragma: no cover - exercised via property test
        raise PathSecurityError(
            f"path escapes the artifacts root: {name!r}"
        ) from exc
    return resolved


def _artifacts_root(artifacts_dir: Path | None) -> Path:
    return Path(artifacts_dir) if artifacts_dir is not None else config.ARTIFACTS_DIR


def _error(code: str, message: str) -> dict[str, Any]:
    return {"error": code, "message": message}


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_parquet_rows(path: Path) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    return table.to_pylist()


def _parse_dt(value: str | datetime | None) -> datetime | None:
    """Parse a loose datetime filter (``YYYY-MM-DD`` or ``YYYY-MM-DD HH:MM:SS``)."""

    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"unrecognised datetime: {value!r}")


def _as_naive(moment: datetime) -> datetime:
    """Drop tz for inclusive-range comparison against naive filters."""

    return moment.replace(tzinfo=None) if moment.tzinfo is not None else moment


# ---------------------------------------------------------------------------
# Tool 1: get_forecast_summary(start, end)  — predictions.csv
# ---------------------------------------------------------------------------
def get_forecast_summary(
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    *,
    artifacts_dir: Path | None = None,
) -> dict[str, Any]:
    """Summarise the May forecast window from ``predictions.csv``.

    Input schema:
        start, end: optional inclusive ``YYYY-MM-DD[ HH:MM:SS]`` bounds on ``id``.
    Output schema (success):
        ``{"count": int, "start": str|None, "end": str|None,
           "total_pred_pow": float, "total_pred_bill": float,
           "total_pred_carbon": float, "avg_pred_pow": float,
           "interval_provisional": bool, "rows": [{id,pred_pow,pred_bill,
           pred_carbon,pred_low,pred_high}, ...]}``.
    Reads: artifacts/predictions.csv only.
    """

    try:
        start_dt = _parse_dt(start)
        end_dt = _parse_dt(end)
    except ValueError as exc:
        return _error("bad_input", str(exc))

    try:
        path = resolve_artifact_path(PREDICTIONS_NAME, artifacts_dir=artifacts_dir)
    except PathSecurityError as exc:
        return _error("path_denied", str(exc))
    if not path.exists():
        return _error("not_found", f"{PREDICTIONS_NAME} 없음 ({NO_DATA})")

    raw_rows = _read_csv_rows(path)
    required = {"id", "pred_pow", "pred_bill", "pred_carbon"}
    if not raw_rows or not required.issubset(raw_rows[0].keys()):
        return _error("bad_schema", f"{PREDICTIONS_NAME} 스키마 불일치")

    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        try:
            moment = _parse_dt(raw["id"])
        except ValueError:
            continue
        if moment is None:
            continue
        naive = _as_naive(moment)
        if start_dt is not None and naive < _as_naive(start_dt):
            continue
        if end_dt is not None and naive > _as_naive(end_dt):
            continue
        row = {
            "id": raw["id"],
            "pred_pow": float(raw["pred_pow"]),
            "pred_bill": float(raw["pred_bill"]),
            "pred_carbon": float(raw["pred_carbon"]),
        }
        if "pred_low" in raw and raw["pred_low"] != "":
            row["pred_low"] = float(raw["pred_low"])
        if "pred_high" in raw and raw["pred_high"] != "":
            row["pred_high"] = float(raw["pred_high"])
        rows.append(row)

    count = len(rows)
    total_pow = sum(r["pred_pow"] for r in rows)
    total_bill = sum(r["pred_bill"] for r in rows)
    total_carbon = sum(r["pred_carbon"] for r in rows)
    return {
        "count": count,
        "start": rows[0]["id"] if rows else None,
        "end": rows[-1]["id"] if rows else None,
        "total_pred_pow": total_pow,
        "total_pred_bill": total_bill,
        "total_pred_carbon": total_carbon,
        "avg_pred_pow": (total_pow / count) if count else 0.0,
        "interval_provisional": bool(config.PRED_INTERVAL_PROVISIONAL),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Tool 2: get_evaluation()  — evaluation.json
# ---------------------------------------------------------------------------
def get_evaluation(*, artifacts_dir: Path | None = None) -> dict[str, Any]:
    """Return MAE (504/505), total error, and the baseline comparison table.

    Input schema: none.
    Output schema (success): the evaluation.json object (``mae_hourly_pow``,
        ``mae_may_bill``, ``mae_may_carbon`` each with ``mae504``/``mae505``,
        ``agg_pow_total_error``, ``baseline_comparison``, ``reference_note``).
    Reads: artifacts/evaluation.json only.
    """

    try:
        path = resolve_artifact_path(EVALUATION_NAME, artifacts_dir=artifacts_dir)
    except PathSecurityError as exc:
        return _error("path_denied", str(exc))
    if not path.exists():
        return _error("not_found", f"{EVALUATION_NAME} 없음 ({NO_DATA})")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _error("bad_schema", f"{EVALUATION_NAME} 파싱 실패: {exc}")
    if not isinstance(data, dict) or "mae_hourly_pow" not in data:
        return _error("bad_schema", f"{EVALUATION_NAME} 스키마 불일치")
    return data


# ---------------------------------------------------------------------------
# Tool 3: get_module_status()  — module_status.csv
# ---------------------------------------------------------------------------
def get_module_status(*, artifacts_dir: Path | None = None) -> dict[str, Any]:
    """Return per-module status rows from ``module_status.csv``.

    Input schema: none.
    Output schema (success):
        ``{"count": int, "modules": [{module, anomaly_count:int,
           anomaly_ratio:float, last_seen:str, status:str}, ...]}``.
    Reads: artifacts/module_status.csv only.
    """

    try:
        path = resolve_artifact_path(MODULE_STATUS_NAME, artifacts_dir=artifacts_dir)
    except PathSecurityError as exc:
        return _error("path_denied", str(exc))
    if not path.exists():
        return _error("not_found", f"{MODULE_STATUS_NAME} 없음 ({NO_DATA})")

    raw_rows = _read_csv_rows(path)
    required = {"module", "anomaly_count", "anomaly_ratio", "last_seen", "status"}
    if raw_rows and not required.issubset(raw_rows[0].keys()):
        return _error("bad_schema", f"{MODULE_STATUS_NAME} 스키마 불일치")

    modules: list[dict[str, Any]] = []
    for raw in raw_rows:
        modules.append(
            {
                "module": raw["module"],
                "anomaly_count": int(raw["anomaly_count"]) if raw["anomaly_count"] else 0,
                "anomaly_ratio": float(raw["anomaly_ratio"]) if raw["anomaly_ratio"] else 0.0,
                "last_seen": raw["last_seen"],
                "status": raw["status"],
            }
        )
    return {"count": len(modules), "modules": modules}


# ---------------------------------------------------------------------------
# Tool 4: get_anomaly_events(module, start, end, type)  — anomaly_events.parquet
# ---------------------------------------------------------------------------
def get_anomaly_events(
    module: str | None = None,
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    type: str | None = None,  # noqa: A002 - matches the design function schema
    *,
    artifacts_dir: Path | None = None,
) -> dict[str, Any]:
    """Return anomaly events filtered by module / time range / type.

    Input schema:
        module: optional module id filter.
        start, end: optional inclusive ``localtime`` bounds.
        type: optional event-type filter.
    Output schema (success):
        ``{"count": int, "events": [{module, localtime:str, type, value:float,
           normal_range, detected_by, score:float}, ...]}``.
    Reads: artifacts/anomaly_events.parquet only.
    """

    try:
        start_dt = _parse_dt(start)
        end_dt = _parse_dt(end)
    except ValueError as exc:
        return _error("bad_input", str(exc))

    try:
        path = resolve_artifact_path(ANOMALY_EVENTS_NAME, artifacts_dir=artifacts_dir)
    except PathSecurityError as exc:
        return _error("path_denied", str(exc))
    if not path.exists():
        return _error("not_found", f"{ANOMALY_EVENTS_NAME} 없음 ({NO_DATA})")

    raw_rows = _read_parquet_rows(path)
    required = {"module", "localtime", "type", "value", "normal_range", "detected_by", "score"}
    if raw_rows and not required.issubset(raw_rows[0].keys()):
        return _error("bad_schema", f"{ANOMALY_EVENTS_NAME} 스키마 불일치")

    events: list[dict[str, Any]] = []
    for raw in raw_rows:
        if module is not None and raw["module"] != module:
            continue
        if type is not None and raw["type"] != type:
            continue
        localtime = raw["localtime"]
        if isinstance(localtime, datetime):
            naive = _as_naive(localtime)
            if start_dt is not None and naive < _as_naive(start_dt):
                continue
            if end_dt is not None and naive > _as_naive(end_dt):
                continue
            localtime_str = localtime.isoformat()
        else:
            localtime_str = str(localtime)
        events.append(
            {
                "module": raw["module"],
                "localtime": localtime_str,
                "type": raw["type"],
                "value": float(raw["value"]),
                "normal_range": raw["normal_range"],
                "detected_by": raw["detected_by"],
                "score": float(raw["score"]),
            }
        )
    return {"count": len(events), "events": events}


# ---------------------------------------------------------------------------
# Tool 5: get_anomaly_trend(module, freq)  — anomaly_hourly.parquet
# ---------------------------------------------------------------------------
def get_anomaly_trend(
    module: str | None = None,
    freq: str = "H",
    *,
    artifacts_dir: Path | None = None,
) -> dict[str, Any]:
    """Return hourly / daily anomaly-count trend from ``anomaly_hourly.parquet``.

    Input schema:
        module: optional module id filter.
        freq: ``"H"`` (per hour, default) or ``"D"`` (per day). Any other value
            returns a ``bad_input`` error.
    Output schema (success):
        ``{"freq": str, "count": int, "points": [{bucket:str, module, type,
           count:int}, ...]}`` sorted by bucket.
    Reads: artifacts/anomaly_hourly.parquet only.
    """

    if freq not in {"H", "D"}:
        return _error("bad_input", f"freq must be 'H' or 'D', got {freq!r}")

    try:
        path = resolve_artifact_path(ANOMALY_HOURLY_NAME, artifacts_dir=artifacts_dir)
    except PathSecurityError as exc:
        return _error("path_denied", str(exc))
    if not path.exists():
        return _error("not_found", f"{ANOMALY_HOURLY_NAME} 없음 ({NO_DATA})")

    raw_rows = _read_parquet_rows(path)
    required = {"hour", "module", "type", "count"}
    if raw_rows and not required.issubset(raw_rows[0].keys()):
        return _error("bad_schema", f"{ANOMALY_HOURLY_NAME} 스키마 불일치")

    aggregated: dict[tuple[str, str, str], int] = {}
    for raw in raw_rows:
        if module is not None and raw["module"] != module:
            continue
        hour = raw["hour"]
        if isinstance(hour, datetime):
            bucket = hour.strftime("%Y-%m-%d" if freq == "D" else "%Y-%m-%d %H:00")
        else:
            bucket = str(hour)
        key = (bucket, raw["module"], raw["type"])
        aggregated[key] = aggregated.get(key, 0) + int(raw["count"])

    points = [
        {"bucket": bucket, "module": mod, "type": etype, "count": count}
        for (bucket, mod, etype), count in sorted(aggregated.items())
    ]
    return {"freq": freq, "count": len(points), "points": points}


# Registry used by the agent for OpenAI function calling (name -> callable).
TOOL_REGISTRY = {
    "get_forecast_summary": get_forecast_summary,
    "get_evaluation": get_evaluation,
    "get_module_status": get_module_status,
    "get_anomaly_events": get_anomaly_events,
    "get_anomaly_trend": get_anomaly_trend,
}


def openai_tool_schemas() -> list[dict[str, Any]]:
    """Return OpenAI ``tools`` function schemas for the five artifacts-only tools."""

    return [
        {
            "type": "function",
            "function": {
                "name": "get_forecast_summary",
                "description": "artifacts/predictions.csv 기반 기간별 전력·요금·탄소 합계/평균",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "string", "description": "YYYY-MM-DD[ HH:MM:SS] 시작"},
                        "end": {"type": "string", "description": "YYYY-MM-DD[ HH:MM:SS] 끝"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_evaluation",
                "description": "artifacts/evaluation.json 기반 MAE(504/505)·총합오차·비교표",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_module_status",
                "description": "artifacts/module_status.csv 기반 설비별 상태·비율·최근 시각",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_anomaly_events",
                "description": "artifacts/anomaly_events.parquet 기반 이상 이벤트 조회",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "module": {"type": "string"},
                        "start": {"type": "string"},
                        "end": {"type": "string"},
                        "type": {"type": "string"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_anomaly_trend",
                "description": "artifacts/anomaly_hourly.parquet 기반 시간/일별 이상 건수 추세",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "module": {"type": "string"},
                        "freq": {"type": "string", "enum": ["H", "D"]},
                    },
                },
            },
        },
    ]
