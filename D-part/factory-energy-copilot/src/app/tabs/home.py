"""홈 페이지 (v3 §4, v4 home.png) — 공장 전체 5월 예측·요금·탄소.

첫 화면: 경고 띠 → KPI 5 → [차트 | 경고 목록] → [일/주 예측 | 총비용 | 리포트 요약].
스크롤 아래: 분석 플로우 6단계 → [모델 비교 | 시간대 프로파일] (선택 기능).
숫자는 artifacts(predictions.csv · actual_vs_pred.csv · evaluation.json 등)에서만 읽고
공용 포매터(fmt)로만 출력한다. 요금·탄소는 설정 계수로 재계산하되 MAE는 항상
evaluation.json(config 기본 계수) 값이다 (v3 §6 규칙 2).
"""

from __future__ import annotations

from typing import Any

from config import config
from src.agent import tools as tools_mod
from src.app import nav, page_logic, ui_logic
from src.app.ui import charts, components, fmt, theme

PANEL_CHART = "hm_chart"
PANEL_ALERTS = "hm_alerts"
PANEL_PERIOD = "hm_period"
PANEL_COST = "hm_cost"
PANEL_REPORT = "hm_report"
PANEL_TOP = "hm_top"
PANEL_FLOW = "hm_flow"
PANEL_MODELS = "hm_models"
PANEL_PROFILE = "hm_profile"

H_MAIN = 330
H_BOTTOM = 250
H_FLOW = 250
H_LOW = 360

METRIC_OPTIONS = ["요금", "탄소", "전력량"]
_METRIC_KEY = {"요금": "bill", "탄소": "carbon", "전력량": "pow"}

UNIT_DESC = {"시간": "24시간 · 1시간 단위", "일": "선택 날짜 · 시간별", "주": "선택 주 · 시간별", "월": "일 합계"}
CARBON_UNIT_NOTE = "탄소 = kWh × 0.424 kgCO₂ (교안 기준)"


def render(bundle: dict[str, Any], ctx: dict[str, Any]) -> None:
    import streamlit as st

    forecast = bundle["forecast"]
    evaluation = bundle["evaluation"]
    settings = ctx["settings"]
    alerts = ctx["alerts"]
    # B-part 선택 모델 예측(forecast_bpart.csv)이 있으면 홈 예측 그래프·KPI는 그것을 쓴다.
    bpart = bundle.get("forecast_bpart") or {}
    if "error" not in bpart and bpart.get("count", 0) > 0:
        forecast = bpart
    forecast_ok = "error" not in forecast and forecast.get("count", 0) > 0

    _render_strip(st, alerts)

    rows = forecast.get("rows", []) if forecast_ok else []
    start, end = ctx["start"], ctx["end"]
    unit = ctx["gran"]
    # 예측 그래프에서 고른 날짜/주가 KPI 기간이 된다(시간·월은 헤더 기간 전체).
    options, picked = _picked_period(st, rows, unit, start, end)
    if picked and unit == "시간":
        k_rows = picked["window"]
        k_start = page_logic._row_dt(k_rows[0]["id"]).date() if k_rows else start
        k_end = page_logic._row_dt(k_rows[-1]["id"]).date() if k_rows else end
        totals = page_logic.scaled_totals(k_rows, settings)
    else:
        k_rows = rows
        k_start, k_end = page_logic.period_bounds(unit, picked["key"]) if picked else (start, end)
        totals = page_logic.scaled_totals(rows, settings, start=k_start, end=k_end)
    buckets = page_logic.resample_rows(
        rows, unit, ("pred_pow", "pred_carbon"), start=start, end=end
    )
    kpi = {
        "label": picked["label"] if picked else "선택 기간 전체",
        "alerts": page_logic.build_alerts(
            bundle["actual_vs_pred"], bundle["forecast"], bundle["trend"], settings,
            start=k_start, end=k_end, pattern=ctx.get("pattern"),
        ),
        "delta": (_prev_window_delta(rows, picked) if unit == "시간"
                  else _prev_period_delta(rows, unit, options, picked, settings)) if picked else None,
        "peak": _peak_hour(k_rows, None, None) if unit == "시간" else _peak_hour(rows, k_start, k_end),
        "anomaly_exp": page_logic.anomaly_expected(
            (bundle.get("anomaly_hourly_frame") or {}).get("frame"), k_rows,
            start=None if unit == "시간" else k_start, end=None if unit == "시간" else k_end,
        ),
    }
    profile = page_logic.role_profile(ctx.get("role", config.DEFAULT_ROLE))
    _render_kpis(st, bundle, ctx, forecast_ok, totals, buckets, unit, profile, kpi)

    chart_col, alert_col = st.columns([2.35, 1])
    with chart_col:
        _render_chart(st, bundle, ctx, rows, profile, options, picked)
    with alert_col:
        _render_alert_panel(st, alerts)

    # Row 3: panel set and order depend on the role (경영진: 리포트 먼저, 설비 관리: 이상 상위 설비).
    panels = {
        "period": lambda: _render_period_panel(st, rows, unit, start, end, forecast_ok),
        "cost": lambda: _render_cost_panel(st, totals, settings, forecast_ok),
        "report": lambda: _render_report_panel(st, bundle, totals, settings),
        "top_modules": lambda: _render_top_modules_panel(st, bundle),
    }
    row3 = [k for k in profile["home_row3"] if k != "period"]  # 기간 합계는 예측 패널 보기로 통합
    for col, key in zip(st.columns(len(row3)), row3):
        with col:
            panels[key]()

    if ctx.get("role") == page_logic.ROLE_EXECUTIVE:
        _render_executive_report(st, bundle, settings)

    # Second screen (예측 근거): analysis detail for the energy role only.
    if profile["home_detail"]:
        _render_flow(st, bundle)
        left, right = st.columns(2)
        with left:
            _render_models(st, bundle)
        with right:
            _render_profile(st, rows, settings, forecast_ok)


# ---------------------------------------------------------------------------
def _render_executive_report(st, bundle, settings) -> None:
    """경영진 월간 경영 보고: 결론 먼저 → 전월 대비(총량·일평균) → 설비별 탄소 비중 → 감축 검토."""

    frame = (bundle.get("modules_hourly") or {}).get("frame")
    months = page_logic.available_months(frame)
    with components.panel(key="exec_report"):
        head_l, head_r = st.columns([4, 1], vertical_alignment="center")
        head_l.markdown(
            components.panel_header("월간 경영 보고", icon_name="chart", desc="학습 원본 실측 · 전월 대비"),
            unsafe_allow_html=True,
        )
        if not months:
            st.markdown(components.error_state(artifact="hourly_from_full.parquet"), unsafe_allow_html=True)
            return
        month = head_r.selectbox("보고 월", months, index=len(months) - 1, key="exec_month",
                                 label_visibility="collapsed")
        r = page_logic.executive_monthly(frame, month, settings, module_status=bundle.get("module_status"))
        cur, ch = r["current"], r["change"]

        def pct(v):
            return "—" if v is None else f"{v:+.2f}%"

        # 결론 한 줄
        if ch:
            verdict = (
                f"{r['month']} 전력 사용은 전월({r['prev_month']}) 대비 일평균 {pct(ch['daily_pct'])}"
                f" (총량 {pct(ch['energy_pct'])}, {cur['days']:.0f}일 기준)."
            )
        else:
            verdict = f"{r['month']}은 데이터 첫 달이라 전월 비교가 없습니다."
        if r["increase_driver"]:
            verdict += f" 증가 기여 1위: {r['increase_driver']['module']}."
        st.markdown(f"**{verdict}**")

        c1, c2, c3, c4 = st.columns(4)
        c1.markdown(components.kpi_tile("월 전력량", fmt.fmt_big(cur["energy"]), unit="kWh",
                    sub=f"일평균 {fmt.fmt_big(cur['daily_energy'])} kWh", compact=True), unsafe_allow_html=True)
        c2.markdown(components.kpi_tile("월 요금", fmt.fmt_big_split(cur["bill"])[0], unit=fmt.fmt_big_split(cur["bill"])[1] + "원",
                    sub=fmt.fmt_full(cur["bill"], "bill"), compact=True), unsafe_allow_html=True)
        c3.markdown(components.kpi_tile("월 탄소", fmt.fmt_big(cur["carbon"]), unit="kgCO₂",
                    sub=fmt.fmt_full(cur["carbon"], "carbon"), compact=True), unsafe_allow_html=True)
        c4.markdown(components.kpi_tile("감축 우선 검토", " · ".join(r["reduction_candidates"][:3]) or "—",
                    sub="탄소 배출 상위 3개 설비", compact=True), unsafe_allow_html=True)

        left, right = st.columns([1.4, 1])
        with left:
            st.plotly_chart(
                charts.module_compare_bar(
                    [{"module": x["module"], "mean": x["carbon"], "share": x["share"]} for x in r["ranking"]],
                    set(r["reduction_candidates"]), height=320,
                ),
                width="stretch", config=charts.PLOTLY_CONFIG,
            )
            st.caption("설비별 월 탄소(kgCO₂)와 비중 · 진한 막대 = 감축 우선 검토 대상")
        with right:
            rows = [
                {"설비": x["module"], "비중": f"{x['share']:.1f}%",
                 "일평균 증감": pct(x["daily_change_pct"])}
                for x in r["ranking"]
            ]
            st.dataframe(rows, hide_index=True, height=300, width="stretch")
            if r["watch"]:
                st.caption("이상 징후 관찰 설비: " + ", ".join(r["watch"]) + " (관찰 기록, 판정 아님)")
        st.caption("총량 증감에는 달마다 다른 날짜 수가 섞여 있어 사용 수준 변화는 일평균으로 판단합니다. "
                   "전년 대비는 데이터가 5개월뿐이라 제공하지 않습니다.")


# ---------------------------------------------------------------------------
def _render_strip(st, alerts: dict[str, Any]) -> None:
    errors = bool(alerts["errors"])
    html = components.alert_strip(
        alerts["count"],
        surge_count=0,
        pattern_count=alerts.get("pattern_count", 0),
        error=errors,
    )
    if alerts.get("pattern_count") and not errors:
        c1, c2 = st.columns([12, 1.4], vertical_alignment="center")
        c1.markdown(html, unsafe_allow_html=True)
        c2.button(
            "보기 ›", key="strip_view", type="tertiary",
            on_click=nav.go, args=(page_logic.PAGE_ANOMALY,),
        )
    else:
        st.markdown(html, unsafe_allow_html=True)


def _picked_period(st, rows, unit, start, end):
    """날짜(일)/주 선택지와 현재 선택. 위젯은 예측 패널에 있지만 KPI가 먼저 그려지므로
    session_state 값으로 미리 결정한다(첫 실행이면 첫 완전 구간)."""

    if unit == "시간":
        # 시간: 날짜 + 시작 시각을 골라 24시간 창(자정을 넘어갈 수 있음)
        options = page_logic.period_options(rows, "일", start=start, end=end)
        if not options:
            return options, None
        labels = [o["label"] for o in options]
        cur = st.session_state.get("home_pick_시간")
        day = options[labels.index(cur)] if cur in labels else options[0]
        hour = int(st.session_state.get("home_hour", 0) or 0)
        from datetime import datetime as _dt

        t0 = _dt.fromisoformat(day["key"]).replace(hour=hour)
        win = page_logic.window_rows(rows, t0, 24)
        label = f"{t0:%m-%d %H시}부터 24시간" + (f" · {len(win)}시간" if len(win) < 24 else "")
        return options, {**day, "hour": hour, "start_dt": t0, "window": win, "label": label}
    options = page_logic.period_options(rows, unit, start=start, end=end)
    if not options:
        return options, None
    labels = [o["label"] for o in options]
    cur = st.session_state.get(f"home_pick_{unit}")
    if cur in labels:
        return options, options[labels.index(cur)]
    return options, options[next((i for i, o in enumerate(options) if not o["partial"]), 0)]


def _watch_modules(pattern) -> int:
    from collections import Counter

    c = Counter(e["module"] for e in (pattern or {}).get("events", []))
    return sum(1 for n in c.values() if n >= config.STATUS_WATCH_PATTERN_COUNT)


def _peak_hour(rows, start, end):
    """선택 기간에서 예측 전력량이 가장 큰 시각."""

    s, e = page_logic.to_date(start), page_logic.to_date(end)
    best = None
    for r in rows:
        m = page_logic._row_dt(r["id"])
        if m is None or not page_logic._in_range(m.date(), s, e):
            continue
        if best is None or r["pred_pow"] > best[1]:
            best = (m, r["pred_pow"])
    return {"time": best[0].strftime("%m-%d %H시"), "pow": best[1]} if best else None


def _prev_window_delta(rows, picked) -> float | None:
    """24시간 창 vs 바로 앞 24시간 예측 전력량 변화율(%)."""

    from datetime import timedelta as _td

    cur = picked["window"]
    prev = page_logic.window_rows(rows, picked["start_dt"] - _td(hours=24), 24)
    if len(cur) < 24 or len(prev) < 24:
        return None
    a, b = sum(r["pred_pow"] for r in cur), sum(r["pred_pow"] for r in prev)
    return (a - b) / b * 100 if b else None


def _prev_period_delta(rows, unit, options, picked, settings) -> float | None:
    """선택 날짜/주 vs 직전 날짜/주 예측 전력량 변화율(%). 둘 다 완전 구간일 때만."""

    i = options.index(picked)
    if i == 0 or picked["partial"] or options[i - 1]["partial"]:
        return None
    cb = page_logic.period_bounds(unit, picked["key"])
    pb = page_logic.period_bounds(unit, options[i - 1]["key"])
    cur = page_logic.scaled_totals(rows, settings, start=cb[0], end=cb[1])["pow"]
    prev = page_logic.scaled_totals(rows, settings, start=pb[0], end=pb[1])["pow"]
    return (cur - prev) / prev * 100 if prev else None


def _render_kpis(st, bundle, ctx, forecast_ok, totals, buckets, unit, profile, kpi=None) -> None:
    settings = ctx["settings"]
    kpi = kpi or {"label": "선택 기간 전체", "alerts": ctx["alerts"], "delta": None, "anomaly_exp": None}
    alerts = kpi["alerts"]
    picked_mode = kpi["label"] != "선택 기간 전체"
    pow_delta = kpi["delta"] if picked_mode else (
        page_logic.delta_pct(buckets, "pred_pow", unit) if forecast_ok else None)
    exp = kpi["anomaly_exp"]
    hist = (f"{exp['hist_start']:%m/%d}–{exp['hist_end']:%m/%d}" if exp else "과거")
    evaluation = bundle["evaluation"]
    differs = page_logic.rates_differ_from_default(settings)
    chip = "설정값 기준" if differs else ""
    dash = "—"

    def v(fn, value):
        return fn(value) if forecast_ok else dash

    price = settings.get("carbon_price")
    cost = totals["carbon_cost"]
    mae = evaluation.get("mae_hourly_pow") if "error" not in evaluation else None
    mae_ok = isinstance(mae, dict)
    events = bundle["events"]
    status = bundle["module_status"]
    ev_ok = "error" not in events
    st_ok = "error" not in status

    tiles = {
        "pow": components.kpi_tile(
            "예측 전력량", v(fmt.fmt_big, totals["pow"]), unit=config.UNIT_LABELS["pow"],
            sub=(f"{kpi['label']} · {fmt.fmt_full(totals['pow'], 'pow')}") if forecast_ok else "Σ 예측(Wh) ÷ 1,000",
            icon_name="zap", tone="power",
            delta=pow_delta if forecast_ok else None,
        ),
        "bill": components.kpi_tile(
            "예측 요금", v(lambda x: fmt.fmt_big_split(x)[0], totals["bill"]),
            unit=(fmt.fmt_big_split(totals["bill"])[1] if forecast_ok else "") + config.UNIT_LABELS["bill"],
            sub=(fmt.fmt_full(totals["bill"], "bill") if forecast_ok else "") + f" · kWh × {settings['bill_rate']:g}",
            icon_name="wallet", tone="bill",
            chip_text=chip,
            delta=page_logic.delta_pct(buckets, "pred_pow", unit) if forecast_ok else None,
        ),
        "carbon": components.kpi_tile(
            "예측 탄소", v(fmt.fmt_big, totals["carbon"]), unit=config.UNIT_LABELS["carbon"],
            sub=(fmt.fmt_full(totals["carbon"], "carbon") if forecast_ok else "") + f" · kWh × {settings['carbon_rate']:g}",
            icon_name="leaf", tone="carbon",
            chip_text=chip,
            delta=page_logic.delta_pct(buckets, "pred_carbon", unit) if forecast_ok else None,
        ),
        "cost": components.kpi_tile(
            "예상 탄소비용(가정)",
            fmt.fmt_big_split(cost)[0] if (forecast_ok and cost is not None) else dash,
            unit=(fmt.fmt_big_split(cost)[1] + "원") if cost is not None else "",
            sub=(fmt.fmt_full(cost, "bill") + " · tCO₂ × 단가") if (forecast_ok and cost is not None) else "단가 설정 ›",
            icon_name="leaf", disabled=cost is None,
            chip_text="설정값 기준" if cost is not None else "",
        ),
        "mae": components.kpi_tile(
            "전력량 MAE(504)",
            fmt.fmt_mae(mae.get("mae504")) if mae_ok else dash, unit="Wh" if mae_ok else "",
            sub=(f"505 기준 {fmt.fmt_mae(mae.get('mae505'))} Wh · 시간당 평균 오차" if mae_ok else "evaluation.json 없음"),
            icon_name="target", tone="accuracy",
        ),
        "peak": (lambda pk: components.kpi_tile(
            "예측 최대 시간", pk["time"] if pk else dash,
            unit="",
            sub=(f"{kpi['label']} · {fmt.fmt_full(pk['pow'], 'pow')}" if pk else "예측 데이터 없음"),
            icon_name="clock", tone="power",
        ))(kpi.get("peak")),
        "events": components.kpi_tile(
            "예상 이상 행수", f"≈{fmt.fmt_count(round(exp['expected']))}",
            sub=f"5월 기록 없음 · {hist} 같은 요일·시각 평균",
            icon_name="anomaly", tone="anomaly",
        ) if exp else components.kpi_tile(
            "기간 이상 행수", fmt.fmt_count(events.get("count")) if ev_ok else dash,
            sub="규칙 3상 동시 · 관찰 기록", icon_name="anomaly", tone="anomaly",
        ),
        "surge": components.kpi_tile(
            "이상 급증 설비", f"{alerts['surge_count']}대" if not alerts["errors"] else dash,
            sub=f"{hist} 관측 · 평소의 {settings['anomaly_surge_factor']:g}배 이상", icon_name="pulse",
        ),
        "sudden": components.kpi_tile(
            "이상 탐지(급변)", f"{len((ctx.get('pattern') or {}).get('events', []))}건",
            sub=f"{hist} · 평균 ±{float(settings['anomaly_k']):g}σ 밖 + 급격한 변화",
            icon_name="anomaly", tone="anomaly",
        ),
        "watchmods": components.kpi_tile(
            "점검 필요 설비", f"{_watch_modules(ctx.get('pattern'))}대",
            sub=f"급변 {config.STATUS_WATCH_PATTERN_COUNT}건 이상 설비 (이상 ≥ {config.STATUS_ALERT_PATTERN_COUNT}건)",
            icon_name="alert_sq",
        ),
        "found": components.kpi_tile(
            "발견 설비",
            (lambda s: f"{s['count']} / {s['cap']}")(ui_logic.discovered_module_summary(status)) if st_ok else dash,
            sub=f"{hist} 관측 · 최대 13대", icon_name="grid",
        ),
        "alerts": components.kpi_tile(
            "경고 건수", str(alerts["count"]) if not alerts["errors"] else dash,
            sub="예측구간 이탈·요금 급증·이상 급증", icon_name="anomaly", icon_tone="warn",
        ),
    }
    for col, key in zip(st.columns(5), profile["kpis"]):
        col.markdown(tiles[key], unsafe_allow_html=True)
    foot_l, foot_r = st.columns([4, 1.4], vertical_alignment="center")
    foot_l.caption(fmt.unit_footnote())
    if price in (None, "") and "cost" in profile["kpis"] and "설정" in page_logic.pages_for_role(ctx["role"]):
        foot_r.button(
            "탄소 가격을 설정하세요 ›", key="cost_set_price", type="tertiary",
            on_click=nav.go, args=(page_logic.PAGE_SETTINGS,),
        )


def _render_chart(st, bundle, ctx, rows, profile, options=None, picked=None) -> None:
    """예측 그래프 — 예측값만 표시(검증 실제값 비교 없음).

    시간: 전체 기간 1시간 단위 · 일: 날짜 1개의 0–23시 · 주: 주 1개의 7일×24시간 ·
    월: 기간 내 일 합계.
    """

    settings = ctx["settings"]
    unit = ctx["gran"]
    start, end = ctx["start"], ctx["end"]
    if options is None:
        options, picked = _picked_period(st, rows, unit, start, end)
    with components.panel(height=H_MAIN, key=PANEL_CHART):
        head_l, head_r = st.columns([3, 6], vertical_alignment="center")
        with head_l:
            st.markdown(
                components.panel_header("예측", icon_name="forecast", desc=UNIT_DESC.get(unit, unit)),
                unsafe_allow_html=True,
            )
        with head_r:
            c_pick, c_metric, c_info = st.columns([4, 4, 1], vertical_alignment="center")
            with c_pick:
                if options and unit == "시간":
                    labels = [o["label"] for o in options]
                    p_day, p_hour = st.columns([3, 2])
                    cur_day = next((i for i, o in enumerate(options) if o["key"] == picked["key"]), 0)
                    p_day.selectbox("날짜", labels, index=cur_day, key="home_pick_시간",
                                    label_visibility="collapsed")
                    p_hour.selectbox("시작 시각", list(range(24)), index=picked["hour"], key="home_hour",
                                     format_func=lambda h: f"{h:02d}시부터", label_visibility="collapsed")
                elif options:
                    labels = [o["label"] for o in options]
                    choice = st.selectbox(
                        "날짜" if unit == "일" else "주", labels, index=options.index(picked),
                        key=f"home_pick_{unit}", label_visibility="collapsed",
                    )
                    picked = options[labels.index(choice)] if choice in labels else options[0]
            with c_metric:
                metric_label = st.segmented_control(
                    "지표", options=METRIC_OPTIONS, default=profile["metric"], key=f"home_metric_{ctx['role']}",
                    label_visibility="collapsed",
                )
            with c_info:
                with st.popover(":material/info:"):
                    st.markdown(
                        "- 상단 시간/일/주/월 선택에 따라 그래프 단위가 바뀜\n"
                        "- 시간: 날짜·시작 시각부터 24시간 · 일: 그 날 0–23시 · 주: 그 주 시간별 · 월: 일 합계\n"
                        "- 예측값만 표시(5월 실제값과의 비교는 표시하지 않음)\n"
                        "- 요금=전력량×단가, 탄소=전력량×배출계수라 세 지표의 모양은 같고 축만 다름"
                    )
        st.caption(_basis_sentence(bundle, settings))
        metric = _METRIC_KEY.get(metric_label or "요금", "bill")
        if not rows:
            st.markdown(
                components.error_state("예측 데이터를 불러오지 못함", artifact=tools_mod.PREDICTIONS_NAME),
                unsafe_allow_html=True,
            )
            return

        rate = {"pow": 1.0, "bill": float(settings["bill_rate"]), "carbon": float(settings["carbon_rate"])}[metric]
        if unit == "월":
            _render_period_view(st, rows, "일", start, end, metric, rate)
            return
        if unit in ("일", "주"):
            if picked is None:
                st.markdown(components.empty_state("선택 기간에 예측 데이터 없음"), unsafe_allow_html=True)
                return
            sel = page_logic.rows_in_period(rows, unit, picked["key"], start=start, end=end)
        else:  # 시간: 고른 시각부터 24시간
            sel = picked["window"] if picked else []
        if not sel:
            st.markdown(components.empty_state("선택 기간에 예측 데이터 없음"), unsafe_allow_html=True)
            return
        chart_rows = [
            {
                "id": r["id"],
                "pred_pow": r["pred_pow"] * rate,
                "pred_low": None if r.get("pred_low") is None else r["pred_low"] * rate,
                "pred_high": None if r.get("pred_high") is None else r["pred_high"] * rate,
            }
            for r in sel
        ]
        fig = charts.forecast_band_chart(
            chart_rows, actuals=None, height=theme.chart_height(H_MAIN, has_caption=True) - 26,
            metric=metric, color=theme.COLOR["accent"],
        )
        if unit == "일":
            fig.update_xaxes(tickformat="%H시", dtick=3 * 3600 * 1000, title="")
        elif unit == "시간":
            fig.update_xaxes(tickformat="%d일 %H시", dtick=2 * 3600 * 1000, title="")
        elif unit == "주":
            fig.update_xaxes(tickformat="%m-%d", dtick=24 * 3600 * 1000, title="")
        st.plotly_chart(fig, width="stretch", config=charts.PLOTLY_CONFIG)
        vals = [r["pred_pow"] for r in chart_rows]
        mean = sum(vals) / len(vals)
        imax = max(range(len(vals)), key=lambda k: vals[k])
        imin = min(range(len(vals)), key=lambda k: vals[k])
        u = config.UNIT_LABELS[metric]
        scope = picked["label"] if picked else "전체 기간"
        st.caption(
            f"{scope} · 최대 {sel[imax]['id'][5:13]}시 {vals[imax]:,.2f} {u} · "
            f"최소 {sel[imin]['id'][5:13]}시 {vals[imin]:,.2f} {u} · "
            f"차이 {vals[imax] - vals[imin]:,.2f} {u} ({(vals[imax] - vals[imin]) / mean * 100 if mean else 0:.2f}%) · "
            "y축 확대(0 시작 아님)"
        )


def _basis_sentence(bundle: dict[str, Any], settings: dict[str, Any]) -> str:
    meta = bundle.get("forecast_bpart_meta") or {}
    bp = bundle.get("forecast_bpart") or {}
    if "error" not in meta and "error" not in bp and bp.get("count"):
        return (
            f"{meta.get('label', meta.get('model', '—'))} 모델 · 2024-12-01 – {meta.get('train_end', '—')} 실측으로 학습, "
            f"이후는 자기 예측값을 이어 붙이는 재귀 예측 · {bp.get('count')}개 시점 · "
            f"요금=전력량×{settings['bill_rate']:g}, 탄소=전력량×{settings['carbon_rate']:g}"
        )
    b = page_logic.forecast_basis(bundle)
    d = lambda v: v if v else "—"  # noqa: E731
    return (
        f"학습 {d(b['train_start'])} – {d(b['train_end'])} 시간별 전력량으로 학습한 {d(b['model'])} 모델이 "
        f"요일·시간대 특성만으로 {d(b['pred_start'])} – {d(b['pred_end'])} 1시간 단위 "
        f"{d(b['points'])}개 시점을 예측 · 요금=전력량×{settings['bill_rate']:g}, "
        f"탄소=전력량×{settings['carbon_rate']:g}"
    )


def _merge_rows(rows: list[dict[str, Any]], avp: dict[str, Any]) -> list[dict[str, Any]]:
    actual_by_id = (
        {r["id"]: r["actual_pow"] for r in avp.get("rows", [])} if "error" not in avp else {}
    )
    return [{**r, "actual_pow": actual_by_id.get(r["id"])} for r in rows]


def _render_alert_panel(st, alerts: dict[str, Any]) -> None:
    with components.panel(height=H_MAIN, key=PANEL_ALERTS):
        st.markdown(
            components.panel_header("경고 목록", icon_name="bell", desc="이상 6종 · 급증"),
            unsafe_allow_html=True,
        )
        if alerts["errors"]:
            st.markdown(
                components.error_state("경고 데이터를 불러오지 못함", artifact=", ".join(alerts["errors"])),
                unsafe_allow_html=True,
            )
            return
        if not alerts["items"]:
            st.markdown('<div class="fec-calm">해당 기간 경고 없음</div>', unsafe_allow_html=True)
            return
        st.caption(f"총 {len(alerts['items'])}건 · 이상 6종(급변) 최근 것부터 · 패널 안에서 스크롤")
        # 수십 건을 st.markdown 하나씩 그리면 요소가 그만큼 늘어 느려진다 → 한 블록으로.
        st.markdown(
            "".join(
                components.alert_row(it["level"], it["title"], it["evidence"], it["time"])
                for it in alerts["items"]
            ),
            unsafe_allow_html=True,
        )


def _render_period_view(st, rows, unit, start, end, metric, rate, actual_rows=None) -> None:
    """헤더 단위(일/주/월)에 맞춘 예측 합계 — 시간 단위 차트와 다른 색."""

    label = {"pow": "전력량", "bill": "요금", "carbon": "탄소"}[metric]
    # 일 합계 차이가 0.01%대라 '16.90만'처럼 반올림하면 최대·최소가 같아 보인다 → 전체 숫자
    fmt_fn = lambda v: fmt.fmt_full(v, metric)  # noqa: E731
    tick = charts.TICK_FORMAT[metric]
    color = theme.COLOR["accent"]  # 시간 단위 차트와 같은 색(단위만 바뀌고 같은 예측값)
    u = config.UNIT_LABELS[metric]
    height = theme.chart_height(H_MAIN, has_caption=True) - 26
    src = actual_rows if actual_rows is not None else rows
    buckets = page_logic.resample_rows(src, unit, ("pred_pow", "actual_pow"), start=start, end=end)
    scaled = [
        {**b,
         "pred_pow": None if b.get("pred_pow") is None else b["pred_pow"] * rate,
         "actual_pow": None if b.get("actual_pow") is None else b["actual_pow"] * rate}
        for b in buckets
    ]
    if not scaled:
        st.markdown(components.empty_state("선택 기간에 예측 데이터 없음"), unsafe_allow_html=True)
        return
    show_act = actual_rows is not None
    if unit == "월" or len(scaled) < 3:
        # 예측 기간(5/10–5/31)이 한 달이 안 돼 월은 1개 구간뿐 → 점+선 대신 합계 막대
        st.plotly_chart(
            charts.period_total_bar(scaled, height=height, color=color, tickformat=tick,
                                    axis_title=f"{unit}별 예측 {label}({u})", fmt_fn=fmt_fn,
                                    show_actual=show_act),
            width="stretch", config=charts.PLOTLY_CONFIG,
        )
        days = sum(b["n"] for b in scaled) / 24
        st.caption(
            f"{unit} 합계 · 예측 기간에 포함된 {days:.0f}일분만 합산(월 전체 아님) · 0부터 시작하는 막대"
        )
        return
    st.plotly_chart(
        charts.period_bar_chart(
            scaled, height=height, color=color, tickformat=tick,
            axis_title=f"{unit}별 예측 {label}({u})", fmt_fn=fmt_fn, show_actual=show_act,
        ),
        width="stretch", config=charts.PLOTLY_CONFIG,
    )
    full = [b["pred_pow"] for b in scaled if not b["partial"] and b.get("pred_pow") is not None]
    if full:
        mean = sum(full) / len(full)
        spread = max(full) - min(full)
        st.caption(
            f"{unit} 합계 · y축 확대(0 시작 아님) · 최대-최소 차이 {spread:,.2f} {u} "
            f"({spread / mean * 100 if mean else 0:.2f}%) · 빈 원 = 부분 구간(평균·확대 범위에서 제외)"
        )


def _render_period_panel(st, rows, unit, start, end, forecast_ok) -> None:
    shown_unit = unit if unit != "시간" else "일"
    # 예측 기간이 5/10~5/31이라 월 단위는 1개 구간(부분)뿐 → 이 패널은 일 단위로 보여 준다
    if shown_unit == "월":
        shown_unit = "일"
    with components.panel(height=H_BOTTOM, key=PANEL_PERIOD):
        st.markdown(
            components.panel_header("일/주 단위 예측 전력량", icon_name="chart", desc=f"{shown_unit} 단위"),
            unsafe_allow_html=True,
        )
        if not forecast_ok:
            st.markdown(
                components.error_state(artifact=tools_mod.PREDICTIONS_NAME), unsafe_allow_html=True
            )
            return
        buckets = page_logic.resample_rows(
            rows, shown_unit, ("pred_pow", "pred_carbon"), start=start, end=end
        )
        st.plotly_chart(
            charts.period_bar_chart(buckets, height=theme.chart_height(H_BOTTOM, has_caption=True)),
            width="stretch", config=charts.PLOTLY_CONFIG,
        )
        full = [b["pred_pow"] for b in buckets if not b["partial"] and b.get("pred_pow") is not None]
        if full:
            mean = sum(full) / len(full)
            spread = max(full) - min(full)
            pct = spread / mean * 100 if mean else 0.0
            st.caption(f"y축 0 시작 아님 · {shown_unit}별 차이 {fmt.fmt_pow(spread)}({pct:.2f}%) · 빈 원 = 부분 구간")


def _render_cost_panel(st, totals, settings, forecast_ok) -> None:
    with components.panel(height=H_BOTTOM, key=PANEL_COST):
        st.markdown(
            components.panel_header("총비용 요약", icon_name="chart", tone="bill"),
            unsafe_allow_html=True,
        )
        if not forecast_ok:
            st.markdown(
                components.error_state(artifact=tools_mod.PREDICTIONS_NAME), unsafe_allow_html=True
            )
            return
        cost = totals["carbon_cost"]
        rows = [
            ["전기요금", fmt.fmt_bill(totals["bill"]), f"Σ pred_pow × {settings['bill_rate']:g}"],
            [
                "예상 탄소비용(가정)",
                fmt.fmt_bill(cost) if cost is not None else "단가 미설정",
                "탄소 합 ÷ 1000 × 단가" if cost is not None else "설정 페이지에서 입력",
            ],
            [
                "합계",
                fmt.fmt_bill(totals["total_cost"]) if totals["total_cost"] is not None else "—",
                "전기요금 + 탄소비용",
            ],
        ]
        st.markdown(
            components.html_table(["항목", "금액(척도)", "계산식"], rows, highlight_rows={2}),
            unsafe_allow_html=True,
        )
        cap = CARBON_UNIT_NOTE
        if page_logic.rates_differ_from_default(settings) or cost is not None:
            cap = "설정값 기준 · " + cap
        st.caption(cap)


def _render_report_panel(st, bundle, totals, settings) -> None:
    status = bundle["module_status"]
    evaluation = bundle["evaluation"]
    with components.panel(height=H_BOTTOM, key=PANEL_REPORT):
        st.markdown(
            components.panel_header("리포트 요약", icon_name="sparkles", desc="규칙 기반 3줄"),
            unsafe_allow_html=True,
        )
        lines: list[str] = []
        if totals["n"]:
            lines.append(
                f"선택 기간 예측 요금 {fmt.fmt_bill(totals['bill'])}, 탄소 {fmt.fmt_carbon(totals['carbon'])} 수준입니다."
            )
        else:
            lines.append(f"예측 합계: {tools_mod.NO_DATA}.")
        mae = evaluation.get("mae_hourly_pow") if "error" not in evaluation else None
        if isinstance(mae, dict):
            lines.append(f"전력량 MAE(504)는 {fmt.fmt_mae(mae.get('mae504'))}입니다 (참고용, 합격 기준 아님).")
        else:
            lines.append(f"MAE: {tools_mod.NO_DATA}.")
        top = ui_logic.top_anomaly_modules(status, limit=1) if "error" not in status else []
        if top:
            t = top[0]
            lines.append(
                f"이상 비율이 가장 높은 설비는 {t['module']} ({fmt.fmt_ratio_pct(t['anomaly_ratio'])}), 관찰 기록일 뿐 설비 판정이 아닙니다."
            )
        else:
            lines.append("규칙 기준 이상 징후 설비는 관찰되지 않았습니다.")
        st.markdown(components.ai_card_lines(lines), unsafe_allow_html=True)
        st.button(
            "AI Copilot에서 열기 ›", key="open_copilot", type="tertiary",
            on_click=nav.go, args=(page_logic.PAGE_COPILOT,),
        )


def _render_top_modules_panel(st, bundle) -> None:
    status = bundle["module_status"]
    with components.panel(height=H_BOTTOM, key=PANEL_TOP):
        st.markdown(
            components.panel_header("이상 비율 상위 설비", icon_name="anomaly", tone="anomaly"),
            unsafe_allow_html=True,
        )
        if "error" in status:
            st.markdown(
                components.error_state(artifact=tools_mod.MODULE_STATUS_NAME), unsafe_allow_html=True
            )
            return
        top = [r for r in ui_logic.top_anomaly_modules(status, limit=5) if r.get("anomaly_count")]
        if not top:
            st.markdown('<div class="fec-calm">기록된 이상 징후 설비 없음</div>', unsafe_allow_html=True)
            return
        rows = [
            [r["module"], fmt.fmt_count(r["anomaly_count"]), fmt.fmt_ratio_pct(r["anomaly_ratio"])]
            for r in top
        ]
        st.markdown(
            components.html_table(["설비", "이상 건수", "이상 비율"], rows), unsafe_allow_html=True
        )
        st.caption("관찰 기록일 뿐 설비 판정이 아님 (상태 기준 O-3 미확정)")


# ---------------------------------------------------------------------------
# 두 번째 화면: 예측 근거 (선택 기능, P2)
# ---------------------------------------------------------------------------
def _render_flow(st, bundle) -> None:
    with components.panel(height=H_FLOW, key=PANEL_FLOW):
        st.markdown(
            components.panel_header("분석 플로우", icon_name="pulse", desc="원본 → 집계 → 모델 → 예측"),
            unsafe_allow_html=True,
        )
        steps = page_logic.flow_steps(bundle)
        cols = st.columns(len(steps))
        for col, s in zip(cols, steps):
            col.markdown(
                components.flow_step(s["no"], s["title"], s["value"], s["caption"]),
                unsafe_allow_html=True,
            )
        selection = bundle.get("selection") or {}
        if "error" not in selection and selection.get("candidate_scores"):
            worse = [
                (m, sc) for m, sc in selection["candidate_scores"].items()
                if m != selection.get("selected_model")
            ]
            if worse:
                with st.expander(f"채택하지 않은 후보 ({len(worse)})"):
                    for m, sc in worse:
                        st.write(f"{m}: 검증 MAE {fmt.fmt_mae(sc)} — 선택 모델보다 높아 채택하지 않음")


def _render_models(st, bundle) -> None:
    evaluation = bundle["evaluation"]
    with components.panel(height=H_LOW, key=PANEL_MODELS):
        st.markdown(
            components.panel_header("베이스라인·후보 모델 비교", icon_name="chart", tone="accuracy"),
            unsafe_allow_html=True,
        )
        if "error" in evaluation or not evaluation.get("baseline_comparison"):
            st.markdown(
                components.error_state(artifact=tools_mod.EVALUATION_NAME), unsafe_allow_html=True
            )
            return
        sel = bundle.get("selection") or {}
        selected = sel.get("selected_model") if "error" not in sel else ui_logic.selected_baseline_model(evaluation)
        comp = evaluation["baseline_comparison"]
        has_may = any("may_mae" in r for r in comp)
        rows, hl = [], set()
        for i, row in enumerate(sorted(comp, key=lambda r: r.get("may_mae", r.get("validation_mae", 0)))):
            name = row.get("model", "")
            if name == selected:
                hl.add(i)
            label = page_logic.MODEL_LABELS.get(name, name)
            kind = "기준" if row.get("kind") == "baseline" else "후보"
            cells = [("● " if name == selected else "") + label, kind, fmt.fmt_mae(row.get("validation_mae"))]
            if has_may:
                cells.append(fmt.fmt_mae(row.get("may_mae")))
            rows.append(cells)
        heads = ["모델", "종류", "과거 검증 MAE"] + (["5월 MAE"] if has_may else [])
        st.markdown(components.html_table(heads, rows, highlight_rows=hl), unsafe_allow_html=True)
        st.caption(f"선정 모델: {page_logic.MODEL_LABELS.get(selected, selected) or tools_mod.NO_DATA} · "
                   "MAE 단위 Wh · 73/89는 참고용(합격 기준 아님)")


def _render_profile(st, rows, settings, forecast_ok, avp=None) -> None:
    with components.panel(height=H_LOW, key=PANEL_PROFILE):
        st.markdown(
            components.panel_header("시간대 프로파일", icon_name="clock", desc="0–23시 평균 예측 전력량"),
            unsafe_allow_html=True,
        )
        if not forecast_ok:
            st.markdown(
                components.error_state(artifact=tools_mod.PREDICTIONS_NAME), unsafe_allow_html=True
            )
            return
        pred = page_logic.hour_of_day_mean(rows, "pred_pow")
        act_rows = (avp or {}).get("rows", []) if "error" not in (avp or {}) else []
        act = page_logic.hour_of_day_mean(act_rows, "actual_pow") if act_rows else []
        st.plotly_chart(
            charts.hourly_profile_chart(
                [{"hour": h, "pow": v} for h, v in enumerate(pred)],
                actual=act or None, height=theme.chart_height(H_LOW, has_caption=True),
            ),
            width="stretch", config=charts.PLOTLY_CONFIG,
        )
        if pred:
            st.caption(
                f"y축 확대 · 시간대별 차이 예측 {max(pred) - min(pred):.3f} kWh"
                + (f" / 실제 {max(act) - min(act):.3f} kWh" if act else "")
                + " · 요금(×180)·탄소(×0.424)는 같은 모양이라 전력량으로 표시"
            )
