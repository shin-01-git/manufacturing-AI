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


def test_fmt_big_no_decimals():
    from src.app.ui import fmt
    assert fmt.fmt_big(19758.65) == "19,759"
    assert fmt.fmt_big(3556690.52) == "356만"
    assert fmt.fmt_big(355669052.0) == "3억 5,567만"
    assert fmt.fmt_full(3556690.523, "bill").startswith("3,556,690.52")


def test_month_granularity_partial():
    rows = [{"id": f"2025-05-{d:02d} {h:02d}:00:00", "pred_pow": 1.0} for d in range(10, 32) for h in range(24)]
    b = page_logic.resample_rows(rows, "월", ("pred_pow",))
    assert len(b) == 1 and b[0]["label"].startswith("2025-05") and b[0]["partial"]
