"""이상 탐지 페이지 (v2 §5, facility 기본).

요약 카드 3개 → 설비 상태표(행 선택)+히트맵 → 방법 비교 2표+이벤트 로그 → 행 선택
드릴다운(헤더 + '관찰 기록일 뿐 설비 판정이 아님' → 전압/역률 3상 동기 차트 또는
anomaly_events 점 그래프 대체 → 세 구획: 관찰 사실/원인 후보/현장 확인 → 시간당
이상 vs 평소). status는 STATUS_THRESHOLDS_APPROVED=False면 전부 '미확정' (O-3).

모든 정보 덩어리(KPI 제외)는 st.container(border=True, height=H) 패널 안에 두고
제목은 panel_header로 둔다 (§A-8). 상태표 행 선택은 st.dataframe의 on_select로,
module명은 artifacts 값 그대로. 설비 상세 드릴다운은 연속 시계열 미포함 환경이라
anomaly_events 점(scatter)만 그리는 (b) 방식이다 (anomaly_context 미존재).
"""

from __future__ import annotations

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

    status_col, heat_col = st.columns([1.65, 1])
    selected = None
    with status_col:
        selected = _render_status_table(st, model, trend)
    with heat_col:
        _render_heatmap(st, trend, date_range)

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


def _render_heatmap(st, trend: dict[str, Any], date_range=None) -> None:
    with components.panel(height=ROW_MID_H, key=PANEL_HEATMAP):
        head_l, head_r = st.columns([3, 2], vertical_alignment="center")
        with head_l:
            st.markdown(
                components.panel_header("탐지 시간대", icon_name="chart", tone="anomaly"),
                unsafe_allow_html=True,
            )
        with head_r:
            mode = st.segmented_control(
                "보기", options=["타임라인", "히트맵"], default="타임라인",
                key="an_view", label_visibility="collapsed",
            ) or "타임라인"
        h = theme.chart_height(ROW_MID_H)
        if mode == "타임라인":
            start, end = date_range if date_range else (None, None)
            points = page_logic.timeline_points(trend, start=start, end=end)
            if not points:
                st.markdown(components.empty_state("선택 조건의 탐지 없음"), unsafe_allow_html=True)
                return
            labels = {o["type"]: o["label"] for o in page_logic.anomaly_type_options(
                {"events": [{"type": p["type"]} for p in points]})}
            st.plotly_chart(
                charts.anomaly_timeline(points, labels, height=h),
                width="stretch", config=charts.PLOTLY_CONFIG,
            )
            return
        hm = ui_logic.heatmap_matrix(trend)
        if not hm["modules"]:
            st.markdown(components.empty_state("선택 조건의 탐지 없음"), unsafe_allow_html=True)
            return
        st.plotly_chart(
            charts.heatmap(hm["modules"], hm["days"], hm["matrix"], height=h),
            width="stretch", config=charts.PLOTLY_CONFIG,
        )


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


def _render_event_log(st, model: dict[str, Any]) -> None:
    with components.panel(height=ROW_BOTTOM_H, key=PANEL_EVENTS):
        st.markdown(
            components.panel_header("이벤트 로그", icon_name="pulse", tone="anomaly"),
            unsafe_allow_html=True,
        )
        if not model["events"]:
            st.markdown(
                components.empty_state(tools_mod.ANOMALY_EVENTS_NAME),
                unsafe_allow_html=True,
            )
            return
        rows = []
        for ev in model["events"][:200]:
            rows.append(
                {
                    "설비": ev["module"],
                    "시각": ui_logic.fmt_localtime_short(ev["localtime"]),
                    "유형": ev["type"],
                    "값": fmt.fmt_mae(ev["value"]),
                    "정상범위": ev.get("normal_range", ""),
                    "탐지": _detected_by_glyph(ev.get("detected_by", "")),
                }
            )
        st.dataframe(rows, width="stretch", hide_index=True)
        st.caption(
            f"총 {fmt.fmt_count(model['event_count'])}건 (최대 200행 표시). 현재 규칙 3상 동시 이벤트."
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
