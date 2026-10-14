"""설정 페이지 (v3 §6, v4 settings.png).

설정값은 ``st.session_state["settings"]``(세션 한정)에만 저장한다. 파일 쓰기·artifacts
수정 없음. MAE·평가 수치는 설정과 무관하게 evaluation.json(config 기본 계수) 값이다.
Slack 웹훅 URL은 환경변수 ``SLACK_WEBHOOK_URL``에서만 읽고 화면에는 연결됨/미설정만 표시한다.
"""

from __future__ import annotations

import os
from typing import Any

from config import config
from src.app import page_logic
from src.app.ui import components

PANEL_COEF = "st_coef"
PANEL_ALERT = "st_alert"
PANEL_SLACK = "st_slack"
PANEL_RULES = "st_rules"

H_TOP = 420
H_SLACK = 100
H_RULES = 190

# settings field -> widget key
_KEYS = {
    "bill_rate": "w_bill_rate",
    "carbon_rate": "w_carbon_rate",
    "carbon_price": "w_carbon_price",
    "alert_interval": "w_alert_interval",
    "bill_surge_pct": "w_bill_surge_pct",
    "anomaly_surge_factor": "w_anomaly_surge_factor",
    "night_start": "w_night_start",
    "night_end": "w_night_end",
}


def slack_connected() -> bool:
    """Whether a webhook is configured. The URL itself is never exposed (NFR-6)."""

    value = os.environ.get("SLACK_WEBHOOK_URL", "")
    return bool(value.strip())


def _store(field: str) -> None:
    import streamlit as st

    cur = page_logic.merged_settings(st.session_state.get(page_logic.SETTINGS_KEY))
    cur[field] = st.session_state.get(_KEYS[field])
    st.session_state[page_logic.SETTINGS_KEY] = cur


def _reset() -> None:
    import streamlit as st

    defaults = page_logic.default_settings()
    st.session_state[page_logic.SETTINGS_KEY] = defaults
    for field, key in _KEYS.items():
        st.session_state[key] = defaults[field]


def render(settings: dict[str, Any]) -> None:
    import streamlit as st

    left, right = st.columns(2)
    with left:
        with components.panel(height=H_TOP, key=PANEL_COEF):
            st.markdown(
                components.panel_header("요금·탄소 계수", icon_name="chart"), unsafe_allow_html=True
            )
            _row(st, "단가 (원/kWh)", lambda: st.number_input(
                "단가", min_value=0.0, value=float(settings["bill_rate"]), step=1.0,
                key=_KEYS["bill_rate"], on_change=_store, args=("bill_rate",),
                label_visibility="collapsed"))
            _row(st, "탄소계수 (kgCO2/kWh)", lambda: st.number_input(
                "탄소계수", min_value=0.0, value=float(settings["carbon_rate"]), step=0.001,
                format="%.3f", key=_KEYS["carbon_rate"], on_change=_store, args=("carbon_rate",),
                label_visibility="collapsed"))
            _row(st, "탄소 가격 (원/tCO2, 가정)", lambda: st.number_input(
                "탄소 가격", min_value=0.0, value=settings.get("carbon_price"), step=1000.0,
                placeholder="미설정", key=_KEYS["carbon_price"], on_change=_store,
                args=("carbon_price",), label_visibility="collapsed"))
            st.button("기본값으로 되돌리기", key="st_reset", on_click=_reset)
            st.caption(
                "요금·탄소·탄소비용 표시만 바뀝니다. MAE와 평가는 항상 기본 계수"
                f"({config.BILL_RATE:g}, {config.CARBON_RATE:g}) 값입니다. 탄소 가격은 임의 값을 넣지 않습니다."
            )
    with right:
        with components.panel(height=H_TOP, key=PANEL_ALERT):
            st.markdown(
                components.panel_header("알림 임계값", icon_name="bell"), unsafe_allow_html=True
            )
            _row(st, "예측구간 이탈 경고", lambda: st.toggle(
                "예측구간 이탈 경고", value=bool(settings["alert_interval"]),
                key=_KEYS["alert_interval"], on_change=_store, args=("alert_interval",),
                label_visibility="collapsed"))
            _row(st, "요금 급증: 일 평균 대비 (%)", lambda: st.number_input(
                "요금 급증", min_value=0.0, value=float(settings["bill_surge_pct"]), step=5.0,
                key=_KEYS["bill_surge_pct"], on_change=_store, args=("bill_surge_pct",),
                label_visibility="collapsed"))
            _row(st, "이상 급증: 평소 건수의 (배)", lambda: st.number_input(
                "이상 급증", min_value=1.0, value=float(settings["anomaly_surge_factor"]), step=0.5,
                key=_KEYS["anomaly_surge_factor"], on_change=_store, args=("anomaly_surge_factor",),
                label_visibility="collapsed"))
            _row(st, "야간 시간대 시작 (시)", lambda: st.number_input(
                "야간 시작", min_value=0, max_value=23, value=int(settings["night_start"]), step=1,
                key=_KEYS["night_start"], on_change=_store, args=("night_start",),
                label_visibility="collapsed"))
            _row(st, "야간 시간대 종료 (시)", lambda: st.number_input(
                "야간 종료", min_value=0, max_value=23, value=int(settings["night_end"]), step=1,
                key=_KEYS["night_end"], on_change=_store, args=("night_end",),
                label_visibility="collapsed"))
            st.caption("화면 경고 표시 기준만 바뀝니다. 탐지 규칙 변경은 배치 재실행 필요.")

    with components.panel(height=H_SLACK, key=PANEL_SLACK):
        c1, c2, c3 = st.columns([6, 2, 2], vertical_alignment="center")
        with c1:
            st.markdown(
                components.panel_header("알림 연동 — Slack", icon_name="bell"),
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(
                components.chip("연결됨" if slack_connected() else "미설정",
                                tone="ok" if slack_connected() else "muted"),
                unsafe_allow_html=True,
            )
        with c3:
            st.button("테스트 전송", key="st_slack_test", disabled=True)
        st.caption("웹훅 URL은 환경변수 SLACK_WEBHOOK_URL에서만 읽으며 화면에 표시하지 않습니다.")

    with components.panel(height=H_RULES, key=PANEL_RULES):
        st.markdown(
            components.panel_header("현재 적용 규칙 (읽기 전용)", icon_name="lock"),
            unsafe_allow_html=True,
        )
        st.markdown(
            components.html_table(
                ["규칙", "값"],
                [
                    ["이상 탐지 (O-9)", f"전압 {config.VOLTAGE_THRESHOLD:g}V 미만 또는 역률 {config.POWER_FACTOR_THRESHOLD:g}% 미만, 3상 동시"],
                    ["예측구간 (O-7)", "P5–P95 (잠정)"],
                    ["MAE 73/89 (O-10)", "참고용 — 합격 기준 아님"],
                ],
            ),
            unsafe_allow_html=True,
        )


def _row(st, label: str, widget) -> None:
    """Label left, input right on one line (v4 §2.11)."""

    c1, c2 = st.columns([3, 2], vertical_alignment="center")
    c1.markdown(f'<div class="fec-form-label">{components._esc(label)}</div>', unsafe_allow_html=True)
    with c2:
        widget()
