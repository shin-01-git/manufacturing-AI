# ============================================================
# D 파트 - 원시 데이터 전처리/집계 (청크 방식) [보강판]
#
# 멘토 제시 주요 컬럼을 모두 반영:
#   activePower, accumActiveEnergy, operation, localtime, 전압/전류 R·S·T
#
# [지키는 원칙]
#   - 원본은 '읽기만' 한다. 절대 수정하지 않는다.
#   - 데이터를 임의로 생성하지 않는다. 있는 값 그대로 집계만 한다.
#   - 전체를 빠짐없이 처리한다 (청크로 끝까지).
#   - 진행 상황을 로그 파일에 실시간으로 남긴다.
#   - 이상치가 묻히지 않도록 평균 + min/max 를 함께 저장한다.
#
# [핵심 공식] kWh(5초 1건) = activePower / 720  (= activePower × 5/3600)
# ============================================================

import pandas as pd
import os
from datetime import datetime

SRC = r"C:\Users\82104\Desktop\DONG-EUI UNIVERSITY\traing-dataset\rtu_data_full.csv"
OUT = r"C:\Users\82104\Desktop\DONG-EUI UNIVERSITY\d_aggregated.csv"
LOG = r"C:\Users\82104\Desktop\DONG-EUI UNIVERSITY\d_preprocess_log.txt"

CHUNK = 1_000_000
KWH_DIVISOR = 720   # activePower / 720 = kWh

# 읽어올 컬럼 (멘토 제시 반영)
USECOLS = [
    "module(equipment)", "localtime", "operation", "activePower",
    "accumActiveEnergy",
    "voltageR", "voltageS", "voltageT",
    "currentR", "currentS", "currentT",
    "powerFactorR",
]


def log(msg):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    with open(LOG, "w", encoding="utf-8") as f:
        f.write(f"=== 전처리(보강판) 시작 {datetime.now():%Y-%m-%d %H:%M:%S} ===\n")

    if not os.path.exists(SRC):
        log(f"[오류] 원본 파일 없음: {SRC}")
        return

    size_gb = os.path.getsize(SRC) / (1024**3)
    log(f"원본: {SRC}  ({size_gb:.2f} GB)")
    log(f"청크 {CHUNK:,}줄 / kWh=÷{KWH_DIVISOR} / 컬럼 {len(USECOLS)}개")

    partials = []
    total_rows = 0
    chunk_no = 0

    reader = pd.read_csv(SRC, usecols=USECOLS, chunksize=CHUNK)

    for chunk in reader:
        chunk_no += 1
        total_rows += len(chunk)

        # 시간 키: localtime(YYYYMMDDhhmmss) 앞 10자리 = YYYYMMDDhh
        hour_key = chunk["localtime"].astype("int64").astype(str).str.zfill(14).str[:10]
        chunk = chunk.assign(hour=hour_key)
        # kWh 환산
        chunk = chunk.assign(kwh=chunk["activePower"] / KWH_DIVISOR)

        g = chunk.groupby(["hour", "module(equipment)"]).agg(
            kwh_sum=("kwh", "sum"),
            ap_mean=("activePower", "mean"),
            ap_min=("activePower", "min"),
            ap_max=("activePower", "max"),
            # 가동 여부
            op_sum=("operation", "sum"),          # 가동(1) 건수
            # 누적 전력량 (시작/끝)
            energy_first=("accumActiveEnergy", "first"),
            energy_last=("accumActiveEnergy", "last"),
            # 전압 R/S/T
            vR_mean=("voltageR", "mean"), vR_min=("voltageR", "min"), vR_max=("voltageR", "max"),
            vS_mean=("voltageS", "mean"), vS_min=("voltageS", "min"), vS_max=("voltageS", "max"),
            vT_mean=("voltageT", "mean"), vT_min=("voltageT", "min"), vT_max=("voltageT", "max"),
            # 전류 R/S/T
            cR_mean=("currentR", "mean"), cR_min=("currentR", "min"), cR_max=("currentR", "max"),
            cS_mean=("currentS", "mean"), cS_min=("currentS", "min"), cS_max=("currentS", "max"),
            cT_mean=("currentT", "mean"), cT_min=("currentT", "min"), cT_max=("currentT", "max"),
            # 역률
            pf_mean=("powerFactorR", "mean"), pf_min=("powerFactorR", "min"),
            n=("kwh", "size"),
        )
        partials.append(g)
        log(f"조각 {chunk_no} 완료 | 누적 {total_rows:,} 줄")

    log(f"모든 조각 읽기 완료. 총 {total_rows:,} 줄. 조각 {chunk_no}개 합치는 중...")

    combined = pd.concat(partials)
    # min 계열은 min, max 계열은 max, 나머지는 평균/합계로 재집계
    final = combined.groupby(level=["hour", "module(equipment)"]).agg(
        kwh_sum=("kwh_sum", "sum"),
        ap_mean=("ap_mean", "mean"), ap_min=("ap_min", "min"), ap_max=("ap_max", "max"),
        op_sum=("op_sum", "sum"),
        energy_first=("energy_first", "first"), energy_last=("energy_last", "last"),
        vR_mean=("vR_mean", "mean"), vR_min=("vR_min", "min"), vR_max=("vR_max", "max"),
        vS_mean=("vS_mean", "mean"), vS_min=("vS_min", "min"), vS_max=("vS_max", "max"),
        vT_mean=("vT_mean", "mean"), vT_min=("vT_min", "min"), vT_max=("vT_max", "max"),
        cR_mean=("cR_mean", "mean"), cR_min=("cR_min", "min"), cR_max=("cR_max", "max"),
        cS_mean=("cS_mean", "mean"), cS_min=("cS_min", "min"), cS_max=("cS_max", "max"),
        cT_mean=("cT_mean", "mean"), cT_min=("cT_min", "min"), cT_max=("cT_max", "max"),
        pf_mean=("pf_mean", "mean"), pf_min=("pf_min", "min"),
        n=("n", "sum"),
    ).reset_index()

    # 가동 비율 (op_sum / n)
    final["op_ratio"] = (final["op_sum"] / final["n"]).round(4)
    # 요금/탄소
    final["bill"] = final["kwh_sum"] * 180
    final["carbon"] = final["kwh_sum"] * 0.424

    final.to_csv(OUT, index=False, encoding="utf-8-sig")

    n_total = int(final["n"].sum())
    log(f"집계 완료! 결과 {len(final):,}행, 컬럼 {len(final.columns)}개")
    log(f"검증 - 처리 줄 {total_rows:,} / n합계 {n_total:,} -> 일치: {total_rows == n_total}")
    log(f"저장: {OUT}")
    log(f"=== 전처리 끝 {datetime.now():%Y-%m-%d %H:%M:%S} ===")


if __name__ == "__main__":
    main()
