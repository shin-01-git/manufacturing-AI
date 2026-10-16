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
    a = page_logic.build_alerts({"error": {}}, {"error": {}}, trend, page_logic.merged_settings(None),
                                include_surge=True)
    assert [it["title"] for it in a["items"]] == ["역률 급감 · 17"]
    # 10/16 피드백: 5초 기준 미달 급증은 거짓 양성이 많아 기본 경고 목록에서 제외
    b = page_logic.build_alerts({"error": {}}, {"error": {}}, trend, page_logic.merged_settings(None))
    assert b["items"] == []


def test_settings_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    s = page_logic.default_settings()
    s["bill_rate"] = 200.0
    assert page_logic.save_settings(s, p)
    out = page_logic.load_saved_settings(p)
    assert out["bill_rate"] == 200.0
    p.write_text('{"bill_rate": [1], "unknown": 3}', encoding="utf-8")
    assert page_logic.load_saved_settings(p) is None


def test_module_status_label_thresholds():
    from src.agent import tools
    # 상태 = 급변 탐지 건수만 (5초 기준 미달 비율은 참고)
    assert tools.module_status_label(0.5, 0) == "정상"
    assert tools.module_status_label(0.0, 1) == "정상"
    assert tools.module_status_label(0.0, 2) == "주의"
    assert tools.module_status_label(0.0, 4) == "이상"


def test_load_series_daily_and_recent():
    import pandas as pd
    hours = pd.date_range("2025-04-28 00:00", periods=48, freq="h")
    ff = pd.DataFrame({"module": "A", "hour": hours, "n": 720, "active_power": 3000.0})
    b = {"features_hourly": {"frame": ff}}
    d = page_logic.load_series(b, "A", "일별")
    assert d["y"] == [72.0, 72.0]
    r = page_logic.load_series(b, "A", "최근 24시간")
    assert len(r["y"]) == 24 and r["y"][0] == 3.0
    assert page_logic.load_series(b, "A", "최근 1시간") is None


def test_window_rows_crosses_midnight():
    from datetime import datetime
    w = page_logic.window_rows(_rows(), datetime(2025, 5, 10, 18), 24)
    assert len(w) == 24 and w[0]["id"].startswith("2025-05-10 18") and w[-1]["id"].startswith("2025-05-11 17")


def test_status_for_period_matches_filtered_events():
    import pandas as pd
    status = {"modules": [{"module": "A", "anomaly_count": 4, "anomaly_ratio": 0.4, "last_seen": "", "status": "미확정"},
                          {"module": "B", "anomaly_count": 0, "anomaly_ratio": 0.0, "last_seen": "", "status": "미확정"}]}
    events = {"count": 1, "events": [{"module": "A", "localtime": "2025-04-02T01:00:00+09:00"}]}
    feats = pd.DataFrame({"module": ["A", "A", "B"], "n": [100, 100, 100],
                          "hour": pd.to_datetime(["2025-04-01 00:00", "2025-04-02 00:00", "2025-04-02 00:00"])})
    out = page_logic.status_for_period(status, events, feats, None, "2025-04-02", "2025-04-02")
    a, b = out["modules"]
    assert a["anomaly_count"] == 1 and a["anomaly_ratio"] == 0.01
    assert b["anomaly_count"] == 0 and b["status"] == "정상"



def test_detect_sudden_needs_level_and_jump():
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(0)
    n = 400
    base = 3000 + rng.normal(0, 10, n)
    base[200] = 3200  # 범위 이탈 + 급변
    base[300:310] = 3060  # 수준은 높지만 서서히(첫 시간만 급변)
    hours = pd.date_range("2025-01-01", periods=n, freq="h")
    f = pd.DataFrame({"module": "A", "hour": hours, "n": 720, "active_power": base,
                      "reactive_power": 600 + rng.normal(0, 5, n), "current_unbalance": 1.4 + rng.normal(0, .1, n),
                      "pf_mean": 92 + rng.normal(0, .3, n), "current_r": 15.0, "current_s": 15.0, "current_t": 15.0})
    out = page_logic.detect_sudden(f, k=3.5)
    hits = [e["hour"] for e in out["events"] if e["type"] == "abnormal_pattern"]
    assert "2025-01-09 08:00:00" in hits
    assert not any(h.startswith("2025-01-13 1") for h in hits[1:]) or len(hits) <= 3


def test_agent_answers_by_intent():
    from src.agent import answers
    assert answers.intent("전력량 예측 MAE 참고값은?") == "accuracy"
    assert answers.intent("요금이 가장 높은 예측 시간대는?") == "peak"
    assert answers.intent("야간에 전력을 많이 쓰는 설비는?") == "night"
    assert answers.parse_dates("5월 15일 요금") == ("2025-05-15", "2025-05-15")
    assert answers.parse_dates("5월 12일 주 전력량") == ("2025-05-12", "2025-05-18")
    assert answers.parse_dates("5/10~5/12 합계") == ("2025-05-10", "2025-05-12")


def test_agent_answers_more_intents():
    from src.agent import answers
    mods = ["13(3호기)", "17(6호기)", "3(분쇄기(2))", "4(분쇄기(1))"]
    assert answers.find_modules("6호기 왜 이상이야?", mods) == ["17(6호기)"]
    assert answers.find_modules("17호기 상태", mods) == ["17(6호기)"]
    assert set(answers.find_modules("분쇄기 상태", mods)) == {"3(분쇄기(2))", "4(분쇄기(1))"}
    assert answers.parse_dates("마지막 주 탄소량") == ("2025-05-26", "2025-05-31")
    assert answers.intent("부하 불균형이 뭐야?") == "define"
    assert answers.intent("전기요금 줄이려면?") == "advice"
    assert answers.intent("부하 패턴 요약해 줘") == "pattern"
    assert answers.intent("오늘 날씨 어때?") == "briefing"
