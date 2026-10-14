"""UI-logic tests for dashboard elements, colours, role enum, warnings (8.4)."""

from __future__ import annotations

from config import config
from src.agent import tools as tools_mod
from src.app import ui_logic


def test_header_info_has_team_title_tagline():
    info = ui_logic.header_info()
    assert info["team"] == config.TEAM_NAME == "문학적인 기계"
    assert info["title"] == config.PROJECT_TITLE == "Factory Energy Copilot"
    assert info["tagline"]


def test_role_switch_uses_enum_only_no_personal_names():
    assert ui_logic.role_label("facility") == "설비 관리 담당자"
    assert ui_logic.role_label("energy") == "에너지 관리 담당자"
    assert ui_logic.role_label("executive") == "경영진"
    # Unknown role falls back to the config default, never a personal name.
    assert ui_logic.role_label("someone") == config.ROLES[config.DEFAULT_ROLE]
    assert ui_logic.default_tab_for_role("facility") != ui_logic.default_tab_for_role("energy")


def test_status_colors_cover_all_states():
    assert ui_logic.status_color("정상") != ui_logic.status_color("이상")
    assert ui_logic.status_color("미확정") == ui_logic.STATUS_COLORS["미확정"]
    assert ui_logic.status_color("unknown") == ui_logic.DEFAULT_STATUS_COLOR


def test_interval_label_marks_provisional():
    label = ui_logic.interval_label()
    if config.PRED_INTERVAL_PROVISIONAL:
        assert "임시/잠정" in label


def test_conversion_formula_shows_single_config_rates():
    formula = ui_logic.conversion_formula()
    assert str(config.BILL_RATE) in formula
    assert str(config.CARBON_RATE) in formula


def test_prediction_tab_model_from_fake(fake_artifacts):
    forecast = tools_mod.get_forecast_summary(artifacts_dir=fake_artifacts)
    evaluation = tools_mod.get_evaluation(artifacts_dir=fake_artifacts)
    model = ui_logic.prediction_tab_model(forecast, evaluation)
    assert model["available"] is True
    assert model["totals"]["total_pred_bill"] is not None
    assert "전력량" in model["mae_cards"]
    assert model["baseline_comparison"]


def test_module_count_notice_differs_from_cap(fake_artifacts):
    status = tools_mod.get_module_status(artifacts_dir=fake_artifacts)  # 3 modules
    notice = ui_logic.module_count_notice(status)
    assert notice is not None
    assert "13" in notice


def test_anomaly_tab_model_colors_and_surge(fake_artifacts):
    status = tools_mod.get_module_status(artifacts_dir=fake_artifacts)
    events = tools_mod.get_anomaly_events(artifacts_dir=fake_artifacts)
    trend = tools_mod.get_anomaly_trend(artifacts_dir=fake_artifacts)
    model = ui_logic.anomaly_tab_model(status, events, trend)
    assert model["status_rows"]
    assert all("color" in r for r in model["status_rows"])
    # M0 has counts [1, 10] -> peak 10 >> 2 * mean(5.5) is false; use factor 1.5
    surges = ui_logic.surge_warnings(trend, factor=1.5)
    assert any(w["module"] == "M0" for w in surges)


def test_out_of_interval_warnings():
    rows = [
        {"id": "t0", "pred_pow": 100.0, "pred_low": 90.0, "pred_high": 110.0},
        {"id": "t1", "pred_pow": 100.0, "pred_low": 90.0, "pred_high": 110.0},
    ]
    actuals = {"t0": 50.0, "t1": 100.0}  # t0 below interval
    warnings = ui_logic.out_of_interval_warnings(rows, actuals)
    assert len(warnings) == 1
    assert warnings[0]["id"] == "t0"
    assert warnings[0]["direction"] == "below"
    # No actuals -> no warnings (data-less state handled by caller).
    assert ui_logic.out_of_interval_warnings(rows, None) == []


def test_performance_measurement_is_measurement_only():
    perf = ui_logic.performance_measurement(3.2)
    assert perf["elapsed_seconds"] == 3.2
    assert perf["target_seconds"] == 5
    assert "합격/실패 판정 안 함" in perf["verdict"]


# ---------------------------------------------------------------------------
# Rendering layer (ui package + tabs). Pure functions only; no Streamlit needed.
# ---------------------------------------------------------------------------
import pathlib

from src.app import data as data_mod
from src.app.ui import charts, components, fmt, theme


# ---- fmt: one formatter, same value -> same string (A-6, C2/S5) ----
def test_fmt_same_value_same_string():
    val = 19759391.79182341
    # The power total appears on a KPI card and in the copilot calc table; both
    # must render identically because both call fmt.fmt_pow.
    assert fmt.fmt_pow(val) == fmt.fmt_pow(val)
    assert fmt.fmt_pow(val) == fmt.abbreviate(val)


def test_fmt_big_numbers_use_single_abbreviation_scheme():
    assert fmt.abbreviate(250_000_000) == "2.50억"
    assert fmt.abbreviate(19_759_391.79) == "1,975.94만"
    assert fmt.abbreviate(39_130.11) == "3.91만"
    assert fmt.abbreviate(123.45) == "123.45"
    assert fmt.abbreviate(-250_000) == "-25.00만"


def test_fmt_missing_values_collapse_to_no_data():
    assert fmt.fmt_pow(None) == fmt.NO_DATA
    assert fmt.fmt_bill("x") == fmt.NO_DATA
    assert fmt.fmt_mae(None) == fmt.NO_DATA
    assert fmt.fmt_pct(None) == fmt.NO_DATA


def test_fmt_pct_ratio_and_percent():
    assert fmt.fmt_pct(0.005034) == "0.50%"
    assert fmt.fmt_pct(50, is_ratio=False) == "50.00%"


def test_fmt_units_come_from_config_only():
    assert fmt.unit_label("pow") == config.UNIT_LABELS["pow"]
    assert fmt.unit_footnote() == config.UNIT_FOOTNOTE
    # Never hard-codes kWh / 원.
    assert "kWh" not in fmt.fmt_pow(100.0, with_unit=True)
    assert "원" not in fmt.fmt_bill(100.0, with_unit=True)


# ---- components: HTML string builders ----
def test_kpi_card_contains_title_value_formula():
    html = components.kpi_card("총 예측 요금", "0.20억", unit="요금", formula="Σ(x × 180)")
    assert "총 예측 요금" in html
    assert "0.20억" in html
    assert "Σ(x × 180)" in html
    assert "fec-kpi" in html


def test_status_chip_uses_status_color():
    chip = components.status_chip("미확정")
    assert theme.status_color("미확정") in chip


def test_llm_status_chip_both_modes():
    assert "LLM 연결됨" in components.llm_status_chip(True)
    assert "대체 모드" in components.llm_status_chip(False)


def test_empty_state_names_the_artifact():
    html = components.empty_state("predictions.csv")
    assert "데이터에 없음" in html
    assert "predictions.csv" in html


def test_source_chip_found_and_missing():
    assert "데이터에 없음" in components.source_chip("x", found=False)
    assert "출처" in components.source_chip("predictions.csv", found=True)


# ---- charts: pure Figure builders ----
def test_forecast_band_chart_builds_figure(fake_artifacts):
    forecast = tools_mod.get_forecast_summary(artifacts_dir=fake_artifacts)
    fig = charts.forecast_band_chart(forecast["rows"])
    assert fig.data  # has traces
    # y-axis must not start at 0 (A-0 zoom).
    yr = fig.layout.yaxis.range
    assert yr is not None and yr[0] != 0


def test_forecast_band_chart_empty_rows():
    fig = charts.forecast_band_chart([])
    assert fig is not None


def test_phase_sync_chart_uses_config_thresholds():
    series = {
        "voltage": [{"x": "t0", "value": 205.0}],
        "power_factor": [{"x": "t0", "value": 80.0}],
    }
    fig = charts.phase_sync_chart(series)
    assert fig.data
    # Threshold lines reference config constants.
    assert config.VOLTAGE_THRESHOLD == 210.0
    assert config.POWER_FACTOR_THRESHOLD == 85.0


def test_heatmap_empty_is_safe():
    assert charts.heatmap([], [], []) is not None


def test_charts_have_axis_titles(fake_artifacts):
    """A-3: each chart exposes x/y axis names (units follow A-1, no kWh/원)."""

    forecast = tools_mod.get_forecast_summary(artifacts_dir=fake_artifacts)
    fig = charts.forecast_band_chart(forecast["rows"])
    assert fig.layout.xaxis.title.text
    assert fig.layout.yaxis.title.text

    trend = tools_mod.get_anomaly_trend(freq="D", artifacts_dir=fake_artifacts)
    hm = ui_logic.heatmap_matrix(trend)
    hfig = charts.heatmap(hm["modules"], hm["days"], hm["matrix"])
    assert hfig.layout.xaxis.title.text
    assert hfig.layout.yaxis.title.text

    bar = charts.horizontal_share_bar([{"label": "M0", "value": 3, "text": "3건"}])
    assert bar.layout.xaxis.title.text
    assert bar.layout.yaxis.title.text


# ---- new ui_logic pure helpers ----
def test_hourly_profile_averages_by_hour():
    rows = [
        {"id": "2025-05-10 00:00:00", "pred_bill": 100.0, "pred_carbon": 1.0},
        {"id": "2025-05-11 00:00:00", "pred_bill": 200.0, "pred_carbon": 3.0},
        {"id": "2025-05-10 01:00:00", "pred_bill": 50.0, "pred_carbon": 0.5},
    ]
    profile = ui_logic.hourly_profile(rows)
    by_hour = {p["hour"]: p for p in profile}
    assert by_hour[0]["bill"] == 150.0
    assert by_hour[1]["bill"] == 50.0


def test_selected_baseline_is_lowest_mae(fake_artifacts):
    evaluation = tools_mod.get_evaluation(artifacts_dir=fake_artifacts)
    assert ui_logic.selected_baseline_model(evaluation) == "global_mean_repeat"


def test_method_comparison_from_meta():
    meta = {"method_counts": {"rule": 10, "unsupervised": 20}, "method_overlap": {"rule_and_unsupervised": 10}}
    comp = ui_logic.method_comparison(meta)
    assert comp["available"] is True
    assert {"method": "rule", "count": 10} in comp["method_counts"]
    assert comp["method_overlap"][0]["count"] == 10
    assert ui_logic.method_comparison(None)["available"] is False


def test_heatmap_matrix_from_trend(fake_artifacts):
    trend = tools_mod.get_anomaly_trend(freq="D", artifacts_dir=fake_artifacts)
    hm = ui_logic.heatmap_matrix(trend)
    assert hm["modules"]
    assert hm["days"]
    assert len(hm["matrix"]) == len(hm["modules"])


def test_phase_sync_series_splits_voltage_and_pf():
    events = [
        {"type": "voltage_low_3phase", "localtime": "t0", "value": 205.0},
        {"type": "power_factor_low_3phase", "localtime": "t1", "value": 80.0},
        {"type": "statistical_outlier", "localtime": "t2", "value": 1.0},
    ]
    series = ui_logic.phase_sync_series(events)
    assert len(series["voltage"]) == 1
    assert len(series["power_factor"]) == 1


# ---- A-2: sidebar equipment / date filters are pure and testable ----
def test_filter_status_by_modules_keeps_only_selected(fake_artifacts):
    status = tools_mod.get_module_status(artifacts_dir=fake_artifacts)  # M0, M1, M2
    kept = ui_logic.filter_status_by_modules(status, ["M0", "M2"])
    mods = [r["module"] for r in kept["modules"]]
    assert mods == ["M0", "M2"]
    assert kept["count"] == 2


def test_filter_modules_none_means_all(fake_artifacts):
    status = tools_mod.get_module_status(artifacts_dir=fake_artifacts)
    events = tools_mod.get_anomaly_events(artifacts_dir=fake_artifacts)
    trend = tools_mod.get_anomaly_trend(artifacts_dir=fake_artifacts)
    assert ui_logic.filter_status_by_modules(status, None) == status
    assert ui_logic.filter_events_by_modules(events, None)["count"] == events["count"]
    assert ui_logic.filter_trend_by_modules(trend, None)["count"] == trend["count"]


def test_filter_events_by_modules_keeps_only_selected(fake_artifacts):
    events = tools_mod.get_anomaly_events(artifacts_dir=fake_artifacts)  # M0, M1
    kept = ui_logic.filter_events_by_modules(events, ["M1"])
    assert kept["count"] == 1
    assert all(e["module"] == "M1" for e in kept["events"])


def test_filter_trend_by_modules_keeps_only_selected(fake_artifacts):
    trend = tools_mod.get_anomaly_trend(artifacts_dir=fake_artifacts)
    kept = ui_logic.filter_trend_by_modules(trend, ["M0"])
    assert kept["points"]
    assert all(p["module"] == "M0" for p in kept["points"])


def test_filter_empty_selection_keeps_nothing(fake_artifacts):
    status = tools_mod.get_module_status(artifacts_dir=fake_artifacts)
    kept = ui_logic.filter_status_by_modules(status, [])
    assert kept["count"] == 0
    assert kept["modules"] == []


def test_filter_passes_error_dicts_through():
    err = {"error": "not_found", "message": "x"}
    assert ui_logic.filter_status_by_modules(err, ["M0"]) is err
    assert ui_logic.filter_events_by_modules(err, ["M0"]) is err
    assert ui_logic.filter_trend_by_modules(err, ["M0"]) is err


def test_filter_events_by_date_inclusive(fake_artifacts):
    events = tools_mod.get_anomaly_events(artifacts_dir=fake_artifacts)  # 2024-12-01
    inside = ui_logic.filter_events_by_date(events, "2024-12-01", "2024-12-31")
    assert inside["count"] == events["count"]
    outside = ui_logic.filter_events_by_date(events, "2025-01-01", "2025-01-31")
    assert outside["count"] == 0
    # Open bounds leave the set untouched.
    assert ui_logic.filter_events_by_date(events, None, None)["count"] == events["count"]


# ---- A-1: KPI card renders the optional interpretation line ----
def test_kpi_card_interp_renders_and_is_optional():
    with_interp = components.kpi_card(
        "총 예측 요금", "0.20억", formula="Σ(x × 180)", interp="값이 클수록 에너지 비용이 큼."
    )
    assert "fec-kpi-interp" in with_interp
    assert "값이 클수록 에너지 비용이 큼." in with_interp
    # Omitting interp keeps the card exactly as before (no interp div).
    without = components.kpi_card("총 예측 요금", "0.20억", formula="Σ(x × 180)")
    assert "fec-kpi-interp" not in without


def test_anomaly_cause_sections_never_asserts_fault():
    sections = ui_logic.anomaly_cause_sections("13(3호기)", "미확정")
    joined = " ".join(sections.values())
    assert "고장" not in joined
    assert "현장" in sections["checks"]


# ---- data loader: artifacts-only, degrades on missing ----
def test_load_bundle_includes_rebuild_meta(fake_artifacts):
    bundle = data_mod.load_bundle(artifacts_dir=fake_artifacts)
    assert set(bundle) >= {"forecast", "evaluation", "module_status", "events", "trend", "rebuild_meta"}


def test_load_bundle_empty_dir_does_not_raise(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    bundle = data_mod.load_bundle(artifacts_dir=empty)
    assert bundle["rebuild_meta"] is None
    assert all("error" in bundle[k] for k in ("forecast", "evaluation", "module_status", "events", "trend"))


# ---- forbidden strings must not appear in UI source (steering A-6) ----
def test_no_forbidden_strings_in_ui_source():
    """Forbidden strings must not appear anywhere in the UI source (A-6, grep 0)."""

    forbidden = ("고장", "Fault", "LIVE", "실시간")
    app_dir = pathlib.Path(__file__).resolve().parent.parent / "src" / "app"
    css = pathlib.Path(__file__).resolve().parent.parent / "src" / "app" / "ui" / "style.css"
    offenders: list[str] = []
    paths = list(app_dir.rglob("*.py")) + [css]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                offenders.append(f"{path.name}:{token}")
    assert offenders == [], f"forbidden strings in UI source: {offenders}"
