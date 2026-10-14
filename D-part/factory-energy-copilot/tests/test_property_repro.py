"""Hypothesis property test for tool-read reproducibility/determinism (Property 8).

Property 8 states that for the same input (same artifacts, same arguments),
running a component twice yields identical outputs. The artifacts-only tools are
pure reads over fixed files, so repeated calls must be byte-for-byte identical.
Tests use a tmp fake-artifacts dir and never touch the shared artifacts.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from tests.conftest import write_fake_artifacts
from src.agent import tools as tools_mod

_module_counts = st.integers(min_value=1, max_value=13)
_n_pred = st.integers(min_value=1, max_value=12)


# Feature: factory-energy-copilot, Property 8: 재현성(결정성) 불변식
@settings(max_examples=100, deadline=None)
@given(module_count=_module_counts, n_pred=_n_pred)
def test_property_tool_reads_are_deterministic(tmp_path_factory, module_count, n_pred):
    """Same artifacts + same args -> identical tool outputs on repeat calls."""

    root = tmp_path_factory.mktemp("artifacts")
    write_fake_artifacts(root, module_count=module_count, n_pred=n_pred)

    first = {
        "forecast": tools_mod.get_forecast_summary(artifacts_dir=root),
        "evaluation": tools_mod.get_evaluation(artifacts_dir=root),
        "module_status": tools_mod.get_module_status(artifacts_dir=root),
        "events": tools_mod.get_anomaly_events(artifacts_dir=root),
        "trend": tools_mod.get_anomaly_trend(artifacts_dir=root),
    }
    second = {
        "forecast": tools_mod.get_forecast_summary(artifacts_dir=root),
        "evaluation": tools_mod.get_evaluation(artifacts_dir=root),
        "module_status": tools_mod.get_module_status(artifacts_dir=root),
        "events": tools_mod.get_anomaly_events(artifacts_dir=root),
        "trend": tools_mod.get_anomaly_trend(artifacts_dir=root),
    }

    assert first == second
    assert first["forecast"]["count"] == n_pred
    assert first["module_status"]["count"] == module_count
