"""질문 의도별 규칙 기반 답변 (10/16 피드백: 챗봇 답이 애매·부정확).

이전에는 어떤 질문이든 '이상' 단어가 있으면 이상 요약, 아니면 기간 브리핑 전체를 돌려줬다.
여기서는 질문에서 의도(요금·탄소 합계 / 정확도 / 최대·최소 시간 / 부하 패턴 / 야간 / 설비 이상 /
모델)와 날짜(5월 15일, 5/15, 05-15, 'N주차' 없이 날짜 범위 'A~B')를 읽고, 그 기간의 숫자만
artifacts 도구에서 꺼내 짧게 답한다. 모든 숫자에 출처 파일을 붙인다.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from config import config
from src.agent import tools as tools_mod

YEAR = 2025
_DATE_RE = re.compile(r"(?:(\d{1,2})\s*월\s*(\d{1,2})\s*일|(\d{1,2})\s*[/.-]\s*(\d{1,2}))")
NIGHT = {22, 23, 0, 1, 2, 3, 4, 5}
WD = "월화수목금토일"


WEEKS = {  # 예측 기간(5/10–5/31) 기준 주차
    "첫": ("2025-05-10", "2025-05-11"), "1": ("2025-05-10", "2025-05-11"),
    "둘": ("2025-05-12", "2025-05-18"), "2": ("2025-05-12", "2025-05-18"),
    "셋": ("2025-05-19", "2025-05-25"), "3": ("2025-05-19", "2025-05-25"),
    "넷": ("2025-05-26", "2025-05-31"), "4": ("2025-05-26", "2025-05-31"),
    "마지막": ("2025-05-26", "2025-05-31"),
}
_WEEK_RE = re.compile(r"(첫|둘|셋|넷|마지막|[1-4])\s*(?:째|번째)?\s*주")


def parse_dates(q: str) -> tuple[str | None, str | None]:
    """질문 속 날짜 1개 → 그 날, 2개 → 범위, 'N째 주/마지막 주' → 그 주. 없으면 전체 예측 기간.
    '오늘/내일'은 데이터에 '오늘'이 없어 예측 첫날(5/10)로 본다."""

    wm = _WEEK_RE.search(q)
    if wm and not _DATE_RE.search(q):
        return WEEKS[wm.group(1)]
    if ("오늘" in q or "내일" in q) and not _DATE_RE.search(q):
        return "2025-05-10", "2025-05-10"
    days = []
    for m in _DATE_RE.finditer(q):
        mo, dd = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        try:
            days.append(date(YEAR, int(mo), int(dd)))
        except ValueError:
            continue
    if not days:
        return None, None
    if len(days) == 1:
        d = days[0]
        if "주" in q and "주의" not in q:  # '5월 12일 주' → 그 주 월–일
            mon = d - timedelta(days=d.weekday())
            return mon.isoformat(), (mon + timedelta(days=6)).isoformat()
        return d.isoformat(), d.isoformat()
    lo, hi = min(days), max(days)
    return lo.isoformat(), hi.isoformat()


GLOSSARY = {
    "부하 불균형": "3상(R·S·T) 전류가 고르지 않은 상태입니다. 불평형률 = 평균에서 가장 먼 상의 차이 ÷ 평균 전류. 한쪽 상에 부하가 몰리면 모터 발열·효율 저하로 이어질 수 있습니다.",
    "무효전력": "실제 일을 하지 않고 전기·자기장을 만드는 데 오가는 전력(var)입니다. 많을수록 같은 일에 더 큰 전류가 흐릅니다.",
    "역률": "실제 쓰인 전력 ÷ 공급된 전력(%)입니다. 낮을수록 손실이 크고 요금 불이익이 생길 수 있습니다.",
    "야간 이상가동": "22–06시에 평소보다 전력이 높고 직전 시간보다 급증한 경우입니다. 끄지 않은 설비나 대기전력 누수 후보입니다.",
    "전류만 증가": "유효전력은 그대로인데 전류만 급증한 경우입니다. 역률 저하·상 불균형·절연 문제를 의심할 수 있습니다.",
    "비정상 패턴": "그 설비 평균에서 크게 벗어나면서 직전 시간 대비 급격히 변한 사용량입니다.",
    "mae": "평균 절대 오차입니다. 시간마다 |실제 − 예측|을 평균한 값(Wh)으로, 작을수록 정확합니다.",
    "σ": "표준편차입니다. ±3.5σ 밖은 정규분포에서 약 0.05% 확률의 드문 값입니다.",
}


def find_modules(q: str, modules: list[str]) -> list[str]:
    """질문 속 설비 지칭 → 설비 id. 'N호기'는 이름 (N호기) 우선, 없으면 id N. 이름 일부(분쇄기·호이스트 등)도 매칭."""

    out = []
    for m in re.finditer(r"(\d{1,2})\s*(호기|번)", q):
        n, unit = m.group(1), m.group(2)
        by_name = [x for x in modules if unit == "호기" and f"({n}호기)" in x]
        by_id = [x for x in modules if x.split("(")[0] == n]
        out += by_name or by_id
    for x in modules:
        name = x[x.find("(") + 1:].rstrip(")")
        core = re.sub(r"[\d()]", "", name)
        if len(core) >= 2 and core in q and "호기" not in core:
            out.append(x)
    return list(dict.fromkeys(out))


def intent(q: str) -> str:
    ql = q.lower()
    if any(k in q for k in ("안녕", "도움", "뭘 할 수", "무엇을 할 수", "사용법")):
        return "help"
    if any(k in q for k in ("뭐야", "무엇", "란?", "이란", "의미", "뜻")):
        return "define"
    if any(k in q for k in ("보고", "브리핑")):
        return "summary"
    if any(k in q for k in ("줄이", "절감", "아끼", "개선", "낮추")):
        return "advice"
    if any(k in ql for k in ("mae", "정확", "오차", "성능")):
        return "accuracy"
    if any(k in q for k in ("모델", "알고리즘", "lightgbm", "LightGBM")):
        return "model"
    if "야간" in q or "밤" in q:
        return "night"
    if any(k in q for k in ("이상", "설비", "점검", "주의", "경고", "고장")):
        return "anomaly"
    if any(k in q for k in ("가장 높", "최대", "피크", "제일 많", "가장 많")):
        return "peak"
    if any(k in q for k in ("가장 낮", "최소", "제일 적", "가장 적")):
        return "low"
    if any(k in q for k in ("패턴", "시간대별", "요일")):
        return "pattern"
    if any(k in q for k in ("요금", "탄소", "비용", "전력량", "얼마", "합계", "총", "평균", "사용량")):
        return "totals"
    if "요약" in q:
        return "summary"
    return "briefing"


HELP_TEXT = (
    "이렇게 물어보세요:\n"
    "- 5월 15일 예측 요금은? / 마지막 주 탄소량 / 하루 평균 요금은?\n"
    "- 요금이 가장 높은 시간대는? / 5월 20일 몇 시에 가장 많이 써?\n"
    "- 주의해서 볼 설비는? / 6호기 상태 어때? / 야간에 전력을 많이 쓰는 설비는?\n"
    "- 전력량 예측 MAE는? / 어떤 모델 썼어? / 부하 불균형이 뭐야? / 요금 줄이려면?"
)


def _rows(start, end, artifacts_dir):
    f = tools_mod.get_forecast_summary(start, f"{end} 23:59:59" if end else None, artifacts_dir=artifacts_dir)
    if "error" in f:
        return None, f
    return f.get("rows", []), f


def _period_text(start, end, rows) -> str:
    if not rows:
        return "—"
    if start and start == end:
        d = date.fromisoformat(start)
        return f"{d:%m-%d}({WD[d.weekday()]})"
    return f"{rows[0]['id'][:16]} – {rows[-1]['id'][:16]} ({len(rows)}시간)"


def answer(question: str, *, artifacts_dir: Path | None = None) -> dict[str, Any] | None:
    """의도별 답변 {"text", "sources"} 또는 None(브리핑으로 넘김)."""

    kind = intent(question)
    start, end = parse_dates(question)
    src_pred = tools_mod.active_artifact(tools_mod.PREDICTIONS_NAME, tools_mod.BPART_FORECAST_NAME,
                                         artifacts_dir=artifacts_dir)
    rate_b, rate_c = float(config.BILL_RATE), float(config.CARBON_RATE)

    if kind == "summary":
        return None  # 역할별 브리핑으로
    if kind == "help":
        return {"text": "안녕하세요. 5월 예측과 설비 이상 데이터를 바탕으로 답합니다.\n" + HELP_TEXT, "sources": []}
    if kind == "define":
        ql = question.lower()
        hits = [(k, v) for k, v in GLOSSARY.items() if k.lower() in ql or k.replace(" ", "") in question.replace(" ", "")]
        if hits:
            return {"text": "\n".join(f"{k}: {v}" for k, v in hits), "sources": []}
        return {"text": "용어를 찾지 못했습니다. 설명 가능한 용어: " + ", ".join(GLOSSARY), "sources": []}

    status_all = tools_mod.get_module_status(artifacts_dir=artifacts_dir)
    mods_all = [r["module"] for r in status_all.get("modules", [])] if "error" not in status_all else []
    targets = find_modules(question, mods_all)
    if targets and kind not in ("totals", "peak", "low", "pattern", "accuracy", "model", "advice"):
        pat = tools_mod.get_pattern_anomalies(artifacts_dir=artifacts_dir)
        evs = pat.get("events", []) if "error" not in pat else []
        lines = []
        for m in targets[:3]:
            row = next((r for r in status_all["modules"] if r["module"] == m), {})
            mine = [e for e in evs if e["module"] == m]
            by_t = Counter(config.ANOMALY_TYPE_LABELS.get(e["type"], e["type"]) for e in mine)
            lines.append(f"{m}: 상태 {row.get('status', '—')} · 급변 탐지 {len(mine)}건"
                         + (f" ({' · '.join(f'{t} {n}' for t, n in by_t.most_common())})" if mine else ""))
            for e in mine[-3:]:
                diff = (e["value"] - e["baseline"]) / e["baseline"] * 100 if e["baseline"] else 0
                lines.append(f"  - {e['hour'][:13]}시 {config.ANOMALY_TYPE_LABELS.get(e['type'], e['type'])}: "
                             f"{e['value']:,.1f}{e.get('unit', '')} (평균 {e['baseline']:,.1f}, {diff:+.1f}%)")
            cp = tools_mod.get_cpart_anomalies(artifacts_dir=artifacts_dir)
            cp_m = [c for c in cp.get("events", []) if c["module"] == m] if "error" not in cp else []
            if cp_m:
                tags = Counter(t for c in cp_m for t in c["conditions"])
                lines.append(f"  - C파트 탐지(5초 고전력 2.9σ): {len(cp_m)}건, 최고 {max(c['power_w'] for c in cp_m):,.0f}W"
                             + (f" · {' · '.join(f'{t} {n}' for t, n in tags.most_common())}" if tags else ""))
            if row.get("anomaly_count"):
                lines.append(f"  - 참고: 5초 순간 기준 미달 {row['anomaly_count']:,}행(비율 {row['anomaly_ratio'] * 100:.2f}%) — 경고 기준 아님")
        lines.append(f"판정: 급변 {config.STATUS_ALERT_PATTERN_COUNT}건 이상 = 이상, "
                     f"{config.STATUS_WATCH_PATTERN_COUNT}건 이상 = 주의 (12/01–04/29 측정)")
        return {"text": "\n".join(lines), "sources": [tools_mod.PATTERN_NAME, tools_mod.MODULE_STATUS_NAME]}

    if kind == "briefing":
        return {"text": "질문을 정확히 이해하지 못했습니다.\n" + HELP_TEXT, "sources": []}

    if kind == "advice":
        rows_all, _ = _rows(None, None, artifacts_dir)
        pat = tools_mod.get_pattern_anomalies(artifacts_dir=artifacts_dir)
        evs = pat.get("events", []) if "error" not in pat else []
        lines = ["데이터에서 보이는 절감 포인트입니다."]
        if rows_all:
            tot = sum(r["pred_pow"] for r in rows_all)
            night = sum(r["pred_pow"] for r in rows_all if int(r["id"][11:13]) in NIGHT)
            lines.append(f"- 야간(22–06시) 예측 사용량이 전체의 {night / tot * 100:.1f}%({night * rate_b:,.0f}원)입니다. "
                         "부하가 하루 종일 거의 일정해, 쓰지 않는 설비의 야간 정지가 가장 직접적인 절감 후보입니다.")
        pf = Counter(e["module"] for e in evs if e["type"] in ("power_factor_drop", "reactive_power_surge"))
        if pf:
            lines.append("- 역률 급감·무효전력 급증이 있었던 설비: " + ", ".join(pf) + " → 역률 개선(콘덴서) 점검 후보")
        ub = Counter(e["module"] for e in evs if e["type"] == "load_imbalance")
        if ub:
            lines.append("- 부하 불균형이 잦은 설비: " + ", ".join(f"{m} {n}건" for m, n in ub.most_common(3)) + " → 상별 부하 재배치 검토")
        lines.append("※ 절감액은 설비별 실측이 아닌 예측·탐지 결과에서 나온 후보입니다.")
        return {"text": "\n".join(lines), "sources": [src_pred, tools_mod.PATTERN_NAME]}
    if kind in ("accuracy", "model"):
        ev = tools_mod.get_evaluation(artifacts_dir=artifacts_dir)
        sel = tools_mod.get_selection(artifacts_dir=artifacts_dir)
        if "error" in ev:
            return {"text": "평가 데이터가 없어 정확도를 답할 수 없습니다.", "sources": []}
        m = ev["mae_hourly_pow"]
        name = (sel.get("selected_label") or sel.get("selected_model")) if "error" not in sel else ev.get("model", "—")
        cv = sel.get("selection_score_validation_mae") if "error" not in sel else None
        lines = [
            f"예측 모델은 {name}입니다. 2024-12-01 – 2025-04-29 시간별 전력량으로 학습해 "
            f"2025-05-10 00:00 – 05-31 00:00 505시점을 예측했습니다.",
            f"5월 정답 대비 시간당 평균 오차(MAE)는 {m['mae504']:.2f} Wh(504시점), {m['mae505']:.2f} Wh(505시점)입니다.",
        ]
        if cv is not None:
            lines.append(f"5월 이전 과거 3회 검증 MAE는 {cv:.2f} Wh입니다.")
        if kind == "model" and "error" not in sel:
            comp = sorted(sel.get("baseline_comparison", []), key=lambda r: r.get("may_mae", 1e9))[:3]
            if comp:
                lines.append("5월 MAE 상위: " + " · ".join(f"{r.get('label', r['model'])} {r['may_mae']:.1f}" for r in comp))
        return {"text": "\n".join(lines), "sources": [tools_mod.BPART_EVALUATION_NAME, tools_mod.BPART_SELECTION_NAME]}

    if kind in ("anomaly", "night"):
        pat = tools_mod.get_pattern_anomalies(artifacts_dir=artifacts_dir)
        status = tools_mod.get_module_status(artifacts_dir=artifacts_dir)
        if "error" in pat:
            return {"text": "이상 탐지 결과(pattern_anomalies.parquet)가 없습니다.", "sources": []}
        evs = pat["events"]
        if kind == "night":
            evs = [e for e in evs if e["type"] == "night_operation"]
            if not evs:
                return {"text": "야간(22–06시) 이상가동으로 탐지된 시간은 없습니다.", "sources": [tools_mod.PATTERN_NAME]}
            lines = [f"야간 이상가동은 {len(evs)}건입니다 (12/01–04/29, 평균보다 높고 직전 시간보다 급증한 야간 시간)."]
            for e in evs[:5]:
                lines.append(f"- {e['hour'][:13]}시 {e['module']}: 유효전력 {e['value']:,.0f}W (평균 {e['baseline']:,.0f}W)")
            return {"text": "\n".join(lines), "sources": [tools_mod.PATTERN_NAME]}
        by_mod = Counter(e["module"] for e in evs)
        by_type = Counter(config.ANOMALY_TYPE_LABELS.get(e["type"], e["type"]) for e in evs)
        rows = status.get("modules", []) if "error" not in status else []
        alert = [r["module"] for r in rows if r["status"] == "이상"]
        watch = [r["module"] for r in rows if r["status"] == "주의"]
        lines = [
            "관찰된 사실",
            f"- 급격한 변화 탐지 {len(evs)}건 (12/01–04/29, 설비별 전체 평균 ±{config.DEFAULT_ANOMALY_K:g}σ 밖 + 직전 시간 대비 급변)",
            "- 유형: " + " · ".join(f"{t} {n}" for t, n in by_type.most_common()),
            f"- 점검 우선: 이상 {', '.join(alert) or '없음'} / 주의 {len(watch)}대",
            "- 설비별 상위: " + " · ".join(f"{m} {n}건" for m, n in by_mod.most_common(3)),
            *([f"- C파트 탐지(5초 고전력 2.9σ): {cp['count']}건, 상위 "
               + " · ".join(f"{m} {n}건" for m, n in Counter(c['module'] for c in cp['events']).most_common(3))]
              if "error" not in (cp := tools_mod.get_cpart_anomalies(artifacts_dir=artifacts_dir)) else []),
            "가능한 원인 후보",
            "- 부하 불균형: 단상 부하 편중·한 상 접촉 불량 / 역률 급감: 무부하 운전·콘덴서 이상",
            "현장 확인 항목",
            "- 해당 시각 운전 일지와 상별 전류 측정값 대조",
        ]
        return {"text": "\n".join(lines), "sources": [tools_mod.PATTERN_NAME, tools_mod.MODULE_STATUS_NAME]}

    rows, f = _rows(start, end, artifacts_dir)
    if rows is None:
        return {"text": "예측 데이터를 불러오지 못했습니다.", "sources": []}
    if not rows:
        return {"text": f"{start} – {end}에는 예측이 없습니다. 예측 기간은 2025-05-10 00:00 – 05-31 00:00입니다.",
                "sources": [src_pred]}
    period = _period_text(start, end, rows)
    if kind == "totals":
        p = sum(r["pred_pow"] for r in rows)
        n_days = len({r["id"][:10] for r in rows if True}) or 1
        full_days = len(rows) / 24
        txt = (f"{period} 예측 전력량 {p:,.2f} kWh, 요금 {p * rate_b:,.0f}원, "
               f"탄소 {p * rate_c:,.2f} kgCO₂입니다.")
        if "평균" in question or len(rows) > 24:
            txt += (f" 하루 평균 {p / full_days:,.2f} kWh({p / full_days * rate_b:,.0f}원), "
                    f"시간 평균 {p / len(rows):,.3f} kWh입니다.")
        txt += f" (요금 = kWh × {rate_b:g}원, 탄소 = kWh × {rate_c:g})"
        if ("오늘" in question or "내일" in question) and not _DATE_RE.search(question):
            txt = "데이터에 '오늘'이 없어 예측 첫날(5/10) 기준으로 답합니다. " + txt
        return {"text": txt, "sources": [src_pred]}
    if kind in ("peak", "low"):
        srt = sorted(rows, key=lambda r: r["pred_pow"], reverse=(kind == "peak"))[:3]
        word = "높은" if kind == "peak" else "낮은"
        items = " · ".join(f"{r['id'][5:13]}시 {r['pred_pow']:.2f} kWh({r['pred_pow'] * rate_b:,.0f}원)" for r in srt)
        spread = max(x["pred_pow"] for x in rows) - min(x["pred_pow"] for x in rows)
        return {"text": (f"{period} 예측 전력량이 가장 {word} 시간은 {items}입니다. "
                         f"최대–최소 차이는 {spread:.2f} kWh로 크지 않습니다."), "sources": [src_pred]}
    if kind == "pattern":
        by_h: dict[int, list[float]] = {}
        wk, we = [], []
        for r in rows:
            t = datetime.strptime(r["id"][:19], "%Y-%m-%d %H:%M:%S")
            by_h.setdefault(t.hour, []).append(r["pred_pow"])
            (we if t.weekday() >= 5 else wk).append(r["pred_pow"])
        means = {h: sum(v) / len(v) for h, v in by_h.items()}
        hi = max(means, key=means.get)
        lo = min(means, key=means.get)
        night = sum(r["pred_pow"] for r in rows if int(r["id"][11:13]) in NIGHT) / sum(r["pred_pow"] for r in rows) * 100
        txt = (f"{period} 시간대별 평균 예측 전력량은 {hi}시가 가장 높고({means[hi]:.3f} kWh) "
               f"{lo}시가 가장 낮습니다({means[lo]:.3f} kWh). 야간(22–06시) 비중은 {night:.1f}%입니다.")
        if wk and we:
            txt += f" 평일 시간 평균 {sum(wk) / len(wk):.3f} kWh, 주말 {sum(we) / len(we):.3f} kWh입니다."
        txt += " 시간대 차이가 작아 공장 부하가 하루 종일 거의 일정한 편입니다."
        return {"text": txt, "sources": [src_pred]}
    return None
