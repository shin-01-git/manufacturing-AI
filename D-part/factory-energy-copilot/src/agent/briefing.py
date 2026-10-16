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


def tools_mod_type_label(raw: str) -> str:
    return config.ANOMALY_TYPE_LABELS.get(raw, raw)


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
    if config.STATUS_THRESHOLDS_APPROVED:
        # 10/16: 상태 = 급변 탐지 건수. 이상 → 주의 순, 건수 많은 순 (ratio 자리에 건수)
        rows = [r for r in status["modules"] if r.get("status") in ("주의", "이상")]
        rows.sort(key=lambda r: (r.get("status") != "이상", -int(r.get("pattern_count", 0) or 0)))
        return [(r["module"], float(r.get("pattern_count", 0) or 0), r.get("last_seen", "")) for r in rows[:limit]]
    rows = [r for r in status["modules"] if int(r.get("anomaly_count", 0) or 0) > 0]
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


HOURLY_FULL = "hourly_from_full.parquet"


def _pct_s(v: float | None) -> str:
    return NO_DATA if v is None else f"{v:+.2f}%"


def _executive_month(artifacts_dir: Path | None) -> dict[str, Any] | None:
    """경영진 증감: 대시보드 월간 경영 보고와 같은 계산(page_logic.executive_monthly)을 쓴다."""

    try:
        import pandas as pd

        from src.app import page_logic

        path = tools_mod.resolve_artifact_path(HOURLY_FULL, artifacts_dir=artifacts_dir)
        if not path.exists():
            return None
        frame = pd.read_parquet(path)
        hour = pd.to_datetime(frame["hour"])
        if getattr(hour.dt, "tz", None) is not None:
            hour = hour.dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
        frame = frame.assign(hour=hour, hourly_pow=frame["hourly_pow"] / config.WH_PER_KWH)
        r = page_logic.executive_monthly(frame, None, page_logic.default_settings())
        return r if r.get("status") == "ok" and r.get("change") else None
    except Exception:  # noqa: BLE001 - 브리핑은 실패해도 '데이터에 없음'으로 계속
        return None


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
    selection = tools_mod.get_selection(artifacts_dir=artifacts_dir)
    if "error" in selection:
        selection = None
    pattern = tools_mod.get_pattern_anomalies(artifacts_dir=artifacts_dir)

    calc = _Calc()
    f_ok = "error" not in forecast and forecast.get("count", 0) > 0
    rows = [r for r in forecast.get("rows", []) if _in_range(r["id"], start, end)] if f_ok else []
    f_ok = f_ok and bool(rows)
    PRED, EVAL, STAT, EV, HOURLY = (
        tools_mod.active_artifact(tools_mod.PREDICTIONS_NAME, tools_mod.BPART_FORECAST_NAME, artifacts_dir=artifacts_dir),
        tools_mod.active_artifact(tools_mod.EVALUATION_NAME, tools_mod.BPART_EVALUATION_NAME, artifacts_dir=artifacts_dir),
        "module_status.csv", "anomaly_events.parquet", "anomaly_hourly.parquet",
    )
    PAT = tools_mod.PATTERN_NAME

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
        carbon_s = calc.add("예측 탄소 합", f"{fmt.fmt_big(tot_carbon)} kgCO₂", PRED)
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

    # -- 이상 6종(패턴) -------------------------------------------------------
    p_ok = "error" not in pattern
    if p_ok:
        from collections import Counter

        by_type = Counter(tools_mod_type_label(e["type"]) for e in pattern["events"])
        k = pattern["count"]
        k_s = calc.add("이상 6종 탐지", f"{k}건", PAT)
        k_detail = " · ".join(
            f"{t} {calc.add(f'이상 6종 {t}', str(n), PAT)}" for t, n in by_type.most_common()
        )
    else:
        k, k_s, k_detail = None, NO_DATA, ""

    # -- anomaly numbers ----------------------------------------------------
    limit = 3 if audience in (EXECUTIVE, ENERGY) else 5
    watch = watch_module_rows(status, limit)
    approved = bool(config.STATUS_THRESHOLDS_APPROVED)
    watch_chips = [(m, f"급변 {int(v)}건" if approved else fmt.fmt_ratio_pct(v)) for m, v, _ in watch]
    for m, ratio_text in watch_chips:
        calc.add(f"{'급변 탐지' if approved else '이상 비율'} {m}", ratio_text, STAT)
    events_ok = "error" not in events

    lines: list[BriefLine] = []
    headline: list[tuple[str, str]]

    def watch_line(label: str = "이상 비율 상위") -> BriefLine:
        if "error" in status:
            return BriefLine(label, NO_DATA, sources=[STAT])
        if not watch:
            return BriefLine(label, "점검이 필요한 설비는 없습니다.", sources=[STAT])
        if approved:
            return BriefLine(label, "급격한 변화가 반복된 설비입니다 (이상·주의, 원인 단정 아님).",
                             tone="watch", sources=[STAT, PAT], chips=watch_chips)
        return BriefLine(label, "설비별 이상 비율입니다 (관찰 기록, 설비 판정 아님).",
                         tone="watch", sources=[STAT], chips=watch_chips)

    def interval_line(label: str = "이상 6종") -> BriefLine:
        if k is None:
            return BriefLine(label, NO_DATA, sources=[PAT])
        if k == 0:
            return BriefLine(label, "평소 패턴을 벗어난 시간은 없습니다.", sources=[PAT])
        return BriefLine(label, f"평소 패턴을 벗어난 시간 {k_s} ({k_detail}) — 해당 시각 운전 기록 확인 대상입니다.",
                         tone="watch", sources=[PAT])

    if audience == EXECUTIVE:
        # 기획서 7(AI 역할 3): 경영진 = 총 비용 · 증감 · 주요 위험, 5줄 이내 + 기준·한계
        ex = _executive_month(artifacts_dir)
        if ex:
            mom_s = calc.add(f"{ex['month']} 일평균 전월 대비", _pct_s(ex["change"]["daily_pct"]), HOURLY_FULL)
            tot_s = calc.add(f"{ex['month']} 총량 전월 대비", _pct_s(ex["change"]["energy_pct"]), HOURLY_FULL)
            cand = ex["reduction_candidates"][:3]
            calc.add("탄소 배출 상위 3", ", ".join(cand), HOURLY_FULL)
            mom_line = BriefLine(
                "증감",
                f"최근 실측 월({ex['month']}) 일평균 사용량은 전월 대비 {mom_s}"
                + ("로 변화가 거의 없습니다." if abs(ex["change"]["daily_pct"] or 0) < 0.5 else "입니다.")
                + f" 총량 {tot_s}는 날짜 수 차이({ex['current']['days']:.0f}일)를 포함합니다.",
                sources=[HOURLY_FULL],
            )
            act_text = f"탄소 배출 상위 {cand[0]} 등 {len(cand)}개 설비를 감축 검토 대상으로"
        else:
            mom_s = NO_DATA
            mom_line = BriefLine("증감", NO_DATA, sources=[HOURLY_FULL])
            act_text = "감축 검토 대상은 데이터에 없음"
        n_watch = (sum(1 for r in status["modules"] if r.get("status") in ("주의", "이상"))
                   if "error" not in status and config.STATUS_THRESHOLDS_APPROVED else len(watch))
        n_watch_s = calc.add("점검 설비 수", f"{n_watch}대", STAT) if "error" not in status else NO_DATA
        headline = [("총 비용", bill_s), ("전월 대비(일평균)", mom_s), ("점검 설비", n_watch_s)]
        risk = watch_line("주요 위험")
        if watch:
            risk = BriefLine("주요 위험", "이상 징후가 반복 관찰되는 설비입니다 (관찰 기록, 설비 판정 아님).",
                             tone="watch", sources=[STAT], chips=watch_chips)
        lines = [
            BriefLine("총 비용", f"{period_label} 공장 전체 예측 요금은 {bill_s}, 탄소는 {carbon_s}입니다." if f_ok else NO_DATA, sources=[PRED]),
            mom_line,
            risk,
            BriefLine("우선 조치", act_text + (", 위 이상 징후 설비는 현장 확인을 권합니다." if watch else "합니다."),
                      tone="watch" if (ex or watch) else "neutral", sources=[HOURLY_FULL, STAT]),
            BriefLine("기준·한계", "학습 원본(12/1~4/29) 실측과 5월 예측 기준(kWh·원·kgCO₂)이며, 이상은 관찰 기록입니다.",
                      sources=[PRED, HOURLY_FULL]),
        ]
    elif audience == ENERGY:
        headline = [("예측 전력량", pow_s), ("예측 요금", bill_s), ("예측 탄소", carbon_s)]
        lines.append(BriefLine("예측 요금·탄소", f"{period_label} 예측 요금은 {bill_s}, 탄소는 {carbon_s}입니다." if f_ok else NO_DATA, sources=[PRED]))
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
        lines.append(interval_line("이상 6종"))
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
    else:  # FACILITY — 10/16 피드백: 급변(이상 6종) 기준, 5초 기준 미달 원본은 쓰지 않음
        rows_s = status.get("modules", []) if "error" not in status else []
        n_alert = sum(1 for r in rows_s if r.get("status") == "이상")
        n_watch_f = sum(1 for r in rows_s if r.get("status") == "주의")
        a_s = calc.add("이상 설비", f"{n_alert}대", STAT) if rows_s else NO_DATA
        w_s = calc.add("주의 설비", f"{n_watch_f}대", STAT) if rows_s else NO_DATA
        headline = [("이상 6종 탐지", k_s), ("이상 설비", a_s), ("주의 설비", w_s)]
        lines.append(watch_line("점검 우선"))
        lines.append(BriefLine("유형별 건수", k_detail if p_ok and k_detail else NO_DATA, sources=[PAT]))
        if p_ok and pattern["events"]:
            recent = sorted(pattern["events"], key=lambda e: str(e["hour"]), reverse=True)[:3]
            txt = " · ".join(
                f"{e['module']} {tools_mod_type_label(e['type'])} {calc.add('탐지 시각 ' + e['module'] + str(i), str(e['hour'])[5:13] + '시', PAT)}"
                for i, e in enumerate(recent))
            lines.append(BriefLine("최근 탐지", txt, sources=[PAT]))
        else:
            lines.append(BriefLine("최근 탐지", NO_DATA if not p_ok else "탐지된 급변이 없습니다.", sources=[PAT]))
        top = watch[0][0] if watch else "점검 우선 설비"
        lines.append(BriefLine(
            "현장 확인",
            f"{top}의 탐지 시각 운전 일지와 상별 전류·역률 기록을 대조해 주세요(확인 필요, 원인 단정 아님).",
            tone="watch", sources=[PAT]))
        k_txt = calc.add("탐지 기준", f"{config.DEFAULT_ANOMALY_K:g}σ", "config")
        lines.append(BriefLine(
            "판정 기준",
            f"설비별 전체 평균 ±{k_txt} 밖이면서 직전 시간 대비 급변한 시간만 이상으로 봅니다.",
            sources=[PAT]))

    limits = [
        "예측은 공장 전체 단위이며 설비별 예측은 아닙니다.",
        "이상 탐지는 학습 원본(12/1~4/29) 기준이며 5월 탐지가 아닙니다.",
    ]
    if audience != EXECUTIVE:
        limits.append("전력량은 데이터(Wh) ÷ 1,000 = kWh 기준이며 요금 = kWh × 180원, 탄소 = kWh × 0.424kgCO₂입니다.")

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
