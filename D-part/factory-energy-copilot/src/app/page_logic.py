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
GRANULARITIES: list[str] = ["시간", "일", "주", "월"]
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
        "kpis": ["pow", "peak", "sudden", "watchmods", "found"],
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
        "carbon_price": float(config.DEFAULT_CARBON_PRICE),  # 원/tCO2, 출처 있는 시세 가정(config), 설정에서 변경
        "alert_interval": True,
        "bill_surge_pct": float(config.DEFAULT_BILL_SURGE_PCT),
        "anomaly_surge_factor": float(config.DEFAULT_ANOMALY_SURGE_FACTOR),
        "anomaly_k": float(config.DEFAULT_ANOMALY_K),
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
    if unit == "월":
        first = moment.date().replace(day=1)
        return first.isoformat(), first.strftime("%Y-%m")
    return moment.strftime("%Y-%m-%d %H:00"), moment.strftime("%m-%d %H시")


def _full_size(unit: str, key: str | None = None) -> int:
    if unit == "월" and key:
        import calendar

        y, m = int(key[:4]), int(key[5:7])
        return calendar.monthrange(y, m)[1] * 24
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
    for b in out:
        b["partial"] = unit != "시간" and b["n"] < _full_size(unit, b["key"])
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
# 경고 목록 제목 = 경고 유형(멘토링 피드백 6종 이름). 6종에 대응하지 않는 원천 유형은 원래 이름.
#   예측범위 이탈·요금 급증 → 비정상 패턴(사용량이 평소/예측 패턴을 벗어남)
#   3상 저역률 이벤트 급증 → 역률 급감 / 3상 저전압은 6종에 없음 → '3상 저전압' 그대로
PATTERN_METRIC = {
    "active_power": "유효전력", "reactive_power": "무효전력", "current_unbalance": "전류 불평형률",
    "pf_mean": "평균 역률", "current_mean": "평균 전류",
}

ALERT_TYPE: dict[str, str] = {
    "interval": config.ANOMALY_TYPE_LABELS["abnormal_pattern"],
    "bill": config.ANOMALY_TYPE_LABELS["abnormal_pattern"],
    "power_factor_low_3phase": config.ANOMALY_TYPE_LABELS["power_factor_drop"],
}


def _dominant_type_by_module(trend: dict[str, Any]) -> dict[str, str]:
    totals: dict[str, dict[str, int]] = {}
    for p in trend.get("points", []) if "error" not in trend else []:
        t = totals.setdefault(p["module"], {})
        t[p.get("type", "")] = t.get(p.get("type", ""), 0) + int(p["count"])
    return {m: max(t, key=t.get) for m, t in totals.items() if t}


def build_alerts(
    avp: dict[str, Any],
    forecast: dict[str, Any],
    trend: dict[str, Any],
    settings: dict[str, Any],
    *,
    start: Any = None,
    end: Any = None,
    pattern: dict[str, Any] | None = None,
    include_surge: bool = False,
) -> dict[str, Any]:
    """Alert items + counts for the strip / bell / panel.

    ``errors`` lists data sources that could not be read, so the UI shows an error
    state instead of a calm "경고 없음" (v3 §8-2).
    """

    from src.app.ui import fmt  # local import: fmt is a pure module

    items: list[dict[str, Any]] = []
    errors: list[str] = []
    s, e = to_date(start), to_date(end)

    # 5월 실제값과 비교하는 '예측구간 이탈'은 확인(검증)용이라 경고에서 뺐다.
    interval_count = 0

    # 이상 6종(설비×시간 패턴 탐지) — 과거 측정 데이터 기준이라 기간 필터 없이 전부 표시
    pattern_count = 0
    if pattern is not None and "error" not in pattern:
        for ev in pattern.get("events", []):
            moment = _row_dt(str(ev.get("hour", "")))
            unit = ev.get("unit", "")
            base = float(ev.get("baseline") or 0.0)
            val = float(ev.get("value") or 0.0)
            diff = (val - base) / base * 100 if base else 0.0
            items.append({
                "level": "warn",
                "kind": "pattern",
                "title": f"{type_label(ev['type'])} · {ev['module']}",
                "evidence": (
                    f"{PATTERN_METRIC.get(ev.get('metric'), ev.get('metric'))} {val:,.1f}{unit} "
                    f"(평소 {base:,.1f}{unit}, {diff:+.1f}%) · z {float(ev.get('z') or 0):+.1f}"
                ),
                "time": moment.strftime("%y-%m-%d %H시") if moment else "",
                "sort": moment or datetime.min,
            })
            pattern_count += 1

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
                        "title": ALERT_TYPE["bill"],
                        "evidence": f"예측 요금 급증 · {fmt.fmt_bill(bill)} > 일 평균 {fmt.fmt_bill(mean)} +{pct:g}%",
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
        dominant = _dominant_type_by_module(trend)
        # 5초 기준 미달 건수의 급증은 거짓 양성이 많아(10/16 피드백) 경고 목록에서 뺀다.
        # 이상치 화면의 '탐지 시간대'에서 참고로만 본다.
        for w in (surge_modules if include_surge else []):
            raw = dominant.get(w["module"], "")
            items.append(
                {
                    "level": "alert",
                    "kind": "surge",
                    "title": f"{ALERT_TYPE.get(raw, type_label(raw))} · {w['module']}",
                    "evidence": f"이상 건수 급증 · 최대 {fmt.fmt_count(w['peak'])}건/시 (평소 {w['mean']}건)",
                    "time": "",
                    "sort": datetime.max,
                    "target": PAGE_ANOMALY,
                }
            )

    # 급증(alert) 먼저, 그다음 최근 것부터
    items.sort(key=lambda it: (0 if it["level"] == "alert" else 1, -it["sort"].timestamp()
                               if it["sort"] not in (datetime.max, datetime.min) else 0))
    return {
        "items": items,
        "interval_count": interval_count,
        "surge_count": len(surge_modules),
        "pattern_count": pattern_count,
        "count": len(items),
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
    elif unit == "월":
        key = sub["hour"].dt.to_period("M").dt.start_time
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


def filter_events_by_hour(events: dict[str, Any], hour: int | None) -> dict[str, Any]:
    """Keep events whose ``localtime`` hour-of-day matches ``hour`` (§2.3 drilldown).

    Same shape as ``filter_events_by_type``. The caller already narrowed
    ``events`` to one module/day; this only narrows by hour-of-day.
    """

    if "error" in events or hour is None:
        return events
    rows = [ev for ev in events.get("events", []) if _hour_of_localtime(ev.get("localtime")) == hour]
    return {**events, "events": rows, "count": len(rows)}


def _hour_of_localtime(value: Any) -> int | None:
    """Extract hour-of-day (0..23) from a loose ``localtime`` timestamp string."""

    text = str(value or "").strip()
    for sep in (" ", "T"):
        if sep in text:
            rest = text.split(sep, 1)[1]
            if rest[:2].isdigit():
                return int(rest[:2])
    return None


# ---------------------------------------------------------------------------
# 탐지 시간대 패널 (3-뷰) — anomaly_hourly_frame(pandas DataFrame) 순수 집계
# ``trend`` 인자는 bundle["anomaly_hourly_frame"]["frame"] (columns: hour(tz-naive
# KST datetime), module, type, count). 모두 pandas 벡터 연산만 쓰고, 65k행을 매
# rerun마다 python for-loop로 다시 훑지 않는다.
# ---------------------------------------------------------------------------
def slice_trend(trend, modules=None, raw_type: str | None = None, start: Any = None, end: Any = None):
    """Filter the anomaly_hourly DataFrame by module(s)/type/date range (shared helper).

    Used by every aggregation function below and by the tab to pre-scope the
    frame before computing ``active_modules`` — keeps the module/type/date
    filtering logic in one place instead of duplicating it per caller.
    """

    sub = trend
    if modules is not None:
        sub = sub[sub["module"].isin(modules)]
    if raw_type:
        sub = sub[sub["type"] == raw_type]
    s, e = to_date(start), to_date(end)
    if s is not None:
        sub = sub[sub["hour"] >= datetime.combine(s, datetime.min.time())]
    if e is not None:
        sub = sub[sub["hour"] < datetime.combine(e + timedelta(days=1), datetime.min.time())]
    return sub


def daily_counts(
    trend, modules: list[str] | None, raw_type: str | None = None, start: Any = None, end: Any = None
):
    """Daily (module, day) anomaly sums + per-type breakdown (§2.1 bars).

    Returns a DataFrame with columns ``module, day, count, by_type`` where
    ``by_type`` is a ``{type: count}`` dict for that module/day. ``modules=None``
    keeps every module.
    """

    import pandas as pd

    cols = ["module", "day", "count", "by_type"]
    if trend is None or len(trend) == 0:
        return pd.DataFrame(columns=cols)
    sub = slice_trend(trend, modules, raw_type, start, end)
    if len(sub) == 0:
        return pd.DataFrame(columns=cols)
    sub = sub.assign(day=sub["hour"].dt.floor("D").dt.date)
    daily = sub.groupby(["module", "day"], as_index=False)["count"].sum()
    type_sum = sub.groupby(["module", "day", "type"])["count"].sum()
    by_type_map: dict[tuple[Any, Any], dict[str, int]] = {}
    for (mod, day, etype), cnt in type_sum.items():
        by_type_map.setdefault((mod, day), {})[etype] = int(cnt)
    daily["by_type"] = [by_type_map.get((r.module, r.day), {}) for r in daily.itertuples()]
    return daily.sort_values(["module", "day"]).reset_index(drop=True)


def surge_days(daily, factor: float):
    """Days where a module's count >= its daily median × ``factor`` (§2.1 ▲).

    Modules whose daily-count median is 0 have no surge days (division-by-zero
    guard; a module with mostly-zero history should not flag every nonzero day).
    """

    import pandas as pd

    cols = ["module", "day", "count"]
    if daily is None or len(daily) == 0:
        return pd.DataFrame(columns=cols)
    out = daily.copy()
    out["_median"] = out.groupby("module")["count"].transform("median")
    hits = out[(out["_median"] > 0) & (out["count"] >= out["_median"] * float(factor))]
    return hits[cols].reset_index(drop=True)


def hour_by_day_matrix(
    trend,
    module: str,
    raw_type: str | None = None,
    start: Any = None,
    end: Any = None,
    weekly_if_days_over: int = 60,
) -> dict[str, Any]:
    """0..23h × date(or ISO-week) count matrix for one module (§2.2 heatmap).

    Returns ``{'rows': [0..23], 'cols': [date,...], 'matrix': [[int]], 'unit':
    '일'|'주', 'p95': int}``. Switches to weekly columns (Monday-keyed) when the
    filtered period spans more than ``weekly_if_days_over`` days; narrowing the
    header date filter below that threshold auto-returns to daily columns. p95
    is computed over the full matrix (0s included, design decision §2).
    """

    import pandas as pd

    empty = {"rows": list(range(24)), "cols": [], "matrix": [], "unit": "일", "p95": 0}
    if trend is None or len(trend) == 0:
        return empty
    sub = slice_trend(trend, [module], raw_type, start, end)
    if len(sub) == 0:
        return empty

    days_span = (sub["hour"].max().date() - sub["hour"].min().date()).days + 1
    unit = "주" if days_span > weekly_if_days_over else "일"
    if unit == "주":
        col_key = (sub["hour"].dt.floor("D") - pd.to_timedelta(sub["hour"].dt.weekday, unit="D")).dt.date
    else:
        col_key = sub["hour"].dt.date
    hour_of_day = sub["hour"].dt.hour

    grouped = sub.assign(_col=col_key, _hr=hour_of_day).groupby(["_hr", "_col"])["count"].sum()
    cols = sorted(col_key.unique())
    rows = list(range(24))
    pivot = grouped.unstack(fill_value=0).reindex(index=rows, columns=cols, fill_value=0)
    matrix = [[int(v) for v in row] for row in pivot.to_numpy()]
    flat = pivot.to_numpy().flatten()
    p95 = int(round(float(pd.Series(flat).quantile(0.95)))) if flat.size else 0
    return {"rows": rows, "cols": cols, "matrix": matrix, "unit": unit, "p95": p95}


def day_hour_profile(trend, module: str, day: Any) -> list[dict[str, Any]]:
    """0..23h per-type counts for one module/day, zero-filled (§2.3 24h bars)."""

    d = to_date(day)
    empty = [{"hour": h, "count": 0, "by_type": {}} for h in range(24)]
    if trend is None or len(trend) == 0 or d is None:
        return empty
    sub = trend[trend["module"] == module]
    start_dt = datetime.combine(d, datetime.min.time())
    end_dt = start_dt + timedelta(days=1)
    sub = sub[(sub["hour"] >= start_dt) & (sub["hour"] < end_dt)]
    if len(sub) == 0:
        return empty
    sub = sub.assign(_hr=sub["hour"].dt.hour)
    totals = sub.groupby("_hr")["count"].sum()
    type_sum = sub.groupby(["_hr", "type"])["count"].sum()
    out = []
    for h in range(24):
        by_type = {}
        if h in type_sum.index.get_level_values(0):
            by_type = {t: int(c) for t, c in type_sum.loc[h].items()}
        out.append({"hour": h, "count": int(totals.get(h, 0)), "by_type": by_type})
    return out


def usual_hour_profile(trend, module: str) -> list[float]:
    """Per-hour average count across every observed day for one module (§2.3 dotted line).

    Average = Σ count at hour h ÷ number of distinct days the module has any
    record (not ÷ number of days with data at hour h specifically), so the
    "usual" line reflects the whole period, not just the active subset.
    """

    if trend is None or len(trend) == 0:
        return [0.0] * 24
    sub = trend[trend["module"] == module]
    if len(sub) == 0:
        return [0.0] * 24
    day_count = sub["hour"].dt.date.nunique() or 1
    sub = sub.assign(_hr=sub["hour"].dt.hour)
    totals = sub.groupby("_hr")["count"].sum()
    return [float(totals.get(h, 0)) / day_count for h in range(24)]


def occurrence_hour_ratio(trend, module: str) -> float:
    """Active hour-slots ÷ total observed hour-slots for one module (§4 chip 1).

    Shared definition reused by ``active_modules`` for its descending sort
    (design decision — ``module_status.anomaly_ratio`` denominator is not in
    ``trend``, so this occurrence-hour ratio is the one consistent "ratio" the
    panel can compute from ``anomaly_hourly`` alone).
    """

    if trend is None or len(trend) == 0:
        return 0.0
    sub = trend[trend["module"] == module]
    if len(sub) == 0:
        return 0.0
    active_hours = sub["hour"].nunique()
    span_start, span_end = sub["hour"].min(), sub["hour"].max()
    total_hours = int((span_end - span_start).total_seconds() // 3600) + 1
    if total_hours <= 0:
        return 0.0
    return active_hours / total_hours


def active_modules(trend) -> list[str]:
    """Modules with count sum > 0, sorted by occurrence-hour ratio descending (§2 header)."""

    if trend is None or len(trend) == 0:
        return []
    totals = trend.groupby("module")["count"].sum()
    modules = [m for m, v in totals.items() if v > 0]
    if not modules:
        return []
    ratios = {m: occurrence_hour_ratio(trend, m) for m in modules}
    return sorted(modules, key=lambda m: -ratios[m])


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

    if ok(sel) and sel.get("source") == "B-part_전력예측":
        comp = sel.get("baseline_comparison") or []
        bases = [r for r in comp if r.get("kind") == "baseline"]
        cands = [r for r in comp if r.get("kind") == "candidate"]
        best_b = min(bases, key=lambda r: r["validation_mae"]) if bases else None
        name = sel.get("selected_model")
        sel_row = next((r for r in comp if r["model"] == name), None)
        return [
            {"no": "1", "title": "원본 데이터", "value": fmt.fmt_count(rows) if rows else dash,
             "caption": "5초 측정 행 (13개 설비)"},
            {"no": "2", "title": "시간 집계", "value": fmt.fmt_count(sel.get("refit_row_count") or 3600),
             "caption": "공장 전체 1시간 전력량(Wh) 시점 수"},
            {"no": "3", "title": "기준 모델", "value": fmt.fmt_mae(best_b["validation_mae"]) if best_b else dash,
             "caption": f"{MODEL_LABELS.get(best_b['model'], best_b['model']) if best_b else ''} · 과거 검증 MAE"},
            {"no": "4", "title": f"후보 {len(cands)}종", "value": fmt.fmt_mae(min(r['validation_mae'] for r in cands)) if cands else dash,
             "caption": "lag·이동평균 특징 · 최저 과거 검증 MAE"},
            {"no": "5", "title": "선정 모델", "value": MODEL_LABELS.get(name, name or dash),
             "caption": "과거 3회 검증 후 전체 기간 재학습"},
            {"no": "6", "title": "5월 예측", "value": fmt.fmt_mae(sel_row["may_mae"]) if sel_row else dash,
             "caption": "505시점 MAE (Wh) · 재귀 예측"},
        ]

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


# ---------------------------------------------------------------------------
# 홈 차트 보조 (팀장 피드백: 예측 설명 문장 · 예측 전력량 y축 확대)
# ---------------------------------------------------------------------------
MODEL_LABELS: dict[str, str] = {
    "calendar_linear_regression": "달력 특성 선형회귀",
    "random_forest": "랜덤 포레스트",
    "lightgbm_reference": "LightGBM",
    "lightgbm_lags": "LightGBM 보수 설정",
    "ridge_lags": "Ridge",
    "xgboost_lags": "XGBoost",
    "hist_lags": "HistGradientBoosting",
    "extra_trees_lags": "ExtraTrees",
    "train_mean": "학습 평균",
    "train_median": "학습 중앙값",
}


def zoom_range(values: list[float], pad: float = 0.2) -> tuple[float, float] | None:
    """[min - pad*span, max + pad*span]. 값이 없으면 None, 값이 1개이거나 모두 같으면 ±5%."""

    vals = [float(v) for v in values if v is not None]
    if not vals:
        return None
    lo, hi = min(vals), max(vals)
    span = hi - lo
    if span <= 0:
        margin = abs(hi) * 0.05 or 1.0
        return lo - margin, hi + margin
    return lo - pad * span, hi + pad * span


def forecast_basis(bundle: dict[str, Any]) -> dict[str, Any]:
    """설명 문장에 들어갈 값. artifacts에 없으면 해당 키는 None."""

    out: dict[str, Any] = {
        "model": None, "train_start": None, "train_end": None,
        "pred_start": None, "pred_end": None, "points": None,
    }
    sel = bundle.get("selection") or {}
    if isinstance(sel, dict) and "error" not in sel and sel.get("selected_model"):
        raw = str(sel["selected_model"])
        out["model"] = MODEL_LABELS.get(raw, raw)
    frame = (bundle.get("modules_hourly") or {}).get("frame")
    if frame is not None:
        lo, hi = frame_period(frame)
        out["train_start"] = lo.isoformat() if lo else None
        out["train_end"] = hi.isoformat() if hi else None
    fc = bundle.get("forecast") or {}
    if isinstance(fc, dict) and "error" not in fc and fc.get("count"):
        out["pred_start"] = str(fc.get("start"))[:16] if fc.get("start") else None
        out["pred_end"] = str(fc.get("end"))[:16] if fc.get("end") else None
        out["points"] = int(fc["count"])
    return out


def hour_of_day_mean(rows: list[dict[str, Any]], field: str) -> list[float]:
    """0~23시별 평균 (id 'YYYY-MM-DD HH:..'). 값이 없는 시는 앞뒤와 무관하게 제외하지 않고 0개면 빈 목록."""

    acc = [0.0] * 24
    cnt = [0] * 24
    for r in rows:
        dt = _row_dt(r.get("id", ""))
        v = r.get(field)
        if dt is None or v is None:
            continue
        acc[dt.hour] += float(v)
        cnt[dt.hour] += 1
    if not any(cnt):
        return []
    return [acc[h] / cnt[h] if cnt[h] else float("nan") for h in range(24)]


# ---------------------------------------------------------------------------
# 경영진 월간 보고 (팀원 d_queries.executive_summary 로직을 대시보드 artifacts 기준으로 이식)
# 학습 원본 실측(hourly_from_full, kWh)만 사용. 전월 대비는 총량과 일평균을 함께 낸다
# (달마다 날짜 수가 달라 총량 증감만 보면 오해가 생김).
# ---------------------------------------------------------------------------
def _pct_change(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev in (None, 0):
        return None
    return (cur - prev) / prev * 100.0


def available_months(frame) -> list[str]:
    if frame is None or len(frame) == 0:
        return []
    return sorted(frame["hour"].dt.strftime("%Y-%m").unique().tolist())


def executive_monthly(frame, month: str | None, settings: dict[str, Any], *, top: int = 3,
                      module_status: dict[str, Any] | None = None) -> dict[str, Any]:
    months = available_months(frame)
    if not months:
        return {"status": "empty"}
    m = month if month in months else months[-1]
    i = months.index(m)
    prev_m = months[i - 1] if i > 0 else None
    key = frame["hour"].dt.strftime("%Y-%m")
    bill_rate, carbon_rate = float(settings["bill_rate"]), float(settings["carbon_rate"])

    def totals(mm):
        sub = frame[key == mm]
        kwh = float(sub["hourly_pow"].sum())
        days = sub["hour"].nunique() / 24 or 0
        daily = kwh / days if days else 0.0
        return sub, {"energy": kwh, "bill": kwh * bill_rate, "carbon": kwh * carbon_rate,
                     "days": days, "daily_energy": daily}

    cur_sub, cur = totals(m)
    prev_sub, prev = totals(prev_m) if prev_m else (None, None)
    change = None
    if prev:
        change = {
            "energy_pct": _pct_change(cur["energy"], prev["energy"]),
            "daily_pct": _pct_change(cur["daily_energy"], prev["daily_energy"]),
        }
    cur_eq = cur_sub.groupby("module")["hourly_pow"].sum().sort_values(ascending=False)
    prev_eq = prev_sub.groupby("module")["hourly_pow"].sum() if prev_m else None
    total = float(cur_eq.sum()) or 1.0
    ranking = []
    for mod, kwh in cur_eq.items():
        kwh = float(kwh)
        d_now = kwh / cur["days"] if cur["days"] else 0.0
        d_prev = (float(prev_eq[mod]) / prev["days"]) if (prev_eq is not None and mod in prev_eq.index and prev["days"]) else None
        ranking.append({
            "module": mod, "kwh": kwh, "carbon": kwh * carbon_rate, "bill": kwh * bill_rate,
            "share": kwh / total * 100.0,
            "daily_change_pct": _pct_change(d_now, d_prev),
            "daily_carbon_change": (d_now - d_prev) * carbon_rate if d_prev is not None else None,
        })
    increased = [r for r in ranking if (r["daily_carbon_change"] or 0) > 0]
    driver = max(increased, key=lambda r: r["daily_carbon_change"]) if increased else None
    watch = []
    if module_status and "error" not in module_status:
        watch = [r["module"] for r in ui_logic.top_anomaly_modules(module_status, limit=top)]
    return {
        "status": "ok", "month": m, "prev_month": prev_m, "months": months,
        "current": cur, "previous": prev, "change": change,
        "ranking": ranking, "reduction_candidates": [r["module"] for r in ranking[:top]],
        "increase_driver": driver, "watch": watch,
    }


_WEEKDAY_KO = "월화수목금토일"


def period_options(
    rows: list[dict[str, Any]], unit: str, *, start: Any = None, end: Any = None, min_points: int = 2
) -> list[dict[str, Any]]:
    """예측 그래프의 날짜(일)/주 선택지. 각 항목: key, label, n, partial.

    시점이 ``min_points`` 미만인 구간(예: 5/31 00시 1개)은 선 하나 그릴 수 없어 뺀다.
    """

    if unit not in ("일", "주"):
        return []
    s, e = to_date(start), to_date(end)
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        moment = _row_dt(row.get("id", ""))
        if moment is None or not _in_range(moment.date(), s, e):
            continue
        key, _ = bucket_key(moment, unit)
        b = seen.setdefault(key, {"key": key, "n": 0})
        b["n"] += 1
    out = []
    for key in sorted(seen):
        b = seen[key]
        if b["n"] < min_points:
            continue
        d = date.fromisoformat(key)
        full = _full_size(unit, key)
        partial = b["n"] < full
        if unit == "일":
            label = f"{d.strftime('%m-%d')} ({_WEEKDAY_KO[d.weekday()]})"
        else:
            label = f"{d.strftime('%m-%d')} – {(d + timedelta(days=6)).strftime('%m-%d')}"
        if partial:
            label += f" · {b['n']}시간"
        out.append({"key": key, "label": label, "n": b["n"], "partial": partial})
    return out


def rows_in_period(
    rows: list[dict[str, Any]], unit: str, key: str, *, start: Any = None, end: Any = None
) -> list[dict[str, Any]]:
    """``period_options``의 key 하나에 속한 시간별 행(시간순)."""

    s, e = to_date(start), to_date(end)
    out = []
    for row in rows:
        moment = _row_dt(row.get("id", ""))
        if moment is None or not _in_range(moment.date(), s, e):
            continue
        if bucket_key(moment, unit)[0] == key:
            out.append(row)
    return sorted(out, key=lambda r: r["id"])


def period_bounds(unit: str, key: str) -> tuple[date, date]:
    """``period_options`` key → (시작일, 종료일) 포함 범위."""

    d = date.fromisoformat(key)
    return (d, d) if unit == "일" else (d, d + timedelta(days=6))


def anomaly_expected(frame: Any, rows: list[dict[str, Any]], *, start: Any = None, end: Any = None) -> dict[str, Any] | None:
    """5월처럼 이상 기록이 없는 기간의 참고 예상 건수.

    과거 anomaly_hourly(시간·설비·유형별 건수)에서 (요일, 시) 평균 건수를 구하고,
    선택 기간의 예측 시점마다 더한다. 평균의 분모는 과거 기간의 해당 요일 일수(0건인 날 포함).
    반환: expected, hist_start, hist_end. 데이터가 없으면 None.
    """

    import pandas as pd

    if frame is None or len(frame) == 0 or not {"hour", "count"}.issubset(frame.columns):
        return None
    hour = pd.to_datetime(frame["hour"])
    tot = frame.assign(h=hour).groupby("h")["count"].sum()
    days = pd.date_range(hour.min().normalize(), hour.max().normalize(), freq="D")
    n_wd = pd.Series(days.weekday).value_counts().to_dict()
    by = tot.groupby([tot.index.weekday, tot.index.hour]).sum().to_dict()
    s, e = to_date(start), to_date(end)
    exp = 0.0
    for row in rows:
        m = _row_dt(row.get("id", ""))
        if m is None or not _in_range(m.date(), s, e):
            continue
        exp += by.get((m.weekday(), m.hour), 0) / max(n_wd.get(m.weekday(), 1), 1)
    return {"expected": exp, "hist_start": days.min().date(), "hist_end": days.max().date()}


def event_counts_by_module(events: list[dict[str, Any]]) -> list[tuple[str, int]]:
    """이벤트 로그 설비 선택지: (설비, 건수) 건수 내림차순."""

    from collections import Counter

    return Counter(str(ev.get("module", "")) for ev in events).most_common()


def events_for_module(events: list[dict[str, Any]], module: str | None, *, limit: int = 200) -> list[dict[str, Any]]:
    """설비 하나(None=전체)의 이벤트를 최신순으로 최대 ``limit``건."""

    picked = events if module is None else [ev for ev in events if str(ev.get("module")) == module]
    return sorted(picked, key=lambda ev: str(ev.get("localtime", "")), reverse=True)[:limit]


_REASON_RULE = {
    "voltage_low_3phase": ("전압", "V", config.VOLTAGE_THRESHOLD,
                           "3상(R·S·T) 전압이 동시에 기준 미만 → 공급 전압 저하 또는 선로 전압강하 가능성"),
    "power_factor_low_3phase": ("역률", "%", config.POWER_FACTOR_THRESHOLD,
                                "3상 역률이 동시에 기준 미만 → 무효전력 비중이 커서 같은 일을 해도 전류·손실 증가"),
}


def anomaly_reasons(events: list[dict[str, Any]], status_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """설비별 '이상이라고 판단한 이유' 근거 수치.

    각 항목: module, type, label, metric, unit, threshold, rule, count, ratio, median, lo, hi, gap,
    peak_hour, peak_hour_share, months[(YYYY-MM, 건수)], first, last.
    events = anomaly_events(5초 측정 행 중 규칙에 걸린 행), status_rows = module_status(이상 비율).
    """

    import pandas as pd

    if not events:
        return []
    df = pd.DataFrame(events)
    if not {"module", "type", "value", "localtime"}.issubset(df.columns):
        return []
    t = pd.to_datetime(df["localtime"].astype(str).str[:19], errors="coerce")
    df = df.assign(t=t, value=pd.to_numeric(df["value"], errors="coerce"))
    ratio = {r["module"]: float(r.get("anomaly_ratio") or 0.0) for r in status_rows}
    out = []
    for (module, raw), g in df.groupby(["module", "type"]):
        metric, unit, thr, rule = _REASON_RULE.get(raw, (type_label(raw), "", None, "규칙 기준 미만"))
        hours = g["t"].dt.hour.value_counts()
        months = g["t"].dt.strftime("%Y-%m").value_counts().sort_index()
        med = float(g["value"].median())
        out.append({
            "module": module, "type": raw, "label": type_label(raw), "metric": metric, "unit": unit,
            "threshold": thr, "rule": rule, "count": int(len(g)), "ratio": ratio.get(module),
            "median": med, "lo": float(g["value"].min()), "hi": float(g["value"].max()),
            "gap": (thr - med) if thr is not None else None,
            "peak_hour": int(hours.idxmax()), "peak_hour_share": float(hours.max() / len(g)),
            "months": list(months.items()),
            "first": g["t"].min(), "last": g["t"].max(),
        })
    return sorted(out, key=lambda r: -(r["ratio"] or 0))



# ---------------------------------------------------------------------------
# 설정값 저장(새로고침·재시작 후에도 유지). 프로젝트 .cache/settings.json (git 제외)
# ---------------------------------------------------------------------------
SETTINGS_FILE = config.PROJECT_ROOT / ".cache" / "settings.json"


def load_saved_settings(path=None) -> dict[str, Any] | None:
    """저장된 설정 중 기본 설정에 있는 키·같은 형식만 돌려준다(없거나 깨지면 None)."""

    import json

    p = path or SETTINGS_FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    base = default_settings()
    out = {}
    for k, v in data.items():
        if k not in base:
            continue
        if v is None:
            continue  # 예전 저장 파일의 '미설정'은 기본값(가정 단가)을 쓰게 둔다
        if isinstance(v, (int, float, bool, str)):
            out[k] = v
    return out or None


def save_settings(settings: dict[str, Any], path=None) -> bool:
    import json

    p = path or SETTINGS_FILE
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        base = default_settings()
        p.write_text(json.dumps({k: settings.get(k) for k in base}, ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return True
    except OSError:
        return False



def load_series(bundle: dict[str, Any], module: str, view: str, start: Any = None, end: Any = None) -> dict[str, Any] | None:
    """설비 부하 패턴 시계열. 최근 1시간(1분 평균) · 최근 24시간(1시간 평균) · 일별(하루 전력량).

    '최근' 기준 = 학습 원본 마지막 측정 시각. 반환: x, y, unit, caption, mean.
    """

    import pandas as pd

    if view == "최근 1시간":
        mf = (bundle.get("minute_recent") or {}).get("frame")
        if mf is None or not len(mf):
            return None
        g = mf[mf["module"] == module].sort_values("minute")
        if not len(g):
            return None
        last = g["minute"].max()
        g = g[g["minute"] > last - pd.Timedelta(minutes=60)]
        y = (g["active_power"] / 1000.0).tolist()
        return {"x": g["minute"].dt.strftime("%H:%M").tolist(), "y": y, "unit": "kW",
                "title": "1분 평균 유효전력", "mean": sum(y) / len(y),
                "caption": f"{module} · {g['minute'].min():%Y-%m-%d %H:%M} – {last:%H:%M} (원본 마지막 1시간, 1분 평균)"}
    ff = (bundle.get("features_hourly") or {}).get("frame")
    if ff is None or not len(ff):
        return None
    g = ff[(ff["module"] == module) & (ff["n"] >= 600)].sort_values("hour")
    if not len(g):
        return None
    if view == "최근 24시간":
        g = g.tail(24)
        y = (g["active_power"] / 1000.0).tolist()  # 1시간 평균 kW = 그 시간 kWh
        return {"x": g["hour"].dt.strftime("%m-%d %H시").tolist(), "y": y, "unit": "kWh",
                "title": "시간별 전력량", "mean": sum(y) / len(y),
                "caption": f"{module} · {g['hour'].min():%Y-%m-%d %H시} – {g['hour'].max():%m-%d %H시} (원본 마지막 24시간)"}
    s, e = to_date(start), to_date(end)
    d = g.assign(day=g["hour"].dt.date)
    if s:
        d = d[d["day"] >= s]
    if e:
        d = d[d["day"] <= e]
    daily = d.groupby("day").agg(kwh=("active_power", lambda v: v.sum() / 1000.0), hours=("active_power", "size"))
    daily = daily[daily["hours"] == 24]
    if not len(daily):
        return None
    y = daily["kwh"].tolist()
    return {"x": [f"{x:%m-%d}" for x in daily.index], "y": y, "unit": "kWh", "title": "일 전력량",
            "mean": sum(y) / len(y),
            "caption": f"{module} · 24시간 모두 측정된 날 {len(y)}일 · 일평균 {sum(y) / len(y):,.1f} kWh"}


def status_for_period(
    status: dict[str, Any],
    events: dict[str, Any],
    features: Any,
    pattern: dict[str, Any] | None,
    start: Any = None,
    end: Any = None,
) -> dict[str, Any]:
    """설비 상태표를 선택 기간(·유형)에 맞춰 다시 계산한다.

    module_status.csv는 전체 기간 값이라, 헤더 기간을 좁히면 상단 '기간 이상 행수'와
    표의 건수 합이 어긋난다. events(이미 기간·유형 필터 적용)로 설비별 건수를,
    module_hourly_features의 측정 행 수(n)로 같은 기간 분모를 구해 비율과 상태를 맞춘다.
    features가 없으면 비율은 건수 비례로 원래 분모를 유지한다.
    """

    if "error" in status or "error" in events:
        return status
    from src.agent import tools as tools_mod

    s, e = to_date(start), to_date(end)
    counts: dict[str, int] = {}
    last: dict[str, str] = {}
    for ev in events.get("events", []):
        m = str(ev.get("module"))
        counts[m] = counts.get(m, 0) + 1
        t = str(ev.get("localtime") or "")
        if t > last.get(m, ""):
            last[m] = t
    denom: dict[str, float] = {}
    if features is not None and len(features):
        f = features
        day = f["hour"].dt.date
        mask = True
        if s:
            mask = day >= s
        if e:
            mask = mask & (day <= e)
        sub = f[mask] if mask is not True else f
        denom = sub.groupby("module")["n"].sum().astype(float).to_dict()
    pat: dict[str, int] = {}
    for ev in (pattern or {}).get("events", []) if pattern and "error" not in pattern else []:
        d = _row_dt(str(ev.get("hour", "")))
        if d is None or not _in_range(d.date(), s, e):
            continue
        pat[ev["module"]] = pat.get(ev["module"], 0) + 1
    rows = []
    for r in status.get("modules", []):
        m = r["module"]
        c = counts.get(m, 0)
        if denom.get(m):
            ratio = c / denom[m]
        else:
            full = int(r.get("anomaly_count") or 0)
            ratio = float(r.get("anomaly_ratio") or 0.0) * (c / full) if full else 0.0
        st_label = r.get("status")
        if config.STATUS_THRESHOLDS_APPROVED:
            st_label = tools_mod.module_status_label(ratio, pat.get(m, 0))
        rows.append({**r, "anomaly_count": c, "anomaly_ratio": ratio,
                     "last_seen": last.get(m, ""), "status": st_label, "pattern_count": pat.get(m, 0)})
    return {**status, "modules": rows}


def window_rows(rows: list[dict[str, Any]], start_dt: datetime, hours: int = 24) -> list[dict[str, Any]]:
    """start_dt부터 ``hours``시간의 예측 행(시간순). 날짜 경계를 넘어갈 수 있다."""

    end_dt = start_dt + timedelta(hours=hours)
    out = [r for r in rows if (m := _row_dt(r.get("id", ""))) is not None and start_dt <= m < end_dt]
    return sorted(out, key=lambda r: r["id"])


# ---------------------------------------------------------------------------
# 이상 6종 — '전체 평균 기준 범위 + 급격한 변화'만 잡는다 (10/16 멘토 피드백: 거짓 양성 축소)
# ---------------------------------------------------------------------------
SUDDEN_RULES = [
    # type, 지표, 단위, 방향(+1 상승, -1 하락, 0 양쪽), 야간 한정
    ("abnormal_pattern", "active_power", "W", 0, False),
    ("night_operation", "active_power", "W", +1, True),
    ("load_imbalance", "current_unbalance", "%", +1, False),
    ("reactive_power_surge", "reactive_power", "var", +1, False),
    ("power_factor_drop", "pf_mean", "%", -1, False),
    ("current_only_increase", "current_mean", "A", +1, False),
]
_SUDDEN_CACHE: dict[tuple, dict[str, Any]] = {}


def detect_sudden(features: Any, *, k: float = 3.5, night_hours: set[int] | None = None) -> dict[str, Any]:
    """설비×시간 특징에서 이상 6종을 탐지한다.

    판정 = 두 조건을 모두 만족하는 시간만:
      ① 범위 이탈: 그 설비 전체 기간 평균에서 ±k·표준편차 밖
      ② 급격한 변화: 직전 시간 대비 변화량이 그 설비 변화량 분포의 ±k·표준편차 밖
    전류만 증가 = 평균 전류가 위 두 조건(상승)이고 같은 시간 유효전력 z < 1.
    반환: {"count", "events": [...], "k"} (events: module, hour, type, metric, value, baseline, z, jump_z, unit, rule)
    """

    import numpy as np
    import pandas as pd

    if features is None or not len(features):
        return {"error": "no_features"}
    night = night_hours if night_hours is not None else {22, 23, 0, 1, 2, 3, 4, 5}
    key = (id(features), len(features), float(k), tuple(sorted(night)))
    if key in _SUDDEN_CACHE:
        return _SUDDEN_CACHE[key]
    f = features[features["n"] >= 600].sort_values(["module", "hour"]).copy()
    if "current_mean" not in f.columns:
        f["current_mean"] = f[["current_r", "current_s", "current_t"]].mean(axis=1)
    g = f.groupby("module")
    z, zd, mean = {}, {}, {}
    for col in {r[1] for r in SUDDEN_RULES}:
        mu, sd = g[col].transform("mean"), g[col].transform("std")
        mean[col] = mu
        z[col] = (f[col] - mu) / sd.replace(0, np.nan)
        d = g[col].diff()
        dg = d.groupby(f["module"])
        zd[col] = (d - dg.transform("mean")) / dg.transform("std").replace(0, np.nan)
    hours = f["hour"].dt.hour
    events = []
    for typ, col, unit, sign, night_only in SUDDEN_RULES:
        a, b = z[col], zd[col]
        if sign > 0:
            m = (a >= k) & (b >= k)
        elif sign < 0:
            m = (a <= -k) & (b <= -k)
        else:
            m = (a.abs() >= k) & (b.abs() >= k)
        if night_only:
            # 야간은 가동이 늘면 바로 비용이라 0.5σ 낮춰 본다. 비정상 패턴으로 이미 잡힌 시간은 제외
            abn = (z["active_power"].abs() >= k) & (zd["active_power"].abs() >= k)
            m = hours.isin(night) & (a >= k - 0.5) & (b >= k - 0.5) & ~abn
        if typ == "current_only_increase":
            m &= z["active_power"] < 1
        rule = (f"전체 평균 {'+' if sign > 0 else '−' if sign < 0 else '±'}{k:g}σ 밖 + 직전 시간 대비 급변"
                + (" (야간)" if night_only else ""))
        for idx in f.index[m.fillna(False)]:
            events.append({
                "module": f.at[idx, "module"],
                "hour": f.at[idx, "hour"].strftime("%Y-%m-%d %H:%M:%S"),
                "type": typ, "metric": col, "unit": unit,
                "value": float(f.at[idx, col]), "baseline": float(mean[col][idx]),
                "z": float(a[idx]), "jump_z": float(b[idx]), "rule": rule,
            })
    events.sort(key=lambda e: (e["hour"], e["module"]))
    out = {"count": len(events), "events": events, "k": float(k)}
    if len(_SUDDEN_CACHE) > 8:
        _SUDDEN_CACHE.clear()
    _SUDDEN_CACHE[key] = out
    return out
