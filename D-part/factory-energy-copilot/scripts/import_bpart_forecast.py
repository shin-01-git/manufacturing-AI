"""B-part(전력예측) 모델 예측을 대시보드 artifact로 변환한다.

입력: B-part_전력예측/audit_outputs/predictions.csv (id, actual, <모델별 예측 Wh>)
      B-part_전력예측/audit_outputs/model_comparison.csv (model, cv_mae, mae)
출력: artifacts/forecast_bpart.csv  (id, pred_pow, pred_bill, pred_carbon — predictions.csv와 같은 Wh 척도)
      artifacts/forecast_bpart_meta.json (선택 모델·MAE·선택 기준)

실제값(actual) 열은 옮기지 않는다 — 대시보드 예측 그래프는 예측만 보여 준다.

사용:
  python scripts/import_bpart_forecast.py --src "../../B-part_전력예측/audit_outputs"
  python scripts/import_bpart_forecast.py --src <dir> --by cv        # 과거 3회 검증 MAE 기준
  python scripts/import_bpart_forecast.py --src <dir> --model ridge_lags
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import config  # noqa: E402

LABELS = {
    "train_mean": "학습 평균", "train_median": "학습 중앙값", "ridge_lags": "Ridge",
    "lightgbm_lags": "LightGBM 보수 설정", "lightgbm_reference": "LightGBM",
    "xgboost_lags": "XGBoost", "hist_lags": "HistGradientBoosting", "extra_trees_lags": "ExtraTrees",
}
BASELINES = {"train_mean", "train_median"}


def pick_model(comparison: list[dict], by: str, model: str | None) -> dict:
    rows = [r for r in comparison if r["model"] not in BASELINES]
    if model:
        hit = [r for r in comparison if r["model"] == model]
        if not hit:
            raise SystemExit(f"모델 없음: {model}")
        return hit[0]
    key = "mae" if by == "may" else "cv_mae"
    return min(rows, key=lambda r: float(r[key]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="B-part audit_outputs 폴더")
    ap.add_argument("--by", choices=["may", "cv"], default="may")
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", default=str(config.ARTIFACTS_DIR))
    args = ap.parse_args()
    src, out = Path(args.src), Path(args.out)

    with (src / "model_comparison.csv").open(encoding="utf-8-sig") as f:
        comparison = list(csv.DictReader(f))
    chosen = pick_model(comparison, args.by, args.model)
    name = chosen["model"]
    cv_best = min(comparison, key=lambda r: float(r["cv_mae"]))["model"]

    with (src / "predictions.csv").open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if name not in reader.fieldnames:
            raise SystemExit(f"predictions.csv에 {name} 열 없음")
        raw = list(reader)
    rows = [(r["id"], float(r[name])) for r in raw]
    actual = [float(r["actual"]) for r in raw if r.get("actual") not in (None, "")]

    bill_rate, carbon_rate = float(config.BILL_RATE), float(config.CARBON_RATE)
    out.mkdir(parents=True, exist_ok=True)
    # 예측구간: scripts/bpart_interval.py가 만든 과거 검증 잔차 분위수(있을 때만)
    iv_path = out / "forecast_bpart_interval.json"
    iv = json.loads(iv_path.read_text(encoding="utf-8")) if iv_path.exists() else None
    with (out / "forecast_bpart.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "pred_pow", "pred_bill", "pred_carbon", "pred_low", "pred_high"])
        for rid, wh in rows:  # predictions.csv와 같은 Wh 척도(대시보드가 ÷1000)
            lo = f"{max(0.0, wh + iv['q05']):.6f}" if iv else ""
            hi = f"{wh + iv['q95']:.6f}" if iv else ""
            w.writerow([rid, f"{wh:.6f}", f"{wh * bill_rate:.6f}", f"{wh * carbon_rate:.6f}", lo, hi])

    # 평가(evaluation.json과 같은 스키마) — 5월 정답과의 MAE
    def mae(n):
        pairs = list(zip([v for _, v in rows][:n], actual[:n]))
        return sum(abs(p - a) for p, a in pairs) / len(pairs) if pairs else None

    m504, m505 = mae(504), mae(505)
    comparison_rows = [
        {"model": r["model"], "label": LABELS.get(r["model"], r["model"]),
         "kind": "baseline" if r["model"] in BASELINES else "candidate",
         "validation_mae": float(r["cv_mae"]), "may_mae": float(r["mae"])}
        for r in comparison
    ]
    evaluation = {
        "schema_version": 1, "source": "B-part_전력예측", "model": name, "row_count": len(rows),
        "mae_hourly_pow": {"mae504": m504, "mae505": m505},
        "mae_may_bill": {"mae504": m504 * bill_rate if m504 else None, "mae505": m505 * bill_rate if m505 else None},
        "mae_may_carbon": {"mae504": m504 * carbon_rate if m504 else None, "mae505": m505 * carbon_rate if m505 else None},
        "predicted_total_pow": sum(v for _, v in rows),
        "baseline_comparison": comparison_rows,
        "reference_note": {"mae_reference_upper": 89.0, "mae_reference_lower": 73.0,
                           "use_as_threshold": False, "note": "참고용(합격 기준 아님)"},
    }
    (out / "evaluation_bpart.json").write_text(json.dumps(evaluation, ensure_ascii=False, indent=2), encoding="utf-8")
    selection = {
        "schema_version": 1, "source": "B-part_전력예측", "selected_model": name,
        "selected_label": LABELS.get(name, name),
        "selection_score_validation_mae": float(chosen["cv_mae"]),
        "candidate_scores": {r["model"]: float(r["cv_mae"]) for r in comparison if r["model"] not in BASELINES},
        "baseline_comparison": comparison_rows,
        "split_time": "2025-03-31T00:00:00", "validation_scheme": "과거 720시간 3회(1/30–2/28, 3/1–3/30, 3/31–4/29)",
        "refit_scope": "full training span (2024-12-01 – 2025-04-29 23:00)", "refit_row_count": 3600,
    }
    (out / "model_selection_bpart.json").write_text(json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8")
    meta = {
        "model": name,
        "label": LABELS.get(name, name),
        "cv_mae_wh": float(chosen["cv_mae"]),
        "may_mae_wh": float(chosen["mae"]),
        "selected_by": "5월 MAE 최소(기준 모델 제외)" if not args.model and args.by == "may"
        else ("과거 3회 검증 MAE 최소(기준 모델 제외)" if not args.model else "수동 지정"),
        "cv_best_model": cv_best,
        "cv_best_label": LABELS.get(cv_best, cv_best),
        "method": "재귀 예측(4/29 23시까지의 실측만 사용, 이후 자기 예측값을 lag로 사용)",
        "train_end": "2025-04-29 23:00",
        "points": len(rows),
        "mae504_wh": m504, "mae505_wh": m505,
        "interval": {"q05": iv["q05"], "q95": iv["q95"], "n": iv["n"]} if iv else None,
        "start": rows[0][0] if rows else None,
        "end": rows[-1][0] if rows else None,
        "source": "B-part_전력예측/audit_outputs",
    }
    (out / "forecast_bpart_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
