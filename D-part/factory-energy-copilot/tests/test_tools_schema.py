"""Input/output schema and error-response tests for the artifacts-only tools (8.1)."""

from __future__ import annotations

from src.agent import tools as tools_mod


def test_forecast_summary_schema(fake_artifacts):
    result = tools_mod.get_forecast_summary(artifacts_dir=fake_artifacts)
    assert "error" not in result
    assert result["count"] == 5
    for key in ("total_pred_pow", "total_pred_bill", "total_pred_carbon", "avg_pred_pow"):
        assert isinstance(result[key], float)
    assert result["interval_provisional"] is True
    row = result["rows"][0]
    assert set(("id", "pred_pow", "pred_bill", "pred_carbon", "pred_low", "pred_high")).issubset(row)


def test_forecast_summary_filtered(fake_artifacts):
    result = tools_mod.get_forecast_summary(
        "2025-05-10 01:00:00", "2025-05-10 02:00:00", artifacts_dir=fake_artifacts
    )
    assert result["count"] == 2


def test_forecast_bad_input_returns_error(fake_artifacts):
    result = tools_mod.get_forecast_summary("not-a-date", artifacts_dir=fake_artifacts)
    assert result["error"] == "bad_input"


def test_evaluation_schema(fake_artifacts):
    result = tools_mod.get_evaluation(artifacts_dir=fake_artifacts)
    assert "error" not in result
    assert "mae_hourly_pow" in result
    assert result["mae_hourly_pow"]["mae505"] == 88.2


def test_module_status_schema(fake_artifacts):
    result = tools_mod.get_module_status(artifacts_dir=fake_artifacts)
    assert "error" not in result
    assert result["count"] == 3
    row = result["modules"][0]
    assert set(("module", "anomaly_count", "anomaly_ratio", "last_seen", "status")) == set(row)
    assert isinstance(row["anomaly_count"], int)
    assert isinstance(row["anomaly_ratio"], float)


def test_anomaly_events_schema_and_filter(fake_artifacts):
    result = tools_mod.get_anomaly_events(module="M0", artifacts_dir=fake_artifacts)
    assert "error" not in result
    assert result["count"] == 1
    event = result["events"][0]
    assert set(("module", "localtime", "type", "value", "normal_range", "detected_by", "score")) == set(event)
    # type filter
    none_result = tools_mod.get_anomaly_events(type="does_not_exist", artifacts_dir=fake_artifacts)
    assert none_result["count"] == 0


def test_anomaly_trend_schema_and_freq(fake_artifacts):
    hourly = tools_mod.get_anomaly_trend(freq="H", artifacts_dir=fake_artifacts)
    assert "error" not in hourly
    assert hourly["freq"] == "H"
    assert hourly["points"]
    daily = tools_mod.get_anomaly_trend(freq="D", module="M0", artifacts_dir=fake_artifacts)
    assert daily["freq"] == "D"
    # M0 both buckets collapse to one day -> single point summed to 11
    assert daily["count"] == 1
    assert daily["points"][0]["count"] == 11


def test_anomaly_trend_bad_freq(fake_artifacts):
    result = tools_mod.get_anomaly_trend(freq="W", artifacts_dir=fake_artifacts)
    assert result["error"] == "bad_input"


def test_missing_artifact_returns_not_found(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = tools_mod.get_forecast_summary(artifacts_dir=empty)
    assert result["error"] == "not_found"
    assert tools_mod.NO_DATA in result["message"]
