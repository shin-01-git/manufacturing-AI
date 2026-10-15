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
