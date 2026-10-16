"""설비별 분석 페이지 (v3 §5, v4 equipment.png) — 학습 원본(hourly_from_full) 실측.

KPI 4 → [전력 소비 추이 | 설비 간 비교] → [부하 패턴 히트맵 | 3상 품질 지표].
설비명은 artifacts의 module 값 그대로 쓴다. 3상 품질 지표는 module_quality_hourly.parquet이
있을 때만 표시하고 없으면 "준비 중" 빈 상태를 둔다 (UI는 원본 CSV를 읽지 않는다, S7).
"""

from __future__ import annotations

from typing import Any

from src.app import page_logic
from src.app.ui import charts, components, fmt, theme

PANEL_TREND = "eq_trend"
PANEL_COMPARE = "eq_compare"
PANEL_PATTERN = "eq_pattern"
PANEL_QUALITY = "eq_quality"

H_MAIN = 320
H_LOW = 270
MAX_SELECT = 5  # 이보다 많이 고르면 추이 차트는 합계 한 줄로 표시
ALL_LABEL = "전체"
HOURLY_NAME = "hourly_from_full.parquet"
QUALITY_NAME = "module_quality_hourly.parquet"


def render(bundle: dict[str, Any], ctx: dict[str, Any]) -> None:
    import streamlit as st

    src = bundle.get("modules_hourly", {})
    if "frame" not in src:
        with components.panel(key="eq_error"):
            st.markdown(
                components.panel_header("설비별 전력", icon_name="grid"), unsafe_allow_html=True
            )
            st.markdown(components.error_state(artifact=HOURLY_NAME), unsafe_allow_html=True)
        return
    frame = src["frame"]
    start, end, unit, settings = ctx["start"], ctx["end"], ctx["gran"], ctx["settings"]

    means = page_logic.module_means(frame, start, end)
    all_modules = [r["module"] for r in means]
    if not all_modules:
        st.markdown(components.empty_state("선택 기간 설비 데이터 없음"), unsafe_allow_html=True)
        return

    all_modules = sorted(frame["module"].unique())
    default = [m for m in page_logic.top_modules(frame, 3) if m in all_modules]
    picked = st.multiselect(
        "설비 선택 (전체 또는 개별)",
        options=[ALL_LABEL] + all_modules,
        default=default,
        key="eq_modules",
    )
    all_mode = ALL_LABEL in picked
    selected = list(all_modules) if all_mode else picked
    if not selected:
        st.markdown(components.empty_state("선택한 설비 없음"), unsafe_allow_html=True)
        return

    _render_kpis(st, frame, means, selected, start, end, bundle)

    left, right = st.columns([1.45, 1])
    with left:
        _render_trend(st, frame, selected, unit, start, end, total=all_mode or len(selected) > MAX_SELECT)
    with right:
        _render_compare(st, means, set(selected))

    left, right = st.columns([1.45, 1])
    with left:
        _render_pattern(st, frame, selected, start, end, settings, bundle)
    with right:
        _render_quality(st, bundle)
    st.caption("예측은 공장 전체 단위입니다. 이 페이지는 학습 원본의 설비별 실측입니다.")


def _render_kpis(st, frame, means, selected, start, end, bundle) -> None:
    by_module = {r["module"]: r for r in means}
    chosen = [by_module[m] for m in selected if m in by_module]
    avg = sum(r["mean"] for r in chosen) / len(chosen) if chosen else None
    share = sum(r["share"] for r in chosen) if chosen else None
    peak = page_logic.peak_hour_of_day(frame, selected, start, end)
    quality = page_logic.quality_summary(bundle.get("quality", {"error": {}}))
    pf_text, pf_sub = "—", "module_quality_hourly 필요"
    if quality:
        pf_vals = [q["pf"] for q in quality if q["module"] in selected]
        if pf_vals:
            pf_text, pf_sub = f"{sum(pf_vals) / len(pf_vals):.1f}%", "(PF_R+PF_S+PF_T)/3"
    tiles = [
        components.kpi_tile("선택 설비 평균 전력", fmt.fmt_pow(avg), unit="kWh", sub="설비당 평균 시간당 전력", compact=True),
        components.kpi_tile(
            "최대 부하 시간대", f"{peak:02d}시" if peak is not None else "—",
            sub="선택 설비 평균 기준", compact=True,
        ),
        components.kpi_tile(
            "선택 설비 전력 비중", f"{share:.1f}%" if share is not None else "—",
            sub="13개 설비 평균 전력 합 대비", compact=True,
        ),
        components.kpi_tile("평균 역률*", pf_text, sub=pf_sub, compact=True),
    ]
    for col, tile in zip(st.columns(4), tiles):
        col.markdown(tile, unsafe_allow_html=True)


def _render_trend(st, frame, selected, unit, start, end, total: bool = False) -> None:
    with components.panel(height=H_MAIN, key=PANEL_TREND):
        st.markdown(
            components.panel_header("전력 소비 추이", icon_name="forecast", desc=f"{unit} 단위 평균"),
            unsafe_allow_html=True,
        )
        series = page_logic.module_trend(frame, selected, unit, start, end)
        if total and series:
            # 선이 너무 많아지는 경우(전체·6개 이상) 선택 설비 합계 한 줄로 표시
            acc: dict = {}
            for xs, ys in series.values():
                for x, y in zip(xs, ys):
                    acc[x] = acc.get(x, 0.0) + y
            keys = sorted(acc)
            series = {f"선택 {len(selected)}개 합계": (keys, [acc[k] for k in keys])}
        st.plotly_chart(
            charts.module_trend_chart(series, height=theme.chart_height(H_MAIN)),
            width="stretch", config=charts.PLOTLY_CONFIG,
        )


def _render_compare(st, means, selected: set[str]) -> None:
    with components.panel(height=H_MAIN, key=PANEL_COMPARE):
        st.markdown(
            components.panel_header("설비 간 비교", icon_name="grid", desc=f"{len(means)}개 설비"),
            unsafe_allow_html=True,
        )
        st.plotly_chart(
            charts.module_compare_bar(means, selected, height=theme.chart_height(H_MAIN)),
            width="stretch", config=charts.PLOTLY_CONFIG,
        )


PATTERN_VIEWS = ["최근 1시간", "최근 24시간", "일별", "요일×시간"]


def _render_pattern(st, frame, selected, start, end, settings, bundle=None) -> None:
    with components.panel(height=H_LOW, key=PANEL_PATTERN):
        head_l, head_m, head_r = st.columns([2, 3.2, 1.8], vertical_alignment="center")
        with head_l:
            st.markdown(
                components.panel_header("부하 패턴", icon_name="clock"),
                unsafe_allow_html=True,
            )
        with head_m:
            view = st.segmented_control(
                "보기", PATTERN_VIEWS, default=PATTERN_VIEWS[1], key="eq_pattern_view",
                label_visibility="collapsed",
            ) or PATTERN_VIEWS[1]
        with head_r:
            module = st.selectbox(
                "설비", options=selected, key="eq_pattern_module", label_visibility="collapsed"
            )
        if view != "요일×시간":
            series = page_logic.load_series(bundle or {}, module, view, start, end)
            if not series:
                st.markdown(components.empty_state("부하 패턴 데이터 없음 (원본 집계 필요)"),
                            unsafe_allow_html=True)
                return
            st.plotly_chart(
                charts.load_series_chart(series, height=theme.chart_height(H_LOW, has_caption=True)),
                width="stretch", config=charts.PLOTLY_CONFIG,
            )
            st.caption(series["caption"])
            return
        pattern = page_logic.load_pattern(frame, module, start, end)
        st.plotly_chart(
            charts.load_pattern_heatmap(
                pattern, page_logic.night_hours(settings), height=theme.chart_height(H_LOW)
            ),
            width="stretch", config=charts.PLOTLY_CONFIG,
        )


def _render_quality(st, bundle) -> None:
    with components.panel(height=H_LOW, key=PANEL_QUALITY):
        st.markdown(
            components.panel_header("3상 품질 지표*", icon_name="cpu"), unsafe_allow_html=True
        )
        quality = page_logic.quality_summary(bundle.get("quality", {"error": {}}))
        if not quality:
            st.markdown(
                components.empty_state(f"준비 중 — {QUALITY_NAME} 필요"), unsafe_allow_html=True
            )
            return
        rows = [
            [
                q["module"],
                f"{q['pf']:.1f}%",
                f"{q['unbalance']:.2f}%" if "unbalance" in q else "—",
                f"{q['reactive']:,.0f} var" if "reactive" in q else "—",
            ]
            for q in sorted(quality, key=lambda q: q["module"])
        ]
        st.markdown(
            components.html_table(["설비", "평균 역률", "전류 불평형률", "무효전력 평균"], rows),
            unsafe_allow_html=True,
        )
        st.caption("PF = (PF_R+PF_S+PF_T)/3 · 불평형률 = max|I−Iavg| / Iavg × 100")
