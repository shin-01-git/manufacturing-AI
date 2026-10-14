# Factory Energy Copilot 구현 가이드

> 이 문서는 멘토의 일반 STEP 7 가이드(탭 3개 "현황·예측/이상탐지/에이전트" 같은
> 일반 예시)를 **우리 시스템(Factory Energy Copilot)에 맞게 구체화**한 것이다.
> 멘토 가이드의 예시 숫자·탭 구성·설비명은 일반 설명용이므로, 아래에서는 전부
> 우리 실제 구성(탭 4개, 실제 데이터 파일, 실제 지표)으로 치환했다. 데이터·스펙과
> 이 문서가 어긋나면 항상 `requirements/design/tasks` 스펙과 artifacts가 우선한다.

---

## 1. 우리 대시보드 탭 구성 (멘토 예시 3개와 다름)

멘토 가이드는 보통 "현황 / 예측·이상 / 에이전트" 3탭을 예시로 든다. 우리는 역할
무관 **개요**를 기본 페이지로 두고, 상단 `segmented_control`로 **4개 페이지**를
전환한다. 순서는 역할에 따라 조정되지만 개요는 항상 맨 앞, AI Copilot은 맨 뒤다.

| 페이지 | 핵심 역할 | 주요 패널 |
|---|---|---|
| 개요 (Overview) | 기본, 역할 무관 | KPI 4개 → 실제 vs 예측 소형 차트 → 설비별 이상 비중 → 시간대 프로파일 / 상태 도넛·상위 설비·최근 경고·AI 요약 3줄 |
| 예측 | energy 기본 | KPI 4개(전력량·요금·탄소·MAE) → 실제 vs 예측 + 예측구간 → 경고 패널 → 베이스라인 비교표 → 시간대 프로파일 |
| 이상 탐지 | facility 기본 | 요약 카드 3개 → 설비 상태표 + 히트맵 → 방법 비교 2표 + 이벤트 로그 → 행 선택 드릴다운 |
| AI Copilot | 공통 | 좌 채팅(출처 칩) / 우상 브리핑 생성기 / 우하 "Python 계산값" 표 |

- 역할 enum은 `facility`(설비 관리 담당자) / `energy`(에너지 관리 담당자) 둘뿐이며,
  개인 이름은 쓰지 않는다 (REQ-USER).
- 기간 칩은 페이지별 실제 데이터 기간을 쓴다(예측/개요=predictions id 범위,
  이상=anomaly_events localtime 범위). 하드코딩하지 않는다.

---

## 2. 실제 데이터 파일 (원본 vs 산출물)

대시보드와 에이전트는 **`artifacts/`만** 읽는다. 4.9GB 원본 CSV는 직접 참조하지
않는다 (artifacts-only, 경로 보안 Property 7).

원본(파이프라인 입력, 서비스는 직접 읽지 않음):

- `data/rtu_data_full_훈련용.csv` — 학습 원본(5초 간격 RTU 로그)
- `data/rtu_ground_truth_may_검증용(5월).csv` — 5월 검증 정답(누수 방지로 분리)

산출물(서비스가 읽는 공유 artifact):

| 파일 | 내용 | 읽는 도구 |
|---|---|---|
| `artifacts/predictions.csv` | 시간별 예측(id, pred_pow, pred_bill, pred_carbon, pred_low/high) | `get_forecast_summary` |
| `artifacts/evaluation.json` | MAE(504/505)·총합오차·베이스라인 비교 | `get_evaluation` |
| `artifacts/module_status.csv` | 설비별 이상 건수·비율·최근 시각·상태 | `get_module_status` |
| `artifacts/anomaly_events.parquet` | 이상 이벤트(module, localtime, type, value, detected_by, score) | `get_anomaly_events` |
| `artifacts/anomaly_hourly.parquet` | 시간/일별 이상 건수 추세 | `get_anomaly_trend` |
| `artifacts/anomaly_rebuild_meta.json` | 방법별 건수·겹침(방법 비교 표용) | 대시보드 로더 |

실제 설비 module 값은 artifacts에서 그대로 읽어 표시한다(예: `1(PM-3)`,
`13(3호기)` 등 최대 13개). 목업의 Boiler/Chiller 같은 이름은 쓰지 않는다.

---

## 3. 실제 구현 순서

멘토 가이드의 일반 흐름을 우리 파이프라인 단계로 치환했다.

1. **스캐폴딩** — config 단일 출처(`config/config.py`), 폴더 구조, 테스트 기틀.
2. **집계** — 원본 5초 로그 → 시간별 전력량 `hourly_pow = Σ(activePower × 5/3600)`.
3. **전처리** — 캘린더 피처 생성(`src/preprocess`), 학습용 features.parquet.
4. **학습·예측·평가** — 모델 학습·선택(`src/train`), predictions.csv·evaluation.json 산출,
   MAE(504/505) 기록(참고용, 합격 기준 아님).
5. **이상 탐지** — 3상 동시 규칙(전압 210V / 역률 85% 미만) + 통계·IF 프록시,
   anomaly_events / anomaly_hourly / module_status 산출(`src/anomaly`).
6. **에이전트** — artifacts-only 5개 도구 + OpenAI 함수 호출, 키 없으면 규칙 기반 대체(`src/agent`).
7. **UI** — Streamlit 대시보드(`src/app`): theme/style/fmt/components/charts + 4개 탭.
8. **문서화** — 본 가이드 및 스펙 동기화.

---

## 4. 실제 지표

| 지표 | 정의 | 포매터 |
|---|---|---|
| 전력량 | `Σ pred_pow` (데이터 척도) | `fmt_pow` |
| 요금 | `전력량 × 180` (정답 척도) | `fmt_bill` |
| 탄소 | `전력량 × 0.424` (정답 척도) | `fmt_carbon` |
| MAE | 504·505 시점 `|예측−실제|` 평균, 참고용 | `fmt_mae` |
| 이상 행수 | anomaly_events 건수(전체 자릿수, 축약 금지) | `fmt_count` |
| 이상 비율 | 이상 행수 / 전체 행수(0..1 → %) | `fmt_ratio_pct` |

- 모든 숫자는 `src/app/ui/fmt.py` **하나**를 통해서만 출력한다(같은 값은 항상 같은
  문자열). 카드·표·hover·에이전트 모두 동일 포매터를 쓴다.
- activePower 단위가 미확인이라 화면에서 "kWh/원"으로 단정하지 않는다. 단위 라벨은
  `config.UNIT_LABELS`, 캡션은 `config.UNIT_FOOTNOTE` 한 곳에서만 바꾼다.
- KPI 카드는 숫자 아래에 **한 줄 해석**을 함께 둔다(예: 요금 카드 "정답 척도 기준
  합계로, 값이 클수록 에너지 비용이 큼"; MAE 카드 "예측과 실제의 평균 차이로
  낮을수록 정확 — 참고용, 합격 기준 아님"). (STEP 7-2 ③)

---

## 5. 색상 규칙 (steering A-2 요약)

- 디자인 토큰 단일 출처: `src/app/ui/theme.py` + `.streamlit/config.toml`.
- 배경 `#F5F6F8`, 서피스 `#FFFFFF`, 네이비 상단바 `#0F1B2D`, 액센트 `#0E9F8E`.
- 상태색(초록 `#16A34A` / 주황 `#D97706` / 빨강 `#DC2626` / 회색 `#9CA3AF`)은
  **설비 상태·경고에만** 쓴다. 차트 계열색으로는 쓰지 않는다(차트는 중립·액센트색).
- 상태 경계값 미승인(O-3)이면 status는 회색 '미확정' 칩 + 이상 비율만 표시한다.
- 차트는 Plotly 템플릿 하나(`fec_template`)로 통일(흰 배경, 옅은 가로 그리드,
  범례 상단 우측). 축 이름은 한국어, 단위는 '데이터 척도'로 표기한다(STEP 7-2 ④).

---

## 6. 에러 발생 시 질문 템플릿 (STEP 7-3)

막혔을 때는 "안 돼요" 대신 아래 형식으로 정보를 모아 질문한다.

```
[무엇을 하려 했는지]  예: 이상 탐지 탭에서 설비 필터를 적용하려 함
[실행한 명령/행동]    예: .venv\Scripts\python.exe -m pytest tests/test_ui_elements.py -q
[기대한 결과]         예: 선택한 설비만 상태표·이벤트에 표시
[실제 결과/오류]      예: ModuleNotFoundError: No module named 'config'
[전체 트레이스백]     (복사해서 그대로 붙임)
[이미 시도한 것]      예: sys.path에 프로젝트 루트 추가 확인
```

- 오류 메시지와 트레이스백은 요약하지 말고 전체를 붙인다.
- 재현 명령(실행 경로·파이썬 버전 포함)을 함께 적는다. 우리 환경은
  `.venv\Scripts\python.exe`(Python 3.11)로 검증한다.

---

## 7. STEP 1 레퍼런스 5질문 자체 점검

멘토 STEP 1의 "좋은 대시보드" 5질문으로 우리 화면을 점검한 결과(요약):

1. **3초 안에 핵심이 보이는가?** — 개요 상단 KPI 4개(전력량·요금·탄소·이상 행수)가
   첫 화면 상단에 고정 높이로 정렬돼 한눈에 들어온다. (충족)
2. **시각적 위계가 분명한가?** — 네이비 상단바 → 페이지 제목 → KPI → 패널 순으로
   크기·색 위계가 잡혀 있고, 모든 블록은 테두리 패널 안에 담긴다. (충족)
3. **필터가 눈에 띄고 실제로 동작하는가?** — 사이드바(역할·날짜·설비)가 왼쪽
   고정 위치에 있고, 설비 필터가 이상 탐지의 상태표·이벤트·히트맵·상위 설비에
   실제로 반영된다. 예측 탭은 공장 전체 단위라 미적용임을 캡션으로 안내한다. (충족)
4. **숫자의 의미가 전달되는가?** — 각 KPI 카드에 계산식·단위 캡션에 더해 한 줄
   해석을 붙여 "무슨 뜻인지"를 바로 읽을 수 있다. (보완 완료)
5. **차트가 자립적으로 읽히는가?** — 각 Plotly 차트에 x·y축 이름과 단위(데이터
   척도), 범례가 있다. y축이 0에서 시작하지 않는 선 그래프는 캡션으로 명시한다. (충족)

> 점검 기준: 성능·3초 로드는 측정값으로만 기록하며 합격/실패를 단정하지 않는다
> (O-6). 상태 라벨·고장 진단은 넣지 않고 "관찰 기록/이상 징후"로만 표현한다.
