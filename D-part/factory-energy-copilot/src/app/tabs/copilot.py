"""AI Copilot 페이지 (v2 §5).

좌 채팅(답변 아래 출처 칩, 숫자 없으면 '데이터에 없음') / 우상 브리핑 생성기
(경영진 5줄·에너지·설비, st.segmented_control) / 우하 "Python 계산값" 표(브리핑
숫자 원본, 같은 포매터). 에이전트 로직은 수정하지 않고 ``src/agent``만 호출한다.
키가 없으면 '대체 모드' 칩을 표시한다 (C5, D4). 추천 질문 3~4개, 미래 표현 금지.

모든 정보 덩어리는 st.container(border=True) 패널 안에 두고 제목은 panel_header로
둔다 (§A-8).
"""

from __future__ import annotations

from typing import Any

from config import config
from src.agent import agent as agent_mod
from src.agent import briefing as briefing_mod
from src.agent import tools as tools_mod
from src.app import page_logic
from src.app.ui import components, fmt

PANEL_CHAT = "cp_chat"
PANEL_BRIEF = "cp_brief"
PANEL_CALC = "cp_calc"

CHAT_H = 560
CALC_H = 340


def render(agent: "agent_mod.Agent", filters: dict[str, Any]) -> None:
    import streamlit as st

    connected = config.has_openai_key()

    left, right = st.columns([1.2, 1])
    with left:
        _render_chat(st, agent, connected, filters.get("role", config.DEFAULT_ROLE))
        calc_slot = st.container()  # filled after the briefing is built (right column)
    with right:
        briefing = _render_briefing(st, agent, filters, connected)
    with calc_slot:
        _render_calc(st, briefing)


def _queue_question(question: str) -> None:
    import streamlit as st

    st.session_state["cp_pending"] = question


def _source_label(source: str) -> str:
    return "AI 서술" if source == "llm" else "계산/규칙 기반"


def _render_chat(st, agent: "agent_mod.Agent", connected: bool, role: str) -> None:
    questions = page_logic.role_profile(role)["questions"]
    audience = page_logic.audience_for_role(role)
    with components.panel(height=CHAT_H, key=PANEL_CHAT):
        head_l, head_r = st.columns([6, 4], vertical_alignment="center")
        with head_l:
            st.markdown(
                components.panel_header("채팅", icon_name="copilot"),
                unsafe_allow_html=True,
            )
        with head_r:
            st.markdown(components.llm_status_chip(connected), unsafe_allow_html=True)

        history = st.session_state.setdefault("copilot_history", [])

        # Render the message list first inside a fixed-height scroll area, then
        # the input box after it. Because the scroll area has a fixed height, a
        # growing history scrolls INSIDE the box and never pushes the input box
        # down or out of the panel — the input stays pinned at the panel bottom.
        messages_box = st.container(height=CHAT_H - 260)
        with messages_box:
            if not history:
                st.caption("질문을 입력하면 대화가 여기에 표시됩니다.")
            for turn in history:
                with st.chat_message(turn["role"]):
                    st.markdown(turn["text"])
                    if turn.get("source"):
                        st.markdown(
                            components.source_chip(_source_label(turn["source"])),
                            unsafe_allow_html=True,
                        )

        chip_cols = st.columns(2)
        for i, q in enumerate(questions[:4]):
            chip_cols[i % 2].button(
                q, key=f"cp_sugg_{i}", width="stretch", on_click=_queue_question, args=(q,)
            )
        question = st.chat_input("질문을 입력하세요") or st.session_state.pop("cp_pending", None)
        if question:
            history.append({"role": "user", "text": question})
            answer = agent.ask(question, audience=audience)
            history.append(
                {"role": "assistant", "text": answer.text, "source": answer.source}
            )
            st.rerun()


def _render_briefing(
    st, agent: "agent_mod.Agent", filters: dict[str, Any], connected: bool
) -> None:
    role = filters.get("role", config.DEFAULT_ROLE)
    role_audience = page_logic.audience_for_role(role)
    options = list(briefing_mod.AUDIENCE_LABELS)
    briefing = None
    with components.panel(key=PANEL_BRIEF):
        head_l, head_r = st.columns([3, 2], vertical_alignment="center")
        with head_l:
            st.markdown(
                components.panel_header("브리핑 생성기", icon_name="sparkles"),
                unsafe_allow_html=True,
            )
        mode_slot = head_r.empty()
        audience = st.segmented_control(
            "대상",
            options=options,
            format_func=lambda a: briefing_mod.AUDIENCE_LABELS.get(a, a),
            default=role_audience if role_audience in options else options[0],
            key=f"cp_aud_{role}",
            label_visibility="collapsed",
        ) or role_audience

        result = agent.briefing(audience, start=filters.get("start"), end=filters.get("end"))
        briefing = result.data.get("briefing")
        if briefing is not None:
            mode_slot.markdown(
                components.chip("AI 생성" if briefing.mode == "llm" else "규칙 기반",
                                tone="accent" if briefing.mode == "llm" else "muted"),
                unsafe_allow_html=True,
            )
            st.markdown(components.briefing_card(briefing), unsafe_allow_html=True)
            dl, cp = st.columns(2)
            dl.download_button(
                "⬇ .md 저장",
                data=briefing_mod.to_markdown(briefing),
                file_name=f"briefing_{briefing.audience}_{filters.get('start')}_{filters.get('end')}.md",
                mime="text/markdown",
                key=f"cp_dl_{audience}",
                width="stretch",
            )
            with cp.expander("텍스트 복사"):
                st.code(briefing_mod.to_plaintext(briefing), language=None)

    return briefing


def _render_calc(st, briefing) -> None:
    with components.panel(height=CALC_H, key=PANEL_CALC):
        st.markdown(
            components.panel_header("Python 계산값", icon_name="cpu", desc="브리핑 숫자 원본"),
            unsafe_allow_html=True,
        )
        st.markdown(_calc_table(briefing), unsafe_allow_html=True)


def _calc_table(briefing) -> str:
    """Calc-value table (label / value / source); values are the exact briefing strings."""

    if briefing is None or not briefing.calc:
        return components.empty_state("브리핑 계산값")
    rows = [[label, value, source] for label, value, source in briefing.calc]
    return components.html_table(["라벨", "값", "출처"], rows)
