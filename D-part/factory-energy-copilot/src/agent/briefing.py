"""Structured briefings (rule-based + optional LLM narration) for the AI Copilot.

A briefing is data, not a string: ``Briefing`` holds headline numbers, 5~7 lines
(label + one sentence), watch modules, limits and the Python calc table. The UI
renders the same card for rule and LLM modes (``mode`` differs only in a chip).

Rules kept from the project spec:
- numbers come only from the artifacts-only tools and are formatted by ``fmt``;
- no 단정 (고장 / 위험 확정 / 실시간); anomalies are *관찰 기록* with 확인 항목 (C4);
- the LLM may only rephrase ``text`` — every number it uses must already exist in
  the rule briefing, otherwise the rule briefing is used (C2, C5).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from config import config
from src.agent import tools as tools_mod
from src.app.ui import fmt

logger = logging.getLogger("factory_energy_copilot.briefing")

EXECUTIVE = "executive"
ENERGY = "energy"
FACILITY = "facility"
AUDIENCE_ALIASES = {"field": FACILITY}  # legacy agent key -> REQ-USER role key
AUDIENCE_LABELS = {
    EXECUTIVE: "경영진 5줄",
    ENERGY: "에너지 관리 담당자",
    FACILITY: "설비 관리 담당자",
}
NO_DATA = tools_mod.NO_DATA
FORBIDDEN = ("고장", "위험 확정", "실시간", "LIVE", "Fault")
WEEKDAYS = "월화수목금토일"

AVP_NAME = "actual_vs_pred.csv"
SELECTION_NAME = "model_selection.json"


@dataclass
class BriefLine:
    label: str
    text: str
    tone: str = "neutral"  # neutral | watch | alert
    sources: list[str] = field(default_factory=list)
    chips: list[tuple[str, str]] = field(default_factory=list)  # (설비, 이상 비율)


@dataclass
class Briefing:
    audience: str
    period_label: str
    mode: str  # "rule" | "llm"
    headline: list[tuple[str, str]]
    lines: list[BriefLine]
    watch_modules: list[tuple[str, str]]
    limits: list[str]
    calc: list[tuple[str, str, str]] = field(default_factory=list)  # (라벨, 값, 출처)

    @property
    def title(self) -> str:
        return {EXECUTIVE: "경영진 브리핑", ENERGY: "에너지 관리 담당자 브리핑",
                FACILITY: "설비 관리 담당자 브리핑"}.get(self.audience, "브리핑")


def normalize_audience(audience: str) -> str:
    audience = AUDIENCE_ALIASES.get(audience, audience)
    return audience if audience in AUDIENCE_LABELS else EXECUTIVE


# ---------------------------------------------------------------------------
# Artifact readers not covered by the five tools (still artifacts-only)
# ---------------------------------------------------------------------------
def _read_json(name: str, artifacts_dir: Path | None) -> dict[str, Any] | None:
    try:
        path = tools_mod.resolve_artifact_path(name, artifacts_dir=artifacts_dir)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (tools_mod.PathSecurityError, OSError, json.JSONDecodeError):
        return None


def _read_avp(artifacts_dir: Path | None) -> list[dict[str, Any]] | None:
    import csv

    try:
        path = tools_mod.resolve_artifact_path(AVP_NAME, artifacts_dir=artifacts_dir)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = []
            for r in csv.DictReader(handle):
                rows.append(
                    {
                        "id": r["id"],
                        "actual": float(r["actual_pow"]),
                        "low": float(r["pred_low"]) if r.get("pred_low") else None,
                        "high": float(r["pred_high"]) if r.get("pred_high") else None,
                    }
                )
            return rows
    except (tools_mod.PathSecurityError, OSError, KeyError, ValueError):
        return None


def _day(text: str) -> date | None:
    try:
        return datetime.strptime(str(text)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _in_range(row_id: str, start: str | None, end: str | None) -> bool:
    d = _day(row_id)
    if d is None:
        return False
    s, e = _day(start) if start else None, _day(end) if end else None
    return (s is None or d >= s) and (e is None or d <= e)


# ---------------------------------------------------------------------------
# Rule-based briefing
# ---------------------------------------------------------------------------
class _Calc:
    """Collects calc-table rows so the table always matches the text."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, label: str, value: str, source: str) -> str:
        self.rows.append((label, value, source))
        return value


def watch_module_rows(status: dict[str, Any], limit: int) -> list[tuple[str, float, str]]:
    """Top modules for the 'watch' line: (module, ratio, last_seen).

    O-3 unapproved -> modules ranked by anomaly ratio (no 주의/이상 verdict).
    Zero-count modules are never listed; the 13-module dump is forbidden.
    """

    if "error" in status:
        return []
    rows = [r for r in status["modules"] if int(r.get("anomaly_count", 0) or 0) > 0]
    if config.STATUS_THRESHOLDS_APPROVED:
        rows = [r for r in rows if r.get("status") in ("주의", "이상")]
    rows.sort(key=lambda r: -float(r.get("anomaly_ratio", 0.0) or 0.0))
    return [(r["module"], float(r["anomaly_ratio"]), r.get("last_seen", "")) for r in rows[:limit]]


def _recent_surge(trend: dict[str, Any]) -> list[tuple[str, float]]:
    """Modules whose last-7-day anomaly count is >= 1.5x their typical 7-day count."""

    if "error" in trend or not trend.get("points"):
        return []
    per: dict[str, dict[str, int]] = {}
    for p in trend["points"]:
        day = str(p["bucket"])[:10]
        per.setdefault(p["module"], {})
        per[p["module"]][day] = per[p["module"]].get(day, 0) + int(p["count"])
    all_days = sorted({d for m in per.values() for d in m})
    if len(all_days) < 14:
        return []
    last7 = set(all_days[-7:])
    out: list[tuple[str, float]] = []
    for module, days in per.items():
        recent = sum(c for d, c in days.items() if d in last7)
        earlier = [c for d, c in days.items() if d not in last7]
        weeks = max(1.0, len(all_days[:-7]) / 7.0)
        typical = sum(earlier) / weeks
        if recent > 0 and typical > 0 and recent >= 1.5 * typical:
            out.append((module, recent / typical))
    return sorted(out, key=lambda x: -x[1])[:3]


def build_briefing(
    audience: str = EXECUTIVE,
    start: str | None = None,
    end: str | None = None,
    *,
    artifacts_dir: Path | None = None,
) -> Briefing:
    audience = normalize_audience(audience)
    forecast = tools_mod.get_forecast_summary(artifacts_dir=artifacts_dir)
    evaluation = tools_mod.get_evaluation(artifacts_dir=artifacts_dir)
    status = tools_mod.get_module_status(artifacts_dir=artifacts_dir)
    trend = tools_mod.get_anomaly_trend(artifacts_dir=artifacts_dir)
    events = tools_mod.get_anomaly_events(artifacts_dir=artifacts_dir)
    selection = _read_json(SELECTION_NAME, artifacts_dir)
    avp = _read_avp(artifacts_dir)

    calc = _Calc()
    f_ok = "error" not in forecast and forecast.get("count", 0) > 0
    rows = [r for r in forecast.get("rows", []) if _in_range(r["id"], start, end)] if f_ok else []
    f_ok = f_ok and bool(rows)
    PRED, EVAL, STAT, EV, HOURLY = (
        "predictions.csv", "evaluation.json", "module_status.csv",
        "anomaly_events.parquet", "anomaly_hourly.parquet",
    )

    # -- forecast numbers ---------------------------------------------------
    if f_ok:
        p_start, p_end = rows[0]["id"], rows[-1]["id"]
        period_label = calc.add("기간", fmt.fmt_period(p_start, p_end), PRED)
        tot_pow = sum(float(r["pred_pow"]) for r in rows)
        tot_bill = sum(float(r["pred_bill"]) for r in rows)
        tot_carbon = sum(float(r["pred_carbon"]) for r in rows)
        avg_pow = tot_pow / len(rows)
        pow_s = calc.add("예측 전력량 합", fmt.fmt_pow_short(tot_pow), PRED)
        bill_s = calc.add("예측 요금 합", fmt.fmt_krw_short(tot_bill), PRED)
        carbon_s = calc.add("예측 탄소 합", fmt.fmt_carbon_short(tot_carbon), PRED)
        avg_s = calc.add("평균 시간당 전력", fmt.fmt_pow_short(avg_pow), PRED)
    else:
        period_label = NO_DATA
        pow_s = bill_s = carbon_s = avg_s = NO_DATA
        avg_pow = 0.0

    mae_block = evaluation.get("mae_hourly_pow") if "error" not in evaluation else None
    if isinstance(mae_block, dict) and mae_block.get("mae504") is not None:
        mae_v = float(mae_block["mae504"])
        mae_s = calc.add("MAE(504)", fmt.fmt_mae(mae_v), EVAL)
        err_s = (
            calc.add("MAE 오차율", fmt.fmt_pct(mae_v / (avg_pow * config.WH_PER_KWH), decimals=2), f"{EVAL}·{PRED}")
            if f_ok and avg_pow
            else NO_DATA
        )
    else:
        mae_s = err_s = NO_DATA

    # -- interval exits -----------------------------------------------------
    k = None
    if avp is not None:
        k = 0
        for r in avp:
            if not _in_range(r["id"], start, end) or r["low"] is None or r["high"] is None:
                continue
            if r["actual"] < r["low"] or r["actual"] > r["high"]:
                k += 1
        k_s = calc.add("예측구간 이탈 시간", f"{k}시간", AVP_NAME)
    else:
        k_s = NO_DATA

    # -- anomaly numbers ----------------------------------------------------
    limit = 3 if audience in (EXECUTIVE, ENERGY) else 5
    watch = watch_module_rows(status, limit)
    watch_chips = [(m, fmt.fmt_ratio_pct(ratio)) for m, ratio, _ in watch]
    for m, ratio_text in watch_chips:
        calc.add(f"이상 비율 {m}", ratio_text, STAT)
    events_ok = "error" not in events

    lines: list[BriefLine] = []
    headline: list[tuple[str, str]]

    def watch_line(label: str = "이상 비율 상위") -> BriefLine:
        if "error" in status:
            return BriefLine(label, NO_DATA, sources=[STAT])
        if not watch:
            return BriefLine(label, "이상 건수가 기록된 설비는 없습니다.", sources=[STAT])
        return BriefLine(label, "설비별 이상 비율입니다 (관찰 기록, 설비 판정 아님).",
                         tone="watch", sources=[STAT], chips=watch_chips)

    def interval_line(label: str = "확인 필요") -> BriefLine:
        if k is None:
            return BriefLine(label, NO_DATA, sources=[AVP_NAME])
        if k == 0:
            return BriefLine(label, "예측구간을 벗어난 시간은 없습니다.", sources=[AVP_NAME])
        return BriefLine(label, f"예측구간 이탈 {k_s} — 해당 시간대 운전 기록 확인이 필요합니다.",
                         tone="watch", sources=[AVP_NAME])

    if audience == EXECUTIVE:
        headline = [("예측 요금", bill_s), ("예측 탄소", carbon_s), ("MAE", mae_s)]
        lines = [
            BriefLine("예측 요금", f"{period_label} 공장 전체 예측 요금은 {bill_s}입니다." if f_ok else NO_DATA, sources=[PRED]),
            BriefLine("예측 탄소", f"예측 탄소배출량은 {carbon_s}(정답 척도)입니다." if f_ok else NO_DATA, sources=[PRED]),
            BriefLine(
                "예측 정확도",
                f"시간당 오차(MAE)는 {mae_s}로, 평균 전력의 {err_s} 수준입니다." if err_s != NO_DATA else
                (f"시간당 오차(MAE)는 {mae_s}입니다." if mae_s != NO_DATA else NO_DATA),
                sources=[EVAL, PRED],
            ),
            watch_line(),
            interval_line(),
        ]
    elif audience == ENERGY:
        headline = [("예측 전력량", pow_s), ("예측 요금", bill_s), ("예측 탄소", carbon_s)]
        lines.append(BriefLine("예측 요금·탄소", f"{period_label} 예측 요금은 {bill_s}, 탄소는 {carbon_s}(정답 척도)입니다." if f_ok else NO_DATA, sources=[PRED]))
        if f_ok:
            peak = max(rows, key=lambda r: float(r["pred_bill"]))
            pd_ = _day(peak["id"])
            peak_s = calc.add(
                "요금 최고 예측 시간대",
                f"{WEEKDAYS[pd_.weekday()]}요일 {int(peak['id'][11:13])}시" if pd_ else NO_DATA, PRED)
            lines.append(BriefLine("요금 최고 시간대", f"예측 요금이 가장 높은 시간대는 {peak_s}입니다.", sources=[PRED]))
            by_day: dict[str, list[float]] = {}
            for r in rows:
                by_day.setdefault(r["id"][:10], []).append(float(r["pred_bill"]))
            full = {d: sum(v) for d, v in by_day.items() if len(v) == 24}
            if len(full) >= 2:
                hi = max(full, key=full.get)
                lo = min(full, key=full.get)
                hi_s = calc.add("최고 예측일", f"{hi[5:]} {fmt.fmt_krw_short(full[hi])}", PRED)
                lo_s = calc.add("최저 예측일", f"{lo[5:]} {fmt.fmt_krw_short(full[lo])}", PRED)
                lines.append(BriefLine("일별 최고·최저", f"예측 요금이 가장 높은 날은 {hi_s}, 가장 낮은 날은 {lo_s}입니다.", sources=[PRED]))
            else:
                lines.append(BriefLine("일별 최고·최저", NO_DATA, sources=[PRED]))
        else:
            lines.append(BriefLine("요금 최고 시간대", NO_DATA, sources=[PRED]))
            lines.append(BriefLine("일별 최고·최저", NO_DATA, sources=[PRED]))
        lines.append(interval_line("구간 이탈"))
        # selected model vs best baseline (selection json + evaluation baseline table)
        base = [r for r in evaluation.get("baseline_comparison", []) if r.get("kind") == "baseline"] if "error" not in evaluation else []
        sel_name = (selection or {}).get("selected_model")
        sel_mae = (selection or {}).get("selection_score_validation_mae")
        if base and sel_name and sel_mae is not None:
            best = min(base, key=lambda r: r["validation_mae"])
            diff = abs(float(sel_mae) - float(best["validation_mae"]))
            sel_s = calc.add("선택 모델 검증 MAE", fmt.fmt_mae(sel_mae), SELECTION_NAME)
            best_s = calc.add("최고 베이스라인 검증 MAE", fmt.fmt_mae(best["validation_mae"]), EVAL)
            diff_s = calc.add("MAE 차이", fmt.fmt_mae(diff), f"{SELECTION_NAME}·{EVAL}")
            if float(sel_mae) < float(best["validation_mae"]):
                txt = f"선택 모델({sel_name}) {sel_s}, 최고 베이스라인({best['model']}) {best_s} 대비 {diff_s} 낮음."
            else:
                txt = (f"선택 모델({sel_name}) {sel_s}, 최고 베이스라인({best['model']}) {best_s}와 "
                       f"비슷한 수준(차이 {diff_s}).")
            lines.append(BriefLine("모델 비교", txt, sources=[SELECTION_NAME, EVAL]))
        else:
            lines.append(BriefLine("모델 비교", NO_DATA, sources=[SELECTION_NAME]))
        lines.append(BriefLine("예측 정확도", f"시간당 오차(MAE) {mae_s}, 평균 전력의 {err_s}입니다." if err_s != NO_DATA else NO_DATA, sources=[EVAL, PRED]))
    else:  # FACILITY
        ev_total = int(events.get("count", 0)) if events_ok else None
        ev_s = calc.add("기간 이상 행수", fmt.fmt_count(ev_total), EV) if ev_total is not None else NO_DATA
        n_mod = len([r for r in status["modules"] if int(r.get("anomaly_count", 0) or 0) > 0]) if "error" not in status else None
        mod_s = calc.add("이상 기록 설비 수", f"{n_mod}대", STAT) if n_mod is not None else NO_DATA
        headline = [("기간 이상 행수", ev_s), ("이상 기록 설비", mod_s), ("예측구간 이탈", k_s)]
        if watch:
            lines.append(BriefLine("이상 비율 상위", "설비별 이상 비율과 최근 관측입니다 (관찰 기록).", tone="watch",
                                   sources=[STAT], chips=watch_chips))
            recent = []
            for m, _, last in watch[:3]:
                recent.append(f"{m} {calc.add('최근 관측 ' + m, fmt.fmt_time(last), STAT)}")
            lines.append(BriefLine("최근 발생", " · ".join(recent), sources=[STAT]))
        else:
            lines.append(watch_line())
            lines.append(BriefLine("최근 발생", NO_DATA if "error" in status else "최근 관측된 이상 기록이 없습니다.", sources=[STAT]))
        if events_ok:
            tcount: dict[str, int] = {}
            for ev in events.get("events", []):
                tcount[ev["type"]] = tcount.get(ev["type"], 0) + 1
            parts = [f"{config.ANOMALY_TYPE_LABELS.get(t, t)} {calc.add('유형 ' + t, fmt.fmt_count(c) + '건', EV)}"
                     for t, c in sorted(tcount.items(), key=lambda kv: -kv[1])]
            lines.append(BriefLine("유형별 건수", " · ".join(parts) if parts else "기록된 이상 유형이 없습니다.", sources=[EV]))
        else:
            lines.append(BriefLine("유형별 건수", NO_DATA, sources=[EV]))
        surge = _recent_surge(trend)
        if "error" in trend:
            lines.append(BriefLine("최근 7일", NO_DATA, sources=[HOURLY]))
        elif surge:
            txt = " · ".join(f"{m} 평소 대비 {calc.add('급증 ' + m, f'{r:.1f}배', HOURLY)}" for m, r in surge)
            lines.append(BriefLine("최근 7일", txt, tone="watch", sources=[HOURLY]))
        else:
            lines.append(BriefLine("최근 7일", "평소 대비 급증한 설비는 없습니다.", sources=[HOURLY]))
        top = watch[0][0] if watch else "상위 설비"
        lines.append(BriefLine(
            "현장 확인",
            f"{top}의 전압·역률 기록과 부하 상태를 현장에서 확인해 주세요(확인 필요, 원인 단정 아님).",
            tone="watch", sources=[STAT]))
        v_s = calc.add("전압 임계값", f"{config.VOLTAGE_THRESHOLD:g}V", "config")
        pf_s = calc.add("역률 임계값", f"{config.POWER_FACTOR_THRESHOLD:g}%", "config")
        lines.append(BriefLine(
            "현장 확인",
            f"전압 {v_s} 미만 또는 역률 {pf_s} 미만이 반복된 시간대의 운전 상황을 확인해 주세요.",
            tone="watch", sources=[EV]))
        lines.append(interval_line("구간 이탈"))

    limits = [
        "예측은 공장 전체 단위이며 설비별 예측은 아닙니다.",
        "이상 탐지는 학습 원본(12/1~4/29) 기준이며 5월 탐지가 아닙니다.",
    ]
    if config.UNIT_FOOTNOTE:
        limits.append("전력량 단위 미확인 — 요금·탄소는 정답 파일과 같은 척도입니다.")

    sources_used: list[str] = []
    for ln in lines:
        for s in ln.sources:
            if s not in sources_used:
                sources_used.append(s)

    return Briefing(
        audience=audience,
        period_label=period_label,
        mode="rule",
        headline=headline,
        lines=lines,
        watch_modules=watch_chips,
        limits=limits,
        calc=calc.rows,
    )


# ---------------------------------------------------------------------------
# Rendering helpers (plain text / markdown for copy and download)
# ---------------------------------------------------------------------------
_CIRCLED = "①②③④⑤⑥⑦⑧"


def to_plaintext(b: Briefing) -> str:
    out = [f"{b.title} · {b.period_label}"]
    out.append("  ".join(f"{k} {v}" for k, v in b.headline))
    for i, ln in enumerate(b.lines):
        extra = (" " + ", ".join(f"{m} {r}" for m, r in ln.chips)) if ln.chips else ""
        out.append(f"{_CIRCLED[i]} {ln.label}: {ln.text}{extra}")
    out.append("분석 한계: " + " / ".join(b.limits))
    srcs = sorted({s for ln in b.lines for s in ln.sources})
    if srcs:
        out.append("출처: " + ", ".join(srcs))
    return "\n".join(out)


def to_markdown(b: Briefing) -> str:
    out = [f"# {b.title}", f"기간: {b.period_label} · 생성 방식: {'AI 생성' if b.mode == 'llm' else '규칙 기반'}", ""]
    out.append("| " + " | ".join(k for k, _ in b.headline) + " |")
    out.append("|" + "---|" * len(b.headline))
    out.append("| " + " | ".join(v for _, v in b.headline) + " |")
    out.append("")
    for i, ln in enumerate(b.lines, 1):
        extra = (" — " + ", ".join(f"{m} {r}" for m, r in ln.chips)) if ln.chips else ""
        out.append(f"{i}. **{ln.label}** {ln.text}{extra}")
    out += ["", "## 분석 한계"] + [f"- {x}" for x in b.limits]
    out += ["", "## Python 계산값", "| 라벨 | 값 | 출처 |", "|---|---|---|"]
    out += [f"| {a} | {v} | {s} |" for a, v, s in b.calc]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Optional LLM narration with strict validation (falls back to the rule briefing)
# ---------------------------------------------------------------------------
_NUM = re.compile(r"\d[\d,]*\.?\d*")


def _numbers(text: str) -> set[str]:
    return {m.rstrip(".,") for m in _NUM.findall(text)}


def _all_text(b: Briefing) -> str:
    parts = [b.period_label] + [v for _, v in b.headline] + [ln.text for ln in b.lines]
    parts += [f"{m} {r}" for m, r in b.watch_modules] + b.limits + [v for _, v, _ in b.calc]
    return " ".join(parts)


def validate_llm_lines(rule: Briefing, payload: Any) -> list[BriefLine] | None:
    """Return validated LLM lines or None. Labels fixed; numbers must pre-exist."""

    if not isinstance(payload, dict) or not isinstance(payload.get("lines"), list):
        return None
    items = payload["lines"]
    if len(items) != len(rule.lines):
        return None
    allowed = _numbers(_all_text(rule))
    out: list[BriefLine] = []
    for base, item in zip(rule.lines, items):
        if not isinstance(item, dict) or item.get("label") != base.label:
            return None
        text = item.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 120:
            return None
        if any(w in text for w in FORBIDDEN):
            return None
        if not _numbers(text) <= allowed:
            return None
        out.append(BriefLine(base.label, text.strip(), base.tone, list(base.sources), list(base.chips)))
    return out


def _openai_json(system: str, user: str) -> dict[str, Any] | None:  # pragma: no cover - network
    from openai import OpenAI

    client = OpenAI(api_key=config.get_openai_api_key())
    resp = client.chat.completions.create(
        model=config.get_openai_model(),
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return json.loads(resp.choices[0].message.content or "{}")


def narrate_with_llm(rule: Briefing, call: Callable[[str, str], dict[str, Any] | None] | None = None) -> Briefing:
    """Ask the LLM to rephrase each line's ``text`` as JSON; validate; else keep rule."""

    if call is None:
        if not config.has_openai_key():
            return rule
        call = _openai_json
    system = (
        "너는 공장 에너지 브리핑 편집자다. 입력 JSON의 각 줄 text를 더 자연스러운 한국어 한 문장으로 "
        "다시 쓴다. label은 그대로 두고, 숫자는 입력에 있는 문자열을 한 글자도 바꾸지 말고 그대로 쓴다. "
        "새 숫자를 만들지 않는다. '고장', '위험 확정', '실시간' 같은 단정 표현은 쓰지 않는다. "
        '출력은 {"lines":[{"label":...,"text":...}, ...]} JSON만, 줄 수와 순서는 입력과 같게.'
    )
    user = json.dumps(
        {"lines": [{"label": ln.label, "text": ln.text} for ln in rule.lines]}, ensure_ascii=False
    )
    try:
        payload = call(system, user)
    except Exception:  # noqa: BLE001 - any failure -> rule briefing (C5)
        logger.warning("llm briefing failed; using rule briefing")
        return rule
    lines = validate_llm_lines(rule, payload)
    if lines is None:
        logger.warning("llm briefing rejected by validation; using rule briefing")
        return rule
    return Briefing(
        audience=rule.audience, period_label=rule.period_label, mode="llm",
        headline=rule.headline, lines=lines, watch_modules=rule.watch_modules,
        limits=rule.limits, calc=rule.calc,
    )
