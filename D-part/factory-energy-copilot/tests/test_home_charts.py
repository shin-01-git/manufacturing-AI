"""홈 차트 보조 함수 테스트 (zoom_range, forecast_basis, 부분 구간 제외)."""

from src.app import page_logic
from src.app.ui import charts


def test_zoom_range_pads_span():
    lo, hi = page_logic.zoom_range([100.0, 110.0])
    assert lo == 98.0 and hi == 112.0


def test_zoom_range_single_and_flat():
    lo, hi = page_logic.zoom_range([50.0])
    assert lo < 50.0 < hi
    lo2, hi2 = page_logic.zoom_range([7.0, 7.0])
    assert lo2 < 7.0 < hi2


def test_zoom_range_empty():
    assert page_logic.zoom_range([]) is None
    assert page_logic.zoom_range([None]) is None


def test_period_chart_excludes_partial_from_range():
    buckets = [
        {"key": "2025-05-10", "label": "05-10", "n": 24, "partial": False, "pred_pow": 1000.0, "pred_carbon": 1.0},
        {"key": "2025-05-11", "label": "05-11", "n": 24, "partial": False, "pred_pow": 1010.0, "pred_carbon": 1.0},
        {"key": "2025-05-31", "label": "05-31(부분)", "n": 1, "partial": True, "pred_pow": 40.0, "pred_carbon": 1.0},
    ]
    fig = charts.period_bar_chart(buckets)
    lo, hi = fig.layout.yaxis.range
    assert lo > 40.0 and lo < 1000.0 < 1010.0 < hi
    xs = [x for tr in fig.data for x in (tr.x or [])]
    assert "05-31(부분)" not in xs


def test_forecast_basis_missing_keys():
    out = page_logic.forecast_basis({"selection": {"error": {}}, "forecast": {"error": {}}})
    assert all(v is None for v in out.values())
    out2 = page_logic.forecast_basis({"selection": {"selected_model": "calendar_linear_regression"}})
    assert out2["model"] == "달력 특성 선형회귀"
