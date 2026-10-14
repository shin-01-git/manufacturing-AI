"""Pure page-logic tests (v3): resample, deltas, settings-based totals, alerts, type chips."""
from __future__ import annotations

from datetime import datetime, timedelta

from config import config
from src.app import page_logic


def _rows(start="2025-05-10 00:00:00", hours=505, base=100.0):
    t0 = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
    return [
        {"id": (t0 + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S"),
         "pred_pow": base, "pred_carbon": base * 0.424,
         "pred_low": base - 5, "pred_high": base + 5}
        for i in range(hours)
    ]


def test_resample_marks_partial_edges():
    days = page_logic.resample_rows(_rows(), "일", ("pred_pow",))
    assert days[-1]["partial"] and days[-1]["n"] == 1 and "(부분)" in days[-1]["label"]
    assert not days[0]["partial"]
    weeks = page_logic.resample_rows(_rows(), "주", ("pred_pow",))
    assert weeks[0]["partial"] and weeks[-1]["partial"]
    assert sum(w["n"] for w in weeks) == 505


def test_delta_hidden_for_hour_unit_and_uses_complete_buckets():
    rows = _rows()
    assert page_logic.delta_pct(page_logic.resample_rows(rows, "시간", ("pred_pow",)), "pred_pow", "시간") is None
    days = page_logic.resample_rows(rows, "일", ("pred_pow",))
    assert page_logic.delta_pct(days, "pred_pow", "일") == 0.0


def test_totals_use_settings_rates_and_carbon_cost_needs_price():
    s = page_logic.default_settings()
    t = page_logic.scaled_totals(_rows(hours=10), s)
    assert t["pow"] == 1000.0
    assert t["bill"] == 1000.0 * config.BILL_RATE
    assert t["carbon_cost"] is None and t["total_cost"] is None
    s["carbon_price"] = 10000.0
    t = page_logic.scaled_totals(_rows(hours=10), s)
    assert t["carbon_cost"] == t["carbon"] / 1000.0 * 10000.0
    assert t["total_cost"] == t["bill"] + t["carbon_cost"]


def test_carbon_price_default_is_blank_and_rates_flag():
    s = page_logic.default_settings()
    assert s["carbon_price"] is None
    assert not page_logic.rates_differ_from_default(s)
    s["bill_rate"] = 200.0
    assert page_logic.rates_differ_from_default(s)


def test_alerts_flag_interval_exit_and_report_errors():
    s = page_logic.default_settings()
    avp = {"rows": [
        {"id": "2025-05-10 00:00:00", "actual_pow": 120.0, "pred_pow": 100.0, "pred_low": 95.0, "pred_high": 105.0},
        {"id": "2025-05-10 01:00:00", "actual_pow": 100.0, "pred_pow": 100.0, "pred_low": 95.0, "pred_high": 105.0},
    ]}
    forecast = {"rows": _rows(hours=2)}
    out = page_logic.build_alerts(avp, forecast, {"points": []}, s)
    assert out["interval_count"] == 1 and out["errors"] == []
    bad = page_logic.build_alerts({"error": {}}, forecast, {"points": []}, s)
    assert "actual_vs_pred.csv" in bad["errors"]
    s["alert_interval"] = False
    assert page_logic.build_alerts(avp, forecast, {"points": []}, s)["interval_count"] == 0


def test_type_options_only_contain_types_in_data():
    events = {"events": [{"type": "voltage_low_3phase"}, {"type": "voltage_low_3phase"}, {"type": "power_factor_low_3phase"}]}
    opts = page_logic.anomaly_type_options(events)
    assert {o["type"] for o in opts} == {"voltage_low_3phase", "power_factor_low_3phase"}
    assert opts[0]["count"] == 2
    assert page_logic.anomaly_type_options({"error": {}}) == []


def test_night_hours_wrap_midnight():
    s = page_logic.default_settings()
    assert page_logic.night_hours(s) == [22, 23, 0, 1, 2, 3, 4, 5]


def test_slack_url_never_rendered(monkeypatch):
    from src.app.tabs import settings as settings_tab
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/secret-token")
    assert settings_tab.slack_connected() is True
    src = (settings_tab.__file__)
    text = open(src, encoding="utf-8").read()
    assert 'os.environ.get("SLACK_WEBHOOK_URL"' in text and "st.write(os.environ" not in text
