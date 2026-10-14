# RTU 전력 예측: 데이터, 검증 및 대시보드

## 목표와 현재 데이터 범위

이 작업은 RTU 센서의 5초 샘플을 시간별 전력량으로 집계하고, 미래 시간별 전력량을 예측한 뒤 고정 단가와 배출계수로 요금·탄소를 추정한다.

- 원본 `rtu_data_full.csv`: 33,696,013행, 19개 컬럼, 13개 equipment, 2024-12-01부터 2025-04-30까지
- `rtu_ground_truth_may.csv`: 2025-05-10 00시부터 2025-05-31 00시까지 505개 시간 관측
- full 데이터 점검에서 결측값은 없었고 중복 equipment-localtime은 집계 과정에서 확인되지 않았다.
- 13개 equipment는 각각 2,592,001개 샘플이며, 전 구간 `operation=1`이다. 각 채널의 평균 `activePower`는 약 3.01 kW로 비슷하고, 합산 평균은 약 39.13 kW다.
- May truth의 `hourly_pow` 평균은 약 39.07 kWh/시간으로, 전체 설비를 합산한 규모와 잘 맞는다. 프로젝트 목표는 공장 전체 부하로 두고 13개 채널을 합산한다.
- 5초 샘플로부터 시간별 집계 시 마지막 경계 시간은 샘플이 부족해 제외하고, 학습에 사용한 완전한 시간 행은 3,600개다.

현재 원본은 2025년 4월 말에 끝난다. 따라서 대시보드의 다음 날/주/달은 **마지막 관측 시점 이후에 대한 역사적 시뮬레이션**이다. 2026년 현재의 실제 내일을 예측하는 기능은 최신 관측 데이터가 추가되기 전에는 성립하지 않는다.

## 데이터 해석과 전처리

### 현재 코드가 쓰는 컬럼

- `localtime`: 시간 버킷 생성과 정렬에 사용한다. `timestamp`의 epoch 시간 대신 사용한다.
- `activePower`: 5초 동안의 유효전력 샘플을 시간 에너지로 환산하는 데 사용한다.
- `module(equipment)`: 샘플 수 기대치와 장비 수 점검에 쓰며, 현재는 13개 장비를 합산한 전체 부하를 대상으로 한다.
- `operation`: 파일에서 읽지만 현재 필터에는 사용하지 않는다.
- `accumActiveEnergy`, 전압, 전류, 역률, 무효전력: 현재 모델 feature에 넣지 않는다. 단위/초기화/예측 시점의 가용성을 더 확인한 뒤 추가해야 한다.

### 집계 방식

현재 단위 가정은 `activePower`가 W이며 각 레코드가 5초 간격이라는 것이다. 레코드별 에너지는 아래와 같이 계산하고 장비·시간별로 합산한다.

`energy_wh = activePower_w × 5 / 3600`

장비마다 시간당 기대 레코드 720개를 기준으로 coverage를 계산한다. 95% 미만 coverage인 시간은 유효한 `hourly_pow`로 취급하지 않으며, 모델 학습에서 제외된다. 각 장비의 시각 중복도 제거 대상으로 점검한다.

이 방식은 `activePower`의 실제 단위가 W이고 5초 값이 해당 구간을 대표한다는 가정이 맞을 때 타당하다. 사용 목적은 사용자가 확인한 공장 13개 설비 전체 합계다. 원본 이름 중 분전반으로 표시된 항목이 있으므로 장비 목록만으로 meter topology를 증명할 수는 없다. 물리적 계량 범위가 겹치는지는 전기 단선도와 대조할 때 최종 확인할 수 있다. 현재 파일에서는 모든 operation 값이 1이라 운전 상태 필터가 결과를 바꾸지는 않는다.

### 정답 파일의 요금·탄소 열 주의

truth의 `may_bill`은 `hourly_pow × 180`, `may_carbon`은 `hourly_pow × 0.424`와 일치한다. 이 값들은 코드가 `hourly_pow`를 Wh로 보고 kWh로 환산한 계산보다 약 1,000배 크다.

대시보드 예측은 `activePower=W → hourly_pow=Wh` 가정에 따라 다음 공식을 사용한다.

- 요금 추정 = `hourly_pow / 1000 × 180원/kWh`
- 탄소 추정 = `hourly_pow / 1000 × 0.424kg CO₂/kWh`

따라서 전력량 MAE가 우선적인 모델 비교 지표다. 요금·탄소 MAE 및 총량은 단가·탄소계수와 truth 스케일이 확인될 때까지 추정치로 취급한다. 180원/kWh는 고정 단순 단가라 누진제, 시간대별 요금, 수요요금 등을 반영하지 않는다. 0.424 역시 고정 배출계수 가정이며 실제 전력믹스에 따른 변화를 반영하지 않는다.

## 예측 feature와 모델

시간 기반 feature는 시간, 요일, 주말 여부, 월/일, 시간·요일 주기 sin/cos를 포함한다. 추가 feature는 다음과 같다.

- `lag_1`, `lag_24`, `lag_168`: 1시간/24시간/168시간 전 전력량
- `rolling_mean_24`, `rolling_mean_168`: 직전 24/168시간 평균

예측은 재귀적이다. 알려진 과거값으로 첫 시각을 예측한 후, 예측값을 다음 시각의 lag 입력 이력에 추가한다. 그래서 긴 기간일수록 오차가 누적될 수 있다.

비교 후보:

- Seasonal naive: 지난주 동일 시간(`lag_168`)을 반복하는 기준선
- Ridge alpha 100 / 1000: 정규화 선형 회귀
- HistGradientBoosting: 트리 부스팅
- LightGBM
- XGBoost

## 검증 및 모델 선정

### 과거 구간의 순차 검증

Dec–Apr 시간별 학습 데이터에서 각 모델을 3회 expanding-window 방식으로 검증한다. 각 validation 구간은 30일이며, 검증 구간을 recursive하게 예측한다. 이 방식은 미래를 학습에 섞지 않고 예측 시 lag값도 실제 미래값으로 미리 주지 않는다.

### 5월 비교 결과와 선택

각 후보를 Dec–Apr 전체 유효 데이터로 다시 학습하고, truth의 시간대들을 포함하는 동일한 recursive forecast를 생성해 May power MAE를 비교한다.

| 모델 | 순차 검증 MAE (Wh) | May MAE (Wh) |
|---|---:|---:|
| Ridge alpha 1000 | 77.394 | 88.499 |
| Ridge alpha 100 | 77.479 | 88.481 |
| LightGBM | 78.239 | **84.342** |
| HistGradientBoosting | 78.266 | 84.651 |
| XGBoost | 80.453 | 89.155 |
| Seasonal naive 168 | 107.589 | 124.718 |

요청한 May 비교 기준으로 최종 대시보드 후보는 **LightGBM + calendar/lag/rolling feature**로 정했다. May power MAE 84.342 Wh가 후보 중 가장 낮으며, HistGradientBoosting보다 약 0.309 Wh, Ridge alpha 100보다 약 4.139 Wh 낮다. 반면 순차 검증에서는 Ridge alpha 1000이 소폭 앞서므로, LightGBM 우위가 모든 기간에서 보편적이라고 단정할 수 없다.

May 데이터를 모델 선택에도 사용했기 때문에 위 May MAE는 완전히 독립적인 최종 테스트 점수가 아니다. 독립적인 성능 보고가 필요하면 추가로 보지 않은 기간/데이터를 최종 test로 확보해야 한다. 현재 대시보드에 이 제한을 표시한다.

## 대시보드

대시보드는 선택된 LightGBM의 예측만 운영 화면에 표시한다.

- 다음 24시간: 마지막 관측값 다음 시각부터 24시간
- 다음 7일: 마지막 관측값 다음 시각부터 168시간
- 다음 달: 마지막 관측이 속한 달 다음 달의 달력 전체
- 각 기간의 시간별 예상 전기요금 및 탄소 배출 그래프
- 예상 사용량을 kWh로 보여주는 절대값 그래프와, 구간 평균을 0으로 둔 편차 그래프
- 편차 그래프에서 평균보다 높은 시간을 초록, 낮은 시간을 주황으로 표시
- 예상 kWh, 누적 요금, 누적 CO₂, May validation MAE
- 탄소 배출량의 kg/ton 단위와 온실가스·지구온난화 설명
- 단위 불일치, 고정 요금/배출계수, recursive horizon, 설비 합산 주의사항

CO₂는 대기에서 열을 가두는 온실가스이며 배출이 누적되면 지구 온난화와 기후 변화에 기여한다. 이 설명은 배출의 일반적인 환경적 의미이며 특정 예측량이 특정 피해량을 발생시킨다는 인과 환산은 아니다.

모델별 비교 그래프는 PPT용으로 `forecast_outputs/analysis/model_comparison.png`에 별도 저장한다. 대시보드에는 후보별 예측 추이 그래프를 넣지 않는다.

## 결과 파일

- `forecast_outputs/hourly_power_processed.csv`: 시간별 집계 및 coverage
- `forecast_outputs/model_comparison.csv`: 순차 검증 및 May별 모델 MAE
- `forecast_outputs/model_predictions_all.csv`: 후보 전체의 May 예측과 실제 비교
- `forecast_outputs/pred_vs_truth.csv`: 최종 선택 모델의 May 비교 결과
- `forecast_outputs/future_forecasts.csv`: 24시간/7일/다음 달 대시보드용 전망
- `forecast_outputs/summary.json`: 학습 범위, 검증, 단위 진단, 선택 근거
- `forecast_outputs/forecast_vs_truth.png`: 최종 모델의 May 추이
- `forecast_outputs/analysis/model_comparison.png`: PPT용 후보 MAE 비교

## 적용 상태와 남은 확인

적용 완료: 원본 보존, 13개 채널 전체합산, 시간별 집계, 시간 coverage 검사, lag/rolling feature, 재귀형 순차 검증, 모든 모델의 동등한 May rollout 비교, May MAE 기반 운영 후보 선택, 24시간/7일/다음 달 예측, 절대 사용량/평균 대비 편차 시각화, 대시보드 경고와 환경 설명.

추가 확인 필요: `activePower` 단위와 sample semantics, 13개 장비 간 계량 범위 중복 여부, `operation` 상태 처리, truth bill/carbon 스케일. 특히 학습 데이터는 약 5개월이고 장비별 생산 일정/외기온도/생산량/휴일 feature가 없어, 예측은 계획 참고치로 제한해야 한다.
