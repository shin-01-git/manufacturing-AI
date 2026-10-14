"""Agent fallback, three-section anomaly format, and forbidden-assertion tests (8.3)."""

from __future__ import annotations

from src.agent import agent as agent_mod


def test_no_key_no_exception_rule_summary(fake_artifacts, monkeypatch):
    """Without OPENAI_API_KEY the agent answers via the rule-based path, no raise."""

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    agent = agent_mod.Agent(artifacts_dir=fake_artifacts)

    result = agent.ask("이번 기간 요약 보고해줘", audience=agent_mod.AUDIENCE_EXECUTIVE)
    assert result.source == "rule"
    assert result.text
    assert not agent_mod.contains_forbidden_assertion(result.text)


def test_executive_briefing_is_five_lines_or_fewer(fake_artifacts, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    agent = agent_mod.Agent(artifacts_dir=fake_artifacts)
    result = agent.briefing(agent_mod.AUDIENCE_EXECUTIVE)
    assert result.source == "rule"
    assert len(result.data["briefing"].lines) == 5


def test_audience_switching_differs(fake_artifacts, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    agent = agent_mod.Agent(artifacts_dir=fake_artifacts)
    execu = agent.briefing(agent_mod.AUDIENCE_EXECUTIVE).text
    energy = agent.briefing(agent_mod.AUDIENCE_ENERGY).text
    field = agent.briefing(agent_mod.AUDIENCE_FIELD).text
    assert execu != energy != field
    assert "경영진" in execu
    assert "에너지 관리 담당자" in energy
    assert "설비 관리 담당자" in field


def test_anomaly_answer_has_three_sections_and_no_assertion(fake_artifacts, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    agent = agent_mod.Agent(artifacts_dir=fake_artifacts)
    result = agent.anomaly(module="M0")
    assert agent_mod.SECTION_OBSERVED in result.text
    assert agent_mod.SECTION_CAUSES in result.text
    assert agent_mod.SECTION_CHECKS in result.text
    assert not agent_mod.contains_forbidden_assertion(result.text)


def test_missing_artifacts_report_no_data(tmp_path, monkeypatch):
    """No artifacts -> rule summary says 데이터에 없음, still no exception (C2/C5)."""

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    empty = tmp_path / "empty"
    empty.mkdir()
    agent = agent_mod.Agent(artifacts_dir=empty)
    result = agent.briefing(agent_mod.AUDIENCE_ENERGY)
    assert agent_mod.NO_DATA in result.text


def test_sanitizer_removes_forbidden_assertions():
    cleaned = agent_mod._sanitize_assertions("이 설비는 고장이며 위험 확정입니다.")
    assert not agent_mod.contains_forbidden_assertion(cleaned)


def test_cache_returns_same_object(fake_artifacts, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    agent = agent_mod.Agent(artifacts_dir=fake_artifacts)
    first = agent.briefing(agent_mod.AUDIENCE_EXECUTIVE)
    second = agent.briefing(agent_mod.AUDIENCE_EXECUTIVE)
    assert first is second
