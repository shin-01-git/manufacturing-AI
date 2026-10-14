"""Page navigation helper: ``st.session_state["page"]`` is the single source of truth."""

from __future__ import annotations


def go(page: str) -> None:
    """Callback for menu / link buttons (``on_click=go, args=(page,)``)."""

    import streamlit as st

    st.session_state["page"] = page
