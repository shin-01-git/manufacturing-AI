"""Hypothesis property test for artifacts-only path security (Property 7)."""

from __future__ import annotations

from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from config import config
from src.agent import tools as tools_mod

# Path fragments that must always escape the artifacts root.
_ESCAPE_PREFIXES = st.sampled_from(
    [
        "../",
        "../../",
        "..\\",
        "..\\..\\",
        "../data/",
        "..\\data\\",
        "/etc/",
        "C:\\Windows\\",
        "data/",
    ]
)
_NAMES = st.sampled_from(
    [
        "rtu_data_full_훈련용.csv",
        "secret.env",
        "predictions.csv",
        "evaluation.json",
        "x.parquet",
    ]
)


# Feature: factory-energy-copilot, Property 7: artifacts-only 경로 보안 불변식
@settings(max_examples=200, deadline=None)
@given(prefix=_ESCAPE_PREFIXES, name=_NAMES)
def test_property_outside_paths_are_denied(prefix, name):
    """Any path escaping the artifacts root (originals included) is rejected."""

    bad = prefix + name
    try:
        resolved = tools_mod.resolve_artifact_path(bad)
    except tools_mod.PathSecurityError:
        return  # denied as required
    # If it did not raise, it must still be inside the artifacts root.
    root = config.ARTIFACTS_DIR.resolve()
    resolved.relative_to(root)  # raises if it escaped -> property violated


# Feature: factory-energy-copilot, Property 7: artifacts-only 경로 보안 불변식
@settings(max_examples=100, deadline=None)
@given(name=_NAMES)
def test_property_inside_paths_are_allowed(name):
    """A plain artifact name resolves inside the artifacts root."""

    resolved = tools_mod.resolve_artifact_path(name)
    root = config.ARTIFACTS_DIR.resolve()
    resolved.relative_to(root)  # must not raise
