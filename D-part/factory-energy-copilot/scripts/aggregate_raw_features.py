"""원본 RTU CSV(5초 측정, 약 4.9GB)를 설비×시간 특징으로 집계한다 (이어서 실행 가능).

출력
  artifacts/module_hourly_features.parquet
      module, hour, n, active_power(W 평균), reactive_power(평균), current_r/s/t(A 평균),
      voltage_r/s/t(V 평균), pf_r/s/t(% 평균), current_unbalance(%), pf_mean(%)
  artifacts/module_minute_recent.parquet   (마지막 7일, 1분 평균: 부하 패턴 '최근 1시간' 보기용)
      module, minute, n, active_power, current_mean

긴 파일을 한 번에 못 돌리는 환경을 위해 바이트 오프셋 체크포인트(.cache/raw_scan)를 두고
--budget 초만큼 처리한 뒤 멈춘다. 같은 명령을 다시 실행하면 이어서 처리하고, 끝나면 결과를 쓴다.

  python scripts/aggregate_raw_features.py --src "../../data/rtu_data_full_훈련용.csv" --budget 150
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import config  # noqa: E402

STATE = ROOT / ".cache" / "raw_scan"
COLS = ["module(equipment)", "localtime", "voltageR", "voltageS", "voltageT", "currentR", "currentS",
        "currentT", "activePower", "powerFactorR", "powerFactorS", "powerFactorT", "reactivePowerLagging"]
SUM_COLS = ["voltageR", "voltageS", "voltageT", "currentR", "currentS", "currentT", "activePower",
            "powerFactorR", "powerFactorS", "powerFactorT", "reactivePowerLagging"]
RECENT_FROM = 20250423000000  # 마지막 7일(4/23–4/29) 1분 집계
CHUNK_BYTES = 64 * 1024 * 1024


def _parse(chunk: bytes, header: list[str]) -> pd.DataFrame:
    """pyarrow CSV 파서(멀티스레드)로 필요한 열만 읽는다. pandas 파서보다 수 배 빠르다."""

    import pyarrow as pa
    from pyarrow import csv as pacsv

    types = {c: pa.float32() for c in SUM_COLS} | {"localtime": pa.int64(), "module(equipment)": pa.string()}
    table = pacsv.read_csv(
        io.BytesIO(chunk),
        read_options=pacsv.ReadOptions(column_names=header, block_size=8 << 20),
        convert_options=pacsv.ConvertOptions(include_columns=COLS, column_types=types),
    )
    return table.to_pandas()


def _aggregate(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    lt = df["localtime"].astype("int64")
    df = df.rename(columns={"module(equipment)": "module"})
    df["hour"] = lt // 10000  # YYYYMMDDHH
    hourly = df.groupby(["module", "hour"])[SUM_COLS].agg(["sum"])
    hourly.columns = [c[0] for c in hourly.columns]
    hourly["n"] = df.groupby(["module", "hour"]).size()
    rec = df[lt >= RECENT_FROM]
    if len(rec):
        rec = rec.assign(minute=(rec["localtime"].astype("int64") // 100),
                         current_mean=rec[["currentR", "currentS", "currentT"]].mean(axis=1))
        minute = rec.groupby(["module", "minute"]).agg(active_power=("activePower", "sum"),
                                                        current_mean=("current_mean", "sum"),
                                                        n=("activePower", "size"))
    else:
        minute = pd.DataFrame()
    return hourly, minute


def _finalize(src: Path) -> None:
    parts = sorted(STATE.glob("h_*.parquet"))
    h = pd.concat([pd.read_parquet(p) for p in parts]).groupby(level=[0, 1]).sum()
    out = pd.DataFrame(index=h.index)
    n = h["n"]
    out["n"] = n
    ren = {"activePower": "active_power", "reactivePowerLagging": "reactive_power",
           "currentR": "current_r", "currentS": "current_s", "currentT": "current_t",
           "voltageR": "voltage_r", "voltageS": "voltage_s", "voltageT": "voltage_t",
           "powerFactorR": "pf_r", "powerFactorS": "pf_s", "powerFactorT": "pf_t"}
    for a, b in ren.items():
        out[b] = h[a] / n
    cur = out[["current_r", "current_s", "current_t"]]
    mean = cur.mean(axis=1)
    # NEMA 방식 전류 불평형률 = 평균에서 가장 먼 상의 편차 / 평균
    out["current_unbalance"] = (cur.sub(mean, axis=0).abs().max(axis=1) / mean.where(mean > 0)) * 100
    out["pf_mean"] = out[["pf_r", "pf_s", "pf_t"]].mean(axis=1)
    out = out.reset_index()
    out["hour"] = pd.to_datetime(out["hour"].astype(str), format="%Y%m%d%H")
    out.to_parquet(config.ARTIFACTS_DIR / "module_hourly_features.parquet", index=False)

    mparts = sorted(STATE.glob("m_*.parquet"))
    if mparts:
        m = pd.concat([pd.read_parquet(p) for p in mparts]).groupby(level=[0, 1]).sum()
        mo = pd.DataFrame({"n": m["n"], "active_power": m["active_power"] / m["n"],
                           "current_mean": m["current_mean"] / m["n"]}).reset_index()
        mo["minute"] = pd.to_datetime(mo["minute"].astype(str), format="%Y%m%d%H%M")
        mo.to_parquet(config.ARTIFACTS_DIR / "module_minute_recent.parquet", index=False)
    meta = {"source": src.name, "rows": int(out["n"].sum()), "modules": int(out["module"].nunique()),
            "hour_start": str(out["hour"].min()), "hour_end": str(out["hour"].max())}
    (config.ARTIFACTS_DIR / "module_hourly_features_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print("DONE", json.dumps(meta, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--budget", type=float, default=150.0)
    args = ap.parse_args()
    src = Path(args.src)
    STATE.mkdir(parents=True, exist_ok=True)
    state_file = STATE / "state.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {"offset": 0, "part": 0, "done": False}
    size = src.stat().st_size
    t0 = time.time()
    with src.open("rb") as fh:
        header = fh.readline().decode("utf-8").strip().split(",")
        if state["offset"] == 0:
            state["offset"] = fh.tell()
        while not state["done"] and time.time() - t0 < args.budget:
            fh.seek(state["offset"])
            buf = fh.read(CHUNK_BYTES)
            if not buf:
                state["done"] = True
                break
            cut = buf.rfind(b"\n") + 1 if len(buf) == CHUNK_BYTES else len(buf)
            chunk = buf[:cut]
            df = _parse(chunk, header)
            h, m = _aggregate(df)
            h.to_parquet(STATE / f"h_{state['part']:04d}.parquet")
            if len(m):
                m.to_parquet(STATE / f"m_{state['part']:04d}.parquet")
            state["offset"] += cut
            state["part"] += 1
            state_file.write_text(json.dumps(state))
            if state["offset"] >= size:
                state["done"] = True
    state_file.write_text(json.dumps(state))
    print(f"progress {state['offset'] / size * 100:.1f}% parts={state['part']} elapsed={time.time() - t0:.0f}s")
    if state["done"]:
        _finalize(src)


if __name__ == "__main__":
    main()
