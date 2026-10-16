"""에너지 관리 담당자용 AI 보고서 (다운로드용 HTML 한 파일).

숫자는 전부 Python이 예측 산출물에서 계산한다. AI(LLM)는 요약 문단과 검토 의견 문장만 쓰며,
입력에 없는 숫자나 단정 표현이 나오면 버리고 규칙 기반 문장을 쓴다(브리핑과 같은 원칙).
브라우저에서 열어 그대로 보거나 인쇄 → PDF로 저장할 수 있다.
"""

from __future__ import annotations

import html
import json
import logging
import re
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable

from config import config

logger = logging.getLogger(__name__)

WEEKDAYS = "월화수목금토일"
FORBIDDEN = ("고장", "위험 확정", "실시간", "LIVE", "Fault", "탄소세")
_NUM = re.compile(r"\d[\d,]*\.?\d*")


# ---------------------------------------------------------------------------
# 계산 (순수 함수)
# ---------------------------------------------------------------------------
def _dt(row_id: str) -> datetime | None:
    try:
        return datetime.strptime(str(row_id)[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def compute_facts(
    rows: list[dict[str, Any]],
    settings: dict[str, Any],
    *,
    evaluation: dict[str, Any] | None = None,
    selection: dict[str, Any] | None = None,
    interval: dict[str, Any] | None = None,
    status_rows: list[dict[str, Any]] | None = None,
    anomaly_sources: dict[str, int] | None = None,
) -> dict[str, Any]:
    """보고서에 들어갈 숫자를 한 번에 계산. rows의 pred_pow 단위는 kWh(시간당)."""

    rate = float(settings.get("bill_rate", config.BILL_RATE))
    c_rate = float(settings.get("carbon_rate", config.CARBON_RATE))
    price = settings.get("carbon_price")
    pts = [(d, float(r["pred_pow"])) for r in rows if (d := _dt(r.get("id", ""))) is not None]
    pts.sort()
    if not pts:
        return {"ok": False}

    total = sum(v for _, v in pts)
    days: dict[str, list[float]] = defaultdict(list)
    hours: dict[int, list[float]] = defaultdict(list)
    wk: dict[str, list[float]] = {"평일": [], "주말": []}
    for d, v in pts:
        days[d.strftime("%Y-%m-%d")].append(v)
        hours[d.hour].append(v)
        wk["주말" if d.weekday() >= 5 else "평일"].append(v)
    daily = []
    for key, vs in days.items():
        d = datetime.strptime(key, "%Y-%m-%d")
        kwh = sum(vs)
        daily.append({"date": key, "wd": WEEKDAYS[d.weekday()], "hours": len(vs), "kwh": kwh,
                      "bill": kwh * rate, "carbon": kwh * c_rate})
    full = [x for x in daily if x["hours"] == 24]
    profile = {h: sum(v) / len(v) for h, v in sorted(hours.items())}
    hi_h = max(profile, key=profile.get)
    lo_h = min(profile, key=profile.get)
    peak_d, peak_v = max(pts, key=lambda p: p[1])
    carbon = total * c_rate
    cost = carbon / 1000.0 * float(price) if price not in (None, "") else None

    mae = None
    if evaluation and "error" not in evaluation:
        m = evaluation.get("mae_hourly_pow") or {}
        mae = m.get("mae504") if isinstance(m, dict) else None
    models = []
    if selection and "error" not in selection:
        models = sorted(selection.get("baseline_comparison") or [],
                        key=lambda r: r.get("may_mae", r.get("validation_mae", 0)))

    status_rows = status_rows or []
    return {
        "ok": True,
        "start": pts[0][0].strftime("%Y-%m-%d %H:%M"),
        "end": pts[-1][0].strftime("%Y-%m-%d %H:%M"),
        "n": len(pts),
        "kwh": total,
        "bill": total * rate,
        "carbon": carbon,
        "carbon_cost": cost,
        "rate": rate, "c_rate": c_rate, "price": price,
        "daily": daily,
        "daily_avg": (sum(x["kwh"] for x in full) / len(full)) if full else total / max(len(daily), 1),
        "day_hi": max(full, key=lambda x: x["kwh"]) if full else None,
        "day_lo": min(full, key=lambda x: x["kwh"]) if full else None,
        "hour_avg": total / len(pts),
        "profile": profile,
        "hour_hi": (hi_h, profile[hi_h]),
        "hour_lo": (lo_h, profile[lo_h]),
        "hour_spread": profile[hi_h] - profile[lo_h],
        "peak": (peak_d.strftime("%m-%d") + f"({WEEKDAYS[peak_d.weekday()]}) {peak_d.hour:02d}시", peak_v),
        "weekday_avg": (sum(wk["평일"]) / len(wk["평일"])) if wk["평일"] else None,
        "weekend_avg": (sum(wk["주말"]) / len(wk["주말"])) if wk["주말"] else None,
        "save_1pct": total * rate * 0.01,
        "mae": mae,
        "mae_pct": (mae / (total / len(pts) * 1000) * 100) if mae else None,
        "interval": (interval or {}),
        "models": models,
        "selected": (selection or {}).get("selected_label") or (selection or {}).get("selected_model"),
        "anomaly": anomaly_sources or {},
        "alert_mods": [r["module"] for r in status_rows if r.get("status") == "이상"],
        "watch_mods": [r["module"] for r in status_rows if r.get("status") == "주의"],
        "top_mods": [(r["module"], r["total"], r.get("main_type", "")) for r in status_rows[:3]],
    }


# ---------------------------------------------------------------------------
# 문장 (규칙 기반 + 선택적 LLM)
# ---------------------------------------------------------------------------
def _won(v: float) -> str:
    return f"{v:,.0f}원"


def _man(v: float) -> str:
    return f"약 {v / 10000:,.1f}만 원"


def rule_text(f: dict[str, Any]) -> dict[str, Any]:
    """요약 문단 + 검토 의견(3개). 모든 숫자는 facts에서 온다."""

    a = f["anomaly"]
    n_anom = sum(a.values())
    summary = (
        f"예측 기간({f['start'][:10]} – {f['end'][:10]}, {f['n']}시간) 공장 전체 예측 전력량은 "
        f"{f['kwh']:,.1f} kWh, 전기요금은 {_man(f['bill'])}, 탄소는 {f['carbon']:,.0f} kgCO₂입니다. "
        f"하루 평균 {f['daily_avg']:,.1f} kWh로 날짜·시간대별 차이가 작아 부하가 일정한 공장입니다. "
        f"예측 오차(MAE)는 시간당 {f['mae']:.1f} Wh입니다." if f.get("mae") else
        f"예측 기간 공장 전체 예측 전력량은 {f['kwh']:,.1f} kWh, 전기요금은 {_man(f['bill'])}입니다."
    )
    recs = [
        f"시간대별 평균 차이가 {f['hour_spread']:.3f} kWh로 작아 사용 시간 이동(피크 이동)만으로는 절감 효과가 제한적입니다. "
        f"상시 가동되는 기저 부하를 줄이는 쪽이 우선입니다.",
        f"사용량을 1% 줄이면 이 기간 요금이 {_won(f['save_1pct'])} 줄어듭니다. 대기 전력·공회전 설비부터 확인하세요.",
        (f"이상치 {n_anom}건 중 이상 판정 설비 {len(f['alert_mods'])}대({', '.join(f['alert_mods'][:3])} 등)는 "
         f"전력 사용이 평소 범위를 벗어난 기록이 있어 설비 관리 담당자와 점검 일정을 맞추세요(원인 단정 아님)."
         if f["alert_mods"] else "점검이 필요한 이상 설비는 없습니다."),
    ]
    return {"summary": summary, "recs": recs, "mode": "rule"}


def _numbers(text: str) -> set[str]:
    return {m.rstrip(".,") for m in _NUM.findall(text)}


def validate_llm(rule: dict[str, Any], payload: Any) -> dict[str, Any] | None:
    """LLM 결과 검증: 형식, 금지어, 새 숫자 금지. 실패하면 None."""

    if not isinstance(payload, dict):
        return None
    summary, recs = payload.get("summary"), payload.get("recs")
    if not isinstance(summary, str) or not isinstance(recs, list) or not (1 <= len(recs) <= 4):
        return None
    if not all(isinstance(r, str) and r.strip() for r in recs):
        return None
    allowed = _numbers(rule["summary"] + " " + " ".join(rule["recs"]) + " " + rule.get("_facts", ""))
    for text in [summary, *recs]:
        if len(text) > 400 or any(w in text for w in FORBIDDEN) or not _numbers(text) <= allowed:
            return None
    return {"summary": summary.strip(), "recs": [r.strip() for r in recs], "mode": "llm"}


def _facts_text(f: dict[str, Any]) -> str:
    """LLM에 넘길 숫자 요약(이 문자열에 있는 숫자만 쓸 수 있다)."""

    d_hi, d_lo = f.get("day_hi"), f.get("day_lo")
    return json.dumps({
        "기간": f"{f['start'][:10]} – {f['end'][:10]} ({f['n']}시간)",
        "예측 전력량": f"{f['kwh']:,.1f} kWh",
        "전기요금": _man(f["bill"]),
        "탄소": f"{f['carbon']:,.0f} kgCO₂",
        "하루 평균": f"{f['daily_avg']:,.1f} kWh",
        "최고 예측일": f"{d_hi['date'][5:]} {d_hi['kwh']:,.1f} kWh" if d_hi else None,
        "최저 예측일": f"{d_lo['date'][5:]} {d_lo['kwh']:,.1f} kWh" if d_lo else None,
        "시간대 평균 차이": f"{f['hour_spread']:.3f} kWh",
        "1% 절감 시 요금 감소": _won(f["save_1pct"]),
        "MAE": f"{f['mae']:.1f} Wh" if f.get("mae") else None,
        "이상치": f"{sum(f['anomaly'].values())}건",
        "이상 설비": f"{len(f['alert_mods'])}대 ({', '.join(f['alert_mods'])})",
    }, ensure_ascii=False)


def _openai_json(system: str, user: str) -> dict[str, Any] | None:  # pragma: no cover - network
    from openai import OpenAI

    client = OpenAI(api_key=config.get_openai_api_key())
    resp = client.chat.completions.create(
        model=config.get_openai_model(),
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        timeout=30,
    )
    return json.loads(resp.choices[0].message.content or "{}")


def write_text(f: dict[str, Any], call: Callable[[str, str], dict[str, Any] | None] | None = None,
               *, use_llm: bool = True) -> dict[str, Any]:
    rule = rule_text(f)
    if not use_llm:
        return rule
    if call is None:
        if not config.has_openai_key():
            return rule
        call = _openai_json
    facts = _facts_text(f)
    system = (
        "너는 공장 에너지 관리 담당자에게 보고서를 쓰는 분석가다. 입력의 facts와 초안을 바탕으로 "
        "summary(3–4문장 요약)와 recs(검토 의견 3개, 각 1–2문장)를 한국어로 쓴다. "
        "숫자는 facts나 초안에 있는 문자열을 그대로만 쓰고 새 숫자를 만들지 않는다. "
        "'고장', '위험 확정', '실시간' 같은 단정 표현은 쓰지 않는다. "
        '출력은 {"summary": "...", "recs": ["...", "...", "..."]} JSON만.'
    )
    user = json.dumps({"facts": json.loads(facts), "draft": {"summary": rule["summary"], "recs": rule["recs"]}},
                      ensure_ascii=False)
    try:
        payload = call(system, user)
    except Exception:  # noqa: BLE001 - 네트워크·키 오류 → 규칙 문장
        logger.warning("llm report failed; using rule text")
        return rule
    checked = validate_llm({**rule, "_facts": facts}, payload)
    if checked is None:
        logger.warning("llm report rejected by validation; using rule text")
        return rule
    return checked


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
_CSS = """
@page { size: A4; margin: 14mm; }
:root { --ac:#0E9F8E; --tx:#1B2430; --mu:#5B6573; --bd:#E3E6EB; --bg:#F7F8FA; }
body { font-family: Pretendard, "Noto Sans KR", "Malgun Gothic", sans-serif; color: var(--tx);
       max-width: 860px; margin: 24px auto; padding: 0 20px; font-size: 14px; line-height: 1.65; }
h1 { font-size: 24px; margin: 0 0 4px; } h2 { font-size: 17px; margin: 26px 0 8px; padding-bottom: 6px;
     border-bottom: 2px solid var(--ac); } .meta { color: var(--mu); font-size: 12.5px; }
.chip { display:inline-block; font-size:11.5px; font-weight:700; padding:1px 8px; border-radius:10px;
        background:#E6F5F3; color:#0B7A6E; margin-left:6px; } .chip.rule { background:#EEF0F3; color:var(--mu); }
.kpis { display:grid; grid-template-columns: repeat(4, 1fr); gap:10px; margin: 14px 0 6px; }
.kpi { border:1px solid var(--bd); border-radius:8px; padding:10px 12px; }
.kpi .l { font-size:12px; color:var(--mu); } .kpi .v { font-size:20px; font-weight:700; font-variant-numeric: tabular-nums; }
.kpi .s { font-size:11.5px; color:var(--mu); }
.summary { background: var(--bg); border-left: 4px solid var(--ac); padding: 12px 14px; border-radius: 0 8px 8px 0; }
table { width:100%; border-collapse: collapse; font-size: 13px; margin: 6px 0 4px; }
th { text-align:left; background: var(--bg); padding:6px 8px; border-bottom:1px solid var(--bd); }
td { padding:5px 8px; border-bottom:1px solid var(--bd); } td.n, th.n { text-align:right; font-variant-numeric: tabular-nums; }
tr.hi td { background:#FFF7E6; } tr.lo td { background:#EEF6FF; }
ol li { margin-bottom: 6px; } .note { color: var(--mu); font-size: 12.5px; }
.cols { display:grid; grid-template-columns: 1fr 1fr; gap: 18px; }
@media print { body { margin: 0; max-width: none; } h2 { break-after: avoid; } table { break-inside: auto; } }
@media (max-width: 640px) { .kpis { grid-template-columns: 1fr 1fr; } .cols { grid-template-columns: 1fr; } }
"""


def _e(v: Any) -> str:
    return html.escape(str(v))


def to_html(f: dict[str, Any], text: dict[str, Any], *, generated_at: str | None = None) -> str:
    if not f.get("ok"):
        return "<!doctype html><meta charset='utf-8'><p>보고서를 만들 예측 데이터가 없습니다.</p>"
    gen = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    mode = ('<span class="chip">AI 작성</span>' if text["mode"] == "llm"
            else '<span class="chip rule">규칙 기반 작성</span>')
    cost = (f"{f['carbon_cost']:,.0f}원" if f["carbon_cost"] is not None else "단가 미설정")
    iv = f["interval"]
    kpi = lambda l, v, s: f'<div class="kpi"><div class="l">{_e(l)}</div><div class="v">{v}</div><div class="s">{_e(s)}</div></div>'  # noqa: E731

    d_hi, d_lo = f.get("day_hi"), f.get("day_lo")
    day_rows = []
    for x in f["daily"]:
        cls = ' class="hi"' if d_hi and x["date"] == d_hi["date"] else (' class="lo"' if d_lo and x["date"] == d_lo["date"] else "")
        part = "" if x["hours"] == 24 else f" ({x['hours']}시간)"
        day_rows.append(f"<tr{cls}><td>{x['date'][5:]} ({x['wd']}){part}</td><td class='n'>{x['kwh']:,.2f}</td>"
                        f"<td class='n'>{x['bill']:,.0f}</td><td class='n'>{x['carbon']:,.1f}</td></tr>")
    prof = sorted(f["profile"].items(), key=lambda kv: -kv[1])
    prof_rows = "".join(f"<tr><td>{h:02d}시</td><td class='n'>{v:,.3f}</td><td class='n'>{v * f['rate']:,.0f}</td></tr>"
                        for h, v in prof[:3])
    prof_rows_lo = "".join(f"<tr><td>{h:02d}시</td><td class='n'>{v:,.3f}</td><td class='n'>{v * f['rate']:,.0f}</td></tr>"
                           for h, v in prof[-3:])
    model_rows = "".join(
        f"<tr><td>{'● ' if r.get('label') == f['selected'] or r.get('model') == f['selected'] else ''}{_e(r.get('label', r.get('model')))}</td>"
        f"<td>{'기준' if r.get('kind') == 'baseline' else '후보'}</td>"
        f"<td class='n'>{r.get('validation_mae', 0):.1f}</td><td class='n'>{r.get('may_mae', 0):.1f}</td></tr>"
        for r in f["models"][:4])
    a = f["anomaly"]
    top = "".join(f"<tr><td>{_e(m)}</td><td class='n'>{n}건</td><td>{_e(t)}</td></tr>" for m, n, t in f["top_mods"])

    body = f"""
<h1>에너지 관리 AI 보고서</h1>
<div class="meta">Factory Energy Copilot · 공장 전체 5월 시간대별 예측 · 작성 {gen} {mode}</div>

<h2>1. 요약</h2>
<div class="kpis">
{kpi("예측 전력량", f"{f['kwh']:,.0f} kWh", f"{f['start'][5:10]} – {f['end'][5:10]} · {f['n']}시간")}
{kpi("예측 전기요금", f"{f['bill'] / 10000:,.1f}만 원", f"kWh × {f['rate']:g}원")}
{kpi("예측 탄소", f"{f['carbon']:,.0f} kgCO₂", f"kWh × {f['c_rate']:g}")}
{kpi("예상 탄소비용(가정)", cost, f"tCO₂ × {float(f['price']):,.0f}원" if f['price'] not in (None, '') else "설정에서 입력")}
</div>
<div class="summary">{_e(text['summary'])}</div>

<h2>2. 검토 의견</h2>
<ol>{''.join(f'<li>{_e(r)}</li>' for r in text['recs'])}</ol>

<h2>3. 일별 예측</h2>
<p class="note">하루 평균 {f['daily_avg']:,.2f} kWh · 노란 줄 = 최고 예측일{f" ({d_hi['date'][5:]})" if d_hi else ''}, 파란 줄 = 최저 예측일{f" ({d_lo['date'][5:]})" if d_lo else ''}</p>
<table><tr><th>날짜</th><th class="n">전력량 (kWh)</th><th class="n">요금 (원)</th><th class="n">탄소 (kgCO₂)</th></tr>
{''.join(day_rows)}
<tr><th>합계</th><th class="n">{f['kwh']:,.2f}</th><th class="n">{f['bill']:,.0f}</th><th class="n">{f['carbon']:,.1f}</th></tr></table>

<h2>4. 시간대 분석</h2>
<p>최대 예측 시각은 {_e(f['peak'][0])} ({f['peak'][1]:,.3f} kWh)입니다. 시간대별 평균의 최고–최저 차이는
{f['hour_spread']:.3f} kWh로 시간당 평균 {f['hour_avg']:,.3f} kWh의 {f['hour_spread'] / f['hour_avg'] * 100:.2f}%입니다.
{f"평일 시간당 평균 {f['weekday_avg']:,.3f} kWh, 주말 {f['weekend_avg']:,.3f} kWh입니다." if f['weekday_avg'] and f['weekend_avg'] else ''}</p>
<div class="cols">
<div><b>평균이 높은 시간대</b><table><tr><th>시각</th><th class="n">kWh</th><th class="n">요금(원)</th></tr>{prof_rows}</table></div>
<div><b>평균이 낮은 시간대</b><table><tr><th>시각</th><th class="n">kWh</th><th class="n">요금(원)</th></tr>{prof_rows_lo}</table></div>
</div>

<h2>5. 예측 근거와 정확도</h2>
<p>모델은 {_e(f['selected'] or '—')}입니다. 2024-12-01 – 2025-04-29 23:00 실측으로 학습하고, 그 뒤는 자기 예측값을 이어 붙이는
재귀 예측으로 5월을 예측했습니다(5월 실측은 학습에 쓰지 않음).
{f"시간당 오차(MAE)는 {f['mae']:.1f} Wh로 평균 전력의 {f['mae_pct']:.2f}%입니다." if f.get('mae') else ''}
{f"예측구간(P5–P95)은 예측값 {iv.get('q05', 0):+.0f} ~ {iv.get('q95', 0):+.0f} Wh입니다(과거 3회 검증 잔차 {iv.get('n', 0):,}개)." if iv.get('q05') is not None else ''}</p>
<table><tr><th>모델</th><th>종류</th><th class="n">과거 검증 MAE (Wh)</th><th class="n">5월 MAE (Wh)</th></tr>{model_rows}</table>

<h2>6. 이상치 요약 (학습 기간 12/01–04/29)</h2>
<p>이상치 {sum(a.values())}건 = {' + '.join(f'{k} {v}' for k, v in a.items())}. 이상 설비 {len(f['alert_mods'])}대, 주의 설비 {len(f['watch_mods'])}대입니다.</p>
<table><tr><th>설비</th><th class="n">이상치</th><th>주요 유형</th></tr>{top}</table>

<h2>7. 기준과 한계</h2>
<ul class="note">
<li>예측은 공장 전체 단위입니다. 설비별 예측은 아닙니다.</li>
<li>전력량 = 데이터(Wh) ÷ 1,000 = kWh · 요금 = kWh × {f['rate']:g}원 · 탄소 = kWh × {f['c_rate']:g} kgCO₂ (대시보드 설정값 기준).</li>
<li>탄소비용은 배출권(KAU) 시세를 가정한 값이며 실제 부과액이 아닙니다.</li>
<li>이상치는 학습 기간 측정 기준이며 5월 탐지가 아닙니다. 원인을 단정하지 않습니다.</li>
<li>{'요약·검토 의견 문장은 AI가 작성했고, 모든 숫자는 Python 계산값입니다.' if text['mode'] == 'llm' else '요약·검토 의견은 규칙 기반 문장이며, 모든 숫자는 Python 계산값입니다.'}</li>
<li>출처: forecast_bpart.csv · evaluation_bpart.json · model_selection_bpart.json · forecast_bpart_interval.json · pattern_anomalies.parquet · c_part_2_88sigma_conditions.csv</li>
</ul>
"""
    return f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>에너지 관리 AI 보고서</title><style>{_CSS}</style></head><body>{body}</body></html>"


def build_energy_report(rows, settings, *, use_llm: bool = True, call=None, **kw) -> str:
    """보고서 HTML 문자열. kw = compute_facts의 키워드 인자."""

    f = compute_facts(rows, settings, **kw)
    if not f.get("ok"):
        return to_html(f, {})
    return to_html(f, write_text(f, call, use_llm=use_llm))
