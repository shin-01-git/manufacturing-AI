"""탐지 시간대 패널(3-뷰) 순수 집계/포매터/차트 테스트 (anomaly-timeline-plan.md §2 항목).

커버리지: 합 보존(daily sum == hourly sum), p95 정확성, 59/60/61일 경계 일→주 전환,
median=0 급증일 없음, 빈 DataFrame 안전 처리, occurrence-hour ratio>90% 분기(active_modules
정렬), fmt 신규 포매터 2개, charts 신규 빌더 3개(빈 입력 포함).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from src.app import page_logic
from src.app.ui import charts, fmt, theme


def _frame(records: list[dict]) -> pd.DataFrame:
    cols = ["hour", "module", "type", "count"]
    if not records:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(records)


def _hourly_series(module: str, start: datetime, hours: int, count: int = 1, etype: str = "voltage_low_3phase"):
    return [
        {"hour": start + timedelta(hours=i), "module": module, "type": etype, "count": count}
        for i in range(hours)
    ]


# ---------------------------------------------------------------------------
# daily_counts / surge_days: sum preservation + median-0 guard
# ---------------------------------------------------------------------------
def test_daily_counts_sum_preservation():
    start = datetime(2024, 12, 1)
    records = _hourly_series("A", start, 24 * 5, count=2)
    trend = _frame(records)
    daily = page_logic.daily_counts(trend, ["A"])
    assert daily["count"].sum() == trend["count"].sum()
    assert len(daily) == 5  # 5 distinct days
    assert daily["by_type"].iloc[0] == {"voltage_low_3phase": 24 * 2}


def test_daily_counts_by_type_breakdown_sums_to_count():
    start = datetime(2024, 12, 1)
    records = _hourly_series("A", start, 10, etype="voltage_low_3phase")
    records += _hourly_series("A", start, 10, etype="power_factor_low_3phase")
    trend = _frame(records)
    daily = page_logic.daily_counts(trend, ["A"])
    row = daily.iloc[0]
    assert sum(row["by_type"].values()) == row["count"]


def test_surge_days_flags_high_days_above_median_factor():
    start = datetime(2024, 12, 1)
    # 9 normal days at count=1, one surge day at count=10.
    records = []
    for d in range(9):
        records.append({"hour": start + timedelta(days=d), "module": "A", "type": "t", "count": 1})
    records.append({"hour": start + timedelta(days=9), "module": "A", "type": "t", "count": 10})
    trend = _frame(records)
    daily = page_logic.daily_counts(trend, ["A"])
    surges = page_logic.surge_days(daily, factor=3.0)
    assert len(surges) == 1
    assert surges.iloc[0]["day"] == (start + timedelta(days=9)).date()


def test_surge_days_median_zero_means_no_surge():
    """A module whose daily-count median is 0 must never be flagged as surging."""

    start = datetime(2024, 12, 1)
    records = [
        {"hour": start, "module": "A", "type": "t", "count": 5},
        {"hour": start + timedelta(days=1), "module": "A", "type": "t", "count": 0},
        {"hour": start + timedelta(days=2), "module": "A", "type": "t", "count": 0},
    ]
    trend = _frame(records)
    daily = page_logic.daily_counts(trend, ["A"])
    # median of [5,0,0] is 0 -> guard should suppress any surge flag.
    surges = page_logic.surge_days(daily, factor=1.0)
    assert len(surges) == 0


def test_surge_days_empty_daily_is_safe():
    empty = pd.DataFrame(columns=["module", "day", "count", "by_type"])
    out = page_logic.surge_days(empty, factor=2.0)
    assert len(out) == 0


# ---------------------------------------------------------------------------
# hour_by_day_matrix: p95 correctness + 60-day boundary
# ---------------------------------------------------------------------------
def test_hour_by_day_matrix_p95_known_input():
    """20 days, hour 0 count=i for day i (0..19); p95 over 20*24=480 cells.

    Only hour-0 cells are nonzero (values 0..19); everything else is 0. The 95th
    percentile of that flat array is computed independently with numpy semantics
    (pandas .quantile uses the same linear interpolation as numpy by default).
    """

    import numpy as np

    start = datetime(2024, 12, 1)
    records = []
    for d in range(20):
        records.append({"hour": start + timedelta(days=d), "module": "A", "type": "t", "count": d})
    trend = _frame(records)
    matrix = page_logic.hour_by_day_matrix(trend, "A")
    flat = [v for row in matrix["matrix"] for v in row]
    expected = int(round(float(np.percentile(flat, 95))))
    assert matrix["p95"] == expected
    assert matrix["unit"] == "일"


@pytest.mark.parametrize("days,expected_unit", [(59, "일"), (60, "일"), (61, "주")])
def test_hour_by_day_matrix_60_day_boundary(days, expected_unit):
    start = datetime(2024, 1, 1)
    records = [
        {"hour": start + timedelta(days=d), "module": "A", "type": "t", "count": 1}
        for d in range(days)
    ]
    trend = _frame(records)
    matrix = page_logic.hour_by_day_matrix(trend, "A", weekly_if_days_over=60)
    assert matrix["unit"] == expected_unit


def test_hour_by_day_matrix_empty_is_safe():
    empty = pd.DataFrame(columns=["hour", "module", "type", "count"])
    matrix = page_logic.hour_by_day_matrix(empty, "A")
    assert matrix["cols"] == []
    assert matrix["matrix"] == []
    assert matrix["p95"] == 0


# ---------------------------------------------------------------------------
# day_hour_profile / usual_hour_profile
# ---------------------------------------------------------------------------
def test_day_hour_profile_zero_fills_24_hours():
    start = datetime(2024, 12, 1, 5)
    records = [{"hour": start, "module": "A", "type": "voltage_low_3phase", "count": 7}]
    trend = _frame(records)
    profile = page_logic.day_hour_profile(trend, "A", "2024-12-01")
    assert len(profile) == 24
    assert profile[5]["count"] == 7
    assert profile[5]["by_type"] == {"voltage_low_3phase": 7}
    assert profile[0]["count"] == 0


def test_usual_hour_profile_divides_by_distinct_days():
    start = datetime(2024, 12, 1, 0)
    records = [
        {"hour": start, "module": "A", "type": "t", "count": 4},
        {"hour": start + timedelta(days=1), "module": "A", "type": "t", "count": 2},
    ]
    trend = _frame(records)
    usual = page_logic.usual_hour_profile(trend, "A")
    assert usual[0] == pytest.approx((4 + 2) / 2)
    assert usual[1] == 0.0


def test_usual_hour_profile_empty_is_safe():
    empty = pd.DataFrame(columns=["hour", "module", "type", "count"])
    assert page_logic.usual_hour_profile(empty, "A") == [0.0] * 24


# ---------------------------------------------------------------------------
# active_modules / occurrence_hour_ratio — >90% "상시 발생" 분기용 데이터
# ---------------------------------------------------------------------------
def test_active_modules_sorted_by_occurrence_hour_ratio_desc():
    start = datetime(2024, 12, 1)
    # Module HIGH: active essentially every hour over 10 days (240 slots).
    high = _hourly_series("HIGH", start, 240, count=1)
    # Module LOW: active only 24 of those 240 hour-slots (10%).
    low = [
        {"hour": start + timedelta(hours=i * 10), "module": "LOW", "type": "t", "count": 1}
        for i in range(24)
    ]
    trend = _frame(high + low)
    modules = page_logic.active_modules(trend)
    assert modules[0] == "HIGH"
    assert modules[1] == "LOW"
    assert page_logic.occurrence_hour_ratio(trend, "HIGH") > 0.9
    assert page_logic.occurrence_hour_ratio(trend, "LOW") < 0.9


def test_active_modules_excludes_zero_count_modules():
    start = datetime(2024, 12, 1)
    records = _hourly_series("A", start, 5, count=1)
    records.append({"hour": start, "module": "ZERO", "type": "t", "count": 0})
    trend = _frame(records)
    assert page_logic.active_modules(trend) == ["A"]


def test_active_modules_empty_is_safe():
    empty = pd.DataFrame(columns=["hour", "module", "type", "count"])
    assert page_logic.active_modules(empty) == []


# ---------------------------------------------------------------------------
# filter_events_by_hour
# ---------------------------------------------------------------------------
def test_filter_events_by_hour_keeps_matching_hour():
    events = {
        "events": [
            {"localtime": "2024-12-01 05:10:00"},
            {"localtime": "2024-12-01 17:00:00"},
        ]
    }
    kept = page_logic.filter_events_by_hour(events, 17)
    assert kept["count"] == 1
    assert kept["events"][0]["localtime"] == "2024-12-01 17:00:00"


def test_filter_events_by_hour_none_keeps_all():
    events = {"events": [{"localtime": "2024-12-01 05:10:00"}]}
    assert page_logic.filter_events_by_hour(events, None) == events


# ---------------------------------------------------------------------------
# fmt: new formatters
# ---------------------------------------------------------------------------
def test_fmt_count_per_day_rounds_and_suffixes():
    assert fmt.fmt_count_per_day(312.4) == "312건"
    assert fmt.fmt_count_per_day(0) == "0건"
    assert fmt.fmt_count_per_day(None) == fmt.NO_DATA


def test_fmt_time_full_keeps_seconds():
    assert fmt.fmt_time_full("2025-05-14 03:00:12") == "03:00:12"
    assert fmt.fmt_time_full("2025-05-14T03:00:12") == "03:00:12"
    assert fmt.fmt_time_full(None) == fmt.NO_DATA
    assert fmt.fmt_time_full("not-a-time") == fmt.NO_DATA


# ---------------------------------------------------------------------------
# charts: new builders — basic shape + empty-input safety
# ---------------------------------------------------------------------------
def test_anomaly_daily_small_multiples_builds_one_row_per_module():
    start = datetime(2024, 12, 1)
    records = _hourly_series("A", start, 24 * 3) + _hourly_series("B", start, 24 * 3)
    trend = _frame(records)
    modules = ["A", "B"]
    daily = page_logic.daily_counts(trend, modules)
    surges = page_logic.surge_days(daily, 3.0)
    fig = charts.anomaly_daily_small_multiples(
        daily, surges, modules=modules, surge_factor=3.0,
        accent_color=theme.COLOR["accent"], alert_color=theme.COLOR["alert"],
        muted_color=theme.COLOR["muted"],
    )
    assert len(fig.data) >= 2  # at least one bar trace per module


def test_anomaly_daily_small_multiples_empty_is_safe():
    empty = pd.DataFrame(columns=["module", "day", "count", "by_type"])
    fig = charts.anomaly_daily_small_multiples(
        empty, empty, modules=[], surge_factor=3.0,
        accent_color=theme.COLOR["accent"], alert_color=theme.COLOR["alert"],
        muted_color=theme.COLOR["muted"],
    )
    assert fig is not None


def test_anomaly_hour_heatmap_with_sidebar_two_traces():
    start = datetime(2024, 12, 1)
    records = _hourly_series("A", start, 24 * 3)
    trend = _frame(records)
    matrix = page_logic.hour_by_day_matrix(trend, "A")
    fig = charts.anomaly_hour_heatmap_with_sidebar(
        matrix, night_hours=[22, 23, 0, 1], accent_color=theme.COLOR["accent"],
        low_color=theme.token("heat_low"), faint_color=theme.token("faint"),
    )
    assert len(fig.data) == 2  # heatmap + sidebar bar


def test_anomaly_hour_heatmap_with_sidebar_empty_is_safe():
    empty_matrix = {"rows": list(range(24)), "cols": [], "matrix": [], "unit": "일", "p95": 0}
    fig = charts.anomaly_hour_heatmap_with_sidebar(
        empty_matrix, night_hours=[], accent_color=theme.COLOR["accent"],
        low_color=theme.token("heat_low"), faint_color=theme.token("faint"),
    )
    assert fig is not None


def test_anomaly_24h_stacked_bar_has_usual_line_trace():
    profile = [{"hour": h, "count": h, "by_type": {"voltage_low_3phase": h}} for h in range(24)]
    usual = [float(h) / 2 for h in range(24)]
    fig = charts.anomaly_24h_stacked_bar(
        profile, usual, type_colors={"voltage_low_3phase": theme.phase("r")},
    )
    names = [t.name for t in fig.data]
    assert "평소" in names


def test_anomaly_24h_stacked_bar_empty_is_safe():
    fig = charts.anomaly_24h_stacked_bar([], [], type_colors={})
    assert fig is not None


# ---------------------------------------------------------------------------
# data.py: anomaly_hourly_frame bundle key (artifacts-only, degrades on missing)
# ---------------------------------------------------------------------------
def test_load_bundle_includes_anomaly_hourly_frame(fake_artifacts):
    from src.app import data as data_mod

    bundle = data_mod.load_bundle(artifacts_dir=fake_artifacts)
    assert "anomaly_hourly_frame" in bundle
    assert "frame" in bundle["anomaly_hourly_frame"]
    frame = bundle["anomaly_hourly_frame"]["frame"]
    assert {"hour", "module", "type", "count"} <= set(frame.columns)


def test_load_bundle_empty_dir_anomaly_hourly_frame_errors(tmp_path):
    from src.app import data as data_mod

    empty = tmp_path / "empty"
    empty.mkdir()
    bundle = data_mod.load_bundle(artifacts_dir=empty)
    assert "error" in bundle["anomaly_hourly_frame"]
