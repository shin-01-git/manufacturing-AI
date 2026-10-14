"""Tools never read outside the artifacts root, incl. the raw CSVs (8.5 / C1, S7)."""

from __future__ import annotations

from pathlib import Path

from config import config
from src.agent import tools as tools_mod


def test_tool_source_does_not_reference_raw_or_validation_csv():
    """Static check: the tools module names no raw/validation data path."""

    text = (Path(__file__).resolve().parent.parent / "src" / "agent" / "tools.py").read_text(
        encoding="utf-8"
    )
    assert "TRAINING_CSV" not in text
    assert "VALIDATION_CSV" not in text
    assert "rtu_data_full" not in text
    assert "rtu_ground_truth" not in text


def test_path_outside_artifacts_is_denied_for_originals():
    """Attempts to reach the raw CSVs under data/ are rejected (Property 7)."""

    for bad in (
        "../data/rtu_data_full_훈련용.csv",
        "..\\data\\rtu_ground_truth_may_검증용(5월).csv",
        "../../etc/passwd",
    ):
        try:
            tools_mod.resolve_artifact_path(bad)
            assert False, f"expected denial for {bad!r}"
        except tools_mod.PathSecurityError:
            pass


def test_tools_only_touch_their_single_artifact(fake_artifacts, monkeypatch):
    """Each tool reads only its own artifact file; removing others still works."""

    # Remove everything except predictions.csv; forecast must still succeed and
    # the other tools must report not_found (not crash, not read elsewhere).
    for name in ("evaluation.json", "module_status.csv", "anomaly_events.parquet", "anomaly_hourly.parquet"):
        (fake_artifacts / name).unlink()

    assert "error" not in tools_mod.get_forecast_summary(artifacts_dir=fake_artifacts)
    assert tools_mod.get_evaluation(artifacts_dir=fake_artifacts)["error"] == "not_found"
    assert tools_mod.get_module_status(artifacts_dir=fake_artifacts)["error"] == "not_found"
    assert tools_mod.get_anomaly_events(artifacts_dir=fake_artifacts)["error"] == "not_found"
    assert tools_mod.get_anomaly_trend(artifacts_dir=fake_artifacts)["error"] == "not_found"


def test_artifacts_dir_default_is_config_root():
    """The default root is config.ARTIFACTS_DIR (shared artifacts contract)."""

    resolved = tools_mod.resolve_artifact_path("predictions.csv")
    assert resolved.parent.resolve() == config.ARTIFACTS_DIR.resolve()
