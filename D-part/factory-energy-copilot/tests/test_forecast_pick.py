"""예측 그래프 날짜/주 선택과 B-part 예측 변환 테스트."""

import csv
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from src.app import page_logic

ROOT = Path(__file__).resolve().parents[1]


def _rows(start="2025-05-10 00:00:00", hours=505):
    t0 = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
    return [{"id": (t0 + timedelta(hours=h)).strftime("%Y-%m-%d %H:%M:%S"), "pred_pow": 39.0 + h * 1e-4}
            for h in range(hours)]


def test_day_options_drop_single_hour_tail():
    opts = page_logic.period_options(_rows(), "일")
    assert len(opts) == 21  # 5/31 00시 1개는 제외
    assert opts[0]["label"].startswith("05-10 (토)")
    assert all(not o["partial"] for o in opts)


def test_week_options_partial_and_rows():
    opts = page_logic.period_options(_rows(), "주")
    assert [o["n"] for o in opts] == [48, 168, 168, 121]
    assert opts[0]["partial"] and not opts[1]["partial"]
    sel = page_logic.rows_in_period(_rows(), "주", opts[1]["key"])
    assert len(sel) == 168 and sel[0]["id"].startswith("2025-05-12 00")


def test_options_respect_date_filter_and_units():
    opts = page_logic.period_options(_rows(), "일", start="2025-05-12", end="2025-05-13")
    assert [o["key"] for o in opts] == ["2025-05-12", "2025-05-13"]
    assert page_logic.period_options(_rows(), "시간") == []


def test_import_bpart_picks_lowest_may_mae(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "model_comparison.csv").write_text(
        "model,cv_mae,mae\ntrain_mean,70,90\nridge_lags,75,88\nlightgbm_reference,78,84\n", encoding="utf-8")
    with (src / "predictions.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "actual", "train_mean", "ridge_lags", "lightgbm_reference"])
        w.writerow(["2025-05-10 00:00:00", 1, 2, 3, 1000])
    out = tmp_path / "out"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "import_bpart_forecast.py"), "--src", str(src),
                    "--out", str(out)], check=True, cwd=ROOT, capture_output=True)
    meta = json.loads((out / "forecast_bpart_meta.json").read_text(encoding="utf-8"))
    assert meta["model"] == "lightgbm_reference" and meta["cv_best_model"] == "train_mean"
    row = next(csv.DictReader((out / "forecast_bpart.csv").open(encoding="utf-8")))
    assert "actual" not in row
    assert float(row["pred_pow"]) == 1000 and abs(float(row["pred_bill"]) - 180000) < 1e-6


def test_period_bounds():
    from datetime import date
    assert page_logic.period_bounds("일", "2025-05-12") == (date(2025, 5, 12), date(2025, 5, 12))
    assert page_logic.period_bounds("주", "2025-05-12") == (date(2025, 5, 12), date(2025, 5, 18))


def test_anomaly_expected_weekday_hour_mean():
    import pandas as pd
    # 과거 2주(월요일 2번): 월 03시에 4건, 2건 → 평균 3건. 다른 시각은 0건.
    frame = pd.DataFrame({
        "hour": pd.to_datetime(["2025-04-07 03:00", "2025-04-14 03:00", "2025-04-20 23:00"]),
        "module": ["a", "a", "a"], "type": ["t", "t", "t"], "count": [4, 2, 0],
    })
    rows = [{"id": "2025-05-12 03:00:00"}, {"id": "2025-05-12 04:00:00"}]
    out = page_logic.anomaly_expected(frame, rows)
    assert out["expected"] == 3.0
    assert page_logic.anomaly_expected(None, rows) is None


def test_event_log_module_filter():
    evs = [
        {"module": "17", "localtime": "2025-04-01T00:00:00+09:00"},
        {"module": "17", "localtime": "2025-04-03T00:00:00+09:00"},
        {"module": "13", "localtime": "2025-04-02T00:00:00+09:00"},
    ]
    assert page_logic.event_counts_by_module(evs) == [("17", 2), ("13", 1)]
    out = page_logic.events_for_module(evs, "17")
    assert [e["localtime"][:10] for e in out] == ["2025-04-03", "2025-04-01"]
    assert len(page_logic.events_for_module(evs, None, limit=2)) == 2


def test_anomaly_reasons_basis():
    evs = [{"module": "13", "type": "voltage_low_3phase", "value": v,
            "localtime": f"2025-0{m}-01T1{h}:00:00+09:00"} for v, m, h in ((200, 3, 0), (204, 4, 1))]
    out = page_logic.anomaly_reasons(evs, [{"module": "13", "anomaly_ratio": 0.005}])
    r = out[0]
    assert r["threshold"] == 210.0 and r["median"] == 202.0 and r["gap"] == 8.0
    assert r["months"] == [("2025-03", 1), ("2025-04", 1)] and r["ratio"] == 0.005
    assert page_logic.anomaly_reasons([], []) == []


def test_alert_titles_use_feedback_types():
    trend = {"points": [{"bucket": f"b{i}", "module": "17", "type": "power_factor_low_3phase",
                         "count": c} for i, c in enumerate([1, 1, 1, 9])]}
    a = page_logic.build_alerts({"error": {}}, {"error": {}}, trend, page_logic.merged_settings(None))
    titles = [it["title"] for it in a["items"]]
    assert titles == ["역률 급감 · 17"]
