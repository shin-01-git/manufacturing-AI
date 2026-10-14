from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "forecast_outputs"
SUMMARY_PATH = OUT_DIR / "summary.json"
FORECAST_PATH = OUT_DIR / "future_forecasts.csv"
COMPARISON_PATH = OUT_DIR / "model_comparison.csv"

st.set_page_config(page_title="RTU Energy Forecast", layout="wide")
st.title("RTU 에너지 전망")
st.caption("선택된 모델의 시간별 전력량을 전기요금과 탄소 배출 추정치로 환산합니다.")

if not SUMMARY_PATH.exists() or not FORECAST_PATH.exists():
    st.error("예측 결과가 없습니다. 먼저 `py rtu_hourly_forecast.py`를 실행하세요.")
    st.stop()

summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
forecast_df = pd.read_csv(FORECAST_PATH, parse_dates=["hour"])
comparison_df = pd.read_csv(COMPARISON_PATH) if COMPARISON_PATH.exists() else pd.DataFrame()
model_name = summary["best_model"]
mae = summary.get("may_holdout_metrics", {})
unit_info = summary.get("unit_assumptions", {})
quality = summary.get("data_quality", {})

st.subheader(f"선정 모델 · {model_name}")
selected = comparison_df[comparison_df["model"] == model_name] if not comparison_df.empty else pd.DataFrame()
cv_mae = float(selected["CV_MAE_hourly_pow"].iloc[0]) if not selected.empty else None
header_metrics = st.columns(4)
header_metrics[0].metric("5월 validation 전력 MAE", f"{mae.get('MAE_hourly_pow', 0):,.1f} Wh")
header_metrics[1].metric("5월 validation 요금 MAE", f"{mae.get('MAE_bill_krw', 0):,.2f}원")
header_metrics[2].metric("5월 validation 탄소 MAE", f"{mae.get('MAE_carbon_kg', 0):,.3f} kg")
header_metrics[3].metric("순차 검증 평균 MAE", f"{cv_mae:,.1f} Wh" if cv_mae is not None else "계산되지 않음")
st.caption("예측 기준 시각: " + str(summary.get("train_range", {}).get("end", "확인 불가")) + " · 이 데이터는 2025년 4월 말까지라 화면의 '다음'은 해당 기준 시각 이후의 과거 시뮬레이션입니다.")

horizon_tabs = st.tabs(["다음 24시간", "다음 7일", "다음 달"])
horizons = [
    ("다음 24시간", forecast_df["horizon_day"].astype(bool)),
    ("다음 7일", forecast_df["horizon_week"].astype(bool)),
    ("다음 달", forecast_df["horizon_month"].astype(bool)),
]

for tab, (label, mask) in zip(horizon_tabs, horizons):
    with tab:
        horizon = forecast_df.loc[mask].copy().sort_values("hour")
        if horizon.empty:
            st.warning(f"{label} 구간이 결과 파일에 없습니다. 학습 스크립트를 다시 실행하세요.")
            continue
        st.caption(f"기간: {horizon['hour'].min():%Y-%m-%d %H:%M} ~ {horizon['hour'].max():%Y-%m-%d %H:%M}")

        total_kwh = horizon["pred_hourly_pow"].sum() / 1000.0
        total_bill = horizon["pred_bill_krw"].sum()
        total_carbon = horizon["pred_carbon_kg"].sum()
        forecast_range_wh = horizon["pred_hourly_pow"].max() - horizon["pred_hourly_pow"].min()
        kpis = st.columns(4)
        kpis[0].metric("예상 사용량", f"{total_kwh:,.1f} kWh")
        kpis[1].metric("예상 전기요금", f"{total_bill:,.0f}원", help="고정 단가 180원/kWh로 계산한 단순 추정치입니다.")
        kpis[2].metric("예상 탄소 배출", f"{total_carbon:,.1f} kg CO₂", help="고정 배출계수 0.424 kg CO₂/kWh를 적용한 추정치입니다.")
        kpis[3].metric("시간별 예측 변동폭", f"{forecast_range_wh:,.1f} Wh")

        power_fig, power_ax = plt.subplots(figsize=(12, 3.5))
        power_kwh = horizon["pred_hourly_pow"] / 1000.0
        power_ax.plot(horizon["hour"], power_kwh, color="#167C80", linewidth=1.8)
        power_ax.set_ylim(power_kwh.min() - 0.05, power_kwh.max() + 0.05)
        power_ax.set_title(f"{label} · 시간별 예상 사용량")
        power_ax.set_ylabel("kWh / hour")
        power_ax.grid(axis="y", alpha=0.22)
        power_fig.tight_layout()
        st.pyplot(power_fig, use_container_width=True)
        plt.close(power_fig)
        st.caption("절대 사용량 그래프의 세로축은 변동을 볼 수 있도록 실제 예측 범위에 맞춰 확대했습니다.")

        deviation_wh = horizon["pred_hourly_pow"] - horizon["pred_hourly_pow"].mean()
        deviation_limit = max(float(deviation_wh.abs().max()) * 1.2, 1.0)
        deviation_fig, deviation_ax = plt.subplots(figsize=(12, 3.2))
        deviation_ax.axhline(0, color="#53615D", linewidth=1)
        deviation_ax.fill_between(
            horizon["hour"], deviation_wh, 0,
            where=deviation_wh >= 0, color="#168C83", alpha=0.28,
        )
        deviation_ax.fill_between(
            horizon["hour"], deviation_wh, 0,
            where=deviation_wh < 0, color="#D17A36", alpha=0.28,
        )
        deviation_ax.plot(horizon["hour"], deviation_wh, color="#354541", linewidth=1.2)
        deviation_ax.set_ylim(-deviation_limit, deviation_limit)
        deviation_ax.set_title(f"{label} · 기간 평균 대비 시간별 증감")
        deviation_ax.set_ylabel("증감 (Wh)")
        deviation_ax.grid(axis="y", alpha=0.22)
        deviation_fig.tight_layout()
        st.pyplot(deviation_fig, use_container_width=True)
        plt.close(deviation_fig)
        st.caption("0선은 해당 예측 구간의 평균입니다. 초록은 평균 초과, 주황은 평균 미달이며, 변동을 읽기 위한 편차 그래프입니다.")

        st.caption("전기요금과 탄소량은 현재 고정 계수로 전력량에 비례해 별도 추이 그래프는 생략했습니다. 시간별 값은 아래 표, 기간 합계는 위 지표에서 확인할 수 있습니다.")

        st.markdown("**탄소 영향**")
        tonnes = total_carbon / 1000.0
        st.metric("구간 누적 CO₂", f"{total_carbon:,.1f} kg", delta=f"{tonnes:,.3f} t", delta_color="off")
        st.write("CO₂는 대기 중 열을 가두는 온실가스로, 배출이 누적되면 지구 온난화와 기후 변화에 기여합니다. 표시한 값은 고정 배출계수에 따른 추정량이며 법정 기준이나 안전 한계가 아닙니다.")

        detail = horizon[["hour", "pred_hourly_pow", "pred_bill_krw", "pred_carbon_kg"]].copy()
        detail.columns = ["시간", "예상 전력량 (Wh)", "예상 요금 (원)", "예상 탄소 (kg CO₂)"]
        st.dataframe(detail, width="stretch", hide_index=True)

st.subheader("모델 선정 근거")
if cv_mae is not None:
    cv_leader = summary.get("cv_best_model", "확인 불가")
    st.write(f"{model_name}은 요청에 따라 5월 validation 전력 MAE가 가장 낮아 선택했습니다. 해당 MAE는 {mae.get('MAE_hourly_pow', 0):,.2f} Wh입니다. 과거 구간의 3회 순차 검증 1위는 {cv_leader}였으므로, May 결과와 순차 검증 결과가 일치하지 않습니다.")
    st.warning("5월 데이터를 모델 선택에도 사용했으므로, 5월 MAE는 완전히 독립적인 최종 테스트 점수가 아닙니다. 일반화 성능 보고서에는 별도 기간을 최종 test로 남겨야 합니다.")
    st.caption("전체 후보별 May MAE 그래프: `forecast_outputs/analysis/model_comparison.png`")
else:
    st.write("모델별 선정 근거를 찾을 수 없습니다. 예측 스크립트를 다시 실행하세요.")

with st.expander("계산 및 데이터 주의점"):
    st.write("현재 전처리는 각 5초 샘플의 activePower(W)에 5/3600을 곱해 Wh로 바꾸고, 장비별 기록을 합산합니다. 적용 단위는 activePower=W, 예측값=Wh라는 가정입니다.")
    st.write(f"요금은 {unit_info.get('electricity_rate_won_per_kwh', 180)}원/kWh, 탄소는 {unit_info.get('carbon_factor_kg_per_kwh', 0.424)}kg CO₂/kWh의 고정 계수로 산출합니다. 실제 누진·시간대별 요금, 부가요금 및 실제 배출계수는 반영하지 않습니다.")
    bill_ratio = unit_info.get("truth_may_bill_to_calculated_bill_median_ratio", 1)
    carbon_ratio = unit_info.get("truth_may_carbon_to_calculated_carbon_median_ratio", 1)
    if bill_ratio > 10 or carbon_ratio > 10:
        st.warning(f"Truth 파일의 요금/탄소 열은 코드의 Wh→kWh 환산값보다 각각 약 {bill_ratio:,.0f}배, {carbon_ratio:,.0f}배 큽니다. 전력 MAE를 우선 신뢰하고, 금액·탄소의 truth 비교 및 절대값 해석은 단위 확인 후 사용하세요.")
    st.write(f"장비 {quality.get('equipment_count', 'N/A')}개의 activePower를 모두 합산합니다. 장비 간 계층 관계나 계량 범위가 겹치면 중복 집계될 수 있습니다. 저커버리지 시간은 {quality.get('low_coverage_hours', 'N/A')}개로 기록되었습니다.")
    st.write("7일 및 다음 달은 재귀 예측입니다. 먼 미래로 갈수록 모델 예측값이 lag 입력으로 다시 들어가 오차가 누적될 수 있습니다. 다음 달 구간은 마지막 관측 날짜 다음 달의 달력 전체로 정의했습니다. 이 화면은 실측 보증치가 아닌 계획 참고치입니다.")

st.caption("예측 실행: `py rtu_hourly_forecast.py` · 이 대시보드는 저장된 학습/검증 결과를 표시합니다.")
