"""설비×시간 특징(module_hourly_features.parquet)에서 시간 급변 5종을 탐지해 artifact로 저장한다.

판정(10/16 멘토 피드백 반영 — 거짓 양성 축소): 두 조건을 모두 만족하는 시간만 이상.
  ① 범위 이탈: 그 설비 전체 기간 평균에서 ±kσ 밖
  ② 급격한 변화: 직전 시간 대비 변화량이 ±kσ 밖
야간 이상가동은 22–06시에 (k−0.5)σ. (전류만 증가는 10/17 팀 기준표에서 제외)
대시보드는 같은 함수(page_logic.detect_sudden)로 설정의 민감도 k에 맞춰 다시 계산한다.

  python scripts/detect_pattern_anomalies.py [--k 3.5]
출력: artifacts/pattern_anomalies.parquet, pattern_anomalies_meta.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import config  # noqa: E402
from src.app import page_logic  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=float, default=config.DEFAULT_ANOMALY_K)
    args = ap.parse_args()
    f = pd.read_parquet(config.ARTIFACTS_DIR / "module_hourly_features.parquet")
    res = page_logic.detect_sudden(f, k=args.k)
    cols = ["module", "hour", "type", "metric", "value", "baseline", "z", "jump_z", "unit", "rule"]
    out = pd.DataFrame(res["events"], columns=cols)
    out["hour"] = pd.to_datetime(out["hour"])
    out.to_parquet(config.ARTIFACTS_DIR / "pattern_anomalies.parquet", index=False)
    meta = {"k": args.k, "hours_evaluated": int((f["n"] >= 600).sum()), "count": len(out),
            "counts": dict(Counter(out["type"])),
            "rule": "전체 평균 ±kσ 밖 + 직전 시간 대비 ±kσ 급변 (두 조건 모두)"}
    (config.ARTIFACTS_DIR / "pattern_anomalies_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
