"""Pure UI-logic helpers for the Streamlit dashboard (8.4, testable without UI).

Everything here is a pure function over artifacts-tool outputs (no Streamlit, no
network). The Streamlit app in ``app.py`` imports these and only adds rendering.
Keeping the logic pure makes the UI rules testable (``test_ui_elements.py``,
``test_app_no_key.py``) and keeps the app artifacts-only (S7).

Design references: header (D1), prediction tab totals/MAE/baseline (D2/S3),
anomaly status colours (D3), AI calc-vs-AI split (D4), role enum facility/energy
(REQ-USER), provisional interval label (O-7), module-count notice (O-5),
out-of-interval / surge warnings (D6).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from config import config
from src.agent import tools as tools_mod
from src.app.ui import theme

# Status -> colour mapping for the module status table (D3). Values come from the
# active theme (no colour literals outside ui/theme.py, steering A-9).
STATUS_COLORS = dict(theme.STATUS_COLOR)  # 미확정 = grey (O-3 unapproved boundary)
DEFAULT_STATUS_COLOR = theme.DEFAULT_STATUS_COLOR

ROLE_DEFAULT_TAB = {
    "facility": "이상",   # 설비 관리 담당자 -> anomaly first
    "energy": "예측",     # 에너지 관리 담당자 -> forecast first
}


def header_info() -> dict[str, str]:
    """Header fields: team, title, one-line tagline (D1)."""

    return {
        "team": config.TEAM_NAME,
        "title": config.PROJECT_TITLE,
        "tagline": config.PROJECT_TAGLINE,
    }


def data_period(forecast: dict[str, Any]) -> str:
    """Data-basis period string for the header (D1)."""

    if "error" in forecast or not forecast.get("count"):
        return tools_mod.NO_DATA
    return f"{forecast.get('start')} ~ {forecast.get('end')}"


def _date_part(value: Any) -> str | None:
    """Return the YYYY-MM-DD part of a loose timestamp string."""

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Split off time on either ' ' or 'T', then trim any trailing timezone.
    for sep in (" ", "T"):
        if sep in text:
            text = text.split(sep, 1)[0]
            break
    return text[:10] if len(text) >= 10 else text


def forecast_period(forecast: dict[str, Any]) -> dict[str, str | None]:
    """Actual predictions id range as {start, end} dates (§7-1). None when missing."""

    if "error" in forecast or not forecast.get("count"):
        return {"start": None, "end": None}
    return {"start": _date_part(forecast.get("start")), "end": _date_part(forecast.get("end"))}


def events_period(events: dict[str, Any]) -> dict[str, str | None]:
    """Anomaly localtime min~max as {start, end} dates (§7-1). None when missing."""

    rows = events.get("events", []) if "error" not in events else []
    stamps = [_date_part(ev.get("localtime")) for ev in rows]
    stamps = [s for s in stamps if s]
    if not stamps:
        return {"start": None, "end": None}
    return {"start": min(stamps), "end": max(stamps)}


def period_chip_text(period: dict[str, str | None]) -> str:
    """Period-chip label from a {start,end} range (§7-1); validation-mode wording."""

    start = period.get("start")
    end = period.get("end")
    if not start or not end:
        return f"분석 기간 {tools_mod.NO_DATA}"
    return f"분석 기간 {start} ~ {end}"


def fmt_localtime_short(value: Any) -> str:
    """Shorten '2025-04-29T23:49:15+09:00' to 'MM-DD HH:MM' (§7-3). Full in tooltip."""

    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    date_part = _date_part(text)
    time_part = ""
    for sep in (" ", "T"):
        if sep in text:
            rest = text.split(sep, 1)[1]
            time_part = rest[:5]  # HH:MM
            break
    mmdd = date_part[5:10] if date_part and len(date_part) >= 10 else (date_part or "")
    return f"{mmdd} {time_part}".strip()


def role_label(role: str) -> str:
    """Resolve a role enum to its display name; never a personal name (REQ-USER)."""

    return config.ROLES.get(role, config.ROLES[config.DEFAULT_ROLE])


def default_tab_for_role(role: str) -> str:
    """Role-based default tab using the config role enum only (facility/energy)."""

    return ROLE_DEFAULT_TAB.get(role, ROLE_DEFAULT_TAB[config.DEFAULT_ROLE])


def status_color(status: str) -> str:
    """Colour for a module status cell (D3)."""

    return STATUS_COLORS.get(status, DEFAULT_STATUS_COLOR)


def interval_label() -> str:
    """Return the provisional interval label when O-7 quantiles are provisional."""

    if config.PRED_INTERVAL_PROVISIONAL:
        return (
            f"임시/잠정 예측구간 (분위수 {config.PRED_INTERVAL_LOW_Q}/"
            f"{config.PRED_INTERVAL_HIGH_Q}, O-7 잠정값)"
        )
    return f"예측구간 (분위수 {config.PRED_INTERVAL_LOW_Q}/{config.PRED_INTERVAL_HIGH_Q})"


def conversion_formula() -> str:
    """Human-readable conversion formula for the prediction tab (D2)."""

    return (
        f"요금 = 전력량 × {config.BILL_RATE} 원/kWh, "
        f"탄소 = 전력량 × {config.CARBON_RATE} kgCO2/kWh (단일 config)"
    )


def module_count_notice(status: dict[str, Any]) -> str | None:
    """O-5 notice when discovered module count differs from the max-13 cap."""

    if "error" in status:
        return None
    count = status.get("count", 0)
    cap = config.MODULE_COUNT_EXPECTED
    if count == cap:
        return None
    if count > cap:
        return f"발견 설비 수 {count}가 상한 {cap}을 초과했습니다 (O-5). 확인이 필요합니다."
    return f"발견 설비 수 {count}가 기대값 {cap}과 다릅니다 (O-5). 데이터 범위를 확인하세요."


def prediction_tab_model(forecast: dict[str, Any], evaluation: dict[str, Any]) -> dict[str, Any]:
    """Assemble the prediction tab view-model (D2, S3).

    Numbers come only from the forecast/evaluation tool outputs. Missing data is
    surfaced as ``데이터에 없음`` rather than a guessed number.
    """

    forecast_ok = "error" not in forecast and forecast.get("count", 0) > 0
    eval_ok = "error" not in evaluation

    totals = {
        "total_pred_pow": forecast["total_pred_pow"] if forecast_ok else None,
        "total_pred_bill": forecast["total_pred_bill"] if forecast_ok else None,
        "total_pred_carbon": forecast["total_pred_carbon"] if forecast_ok else None,
    }

    mae_cards: dict[str, Any] = {}
    if eval_ok:
        for key, label in (
            ("mae_hourly_pow", "전력량"),
            ("mae_may_bill", "요금"),
            ("mae_may_carbon", "탄소"),
        ):
            block = evaluation.get(key)
            mae_cards[label] = (
                {"mae504": block.get("mae504"), "mae505": block.get("mae505")}
                if isinstance(block, dict)
                else tools_mod.NO_DATA
            )
    else:
        mae_cards = {"전력량": tools_mod.NO_DATA, "요금": tools_mod.NO_DATA, "탄소": tools_mod.NO_DATA}

    baseline = evaluation.get("baseline_comparison", []) if eval_ok else []

    return {
        "available": forecast_ok,
        "totals": totals,
        "mae_cards": mae_cards,
        "baseline_comparison": baseline,
        "conversion_formula": conversion_formula(),
        "interval_label": interval_label(),
        "rows": forecast.get("rows", []) if forecast_ok else [],
    }


def out_of_interval_warnings(
    rows: list[dict[str, Any]], actuals: dict[str, float] | None
) -> list[dict[str, Any]]:
    """Flag timestamps whose actual value falls outside [pred_low, pred_high] (D6).

    ``actuals`` maps ``id`` -> actual value. When no actuals are supplied the
    result is empty (the data-less state is the caller's responsibility to show).
    """

    if not actuals:
        return []
    warnings: list[dict[str, Any]] = []
    for row in rows:
        low = row.get("pred_low")
        high = row.get("pred_high")
        actual = actuals.get(row["id"])
        if actual is None or low is None or high is None:
            continue
        if actual < low or actual > high:
            warnings.append(
                {
                    "id": row["id"],
                    "actual": actual,
                    "pred_low": low,
                    "pred_high": high,
                    "direction": "below" if actual < low else "above",
                }
            )
    return warnings


def surge_warnings(trend: dict[str, Any], factor: float = 2.0) -> list[dict[str, Any]]:
    """Flag modules whose per-bucket anomaly count surges above factor×mean (D6).

    Count surge is based only on anomaly_hourly counts (C2). Returns an empty
    list when the trend data is missing.
    """

    if "error" in trend or not trend.get("points"):
        return []
    per_module: dict[str, list[int]] = {}
    for point in trend["points"]:
        per_module.setdefault(point["module"], []).append(int(point["count"]))

    warnings: list[dict[str, Any]] = []
    for module, counts in per_module.items():
        if len(counts) < 2:
            continue
        mean = sum(counts) / len(counts)
        peak = max(counts)
        if mean > 0 and peak >= factor * mean:
            warnings.append({"module": module, "peak": peak, "mean": round(mean, 3)})
    return warnings


def anomaly_tab_model(
    status: dict[str, Any],
    events: dict[str, Any],
    trend: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the anomaly tab view-model (D3): status rows (coloured), events,
    surge warnings. The selected-module time-series is rendered by the app from
    the raw events; here we expose counts and colours."""

    status_rows: list[dict[str, Any]] = []
    if "error" not in status:
        for row in status["modules"]:
            status_rows.append({**row, "color": status_color(row["status"])})

    return {
        "status_rows": status_rows,
        "module_notice": module_count_notice(status),
        "events": events.get("events", []) if "error" not in events else [],
        "event_count": events.get("count", 0) if "error" not in events else 0,
        "surge_warnings": surge_warnings(trend),
    }


def _as_module_set(modules: list[str] | set[str] | None) -> set[str] | None:
    """Normalise a selected-module filter to a set, or ``None`` for 'all' (A-2).

    ``None`` means no filtering (전체 선택). An empty collection means 'nothing
    selected' and keeps nothing — the caller shows the '선택한 설비 없음' empty
    state in that case.
    """

    if modules is None:
        return None
    return set(modules)


def filter_status_by_modules(
    status: dict[str, Any], modules: list[str] | set[str] | None
) -> dict[str, Any]:
    """Return a module_status dict kept to the selected modules (A-2, sidebar).

    Pure over the tool output: ``None`` keeps every module (전체); otherwise only
    rows whose ``module`` is in the selection survive. Error dicts pass through
    unchanged so the caller still shows its empty state.
    """

    if "error" in status:
        return status
    allow = _as_module_set(modules)
    rows = status.get("modules", [])
    if allow is not None:
        rows = [r for r in rows if r.get("module") in allow]
    return {"count": len(rows), "modules": rows}


def filter_events_by_modules(
    events: dict[str, Any], modules: list[str] | set[str] | None
) -> dict[str, Any]:
    """Return an anomaly-events dict kept to the selected modules (A-2, sidebar).

    ``None`` keeps everything (전체). Error dicts pass through unchanged.
    """

    if "error" in events:
        return events
    allow = _as_module_set(modules)
    rows = events.get("events", [])
    if allow is not None:
        rows = [r for r in rows if r.get("module") in allow]
    return {"count": len(rows), "events": rows}


def filter_events_by_date(
    events: dict[str, Any], start: str | None, end: str | None
) -> dict[str, Any]:
    """Return an anomaly-events dict kept to a [start, end] date range (A-2).

    ``start``/``end`` are ``YYYY-MM-DD`` date strings (inclusive); either may be
    ``None`` to leave that bound open. Comparison is on the ``localtime`` date
    part so it is tz-agnostic. Error dicts pass through unchanged.
    """

    if "error" in events:
        return events
    if not start and not end:
        return events
    rows = events.get("events", [])
    kept = []
    for r in rows:
        day = _date_part(r.get("localtime"))
        if day is None:
            continue
        if start and day < start:
            continue
        if end and day > end:
            continue
        kept.append(r)
    return {"count": len(kept), "events": kept}


def filter_trend_by_modules(
    trend: dict[str, Any], modules: list[str] | set[str] | None
) -> dict[str, Any]:
    """Return an anomaly-trend dict kept to the selected modules (A-2, sidebar).

    ``None`` keeps every point (전체). Error dicts pass through unchanged so the
    heatmap/top-module helpers still render their empty states.
    """

    if "error" in trend:
        return trend
    allow = _as_module_set(modules)
    points = trend.get("points", [])
    if allow is not None:
        points = [p for p in points if p.get("module") in allow]
    return {"freq": trend.get("freq", "H"), "count": len(points), "points": points}


def performance_measurement(elapsed_seconds: float) -> dict[str, Any]:
    """Record first-screen load time as measurement only; no pass/fail (O-6, D7)."""

    return {
        "elapsed_seconds": round(float(elapsed_seconds), 4),
        "target_seconds": 5,
        "verdict": "측정값만 기록 (O-6 미승인, 합격/실패 판정 안 함)",
    }


def _hour_of_day(id_text: str) -> int | None:
    """Extract hour-of-day (0..23) from a prediction ``id`` timestamp string."""

    text = str(id_text).strip()
    # Expected forms: 'YYYY-MM-DD HH:MM:SS' or ISO 'YYYY-MM-DDTHH:MM:SS'.
    for sep in (" ", "T"):
        if sep in text:
            time_part = text.split(sep, 1)[1]
            hh = time_part[:2]
            if hh.isdigit():
                return int(hh)
    return None


def hourly_profile(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Average bill/carbon profile over hour-of-day 0..23 (A-0 replacement).

    Replaces the near-flat daily bars with an hour-of-day average profile. Rows
    missing a parseable hour are skipped. Returns one entry per hour present,
    sorted by hour.
    """

    buckets: dict[int, dict[str, float]] = {}
    for row in rows:
        hour = _hour_of_day(row.get("id", ""))
        if hour is None:
            continue
        bucket = buckets.setdefault(hour, {"bill": 0.0, "carbon": 0.0, "n": 0.0})
        bucket["bill"] += float(row.get("pred_bill", 0.0))
        bucket["carbon"] += float(row.get("pred_carbon", 0.0))
        bucket["n"] += 1.0
    profile: list[dict[str, Any]] = []
    for hour in sorted(buckets):
        b = buckets[hour]
        n = b["n"] or 1.0
        profile.append({"hour": hour, "bill": b["bill"] / n, "carbon": b["carbon"] / n})
    return profile


def selected_baseline_model(evaluation: dict[str, Any]) -> str | None:
    """Return the lowest-validation-MAE model to highlight in the baseline table.

    ``evaluation.json`` has no explicit 'selected' flag, so the view highlights
    the best (lowest) validation MAE among the comparison rows (deterministic).
    Returns ``None`` when the comparison is empty/missing.
    """

    if "error" in evaluation:
        return None
    rows = evaluation.get("baseline_comparison") or []
    best: str | None = None
    best_mae = float("inf")
    for row in rows:
        mae = row.get("validation_mae")
        if isinstance(mae, (int, float)) and mae < best_mae:
            best_mae = float(mae)
            best = row.get("model")
    return best


def method_comparison(rebuild_meta: dict[str, Any] | None) -> dict[str, Any]:
    """Build the two method-comparison tables (B3): per-method counts + overlap.

    Reads ``method_counts`` and ``method_overlap`` from anomaly_rebuild_meta. The
    caption 'regra 대비 프록시 — 정답 아님' is the caller's responsibility. Returns
    empty lists when the meta is missing so the caller shows an empty state.
    """

    if not rebuild_meta:
        return {"available": False, "method_counts": [], "method_overlap": []}
    counts = rebuild_meta.get("method_counts") or {}
    overlap = rebuild_meta.get("method_overlap") or {}
    count_rows = [{"method": k, "count": int(v)} for k, v in sorted(counts.items())]
    overlap_rows = [{"combination": k, "count": int(v)} for k, v in sorted(overlap.items())]
    return {
        "available": bool(count_rows or overlap_rows),
        "method_counts": count_rows,
        "method_overlap": overlap_rows,
    }


def heatmap_matrix(trend: dict[str, Any]) -> dict[str, Any]:
    """Build a module × day matrix of anomaly counts from the trend points (D3).

    Uses the daily aggregation (``bucket`` is a date-ish string). Returns
    ``{"modules": [...], "days": [...], "matrix": [[...]]}``; empty when missing.
    """

    if "error" in trend or not trend.get("points"):
        return {"modules": [], "days": [], "matrix": []}
    modules: list[str] = []
    days: list[str] = []
    cells: dict[tuple[str, str], int] = {}
    for point in trend["points"]:
        module = point["module"]
        day = str(point["bucket"]).split(" ", 1)[0]
        if module not in modules:
            modules.append(module)
        if day not in days:
            days.append(day)
        cells[(module, day)] = cells.get((module, day), 0) + int(point["count"])
    modules.sort()
    days.sort()
    matrix = [[cells.get((m, d), 0) for d in days] for m in modules]
    return {"modules": modules, "days": days, "matrix": matrix}


def module_sparklines(trend: dict[str, Any]) -> dict[str, list[float]]:
    """Per-module anomaly-count sparkline series from the hourly trend.

    Buckets are summed across types and ordered by bucket, giving a small list
    of counts per module for the status table's LineChartColumn. Empty dict when
    the trend is missing. No per-module 24h artifact exists, so this is the
    available proxy series (A-0).
    """

    if "error" in trend or not trend.get("points"):
        return {}
    per_module: dict[str, dict[str, float]] = {}
    for point in trend["points"]:
        bucket = str(point.get("bucket"))
        mod = point["module"]
        series = per_module.setdefault(mod, {})
        series[bucket] = series.get(bucket, 0.0) + float(point.get("count", 0))
    out: dict[str, list[float]] = {}
    for mod, buckets in per_module.items():
        out[mod] = [buckets[b] for b in sorted(buckets)]
    return out


def phase_sync_series(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Split anomaly events into voltage / power-factor series for the sync chart.

    Groups by event ``type`` substring (voltage / power_factor). Non-matching
    types are ignored. Each series is a list of ``{"x": localtime, "value":...}``
    sorted by time.
    """

    voltage: list[dict[str, Any]] = []
    power_factor: list[dict[str, Any]] = []
    for ev in events:
        etype = str(ev.get("type", "")).lower()
        point = {"x": ev.get("localtime"), "value": float(ev.get("value", 0.0))}
        if "voltage" in etype:
            voltage.append(point)
        elif "power_factor" in etype or "powerfactor" in etype or "역률" in etype:
            power_factor.append(point)
    voltage.sort(key=lambda p: str(p["x"]))
    power_factor.sort(key=lambda p: str(p["x"]))
    return {"voltage": voltage, "power_factor": power_factor}


def anomaly_cause_sections(module: str, status_label: str | None) -> dict[str, str]:
    """Three-section drill-down text for a module (C4): observed / causes / checks.

    Never asserts a failure verdict; frames findings as observations, candidate
    factors, and field-check items. Numbers belong to the caller's tables.
    """

    observed = (
        f"설비 {module}의 전압·역률 이상 이벤트가 기록되었습니다. "
        "이는 관찰 기록일 뿐 설비 측 판정이 아닙니다."
    )
    if status_label:
        observed += f" 현재 상태 라벨: {status_label}."
    causes = (
        "3상 전압/역률이 임계 아래로 동시에 내려간 구간입니다. "
        "설비 측 요인(현장 확인 필요)·계측 요인 등 후보가 있으며 단정하지 않습니다."
    )
    checks = (
        "해당 설비의 전압·역률 시계열과 세 방법 비교표를 현장에서 교차 확인하세요. "
        "확정 전까지 '이상 구간/이상 징후'로만 다룹니다."
    )
    return {"observed": observed, "causes": causes, "checks": checks}


def discovered_module_summary(status: dict[str, Any]) -> dict[str, Any]:
    """Summarise discovered module count vs the max-13 cap (O-5) for the header."""

    if "error" in status:
        return {"count": 0, "cap": config.MODULE_COUNT_EXPECTED, "matches_cap": False}
    count = status.get("count", 0)
    return {
        "count": count,
        "cap": config.MODULE_COUNT_EXPECTED,
        "matches_cap": count == config.MODULE_COUNT_EXPECTED,
    }


def status_rows_sorted(status: dict[str, Any]) -> list[dict[str, Any]]:
    """Status rows sorted by anomaly ratio desc; zero-count modules last (§7-5)."""

    if "error" in status:
        return []
    rows = list(status.get("modules", []))

    def _key(row: dict[str, Any]) -> tuple[int, float, int]:
        count = int(row.get("anomaly_count", 0) or 0)
        ratio = float(row.get("anomaly_ratio", 0.0) or 0.0)
        # Zero-count rows sink to the bottom (first sort key 1 vs 0), then by
        # ratio desc (negated), then count desc.
        return (1 if count == 0 else 0, -ratio, -count)

    return sorted(rows, key=_key)


def top_anomaly_modules(status: dict[str, Any], *, limit: int = 5) -> list[dict[str, Any]]:
    """Top-N modules by anomaly ratio with at least one anomaly (overview §).

    Returns rows with positive anomaly_count only, so the "이상 징후 상위 설비"
    panel never pads with zero-count modules.
    """

    rows = [r for r in status_rows_sorted(status) if int(r.get("anomaly_count", 0) or 0) > 0]
    return rows[:limit]


def status_donut_counts(status: dict[str, Any], *, approved: bool) -> list[dict[str, Any]]:
    """Status-summary donut segments (overview). When O-3 boundaries are
    unapproved every discovered module is '미확정'; otherwise group by status.
    """

    if "error" in status:
        return []
    modules = status.get("modules", [])
    if not modules:
        return []
    if not approved:
        return [{"label": "미확정", "count": len(modules)}]
    buckets: dict[str, int] = {}
    for row in modules:
        buckets[row.get("status", "미확정")] = buckets.get(row.get("status", "미확정"), 0) + 1
    order = ["정상", "주의", "이상", "미확정"]
    out = [{"label": k, "count": buckets[k]} for k in order if k in buckets]
    out += [{"label": k, "count": v} for k, v in buckets.items() if k not in order]
    return out


def module_power_share(forecast: dict[str, Any]) -> list[dict[str, Any]]:
    """Placeholder for a per-module power-share bar on the overview.

    No per-module power artifact exists, so the overview uses the discovered
    modules' anomaly counts as the available quantitative share instead. This
    helper is intentionally thin; the overview tab builds its bar from
    ``module_status`` directly. Kept for symmetry/testability.
    """

    return []


def overview_model(
    forecast: dict[str, Any],
    evaluation: dict[str, Any],
    status: dict[str, Any],
    events: dict[str, Any],
    trend: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the Overview page view-model (v2 §5, default page).

    Pure aggregation over the existing tool outputs; no new artifact. Reuses the
    prediction/anomaly view-models so numbers stay single-sourced.
    """

    pred = prediction_tab_model(forecast, evaluation)
    anomaly = anomaly_tab_model(status, events, trend)
    return {
        "forecast_available": pred["available"],
        "totals": pred["totals"],
        "mae_cards": pred["mae_cards"],
        "rows": pred["rows"],
        "hourly_profile": hourly_profile(pred["rows"]),
        "top_modules": top_anomaly_modules(status),
        "status_donut": status_donut_counts(
            status, approved=bool(config.STATUS_THRESHOLDS_APPROVED)
        ),
        "surge_warnings": anomaly["surge_warnings"],
        "event_count": anomaly["event_count"],
        "module_summary": discovered_module_summary(status),
    }


def load_all(artifacts_dir: Path | None = None) -> dict[str, Any]:
    """Load every artifacts-tool output used by the dashboard (artifacts-only).

    Never raises on missing artifacts: each tool returns an error dict instead,
    so the app can render data-less states without crashing (S7, C5).
    """

    return {
        "forecast": tools_mod.get_forecast_summary(artifacts_dir=artifacts_dir),
        "evaluation": tools_mod.get_evaluation(artifacts_dir=artifacts_dir),
        "module_status": tools_mod.get_module_status(artifacts_dir=artifacts_dir),
        "events": tools_mod.get_anomaly_events(artifacts_dir=artifacts_dir),
        "trend": tools_mod.get_anomaly_trend(artifacts_dir=artifacts_dir),
    }
