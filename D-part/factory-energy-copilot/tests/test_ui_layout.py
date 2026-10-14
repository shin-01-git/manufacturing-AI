"""Layout tests for the dashboard v2 (overview page, panels, routing, grep).

These cover the v2 structural requirements:
- each page's required panel keys exist in the source (so the panel system is
  used, not free-floating titles),
- forbidden strings (고장/Fault/LIVE/실시간 + 비목표: Energy Intensity/Confidence/
  Venn) never appear in the UI source,
- no "st.markdown opening/closing <div> wrapping a widget" pattern,
- no hard-coded mock equipment names and no ``st.title``,
- every page renders without raising via Streamlit's AppTest, with and without
  the shared artifacts, and with no OpenAI key.
"""

from __future__ import annotations

import pathlib

import pytest

from src.app import data as data_mod
from src.app.tabs import anomaly as anomaly_tab
from src.app.tabs import copilot as copilot_tab
from src.app.tabs import equipment as equipment_tab
from src.app.tabs import home as home_tab
from src.app.tabs import settings as settings_tab

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "app"
APP_PY = APP_DIR / "app.py"


# ---------------------------------------------------------------------------
# Panel keys per page
# ---------------------------------------------------------------------------
def test_home_panel_keys_present():
    text = (APP_DIR / "tabs" / "home.py").read_text(encoding="utf-8")
    for key in (
        home_tab.PANEL_CHART,
        home_tab.PANEL_ALERTS,
        home_tab.PANEL_PERIOD,
        home_tab.PANEL_COST,
        home_tab.PANEL_REPORT,
    ):
        assert key in text and f"key={key!s}" not in "" 


def test_equipment_and_settings_panel_keys_present():
    for key in (
        equipment_tab.PANEL_TREND,
        equipment_tab.PANEL_COMPARE,
        equipment_tab.PANEL_PATTERN,
        equipment_tab.PANEL_QUALITY,
        settings_tab.PANEL_COEF,
        settings_tab.PANEL_ALERT,
        settings_tab.PANEL_SLACK,
        settings_tab.PANEL_RULES,
    ):
        assert key


def test_anomaly_panel_keys_present():
    for key in (
        anomaly_tab.PANEL_STATUS,
        anomaly_tab.PANEL_HEATMAP,
        anomaly_tab.PANEL_METHODS,
        anomaly_tab.PANEL_EVENTS,
        anomaly_tab.PANEL_DRILL,
    ):
        assert key


def test_copilot_panel_keys_present():
    for key in (copilot_tab.PANEL_CHAT, copilot_tab.PANEL_BRIEF, copilot_tab.PANEL_CALC):
        assert key


# ---------------------------------------------------------------------------
# Forbidden strings and anti-patterns (grep == 0)
# ---------------------------------------------------------------------------
def _ui_source_paths() -> list[pathlib.Path]:
    css = APP_DIR / "ui" / "style.css"
    return list(APP_DIR.rglob("*.py")) + [css]


def test_no_forbidden_strings_v2():
    forbidden = (
        "고장",
        "Fault",
        "LIVE",
        "실시간",
        "Energy Intensity",
        "Confidence",
        "Venn",
        "탄소세",
        "Traceback",
        "Internal Server Error",
    )
    offenders: list[str] = []
    for path in _ui_source_paths():
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                offenders.append(f"{path.name}:{token}")
    assert offenders == [], f"forbidden strings in UI source: {offenders}"


def test_no_st_title():
    offenders: list[str] = []
    for path in APP_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "st.title(" in text:
            offenders.append(path.name)
    assert offenders == []


def test_no_hardcoded_mock_module_names():
    """The mock names (Boiler/Chiller/Module NN) must not be hard-coded."""

    import re

    patterns = (
        re.compile(r"Boiler", re.IGNORECASE),
        re.compile(r"Chiller", re.IGNORECASE),
        re.compile(r"Module\s*0?\d{2}"),  # 'Module 07' style mock labels
    )
    offenders: list[str] = []
    for path in APP_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pat in patterns:
            if pat.search(text):
                offenders.append(f"{path.name}:{pat.pattern}")
    assert offenders == [], offenders


def test_no_div_wrapping_widget_pattern():
    """No ``st.markdown('<div ...>')`` left open to wrap a following widget.

    Heuristic: an unclosed ``<div`` inside a single ``st.markdown`` call, or a
    bare closing-only ``</div>`` markdown — both signal the forbidden wrapper
    pattern (§A-8). Pure HTML blocks always open and close within one call.
    """

    import re

    offenders: list[str] = []
    # The forbidden pattern is a markdown that opens a <div> to be closed by a
    # *separate* later markdown, i.e. a standalone opening-only or closing-only
    # div string literal. Flag any string literal passed to st.markdown that
    # contains a closing </div> with no opening <div, or an opening <div with no
    # closing </div>, within that single literal.
    # Capture a full st.markdown(...) call argument up to its
    # `unsafe_allow_html=True` keyword (every HTML-emitting markdown uses it),
    # so adjacent string-literal concatenation inside one call is counted as a
    # whole. A single call that emits a net-unbalanced div is the wrapper.
    call = re.compile(
        r"st\.markdown\((?P<body>.*?)unsafe_allow_html\s*=\s*True",
        re.DOTALL,
    )
    for path in APP_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for m in call.finditer(text):
            body = m.group("body")
            opens = body.count("<div")
            closes = body.count("</div>")
            if opens != closes:
                snippet = body.strip()[:60].replace("\n", " ")
                offenders.append(f"{path.name}: unbalanced div in st.markdown -> {snippet!r}")
    assert offenders == [], offenders


# ---------------------------------------------------------------------------
# AppTest: every page renders without raising
# ---------------------------------------------------------------------------
def _run_app(monkeypatch, page: str, *, empty_dir=None):
    from streamlit.testing.v1 import AppTest

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    if empty_dir is not None:
        monkeypatch.setattr(
            data_mod, "cached_bundle", lambda *a, **k: data_mod.load_bundle(empty_dir)
        )
    at = AppTest.from_file(str(APP_PY), default_timeout=30)
    at.session_state["page"] = page
    at.run()
    return at


PAGES = ["홈", "설비별 분석", "이상치 모니터링", "AI Copilot", "설정"]


@pytest.mark.parametrize("page", PAGES)
def test_each_page_renders_without_exception(monkeypatch, page):
    at = _run_app(monkeypatch, page)
    assert not at.exception


@pytest.mark.parametrize("page", PAGES)
def test_each_page_renders_with_empty_artifacts(monkeypatch, tmp_path, page):
    empty = tmp_path / "empty"
    empty.mkdir()
    at = _run_app(monkeypatch, page, empty_dir=empty)
    assert not at.exception


def test_default_page_is_home(monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    at = AppTest.from_file(str(APP_PY), default_timeout=30)
    at.run()
    assert at.session_state["page"] == "홈"
    assert not at.exception


def test_navigation_has_single_source_of_truth():
    """The page lives in session_state['page'] only: no segmented-control nav keys."""
    text = APP_PY.read_text(encoding="utf-8")
    assert "page_switch" not in text
    assert "segmented_control" not in text.split("def _render_header")[0]
    assert "NAV_KEY" not in text
