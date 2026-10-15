# 설비 전력 예측

분석 보고서는 **audit_outputs/analysis_report.pdf**입니다. 계산 방법, 학습 모델과 적용 기법, 간단한 MAE 비교표, 실제·예측 평균 비교, 모델별 그래프, 선정 이유를 담았습니다.

## 실행

```powershell
python -m pip install -r requirements.txt
python -X utf8 power_training.py
python -m streamlit run forecast_dashboard.py
```

원본 전체 재집계: `python -X utf8 power_training.py --rebuild-data`

원본은 바탕화면 ai 폴더의 rtu_data_full.csv와 rtu_ground_truth_may.csv입니다. 원본과 session-14.pdf는 보존합니다.

## 현재 모델

lag와 이동 평균을 유지한 학습 모델 6종 및 평균·중앙값 기준 2개를 비교합니다. 추가 표준편차와 seasonal naive는 제외했습니다. 성능 비교는 과거 3회 검증 MAE와 5월 MAE 두 개만 표시합니다. 선정은 과거 검증 기준이며 5월은 반복 확인한 비교 구간입니다.

## 결과

- audit_outputs/analysis_report.pdf: 통합 보고서
- model_comparison.csv: 모델명·과거 검증 MAE·5월 MAE
- mean_comparison.csv: 실제·예측 평균
- predictions.csv: 동일한 505시간의 실제값과 모델별 예측
- plots/prediction_*.png: 8개 모델별 전체 구간 및 96시간 확대 그래프
- plots/model_comparison.png / plots/mean_comparison.png: MAE·평균 비교 그래프
- models/: 현재 8개 후보 학습 결과
- 나머지 가공 데이터·검증 점수·메타데이터는 검산과 재학습용입니다.

결과 파일은 모두 audit_outputs 아래에 있습니다. power_training.py는 집계·학습, forecast_report.py는 PDF 제작을 담당합니다.
