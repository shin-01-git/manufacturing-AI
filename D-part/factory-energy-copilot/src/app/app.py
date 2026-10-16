"""Streamlit dashboard entry point (artifacts-only). Run: streamlit run src/app/app.py

v3/v4 layout: left sidebar = brand + vertical page menu + (role / data source / LLM
status at the bottom). Every page draws its own header with a filter bar (기간 단위
토글 + 날짜 + 알림 벨). The current page lives in ``st.session_state["page"]`` only;
the menu buttons just set it through the ``_go`` callback.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Streamlit runs this file directly, so the project root is not on sys.path.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config import config
from src.agent import agent as agent_mod
from src.app import data as data_mod
from src.app import nav, page_logic, ui_logic
from src.app.tabs import anomaly as anomaly_tab
from src.app.tabs import copilot as copilot_tab
from src.app.tabs import equipment as equipment_tab
from src.app.tabs import home as home_tab
from src.app.tabs import settings as settings_tab
from src.app.ui import components, theme

PAGES = page_logic.PAGES
DEFAULT_PAGE = page_logic.DEFAULT_PAGE

PAGE_DESC = {
    page_logic.PAGE_HOME: "공장 전체 5월 예측·요금·탄소를 한눈에",
    page_logic.PAGE_EQUIPMENT: "설비별 전력 소비 추이·비교·부하 패턴 (학습 원본 실측)",
    page_logic.PAGE_ANOMALY: "관찰 기록 기반 이상 구간 — 설비 판정이 아닌 징후 표시",
    page_logic.PAGE_COPILOT: "숫자는 artifacts 계산값, 서술은 AI/규칙 요약",
    page_logic.PAGE_SETTINGS: "단가·계수·화면 경고 기준 (세션 한정)",
}


def _resolve_page() -> str:
    import streamlit as st

    role = st.session_state.get("role", config.DEFAULT_ROLE)
    page = st.session_state.get("page", DEFAULT_PAGE)
    # A page hidden for the current role (e.g. 설정 for 경영진) falls back to 홈.
    if page not in page_logic.pages_for_role(role):
        page = DEFAULT_PAGE
    st.session_state["page"] = page
    return page


def _inject_css() -> None:
    import streamlit as st

    css = theme.load_css()
    if css:
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def _render_sidebar(page: str) -> str:
    """Brand + menu on top, role / data source / LLM status pinned at the bottom."""

    import streamlit as st

    sb = st.sidebar
    info = ui_logic.header_info()
    sb.markdown(components.brand(info["title"], info["team"]), unsafe_allow_html=True)

    current_role = st.session_state.get("role", config.DEFAULT_ROLE)
    with sb.container(key="nav"):
        for name in page_logic.pages_for_role(current_role):
            st.button(
                name,
                key=f"nav_{PAGES.index(name)}",
                icon=page_logic.PAGE_ICONS[name],
                type="primary" if name == page else "tertiary",
                width="stretch",
                on_click=nav.go,
                args=(name,),
            )

    with sb.container(key="sidebar_footer"):
        role = st.selectbox(
            "역할",
            options=list(config.ROLES.keys()),
            format_func=lambda key: config.ROLES[key],
            index=list(config.ROLES.keys()).index(config.DEFAULT_ROLE),
            key="role",
        )
        st.markdown(
            f'<div class="fec-side-note">{page_logic.role_profile(role)["summary"]}</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="fec-side-note">{config.DATA_SOURCE_LABEL}</div>', unsafe_allow_html=True
        )
        connected = config.has_openai_key()
        cls = "fec-side-llm" if connected else "fec-side-llm fec-side-llm-off"
        text = "LLM 연결됨" if connected else "대체 모드: 규칙 기반 요약"
        st.markdown(f'<span class="{cls}">{text}</span>', unsafe_allow_html=True)
    return role


def _period_for_page(page: str, bundle: dict) -> dict:
    """Actual data period of the page (no hard-coding, §A-7 R1)."""

    if page == page_logic.PAGE_ANOMALY:
        # 로드 시 한 번 계산한 값 사용 (매 rerun 6.5만 건 순회 방지)
        return bundle.get("events_period") or ui_logic.events_period(bundle["events"])
    if page == page_logic.PAGE_EQUIPMENT:
        frame = bundle.get("modules_hourly", {}).get("frame")
        lo, hi = page_logic.frame_period(frame)
        return {
            "start": lo.isoformat() if lo else None,
            "end": hi.isoformat() if hi else None,
        }
    return ui_logic.forecast_period(bundle["forecast"])


def _period_chip(page: str, period: dict) -> str:
    if page in (page_logic.PAGE_SETTINGS, page_logic.PAGE_COPILOT):
        return ""
    if not (period.get("start") and period.get("end")):
        return ""
    label = {
        page_logic.PAGE_HOME: "예측",
        page_logic.PAGE_EQUIPMENT: "학습 원본",
        page_logic.PAGE_ANOMALY: "탐지",
    }.get(page, "")
    suffix = " · 5월 시간대별 예측" if page == page_logic.PAGE_HOME else ""
    return f"{label} {period['start']} ~ {period['end']}{suffix}".strip()


def _selected_range(page: str, period: dict) -> tuple[str | None, str | None]:
    """Current date-filter value from widget state (falls back to the full period)."""

    import streamlit as st

    lo, hi = period.get("start"), period.get("end")
    value = st.session_state.get(f"dr_{page}")
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return value[0].isoformat(), value[1].isoformat()
    return lo, hi


def _render_header(page: str, period: dict, alerts: dict) -> dict:
    """Page title (left) + filter bar and alert bell (right). Returns filter values."""

    import streamlit as st

    left, right = st.columns([7, 5], vertical_alignment="center")
    with left:
        st.markdown(
            components.page_header_left(
                page, chip_text=_period_chip(page, period), desc=PAGE_DESC.get(page, "")
            ),
            unsafe_allow_html=True,
        )

    gran = page_logic.DEFAULT_GRANULARITY
    start, end = period.get("start"), period.get("end")
    with right:
        with st.container(key="filterbar"):
            c_gran, c_date, c_bell = st.columns([4.4, 3.6, 1], vertical_alignment="center")
            if page in page_logic.PAGES_WITH_GRANULARITY:
                with c_gran:
                    chosen = st.segmented_control(
                        "기간 단위",
                        options=page_logic.GRANULARITIES,
                        default=page_logic.DEFAULT_GRANULARITY,
                        key="gran",
                        label_visibility="collapsed",
                    )
                    gran = chosen or page_logic.DEFAULT_GRANULARITY
            if page in page_logic.PAGES_WITH_DATE and start and end:
                lo = page_logic.to_date(start)
                hi = page_logic.to_date(end)
                with c_date:
                    picked = st.date_input(
                        "날짜 범위",
                        value=(lo, hi),
                        min_value=lo,
                        max_value=hi,
                        key=f"dr_{page}",
                        label_visibility="collapsed",
                    )
                if isinstance(picked, (tuple, list)) and len(picked) == 2:
                    start, end = picked[0].isoformat(), picked[1].isoformat()
            with c_bell:
                _render_bell(alerts)
    return {"gran": gran, "start": start, "end": end}


def _render_bell(alerts: dict) -> None:
    import streamlit as st

    count = alerts["count"] if not alerts["errors"] else 0
    label = ":material/notifications:" + (f" {count}" if count else "")
    with st.popover(label, key="bell", width="stretch"):
        if alerts["errors"]:
            st.markdown(
                components.error_state(
                    "경고 데이터를 불러오지 못함", artifact=", ".join(alerts["errors"])
                ),
                unsafe_allow_html=True,
            )
        elif not alerts["items"]:
            st.markdown('<div class="fec-calm">경고 없음</div>', unsafe_allow_html=True)
        else:
            for it in alerts["items"][:8]:
                st.markdown(
                    components.alert_row(it["level"], it["title"], it["evidence"], it["time"]),
                    unsafe_allow_html=True,
                )


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title=config.PROJECT_TITLE, layout="wide")
    _inject_css()
    theme.register_template()

    page = _resolve_page()
    bundle = data_mod.cached_bundle()
    role = _render_sidebar(page)
    if page_logic.SETTINGS_KEY not in st.session_state:
        saved = page_logic.load_saved_settings()
        if saved:
            st.session_state[page_logic.SETTINGS_KEY] = page_logic.merged_settings(saved)
    settings = page_logic.merged_settings(st.session_state.get(page_logic.SETTINGS_KEY))

    period = _period_for_page(page, bundle)
    # Alerts follow the home/forecast date range regardless of the visible page so
    # the bell count is consistent on every page.
    alert_period = ui_logic.forecast_period(bundle["forecast"])
    a_start, a_end = _selected_range(page_logic.PAGE_HOME, alert_period)
    # 이상 6종 = 전체 평균 ±kσ 밖 + 급격한 변화(설정의 민감도 k). 특징 파일이 없으면 artifact 값
    feats = (bundle.get("features_hourly") or {}).get("frame")
    pattern = (
        page_logic.detect_sudden(feats, k=float(settings["anomaly_k"]),
                                 night_hours=set(page_logic.night_hours(settings)))
        if feats is not None else bundle.get("pattern")
    )
    alerts = page_logic.build_alerts(
        bundle["actual_vs_pred"], bundle["forecast"], bundle["trend"], settings,
        start=a_start, end=a_end, pattern=pattern,
    )

    filters = _render_header(page, period, alerts)
    ctx = {
        "role": role,
        "settings": settings,
        "alerts": alerts,
        "gran": filters["gran"],
        "start": filters["start"],
        "end": filters["end"],
        "pattern": pattern,
    }

    if page == page_logic.PAGE_HOME:
        home_tab.render(bundle, ctx)
    elif page == page_logic.PAGE_EQUIPMENT:
        equipment_tab.render(bundle, ctx)
    elif page == page_logic.PAGE_ANOMALY:
        anomaly_tab.render(
            bundle["module_status"],
            bundle["events"],
            bundle["trend"],
            bundle.get("rebuild_meta"),
            selected_modules=None,
            date_range=(ctx["start"], ctx["end"]),
            settings=settings,
            role=role,
            hourly_frame=bundle.get("anomaly_hourly_frame"),
            pattern=pattern,
            cpart=bundle.get("cpart"),
            features=(bundle.get("features_hourly") or {}).get("frame"),
        )
    elif page == page_logic.PAGE_COPILOT:
        copilot_tab.render(agent_mod.Agent(), {"role": role, "start": ctx["start"], "end": ctx["end"]})
    elif page == page_logic.PAGE_SETTINGS:
        settings_tab.render(settings)

    st.markdown(
        '<div class="fec-footer">과거 전력 소비 데이터(2024-12-01 – 2025-04-29)로 학습해 '
        "2025-05-10 00:00 – 05-31 00:00 시간대별 전력 사용량을 예측한 결과입니다. "
        "수치는 artifacts(batch) 산출물 기준입니다.</div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()