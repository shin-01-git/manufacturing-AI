"""App logic runs without an OpenAI key and without the shared artifacts (8.5 / C5, S7)."""

from __future__ import annotations

from src.agent import agent as agent_mod
from src.app import ui_logic


def test_load_all_without_artifacts_does_not_raise(tmp_path, monkeypatch):
    """load_all over an empty dir returns error dicts, never raises (S7)."""

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    empty = tmp_path / "empty"
    empty.mkdir()
    bundle = ui_logic.load_all(artifacts_dir=empty)
    assert set(bundle) == {"forecast", "evaluation", "module_status", "events", "trend"}
    assert all("error" in part for part in bundle.values())


def test_data_period_handles_missing(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    bundle = ui_logic.load_all(artifacts_dir=empty)
    assert ui_logic.data_period(bundle["forecast"]) == ui_logic.tools_mod.NO_DATA


def test_prediction_tab_model_without_data_shows_no_data(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    bundle = ui_logic.load_all(artifacts_dir=empty)
    model = ui_logic.prediction_tab_model(bundle["forecast"], bundle["evaluation"])
    assert model["available"] is False
    assert model["totals"]["total_pred_pow"] is None


def test_anomaly_tab_model_without_data_is_empty(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    bundle = ui_logic.load_all(artifacts_dir=empty)
    model = ui_logic.anomaly_tab_model(bundle["module_status"], bundle["events"], bundle["trend"])
    assert model["status_rows"] == []
    assert model["event_count"] == 0
    assert model["surge_warnings"] == []


def test_agent_without_key_runs_on_full_app_bundle(fake_artifacts, monkeypatch):
    """Agent used by the AI tab works with no key, over the fake artifacts (C5)."""

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    agent = agent_mod.Agent(artifacts_dir=fake_artifacts)
    for audience in agent_mod.AUDIENCES:
        result = agent.briefing(audience)
        assert result.source == "rule"
        assert result.text
    answer = agent.ask("설비 이상 알려줘")
    assert answer.source == "rule"
    assert not agent_mod.contains_forbidden_assertion(answer.text)


def test_app_module_imports_without_streamlit_running():
    """Importing the app module must not launch Streamlit or read originals."""

    import importlib

    module = importlib.import_module("src.app.app")
    assert hasattr(module, "main")
