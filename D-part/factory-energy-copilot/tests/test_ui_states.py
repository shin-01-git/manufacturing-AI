"""UI error/empty state tests (v3 §8): remove one artifact at a time and render pages."""
from __future__ import annotations

import pathlib
import shutil

import pytest

from config import config
from src.app import data as data_mod

APP_PY = pathlib.Path(__file__).resolve().parent.parent / "src" / "app" / "app.py"
REAL = config.ARTIFACTS_DIR

COPY_FILES = [
    "predictions.csv", "evaluation.json", "module_status.csv", "anomaly_events.parquet",
    "anomaly_hourly.parquet", "actual_vs_pred.csv", "hourly_from_full.parquet",
    "anomaly_rebuild_meta.json", "model_selection.json", "aggregate_run.json",
]
# removed artifact -> (page that must show the error state, text that must NOT appear there)
CASES = {
    "predictions.csv": ("홈", None),
    "actual_vs_pred.csv": ("홈", None),
    "evaluation.json": ("홈", None),
    "module_status.csv": ("이상치 모니터링", "13 / 13"),
    "anomaly_events.parquet": ("이상치 모니터링", "64,934"),
    "anomaly_hourly.parquet": ("이상치 모니터링", "64,934"),
    "hourly_from_full.parquet": ("설비별 분석", None),
}

pytestmark = pytest.mark.skipif(
    not (REAL / "predictions.csv").exists(), reason="shared artifacts not built"
)


def _page_text(at) -> str:
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption]
    return "\n".join(str(p) for p in parts)


def _run(monkeypatch, tmp_path, missing: str, page: str):
    from streamlit.testing.v1 import AppTest

    root = tmp_path / "art"
    root.mkdir(parents=True)
    for name in COPY_FILES:
        if name != missing and (REAL / name).exists():
            shutil.copy2(REAL / name, root / name)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(data_mod, "cached_bundle", lambda *a, **k: data_mod.load_bundle(root))
    at = AppTest.from_file(str(APP_PY), default_timeout=60)
    at.session_state["role"] = "energy"  # energy sees MAE / model panels
    at.session_state["page"] = page
    at.run()
    return at


@pytest.mark.parametrize("missing", list(CASES))
def test_missing_artifact_shows_error_state_not_calm_numbers(monkeypatch, tmp_path, missing):
    page, forbidden = CASES[missing]
    at = _run(monkeypatch, tmp_path, missing, page)
    assert not at.exception
    text = _page_text(at)
    assert missing in text, f"error state for {missing} not shown on {page}"
    assert "Traceback" not in text and "Internal Server Error" not in text
    if forbidden:
        assert forbidden not in text


@pytest.mark.parametrize("missing", list(CASES))
def test_other_pages_survive_a_missing_artifact(monkeypatch, tmp_path, missing):
    for page in ("홈", "설비별 분석", "이상치 모니터링", "AI Copilot", "설정"):
        at = _run(monkeypatch, tmp_path / page.replace(" ", "_"), missing, page)
        assert not at.exception, f"{page} raised without {missing}"


def test_error_alert_strip_withholds_summary_numbers(monkeypatch, tmp_path):
    at = _run(monkeypatch, tmp_path, "actual_vs_pred.csv", "홈")
    text = _page_text(at)
    assert "경고 없음" not in text
    assert "불러오지 못함" in text
