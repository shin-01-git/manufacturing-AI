"""Role profiles (v3): each role changes menu, home KPIs/panels, anomaly detail, copilot defaults."""
from __future__ import annotations

import pathlib

import pytest

from config import config
from src.agent import agent as agent_mod
from src.app import page_logic

APP_PY = pathlib.Path(__file__).resolve().parent.parent / "src" / "app" / "app.py"
ROLES = list(config.ROLES)


def test_three_roles_defined_by_role_key_only():
    assert set(config.ROLES) == {"facility", "energy", "executive"}
    assert config.ROLES["executive"] == "경영진"
    assert set(page_logic.ROLE_PROFILES) == set(config.ROLES)


def test_profiles_are_distinct_and_valid():
    kpis = {r: tuple(page_logic.role_profile(r)["kpis"]) for r in ROLES}
    assert len(set(kpis.values())) == 3
    for r in ROLES:
        p = page_logic.role_profile(r)
        assert p["pages"][0] == page_logic.PAGE_HOME
        assert page_logic.PAGE_COPILOT in p["pages"]
        assert len(p["kpis"]) == 5 and len(p["home_row3"]) == 3
        assert p["audience"] in agent_mod.AUDIENCES
        assert len(p["questions"]) >= 4


def test_executive_hides_settings_and_equipment_and_detail():
    pages = page_logic.pages_for_role("executive")
    assert page_logic.PAGE_SETTINGS not in pages and page_logic.PAGE_EQUIPMENT not in pages
    assert not page_logic.role_profile("executive")["anomaly_detail"]
    assert page_logic.audience_for_role("executive") == "executive"


@pytest.mark.parametrize("role", ROLES)
def test_every_visible_page_renders_for_each_role(monkeypatch, role):
    from streamlit.testing.v1 import AppTest

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    for page in page_logic.pages_for_role(role):
        at = AppTest.from_file(str(APP_PY), default_timeout=60)
        at.session_state["role"] = role
        at.session_state["page"] = page
        at.run()
        assert not at.exception, f"{role}/{page}"
        assert at.session_state["page"] == page


def test_hidden_page_falls_back_to_home(monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    at = AppTest.from_file(str(APP_PY), default_timeout=60)
    at.session_state["role"] = "executive"
    at.session_state["page"] = page_logic.PAGE_SETTINGS
    at.run()
    assert at.session_state["page"] == page_logic.PAGE_HOME
    assert not at.exception


def test_home_kpis_differ_by_role(monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    labels = {}
    for role in ROLES:
        at = AppTest.from_file(str(APP_PY), default_timeout=60)
        at.session_state["role"] = role
        at.run()
        text = "\n".join(m.value for m in at.markdown)
        labels[role] = text
    tile = 'fec-tile-label">'
    assert tile + "전력량 MAE(504)" in labels["energy"] and tile + "전력량 MAE(504)" not in labels["executive"]
    assert tile + "이상 급증 설비" in labels["facility"] and tile + "경고 건수" in labels["executive"]
    assert "분석 플로우" in labels["energy"] and "분석 플로우" not in labels["executive"]
