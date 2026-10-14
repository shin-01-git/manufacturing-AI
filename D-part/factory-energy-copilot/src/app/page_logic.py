"""Pure page logic for the v3 dashboard (no Streamlit import → unit-testable).

- 페이지 레지스트리, 설정 기본값(세션 한정), 기간 단위 리샘플(시간/일/주)
- 설정 계수 기반 요금·탄소·탄소비용 재계산 (MAE/평가는 항상 config 기본 계수, §6 규칙 2)
- 경고 목록 (예측구간 이탈 / 요금 급증 / 이상 급증 설비)
- 설비별 분석용 집계 (hourly_from_full), 이상 유형 칩, 타임라인, 분석 플로우 단계

All numbers come from artifacts passed in by the caller; nothing here reads files.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from config import config
from src.app import ui_logic

# ---------------------------------------------------------------------------
# Pages (IA, v3 §2)
# ---------------------------------------------------------------------------
PAGE_HOME = "홈"
PAGE_EQUIPMENT = "설비별 분석"
PAGE_ANOMALY = "이상치 모니터링"
PAGE_COPILOT = "AI Copilot"
PAGE_SETTINGS = "설정"
PAGES: list[str] = [PAGE_HOME, PAGE_EQUIPMENT, PAGE_ANOMALY, PAGE_COPILOT, PAGE_SETTINGS]
DEFAULT_PAGE = PAGE_HOME
PAGE_ICONS: dict[str, str] = {
    PAGE_HOME: ":material/home:",
    PAGE_EQUIPMENT: ":material/monitoring:",
    PAGE_ANOMALY: ":material/query_stats:",
    PAGE_COPILOT: ":material/smart_toy:",
    PAGE_SETTINGS: ":material/settings:",
}
# Pages that expose the 시간/일/주 toggle and a date filter in the header bar.
PAGES_WITH_GRANULARITY = {PAGE_HOME, PAGE_EQUIPMENT}
PAGES_WITH_DATE = {PAGE_HOME, PAGE_EQUIPMENT, PAGE_ANOMALY, PAGE_COPILOT}
GRANULARITIES: list[str] = ["시간", "일", "주"]
DEFAULT_GRANULARITY = "일"

SETTINGS_KEY = "settings"

# ---------------------------------------------------------------------------
# Role profiles (REQ-USER: role keys only, never personal names)
# 역할은 (1) 보이는 메뉴, (2) 홈 KPI 구성/하단 패널 순서/상세 섹션, (3) 이상치 페이지 상세도,
# (4) AI Copilot 기본 브리핑 대상·추천 질문을 바꾼다. 데이터 값 자체는 역할과 무관하다.
# ---------------------------------------------------------------------------
ROLE_FACILITY = "facility"
ROLE_ENERGY = "energy"
ROLE_EXECUTIVE = "executive"

ROLE_PROFILES: dict[str, dict[str, Any]] = {
    ROLE_FACILITY: {
        "pages": [PAGE_HOME, PAGE_ANOMALY, PAGE_EQUIPMENT, PAGE_COPILOT, PAGE_SETTINGS],
        "audience": "facility",
        "kpis": ["pow", "interval", "events", "surge", "found"],
        "home_row3": ["period", "top_modules", "report"],
        "home_detail": False,
        "metric": "전력량",
        "anomaly_detail": True,
        "summary": "이상 구간·설비 중심 (상태표, 이벤트, 드릴다운)",
        "questions": [
            "주의해서 볼 설비가 있어?",
            "이상 이벤트가 많은 설비를 알려줘",
            "야간에 전력을 많이 쓰는 설비는?",
            "부하 패턴 요약해 줘",
        ],
    },
    ROLE_ENERGY: {
        "pages": [PAGE_HOME, PAGE_EQUIPMENT, PAGE_ANOMALY, PAGE_COPILOT, PAGE_SETTINGS],
        "audience": "energy",
        "kpis": ["pow", "bill", "carbon", "cost", "mae"],
        "home_row3": ["period", "cost", "report"],
        "home_detail": True,
        "metric": "요금",
        "anomaly_detail": True,
        "summary": "예측·요금·탄소·정확도와 예측 근거 중심",
        "questions": [
            "이번 기간 총 예측 요금과 탄소는 얼마야?",
            "전력량 예측 MAE 참고값은?",
            "요금이 가장 높은 예측 시간대는?",
            "부하 패턴 요약해 줘",
        ],
    },
    ROLE_EXECUTIVE: {
        "pages": [PAGE_HOME, PAGE_ANOMALY, PAGE_COPILOT],
        "audience": "executive",
        "kpis": ["bill", "carbon", "cost", "pow", "alerts"],
        "home_row3": ["report", "cost", "period"],
        "home_detail": False,
        "metric": "요금",
        "anomaly_detail": False,
        "summary": "비용·탄소·경고 요약 중심 (세부 설정·분석 화면 숨김)",
        "questions": [
            "이번 기간 총 예측 요금과 탄소는 얼마야?",
            "주의해서 볼 설비가 있어?",
            "경영진 보고용으로 5줄 요약해 줘",
            "요금이 가장 높은 예측 시간대는?",
        ],
    },
}


def role_profile(role: str) -> dict[str, Any]:
    return ROLE_PROFILES.get(role, ROLE_PROFILES[config.DEFAULT_ROLE])


def pages_for_role(role: str) -> list[str]:
    return list(role_profile(role)["pages"])


def audience_for_role(role: str) -> str:
    return str(role_profile(role)["audience"])


# ---------------------------------------------------------------------------
# Settings (session-only, §6)
# ---------------------------------------------------------------------------
def default_settings() -> dict[str, Any]:
    return {
        "bill_rate": float(config.BILL_RATE),
        "carbon_rate": float(config.CARBON_RATE),
        "carbon_price": None,  # 원/tCO2, 빈칸 = 미설정 (임의 시장가 하드코딩 금지)
        "alert_interval": True,
        "bill_surge_pct": float(config.DEFAULT_BILL_SURGE_PCT),
        "anomaly_surge_factor": float(config.DEFAULT_ANOMALY_SURGE_FACTOR),
        "night_start": int(config.DEFAULT_NIGHT_START_HOUR),
        "night_end": int(config.DEFAULT_NIGHT_END_HOUR),
    }


def merged_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    out = default_settings()
    if isinstance(raw, dict):
        out.update({k: raw[k] for k in out if k in raw})
    return out


def rates_differ_from_default(settings: dict[str, Any]) -> bool:
    d = default_settings()
    return (
        abs(float(settings["bill_rate"]) - d["bill_rate"]) > 1e-12
        or abs(float(settings["carbon_rate"]) - d["carbon_rate"]) > 1e-12
    )


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------
def to_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _row_dt(row_id: str) -> datetime | None:
    try:
        return datetime.strptime(str(row_id)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _in_range(d: date, start: date | None, end: date | None) -> bool:
    return (start is None or d >= start) and (end is None or d <= end)


# ---------------------------------------------------------------------------
# Resample (시간/일/주) — forecast rows
# ---------------------------------------------------------------------------
def bucket_key(moment: datetime, unit: str) -> tuple[str, str]:
    """Return ``(sort_key, label)`` for the bucket the moment falls into."""

    if unit == "일":
        d = moment.date()
        return d.isoformat(), d.strftime("%m-%d")
    if unit == "주":
        monday = moment.date() - timedelta(days=moment.weekday())
        return monday.isoformat(), monday.strftime("%m-%d") + " 주"
    return moment.strftime("%Y-%m-%d %H:00"), moment.strftime("%m-%d %H시")


def _full_size(unit: str) -> int:
    return {"시간": 1, "일": 24, "주": 168}.get(unit, 1)


def resample_rows(
    rows: list[dict[str, Any]],
    unit: str,
    fields: tuple[str, ...],
    *,
    start: Any = None,
    end: Any = None,
) -> list[dict[str, Any]]:
    """Sum ``fields`` per bucket. Each bucket: key, label, n, partial, <fields...>.

    ``partial`` marks a bucket with fewer hours than a complete day/week (5/10 is a
    Saturday and the last day holds a single hour, so edge buckets are partial).
    ``None`` field values are skipped (a bucket's field is None if all are None).
    """

    s, e = to_date(start), to_date(end)
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        moment = _row_dt(row.get("id", ""))
        if moment is None or not _in_range(moment.date(), s, e):
            continue
        key, label = bucket_key(moment, unit)
        b = buckets.setdefault(key, {"key": key, "label": label, "n": 0, **{f: None for f in fields}})
        b["n"] += 1
        for f in fields:
            v = row.get(f)
            if v is None:
                continue
            b[f] = (b[f] or 0.0) + float(v)
    out = [buckets[k] for k in sorted(buckets)]
    full = _full_size(unit)
    for b in out:
        b["partial"] = unit != "시간" and b["n"] < full
        if b["partial"]:
            b["label"] = f"{b['label']}(부분)"
    return out


def delta_pct(buckets: list[dict[str, Any]], field: str, unit: str) -> float | None:
    """Last complete bucket vs the one before it (%). Hidden (None) for 시간 unit.

    Only complete buckets are compared so a partial edge bucket never fakes a drop.
    """

    if unit == "시간":
        return None
    complete = [b for b in buckets if not b["partial"] and b.get(field) is not None]
    if len(complete) < 2:
        return None
    prev, curr = complete[-2][field], complete[-1][field]
    if not prev:
        return None
    return (curr - prev) / prev * 100.0


# ---------------------------------------------------------------------------
# Totals with settings coefficients (§6)
# ---------------------------------------------------------------------------
def scaled_totals(
    rows: list[dict[str, Any]], settings: dict[str, Any], *, start: Any = None, end: Any = None
) -> dict[str, Any]:
    """Σ pred_pow over the date range; bill/carbon recomputed with settings rates."""

    s, e = to_date(start), to_date(end)
    pow_total = 0.0
    n = 0
    for row in rows:
        moment = _row_dt(row.get("id", ""))
        if moment is None or not _in_range(moment.date(), s, e):
            continue
        pow_total += float(row.get("pred_pow", 0.0))
        n += 1
    bill = pow_total * float(settings["bill_rate"])
    carbon = pow_total * float(settings["carbon_rate"])
    price = settings.get("carbon_price")
    cost = carbon_cost(carbon, price)
    return {
        "n": n,
        "pow": pow_total,
        "bill": bill,
        "carbon": carbon,
        "carbon_cost": cost,
        "total_cost": (bill + cost) if cost is not None else None,
    }


def carbon_cost(carbon_total: float, price_per_ton: Any) -> float | None:
    """Carbon amount (kgCO2 scale) → t (÷1000) × price (원/tCO2). None if unset."""

    if price_per_ton in (None, ""):
        return None
    try:
        price = float(price_per_ton)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    return float(carbon_total) / 1000.0 * price


# ---------------------------------------------------------------------------
# Alerts (§4.1 경고 목록)
# ---------------------------------------------------------------------------
def build_alerts(
    avp: dict[str, Any],
    forecast: dict[str, Any],
    trend: dict[str, Any],
    settings: dict[str, Any],
    *,
    start: Any = None,
    end: Any = None,
) -> dict[str, Any]:
    """Alert items + counts for the strip / bell / panel.

    ``errors`` lists data sources that could not be read, so the UI shows an error
    state instead of a calm "경고 없음" (v3 §8-2).
    """

    from src.app.ui import fmt  # local import: fmt is a pure module

    items: list[dict[str, Any]] = []
    errors: list[str] = []
    s, e = to_date(start), to_date(end)

    interval_count = 0
    if settings.get("alert_interval", True):
        if "error" in avp:
            errors.append("actual_vs_pred.csv")
        else:
            outs = []
            for r in avp.get("rows", []):
                moment = _row_dt(r["id"])
                if moment is None or not _in_range(moment.date(), s, e):
                    continue
                lo, hi = r.get("pred_low"), r.get("pred_high")
                if lo is None or hi is None:
                    continue
                if r["actual_pow"] > hi:
                    outs.append((r, "상한 초과", r["actual_pow"] - hi, moment))
                elif r["actual_pow"] < lo:
                    outs.append((r, "하한 미달", lo - r["actual_pow"], moment))
            interval_count = len(outs)
            for r, direction, gap, moment in sorted(outs, key=lambda x: -x[2])[:20]:
                bound = r["pred_high"] if direction == "상한 초과" else r["pred_low"]
                items.append(
                    {
                        "level": "warn",
                        "kind": "interval",
                        "title": f"예측구간 {direction}",
                        "evidence": (
                            f"실제 {fmt.fmt_pow(r['actual_pow'])} / 경계 {fmt.fmt_pow(bound)} "
                            f"({fmt.fmt_pow(gap)} 차이)"
                        ),
                        "time": moment.strftime("%m-%d %H:%M"),
                        "sort": moment,
                    }
                )

    if "error" in forecast:
        errors.append("predictions.csv")
    else:
        pct = float(settings.get("bill_surge_pct", config.DEFAULT_BILL_SURGE_PCT))
        by_day: dict[str, list[float]] = {}
        parsed = []
        for r in forecast.get("rows", []):
            moment = _row_dt(r["id"])
            if moment is None or not _in_range(moment.date(), s, e):
                continue
            bill = float(r["pred_pow"]) * float(settings["bill_rate"])
            by_day.setdefault(moment.date().isoformat(), []).append(bill)
            parsed.append((moment, bill))
        for moment, bill in parsed:
            vals = by_day[moment.date().isoformat()]
            mean = sum(vals) / len(vals)
            if len(vals) >= 12 and mean > 0 and bill > mean * (1 + pct / 100.0):
                items.append(
                    {
                        "level": "warn",
                        "kind": "bill",
                        "title": "예측 요금 급증 시간대",
                        "evidence": f"예측 {fmt.fmt_bill(bill)} > 일 평균 {fmt.fmt_bill(mean)} +{pct:g}%",
                        "time": moment.strftime("%m-%d %H:%M"),
                        "sort": moment,
                    }
                )

    surge_modules: list[dict[str, Any]] = []
    if "error" in trend:
        errors.append("anomaly_hourly.parquet")
    else:
        factor = float(settings.get("anomaly_surge_factor", config.DEFAULT_ANOMALY_SURGE_FACTOR))
        surge_modules = ui_logic.surge_warnings(trend, factor=factor)
        for w in surge_modules:
            items.append(
                {
                    "level": "alert",
                    "kind": "surge",
                    "title": f"{w['module']} 이상 급증",
                    "evidence": f"최대 {fmt.fmt_count(w['peak'])}건 (평소 {w['mean']}건)",
                    "time": "",
                    "sort": datetime.max,
                    "target": PAGE_ANOMALY,
                }
            )

    items.sort(key=lambda it: (0 if it["level"] == "alert" else 1, it["sort"]))
    return {
        "items": items,
        "interval_count": interval_count,
        "surge_count": len(surge_modules),
        "count": len(items) if interval_count <= 20 else interval_count + len(surge_modules),
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# 설비별 분석 (hourly_from_full) — pandas
# ---------------------------------------------------------------------------
def frame_period(frame) -> tuple[date | None, date | None]:
    if frame is None or len(frame) == 0:
        return None, None
    return frame["hour"].min().date(), frame["hour"].max().date()


def _slice(frame, start: Any, end: Any):
    s, e = to_date(start), to_date(end)
    out = frame
    if s is not None:
        out = out[out["hour"] >= datetime.combine(s, datetime.min.time())]
    if e is not None:
        out = out[out["hour"] < datetime.combine(e + timedelta(days=1), datetime.min.time())]
    return out


def module_means(frame, start: Any = None, end: Any = None) -> list[dict[str, Any]]:
    """Mean hourly power per module over the range, descending."""

    sub = _slice(frame, start, end)
    if len(sub) == 0:
        return []
    grp = sub.groupby("module")["hourly_pow"].mean().sort_values(ascending=False)
    total = float(grp.sum()) or 1.0
    return [
        {"module": m, "mean": float(v), "share": float(v) / total * 100.0}
        for m, v in grp.items()
    ]


def top_modules(frame, n: int = 3) -> list[str]:
    return [r["module"] for r in module_means(frame)[:n]]


def module_trend(frame, modules: list[str], unit: str, start: Any = None, end: Any = None) -> dict[str, Any]:
    """{module: (x labels/timestamps, mean hourly power)} resampled per unit."""

    import pandas as pd

    sub = _slice(frame, start, end)
    sub = sub[sub["module"].isin(modules)]
    if len(sub) == 0:
        return {}
    if unit == "일":
        key = sub["hour"].dt.floor("D")
    elif unit == "주":
        key = (sub["hour"].dt.floor("D") - pd.to_timedelta(sub["hour"].dt.weekday, unit="D"))
    else:
        key = sub["hour"]
    g = sub.assign(_k=key).groupby(["module", "_k"])["hourly_pow"].mean().reset_index()
    return {
        m: (list(grp["_k"]), [float(v) for v in grp["hourly_pow"]])
        for m, grp in g.groupby("module")
    }


def peak_hour_of_day(frame, modules: list[str], start: Any = None, end: Any = None) -> int | None:
    sub = _slice(frame, start, end)
    sub = sub[sub["module"].isin(modules)]
    if len(sub) == 0:
        return None
    by_hour = sub.groupby(sub["hour"].dt.hour)["hourly_pow"].mean()
    return int(by_hour.idxmax())


def load_pattern(frame, module: str, start: Any = None, end: Any = None) -> dict[str, Any]:
    """7 (Mon..Sun) × 24 mean power matrix for one module."""

    sub = _slice(frame, start, end)
    sub = sub[sub["module"] == module]
    matrix: list[list[float | None]] = [[None] * 24 for _ in range(7)]
    if len(sub) == 0:
        return {"days": ["월", "화", "수", "목", "금", "토", "일"], "hours": list(range(24)), "matrix": matrix}
    g = sub.groupby([sub["hour"].dt.weekday, sub["hour"].dt.hour])["hourly_pow"].mean()
    for (wd, hr), v in g.items():
        matrix[int(wd)][int(hr)] = float(v)
    return {"days": ["월", "화", "수", "목", "금", "토", "일"], "hours": list(range(24)), "matrix": matrix}


def night_hours(settings: dict[str, Any]) -> list[int]:
    s, e = int(settings["night_start"]), int(settings["night_end"])
    if s == e:
        return []
    hours = []
    h = s
    while h != e:
        hours.append(h)
        h = (h + 1) % 24
    return hours


def quality_summary(quality: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Per-module 3-phase quality metrics, or None if the artifact is absent."""

    if "error" in quality or "frame" not in quality:
        return None
    frame = quality["frame"]
    cols = set(frame.columns)
    need = {"module", "pf_mean_R", "pf_mean_S", "pf_mean_T"}
    if not need <= cols:
        return None
    out = []
    for module, grp in frame.groupby("module"):
        pf = float((grp["pf_mean_R"].mean() + grp["pf_mean_S"].mean() + grp["pf_mean_T"].mean()) / 3.0)
        row = {"module": module, "pf": pf}
        if "current_unbalance_pct" in cols:
            row["unbalance"] = float(grp["current_unbalance_pct"].mean())
        if "reactive_mean" in cols:
            row["reactive"] = float(grp["reactive_mean"].mean())
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# 이상 유형 칩 / 타임라인 (§7)
# ---------------------------------------------------------------------------
def type_label(raw: str) -> str:
    return config.ANOMALY_TYPE_LABELS.get(raw, raw)


def anomaly_type_options(events: dict[str, Any]) -> list[dict[str, Any]]:
    """Types that actually occur in ``anomaly_events.type`` (never invented)."""

    if "error" in events:
        return []
    counts: dict[str, int] = {}
    for ev in events.get("events", []):
        counts[ev["type"]] = counts.get(ev["type"], 0) + 1
    return [
        {"type": t, "label": type_label(t), "count": c}
        for t, c in sorted(counts.items(), key=lambda kv: -kv[1])
    ]


def filter_events_by_type(events: dict[str, Any], raw_type: str | None) -> dict[str, Any]:
    if "error" in events or not raw_type:
        return events
    rows = [ev for ev in events.get("events", []) if ev["type"] == raw_type]
    return {**events, "events": rows, "count": len(rows)}


def filter_trend_by_type(trend: dict[str, Any], raw_type: str | None) -> dict[str, Any]:
    if "error" in trend or not raw_type:
        return trend
    pts = [p for p in trend.get("points", []) if p.get("type") == raw_type]
    return {**trend, "points": pts, "count": len(pts)}


def timeline_points(
    trend: dict[str, Any], *, start: Any = None, end: Any = None
) -> list[dict[str, Any]]:
    """Daily (module, type) detection counts for the timeline scatter."""

    if "error" in trend:
        return []
    s, e = to_date(start), to_date(end)
    agg: dict[tuple[str, str, str], int] = {}
    for p in trend.get("points", []):
        day = str(p["bucket"])[:10]
        d = to_date(day)
        if d is None or not _in_range(d, s, e):
            continue
        k = (p["module"], day, p.get("type", ""))
        agg[k] = agg.get(k, 0) + int(p["count"])
    return [
        {"module": m, "day": day, "type": t, "count": c}
        for (m, day, t), c in sorted(agg.items(), key=lambda kv: (kv[0][1], kv[0][0]))
    ]


# ---------------------------------------------------------------------------
# 분석 플로우 (§4.2) — 값은 artifacts에서만, 없으면 "—"
# ---------------------------------------------------------------------------
def flow_steps(bundle: dict[str, Any]) -> list[dict[str, str]]:
    from src.app.ui import fmt

    dash = "—"
    agg = bundle.get("aggregate_run") or {}
    sel = bundle.get("selection") or {}
    ev = bundle.get("evaluation") or {}

    def ok(d: dict[str, Any]) -> bool:
        return isinstance(d, dict) and "error" not in d

    rows = agg.get("rows_processed") if ok(agg) else None
    complete = agg.get("complete_group_count") if ok(agg) else None
    excluded = agg.get("excluded_incomplete_group_count") if ok(agg) else None
    n720 = agg.get("complete_n") if ok(agg) else None

    base = [r for r in (sel.get("baseline_comparison") or []) if r.get("kind") == "baseline"] if ok(sel) else []
    cand = [r for r in (sel.get("baseline_comparison") or []) if r.get("kind") == "candidate"] if ok(sel) else []
    best_base = min((r["validation_mae"] for r in base), default=None)
    best_cand = min((r["validation_mae"] for r in cand), default=None)
    mae = ev.get("mae_hourly_pow") if ok(ev) else None

    return [
        {
            "no": "1", "title": "원본 데이터",
            "value": fmt.fmt_count(rows) if rows else dash,
            "caption": "5초 원본 행 수 (aggregate_run.json)",
        },
        {
            "no": "2", "title": "시간 집계",
            "value": fmt.fmt_count(complete) if complete else dash,
            "caption": (
                f"완전 시간(n={n720}) 수 · 제외 {fmt.fmt_count(excluded)}"
                if complete and excluded is not None else "완전 시간 수"
            ),
        },
        {
            "no": "3", "title": "베이스라인 3종",
            "value": fmt.fmt_mae(best_base) if best_base is not None else dash,
            "caption": "최고 베이스라인 검증 MAE",
        },
        {
            "no": "4", "title": "후보 모델",
            "value": fmt.fmt_mae(best_cand) if best_cand is not None else dash,
            "caption": "선형회귀·트리 중 최저 검증 MAE",
        },
        {
            "no": "5", "title": "선택·재학습",
            "value": str(sel.get("selected_model", dash)) if ok(sel) else dash,
            "caption": f"분할 {str(sel.get('split_time', ''))[:10]}" if ok(sel) and sel.get("split_time") else "분할 시각",
        },
        {
            "no": "6", "title": "5월 예측·평가",
            "value": fmt.fmt_mae(mae.get("mae505")) if isinstance(mae, dict) else dash,
            "caption": "MAE(505) · 시점 수 " + (str(ev.get("row_count")) if ok(ev) else dash),
        },
    ]
