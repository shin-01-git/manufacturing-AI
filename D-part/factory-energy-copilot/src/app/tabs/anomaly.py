"""이상 탐지 페이지 (v2 §5, facility 기본).

요약 카드 3개 → 설비 상태표(행 선택)+탐지 시간대(3-뷰 클릭 드릴다운) → 방법 비교
2표+이벤트 로그 → 행 선택 드릴다운(헤더 + '관찰 기록일 뿐 설비 판정이 아님' →
전압/역률 3상 동기 차트 또는 anomaly_events 점 그래프 대체 → 세 구획: 관찰 사실/
원인 후보/현장 확인 → 시간당 이상 vs 평소). status는 STATUS_THRESHOLDS_APPROVED=
False면 전부 '미확정' (O-3).

모든 정보 덩어리(KPI 제외)는 st.container(border=True, height=H) 패널 안에 두고
제목은 panel_header로 둔다 (§A-8). 상태표 행 선택은 st.dataframe의 on_select로,
module명은 artifacts 값 그대로. 설비 상세 드릴다운은 연속 시계열 미포함 환경이라
anomaly_events 점(scatter)만 그리는 (b) 방식이다 (anomaly_context 미존재).

탐지 시간대 패널(PANEL_HEATMAP)은 anomaly_hourly_frame(pandas DataFrame, bundle)
기반 3-뷰(일별 추이/시간대 패턴/일자 상세) 클릭 드릴다운이다. 유형 칩(raw_type)은
위쪽 페이지 레벨 필터를 그대로 받아 쓰고 이 패널 안에서 새로 만들지 않는다.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from config import config
from src.agent import tools as tools_mod
from src.app import page_logic, ui_logic
from src.app.ui import charts, components, fmt, theme

SELECTED_KEY = "selected_module"
STATUS_TABLE_KEY = "anomaly_status_table"

PANEL_STATUS = "an_status"
PANEL_HEATMAP = "an_heatmap"
PANEL_METHODS = "an_methods"
PANEL_EVENTS = "an_events"
PANEL_DRILL = "an_drill"

ROW_MID_H = 330
ROW_LOW_H = 420
ROW_BOTTOM_H = 270

# 탐지 시간대 패널 (§2) 세션 키 — 바/셀 클릭 드릴다운이 공유하는 상태.
TL_VIEWS = ["일별 추이", "시간대 패턴", "일자 상세"]
TL_VIEW_KEY = "tl_view"
TL_MODULE_KEY = "tl_module"
TL_DAY_KEY = "tl_day"
TL_DAY_INPUT_KEY = "tl_day_input"
TL_HOUR_KEY = "tl_hour"


def render(
    status: dict[str, Any],
    events: dict[str, Any],
    trend: dict[str, Any],
    rebuild_meta: dict[str, Any] | None,
    *,
    selected_modules: list[str] | None = None,
    date_range: tuple[str | None, str | None] | None = None,
    artifacts_dir=None,
    settings: dict[str, Any] | None = None,
    role: str = config.DEFAULT_ROLE,
    hourly_frame: dict[str, Any] | None = None,
) -> None:
    import streamlit as st

    # Missing artifacts -> error state (not a calm "0건"), numbers withheld (v3 §8).
    missing = [
        name
        for name, data in (
            (tools_mod.MODULE_STATUS_NAME, status),
            (tools_mod.ANOMALY_EVENTS_NAME, events),
            (tools_mod.ANOMALY_HOURLY_NAME, trend),
        )
        if "error" in data
    ]
    if missing:
        with components.panel(key="an_error"):
            st.markdown(
                components.panel_header("이상치 모니터링", icon_name="anomaly", tone="anomaly"),
                unsafe_allow_html=True,
            )
            st.markdown(
                components.error_state("이상 탐지 데이터를 불러오지 못함", artifact=", ".join(missing)),
                unsafe_allow_html=True,
            )
        return

    # In-page filters: equipment multiselect + type pills (v3 §7). Options come from data.
    module_ids = [r["module"] for r in status.get("modules", [])]
    chosen = st.multiselect(
        "설비 선택 (비우면 전체)", options=module_ids, default=[], key="an_modules", placeholder="전체"
    )
    selected_modules = chosen or None

    options = page_logic.anomaly_type_options(events)
    raw_type = None
    if options:
        labels = {"전체": None, **{f"{o['label']} ({fmt.fmt_count(o['count'])})": o["type"] for o in options}}
        with st.container(key="typepills"):
            pick = st.pills(
                "이상 유형", options=list(labels), default="전체", selection_mode="single",
                key="an_type", label_visibility="collapsed",
            )
        raw_type = labels.get(pick or "전체")

    status = ui_logic.filter_status_by_modules(status, selected_modules)
    events = ui_logic.filter_events_by_modules(events, selected_modules)
    trend = ui_logic.filter_trend_by_modules(trend, selected_modules)
    if date_range:
        events = ui_logic.filter_events_by_date(events, date_range[0], date_range[1])
    events = page_logic.filter_events_by_type(events, raw_type)
    trend = page_logic.filter_trend_by_type(trend, raw_type)

    model = ui_logic.anomaly_tab_model(status, events, trend)

    _render_summary(st, status, model)

    merged_settings = page_logic.merged_settings(settings)
    status_col, heat_col = st.columns([1.65, 1])
    selected = None
    with status_col:
        selected = _render_status_table(st, model, trend)
    with heat_col:
        _render_detection_timeline(
            st, hourly_frame or {}, raw_type, date_range, merged_settings, artifacts_dir
        )

    _render_reasons(st, model)

    # 경영진 view: summary only (KPI + 상태표 + 타임라인); detail panels are for the
    # facility / energy roles.
    if not page_logic.role_profile(role)["anomaly_detail"]:
        st.caption("경영진 보기: 요약만 표시합니다. 이벤트 로그·방법 비교·설비 상세는 담당자 역할에서 확인하세요.")
        return

    method_col, event_col = st.columns([1, 1.6])
    with method_col:
        _render_method_comparison(st, rebuild_meta)
    with event_col:
        _render_event_log(st, model)

    _render_drilldown(st, status, trend, selected, artifacts_dir)


_REASON_CACHE: dict[tuple, list] = {}


def _cached_reasons(events, status_rows):
    """6.5만 행 집계(~0.4s)를 rerun마다 하지 않도록 필터 결과의 지문으로 캐시."""

    key = (
        len(events),
        str(events[0].get("localtime")) if events else "",
        str(events[-1].get("localtime")) if events else "",
        tuple(sorted((r["module"], r.get("anomaly_ratio")) for r in status_rows)),
    )
    if key not in _REASON_CACHE:
        if len(_REASON_CACHE) > 16:
            _REASON_CACHE.clear()
        _REASON_CACHE[key] = page_logic.anomaly_reasons(events, status_rows)
    return _REASON_CACHE[key]


def _render_reasons(st, model: dict[str, Any]) -> None:
    """설비 상태표의 이상 비율 아래: 왜 이상으로 판단했는지(기준·측정값·시각·월별)."""

    reasons = _cached_reasons(model["events"], model["status_rows"])
    with components.panel(key="an_reasons"):
        st.markdown(
            components.panel_header("이상 판단 근거", icon_name="anomaly", tone="anomaly",
                                    desc="설비 상태표 이상 비율의 이유"),
            unsafe_allow_html=True,
        )
        if not reasons:
            st.markdown(components.empty_state("선택 조건의 이상 기록 없음"), unsafe_allow_html=True)
            return
        cols = st.columns(min(len(reasons), 3))
        for i, r in enumerate(reasons):
            with cols[i % len(cols)]:
                thr = f"{r['threshold']:g}{r['unit']}" if r["threshold"] is not None else "—"
                ratio = f"{r['ratio'] * 100:.2f}%" if r["ratio"] is not None else "—"
                months = " · ".join(f"{m[5:]}월 {fmt.fmt_count(n)}" for m, n in r["months"])
                st.markdown(f"**{r['module']}** — {r['label']} · 이상 비율 {ratio}")
                st.markdown(
                    f"- **판단 기준**: 5초 측정값에서 {r['metric']}이 R·S·T **세 상 모두 {thr} 미만**인 행\n"
                    f"- **측정값**: 중앙 {r['median']:.1f}{r['unit']} "
                    f"(범위 {r['lo']:.1f}–{r['hi']:.1f}{r['unit']}) → 기준보다 "
                    f"{r['gap']:.1f}{'%p' if r['unit'] == '%' else r['unit']} 낮음\n"
                    f"- **건수**: {fmt.fmt_count(r['count'])}행 / 전체 측정 행의 {ratio}\n"
                    + (f"- **발생 시각**: {r['peak_hour']:02d}시에 집중 ({r['peak_hour_share'] * 100:.1f}%)\n"
                       if r["peak_hour_share"] >= 1.5 / 24 else
                       f"- **발생 시각**: 특정 시간대 쏠림 없음 (최다 {r['peak_hour']:02d}시도 "
                       f"{r['peak_hour_share'] * 100:.1f}% ≈ 1/24)\n")
                    + 
                    f"- **기간**: {r['first']:%Y-%m-%d %H:%M} – {r['last']:%Y-%m-%d %H:%M}\n"
                    f"- **월별**: {months}\n"
                    f"- **의미**: {r['rule']}"
                )
        st.caption(
            "이상 비율 = 규칙에 걸린 5초 측정 행 ÷ 해당 설비 전체 측정 행. 비율은 1% 안팎이지만 거의 매시간 "
            "발생해 일시적 사건보다 상시 운전 조건(기준값·설비 특성)을 먼저 확인할 대상입니다. "
            "비지도 탐지(IsolationForest)는 전체의 21.8%를 이상으로 잡는 과탐지라 상태 판단에서 제외."
        )


def _status_approved() -> bool:
    return bool(config.STATUS_THRESHOLDS_APPROVED)


def _render_summary(st, status: dict[str, Any], model: dict[str, Any]) -> None:
    summary = ui_logic.discovered_module_summary(status)
    found = summary["count"]
    cap = summary["cap"]

    if _status_approved():
        watch = sum(1 for r in model["status_rows"] if r["status"] != "정상")
        watch_text = str(watch)
    else:
        watch_text = "기준 미확정"

    cards = [
        components.kpi_tile(
            "발견 설비", f"{found} / {cap}", sub="O-5 최대 13 상한", compact=True
        ),
        components.kpi_tile(
            "기간 이상 행수", fmt.fmt_count(model["event_count"]),
            sub="anomaly_events 건수 · 관찰 기록", tone="anomaly", compact=True,
        ),
        components.kpi_tile(
            "주의 설비", watch_text,
            sub="O-3 경계값 미승인 시 '기준 미확정'",
            disabled=not _status_approved(), compact=True,
        ),
    ]
    for col, card in zip(st.columns(3), cards):
        col.markdown(card, unsafe_allow_html=True)
    notice = model["module_notice"]
    if notice:
        st.caption(notice)


def _render_status_table(st, model: dict[str, Any], trend: dict[str, Any]) -> str | None:
    with components.panel(height=ROW_MID_H, key=PANEL_STATUS):
        st.markdown(
            components.panel_header(
                "설비 상태표", icon_name="grid", desc="행 선택 → 하단 상세"
            ),
            unsafe_allow_html=True,
        )
        if not model["status_rows"]:
            st.markdown(
                components.empty_state(tools_mod.MODULE_STATUS_NAME),
                unsafe_allow_html=True,
            )
            return None

        sorted_rows = ui_logic.status_rows_sorted(
            {"modules": [dict(r) for r in model["status_rows"]]}
        )
        sparks = ui_logic.module_sparklines(trend)
        module_order = [r["module"] for r in sorted_rows]

        rows = []
        for r in sorted_rows:
            count = int(r.get("anomaly_count", 0) or 0)
            has_record = bool(r.get("last_seen"))
            label = r["status"] if _status_approved() else "미확정"
            rows.append(
                {
                    "설비": r["module"],
                    "상태": components.status_text(label, has_record=has_record),
                    "이상 건수": count,
                    "이상 비율": float(r.get("anomaly_ratio", 0.0) or 0.0),
                    "24h 추세": sparks.get(r["module"], []),
                    "최근 관측": ui_logic.fmt_localtime_short(r.get("last_seen")),
                    "_full_last_seen": r.get("last_seen") or "",
                }
            )

        # ProgressColumn renders the raw stored value with its format string, so
        # store ratio×100 and label with %.2f%% (§7-2: 0.0050 -> 0.50%).
        for row in rows:
            row["이상 비율"] = row["이상 비율"] * 100.0
        max_ratio_pct = max((row["이상 비율"] for row in rows), default=0.0) or 1.0

        selected_module = None
        try:
            import streamlit as _st

            selection = _st.dataframe(
                rows,
                width="stretch",
                hide_index=True,
                column_config={
                    "이상 비율": _st.column_config.ProgressColumn(
                        "이상 비율",
                        help="이상 행수 / 전체 행수",
                        min_value=0.0,
                        max_value=max_ratio_pct,
                        format="%.2f%%",
                    ),
                    "24h 추세": _st.column_config.LineChartColumn("추세", width="small"),
                    "최근 관측": _st.column_config.TextColumn("최근 관측"),
                    "_full_last_seen": None,
                },
                on_select="rerun",
                selection_mode="single-row",
                key=STATUS_TABLE_KEY,
                height=theme.chart_height(ROW_MID_H, has_caption=True),
            )
            sel_rows = (
                selection.get("selection", {}).get("rows", [])
                if isinstance(selection, dict)
                else getattr(getattr(selection, "selection", None), "rows", [])
            )
            if sel_rows:
                idx = sel_rows[0]
                if 0 <= idx < len(module_order):
                    selected_module = module_order[idx]
        except Exception:  # pragma: no cover - older streamlit
            _st = __import__("streamlit")
            _st.dataframe(rows, width="stretch", hide_index=True)

        if not _status_approved():
            st.caption(
                "상태 경계값 미승인(O-3) — 모든 상태를 '미확정'으로 표시하고 이상 비율만 참고합니다."
            )
        if selected_module:
            st.session_state[SELECTED_KEY] = selected_module
        return selected_module


def _tl_type_labels(raw_type: str | None) -> dict[str, str]:
    """Type-label map for chart hovers; limited to the active filter (or all known)."""

    if raw_type:
        return {raw_type: page_logic.type_label(raw_type)}
    return dict(config.ANOMALY_TYPE_LABELS)


def _tl_goto_detail(st, module: str, day: Any) -> None:
    """Click-drilldown entry: jump §2.1/§2.2 -> §2.3 with the clicked (day, module)."""

    st.session_state[TL_MODULE_KEY] = module
    st.session_state[TL_DAY_KEY] = str(day)
    st.session_state[TL_VIEW_KEY] = "일자 상세"
    st.session_state.pop(TL_HOUR_KEY, None)
    st.rerun()


def _render_detection_timeline(
    st,
    hourly_frame: dict[str, Any],
    raw_type: str | None,
    date_range: tuple[str | None, str | None] | None,
    settings: dict[str, Any],
    artifacts_dir=None,
) -> None:
    """탐지 시간대 패널 (§2): 일별 추이 / 시간대 패턴 / 일자 상세, 3-뷰 클릭 드릴다운.

    ``hourly_frame`` is ``bundle["anomaly_hourly_frame"]`` (``{"frame": DataFrame}``
    or an error dict). ``raw_type`` is the page-level type-chip filter (shared,
    not a new widget here per the plan). ``settings`` carries
    ``anomaly_surge_factor`` / ``night_start`` / ``night_end`` (merged defaults).
    """

    with components.panel(height=ROW_MID_H, key=PANEL_HEATMAP):
        head_l, head_r = st.columns([3, 2], vertical_alignment="center")
        with head_l:
            st.markdown(
                components.panel_header("탐지 시간대", icon_name="chart", tone="anomaly"),
                unsafe_allow_html=True,
            )
        with head_r:
            view = st.segmented_control(
                "뷰", options=TL_VIEWS, default=TL_VIEWS[0],
                key=TL_VIEW_KEY, label_visibility="collapsed",
            ) or TL_VIEWS[0]

        if "error" in hourly_frame or "frame" not in hourly_frame:
            st.markdown(
                components.empty_state(tools_mod.ANOMALY_HOURLY_NAME), unsafe_allow_html=True
            )
            return
        trend = hourly_frame["frame"]
        start, end = date_range if date_range else (None, None)
        scoped = page_logic.slice_trend(trend, None, raw_type, start, end)

        all_modules = sorted(trend["module"].unique()) if len(trend) else []
        modules = page_logic.active_modules(scoped)
        dropped = max(len(all_modules) - len(modules), 0)
        if not modules:
            st.markdown(components.empty_state("선택 조건의 탐지 없음"), unsafe_allow_html=True)
            return

        module = st.selectbox(
            "설비", options=modules, key=TL_MODULE_KEY, label_visibility="collapsed",
        )
        if dropped:
            st.caption(f"기록 없는 설비 {dropped}대는 표시하지 않음")

        _render_tl_chips(st, scoped, module, settings)

        h = theme.chart_height(ROW_MID_H, has_caption=bool(dropped))
        if view == "일별 추이":
            _render_tl_daily(st, scoped, modules, raw_type, settings, h)
        elif view == "시간대 패턴":
            _render_tl_heatmap(st, scoped, module, settings, h)
        else:
            _render_tl_day_detail(st, scoped, module, raw_type, h, artifacts_dir)


def _render_tl_chips(st, scoped, module: str, settings: dict[str, Any]) -> None:
    """§4 요약 칩 3개: 발생 시간 비율 / 일평균 N건 / 급증일 K일."""

    ratio = page_logic.occurrence_hour_ratio(scoped, module)
    daily = page_logic.daily_counts(scoped, [module])
    avg = float(daily["count"].mean()) if len(daily) else 0.0
    factor = float(settings.get("anomaly_surge_factor", config.DEFAULT_ANOMALY_SURGE_FACTOR))
    surges = page_logic.surge_days(daily, factor)

    ratio_text = fmt.fmt_ratio_pct(ratio)

    # 칩 3개를 고정 폭 컬럼에 나눠 넣으면 긴 문구가 옆 칩을 덮는다 → 한 줄 flex(줄바꿈 허용)로
    # 짧은 수치만 칩에 두고, 해석 문구는 칩 아래 캡션 한 줄로 뺀다.
    chips = [
        components.chip(f"발생 시간 비율 {ratio_text}", tone="anomaly"),
        components.chip(f"일평균 {fmt.fmt_count_per_day(avg)}", tone="muted"),
        components.chip(f"급증일 {fmt.fmt_count(len(surges))}일", tone="watch" if len(surges) else "muted"),
    ]
    st.markdown(
        '<div style="display:flex;flex-wrap:wrap;gap:6px;margin:2px 0 4px">' + "".join(chips) + "</div>",
        unsafe_allow_html=True,
    )
    if ratio > 0.9:
        st.caption(f"{module} · 거의 매시간 발생 → 개별 사건보다 상시 상태. 기준값·운전 조건 확인 필요")


def _render_tl_daily(st, scoped, modules: list[str], raw_type, settings, height: int) -> None:
    factor = float(settings.get("anomaly_surge_factor", config.DEFAULT_ANOMALY_SURGE_FACTOR))
    daily = page_logic.daily_counts(scoped, modules, raw_type)
    surges = page_logic.surge_days(daily, factor)
    if len(daily) == 0:
        st.markdown(components.empty_state("선택 조건의 탐지 없음"), unsafe_allow_html=True)
        return
    fig = charts.anomaly_daily_small_multiples(
        daily, surges,
        modules=modules, surge_factor=factor,
        accent_color=theme.COLOR["accent"], alert_color=theme.COLOR["alert"],
        muted_color=theme.COLOR["muted"], type_labels=_tl_type_labels(raw_type),
        height=height,
    )
    selection = st.plotly_chart(
        fig, width="stretch", config=charts.PLOTLY_CONFIG,
        on_select="rerun", selection_mode="points", key="tl_daily",
    )
    points = _plotly_selected_points(selection)
    if points:
        custom = points[0].get("customdata") or []
        if len(custom) >= 3:
            _tl_goto_detail(st, custom[2], custom[1])


def _render_tl_heatmap(st, scoped, module: str, settings, height: int) -> None:
    matrix = page_logic.hour_by_day_matrix(
        scoped, module, weekly_if_days_over=60,
    )
    if not matrix["cols"]:
        st.markdown(components.empty_state("선택 조건의 탐지 없음"), unsafe_allow_html=True)
        return
    if matrix["unit"] == "주":
        st.caption("주 단위로 묶어 표시")
    night = page_logic.night_hours(settings)
    fig = charts.anomaly_hour_heatmap_with_sidebar(
        matrix, night_hours=night, accent_color=theme.COLOR["accent"],
        low_color=theme.token("heat_low"), faint_color=theme.token("faint"), height=height,
    )
    selection = st.plotly_chart(
        fig, width="stretch", config=charts.PLOTLY_CONFIG,
        on_select="rerun", selection_mode="points", key="tl_hour_heat",
    )
    points = _plotly_selected_points(selection)
    if points:
        custom = points[0].get("customdata")
        day_iso = custom if isinstance(custom, str) else (custom[0] if custom else None)
        if day_iso:
            from datetime import date as _date

            day = _date.fromisoformat(day_iso)
            if matrix["unit"] == "주":
                day = day - timedelta(days=day.weekday())  # 월요일로 매핑 (설계 결정 3)
            _tl_goto_detail(st, module, day)


def _render_tl_day_detail(st, scoped, module: str, raw_type, height: int, artifacts_dir=None) -> None:
    from datetime import date as _date, datetime as _datetime

    all_days = sorted(scoped[scoped["module"] == module]["hour"].dt.date.unique())
    if not all_days:
        st.markdown(components.empty_state("선택 조건의 탐지 없음"), unsafe_allow_html=True)
        return

    stored_day = st.session_state.get(TL_DAY_KEY)
    try:
        current_day = _date.fromisoformat(str(stored_day)[:10]) if stored_day else all_days[-1]
    except ValueError:
        current_day = all_days[-1]
    if current_day not in all_days:
        current_day = min(all_days, key=lambda d: abs((d - current_day).days))

    nav_prev, nav_date, nav_next, nav_label = st.columns([1, 2, 1, 3], vertical_alignment="center")
    idx = all_days.index(current_day)
    with nav_prev:
        if st.button("◀ 이전 날", key="tl_prev_day", disabled=idx <= 0, width="stretch"):
            current_day = all_days[idx - 1]
            st.session_state[TL_DAY_KEY] = str(current_day)
            st.session_state.pop(TL_HOUR_KEY, None)
            st.rerun()
    with nav_date:
        picked = st.date_input(
            "날짜", value=current_day, min_value=all_days[0], max_value=all_days[-1],
            key=TL_DAY_INPUT_KEY, label_visibility="collapsed",
        )
        if picked != current_day:
            current_day = picked
            st.session_state[TL_DAY_KEY] = str(current_day)
            st.session_state.pop(TL_HOUR_KEY, None)
            st.rerun()
    with nav_next:
        if st.button("다음 날 ▶", key="tl_next_day", disabled=idx >= len(all_days) - 1, width="stretch"):
            current_day = all_days[idx + 1]
            st.session_state[TL_DAY_KEY] = str(current_day)
            st.session_state.pop(TL_HOUR_KEY, None)
            st.rerun()

    profile = page_logic.day_hour_profile(scoped, module, current_day)
    usual = page_logic.usual_hour_profile(scoped, module)
    day_total = sum(p["count"] for p in profile)
    usual_total = sum(usual)
    with nav_label:
        st.caption(f"{module} · 그날 {fmt.fmt_count_per_day(day_total)} / 평소 {fmt.fmt_count_per_day(usual_total)}")

    type_colors = {
        "voltage_low_3phase": theme.phase("r"),
        "power_factor_low_3phase": theme.phase("s"),
    }
    fig = charts.anomaly_24h_stacked_bar(
        profile, usual, type_colors=type_colors, type_labels=_tl_type_labels(raw_type),
        muted_color=theme.COLOR["muted"], height=max(height - 60, 160),
    )
    selection = st.plotly_chart(
        fig, width="stretch", config=charts.PLOTLY_CONFIG,
        on_select="rerun", selection_mode="points", key="tl_hourbar",
    )
    points = _plotly_selected_points(selection)
    clicked_hour = int(points[0]["x"]) if points and "x" in points[0] else None
    if clicked_hour is not None:
        st.session_state[TL_HOUR_KEY] = clicked_hour

    hour = st.session_state.get(TL_HOUR_KEY)
    day_events = tools_mod.get_anomaly_events(
        module,
        start=_datetime.combine(current_day, _datetime.min.time()),
        end=_datetime.combine(current_day, _datetime.min.time()) + timedelta(hours=23, minutes=59, seconds=59),
        artifacts_dir=artifacts_dir,
    )
    if "error" not in day_events and raw_type:
        day_events = page_logic.filter_events_by_type(day_events, raw_type)
    if "error" not in day_events and hour is not None:
        day_events = page_logic.filter_events_by_hour(day_events, hour)

    rows = []
    for ev in (day_events.get("events", []) if "error" not in day_events else [])[:200]:
        rows.append(
            {
                "시각(초)": fmt.fmt_time_full(ev["localtime"]),
                "유형": page_logic.type_label(ev["type"]),
                "값": fmt.fmt_mae(ev["value"]),
                "정상범위": ev.get("normal_range", ""),
                "탐지 방법": _detected_by_glyph(ev.get("detected_by", "")),
                "점수": fmt.fmt_mae(ev.get("score")),
            }
        )
    if rows:
        st.dataframe(rows, width="stretch", hide_index=True, height=160)
    else:
        st.caption("시간대를 클릭하면 그 시간의 이벤트가 표에 표시됩니다." if hour is None else "선택 시간대 이벤트 없음")


def _plotly_selected_points(selection) -> list[dict[str, Any]]:
    """Normalise ``st.plotly_chart(on_select='rerun')`` return value -> points list."""

    if isinstance(selection, dict):
        return selection.get("selection", {}).get("points", []) or []
    sel = getattr(selection, "selection", None)
    return list(getattr(sel, "points", []) or [])


def _render_method_comparison(st, rebuild_meta: dict[str, Any] | None) -> None:
    with components.panel(height=ROW_BOTTOM_H, key=PANEL_METHODS):
        st.markdown(
            components.panel_header(
                "방법 비교", icon_name="grid", desc="규칙 대비 프록시 — 정답 아님"
            ),
            unsafe_allow_html=True,
        )
        comp = ui_logic.method_comparison(rebuild_meta)
        if not comp["available"]:
            st.markdown(
                components.empty_state("anomaly_rebuild_meta.json"),
                unsafe_allow_html=True,
            )
            return
        st.caption("방법별 탐지 건수")
        count_rows = [[r["method"], fmt.fmt_count(r["count"])] for r in comp["method_counts"]]
        st.markdown(
            components.html_table(["방법", "건수"], count_rows), unsafe_allow_html=True
        )
        st.caption("방법 간 겹침")
        overlap_rows = [
            [r["combination"], fmt.fmt_count(r["count"])] for r in comp["method_overlap"]
        ]
        st.markdown(
            components.html_table(["조합", "건수"], overlap_rows), unsafe_allow_html=True
        )
        st.caption("규칙 대비 프록시 지표 — 정답 아님 (B3).")


def _detected_by_glyph(detected_by: Any) -> str:
    """Render detected-by as 규칙 ● 통계 ○ IF ● style glyphs from the raw field."""

    text = str(detected_by)
    rule = "●" if ("rule" in text or "규칙" in text) else "○"
    stat = "●" if ("statistical" in text or "통계" in text) else "○"
    iso = "●" if ("isolation" in text or "unsupervised" in text or "IF" in text) else "○"
    return f"규칙 {rule} 통계 {stat} IF {iso}"


EVENT_ALL = "전체 설비"


def _render_event_log(st, model: dict[str, Any]) -> None:
    with components.panel(height=ROW_BOTTOM_H, key=PANEL_EVENTS):
        head_l, head_r = st.columns([3, 2], vertical_alignment="center")
        head_l.markdown(
            components.panel_header("이벤트 로그", icon_name="pulse", tone="anomaly"),
            unsafe_allow_html=True,
        )
        events = model["events"]
        if not events:
            st.markdown(
                components.empty_state(tools_mod.ANOMALY_EVENTS_NAME),
                unsafe_allow_html=True,
            )
            return
        counts = page_logic.event_counts_by_module(events)
        labels = [f"{EVENT_ALL} · {fmt.fmt_count(len(events))}건"] + [
            f"{m} · {fmt.fmt_count(n)}건" for m, n in counts
        ]
        choice = head_r.selectbox("설비", labels, key="event_log_module", label_visibility="collapsed")
        module = None if labels.index(choice) == 0 else counts[labels.index(choice) - 1][0]
        picked = page_logic.events_for_module(events, module, limit=200)
        rows = [
            {
                "설비": ev["module"],
                "시각": str(ev["localtime"])[:19].replace("T", " "),
                "유형": page_logic.type_label(ev["type"]),
                "값": fmt.fmt_mae(ev["value"]),
                "정상범위": ev.get("normal_range", ""),
                "탐지": _detected_by_glyph(ev.get("detected_by", "")),
            }
            for ev in picked
        ]
        st.dataframe(rows, width="stretch", hide_index=True)
        total = len(events) if module is None else dict(counts)[module]
        st.caption(
            f"{module or EVENT_ALL} {fmt.fmt_count(total)}건 중 최근 {len(rows)}건 (최신순, 최대 200행)."
        )


def _render_drilldown(st, status: dict[str, Any], trend, selected, artifacts_dir) -> None:
    with components.panel(height=ROW_LOW_H, key=PANEL_DRILL):
        st.markdown(
            components.panel_header("설비 상세", icon_name="anomaly", tone="anomaly"),
            unsafe_allow_html=True,
        )
        if "error" in status or not status.get("modules"):
            st.markdown(
                components.empty_state(tools_mod.MODULE_STATUS_NAME),
                unsafe_allow_html=True,
            )
            return

        selected = selected or st.session_state.get(SELECTED_KEY)
        if not selected:
            st.caption("설비 상태표에서 행을 선택하면 상세가 표시됩니다.")
            return

        status_label = None
        for r in status["modules"]:
            if r["module"] == selected:
                status_label = r["status"] if _status_approved() else "미확정"
                break

        st.markdown(
            components.breadcrumb(["이상 탐지", selected]),
            unsafe_allow_html=True,
        )
        st.caption("관찰 기록일 뿐 설비 판정이 아님.")

        filtered = tools_mod.get_anomaly_events(selected, artifacts_dir=artifacts_dir)
        events = filtered.get("events", []) if "error" not in filtered else []
        series = ui_logic.phase_sync_series(events)

        if series["voltage"] or series["power_factor"]:
            st.plotly_chart(
                charts.phase_sync_chart(series, height=theme.chart_height(ROW_LOW_H)),
                width="stretch",
            config=charts.PLOTLY_CONFIG,
            )
            st.caption("임계선 전압 210 / 역률 85 (config). 미달 구간이 이상 후보입니다.")
        elif events:
            st.plotly_chart(
                charts.anomaly_points_chart(events, height=theme.chart_height(ROW_LOW_H)),
                width="stretch",
            config=charts.PLOTLY_CONFIG,
            )
            st.caption("연속 시계열 미포함 — 이상 행만 표시 (anomaly_context 미존재, (b) 방식).")
        else:
            st.markdown(
                components.empty_state(f"{tools_mod.ANOMALY_EVENTS_NAME} ({selected})"),
                unsafe_allow_html=True,
            )

        sections = ui_logic.anomaly_cause_sections(selected, status_label)
        c1, c2, c3 = st.columns(3, gap="medium")
        c1.markdown(_note_block("관찰 사실", sections["observed"], "info"), unsafe_allow_html=True)
        c2.markdown(_note_block("원인 후보", sections["causes"], "cause"), unsafe_allow_html=True)
        c3.markdown(_note_block("현장 확인", sections["checks"], "check"), unsafe_allow_html=True)


def _note_block(title: str, body: str, kind: str = "") -> str:
    """Drill-down note with a 4px left colour band: info / cause / check."""

    import html as _html

    kind_cls = f" fec-note-{kind}" if kind in ("info", "cause", "check") else ""
    return (
        f'<div class="fec-note{kind_cls}"><div class="fec-note-title">{_html.escape(title)}</div>'
        f'<div>{_html.escape(body)}</div></div>'
    )
