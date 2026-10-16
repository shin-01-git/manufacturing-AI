"""B-part LightGBM(lightgbm_reference)의 예측구간(P5–P95)을 과거 3회 검증 잔차로 만든다.

B-part power_training.py와 같은 특징·모델·재귀 예측을 그대로 다시 실행한다.
검증 구간 = 학습 끝에서 60/30/0일 전 기준 720시간 3개(5월 데이터 사용 안 함).
잔차(실제−예측) 2,160개의 5·95 분위수를 예측값에 더해 구간을 만든다.

  python scripts/bpart_interval.py --hourly <audit_outputs/hourly_processed.csv>
출력: artifacts/forecast_bpart_interval.json  {q05, q95, n, folds, model}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import config  # noqa: E402

LAGS, WINDOWS = (1, 24, 168), (24, 168)


def calendar(dates) -> np.ndarray:
    d = pd.DatetimeIndex(dates)
    return np.column_stack([d.hour, d.dayofweek, (d.dayofweek >= 5).astype(int), d.month, d.day,
                            np.sin(d.hour * 2 * np.pi / 24), np.cos(d.hour * 2 * np.pi / 24),
                            np.sin(d.dayofweek * 2 * np.pi / 7), np.cos(d.dayofweek * 2 * np.pi / 7)])


def features(s: pd.Series) -> pd.DataFrame:
    f = pd.DataFrame(calendar(s.index), index=s.index)
    for k in LAGS:
        f[f"lag_{k}"] = s.shift(k)
    for k in WINDOWS:
        f[f"mean_{k}"] = s.shift(1).rolling(k).mean()
    return f


def train(s: pd.Series):
    from lightgbm import LGBMRegressor

    f = features(s)
    ok = f.notna().all(axis=1)
    m = LGBMRegressor(objective="regression_l1", n_estimators=600, learning_rate=.05, num_leaves=31,
                      subsample=.9, colsample_bytree=.9, n_jobs=2, random_state=42, verbosity=-1)
    m.fit(f.loc[ok].to_numpy(), s.loc[ok].to_numpy())
    return m


def forecast(s: pd.Series, dates, m) -> np.ndarray:
    a = list(s.to_numpy(dtype=float))
    out = []
    for row in calendar(dates):
        x = np.r_[row, [a[-k] for k in LAGS] + [np.mean(a[-k:]) for k in WINDOWS]].reshape(1, -1)
        v = max(0.0, float(m.booster_.predict(x, num_threads=1)[0]))
        a.append(v)
        out.append(v)
    return np.array(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hourly", required=True)
    args = ap.parse_args()
    h = pd.read_csv(args.hourly, index_col=0, parse_dates=True)
    s = h["energy_Wh"].dropna()
    res, folds = [], []
    for back in (60, 30, 0):
        end = s.index.max() - pd.Timedelta(days=back)
        start = end - pd.Timedelta(hours=719)
        a, b = s.loc[s.index < start], s.loc[start:end]
        p = forecast(a, b.index, train(a))
        r = b.to_numpy() - p
        res.append(r)
        folds.append({"valid_start": str(b.index.min()), "valid_end": str(b.index.max()),
                      "mae_wh": float(np.abs(r).mean())})
    r = np.concatenate(res)
    out = {"model": "lightgbm_reference", "q05": float(np.quantile(r, .05)), "q95": float(np.quantile(r, .95)),
           "n": int(len(r)), "folds": folds,
           "coverage_note": "과거 3회 검증 잔차 분위수 — 5월 데이터 미사용"}
    (config.ARTIFACTS_DIR / "forecast_bpart_interval.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
