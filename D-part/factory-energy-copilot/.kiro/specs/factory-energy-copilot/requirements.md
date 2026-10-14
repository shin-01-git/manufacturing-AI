# Requirements Document

## Introduction

본 문서는 동의대 제조 AI 과정 2팀(팀명: **문학적인 기계**)의 프로젝트 **Factory Energy Copilot**에 대한 요구사항을 정의한다. 이 시스템의 목표는 2025년 5월 공장 전체 전력량을 예측하고, 전기요금·탄소배출량으로 환산하며, 5초 원본 데이터의 이상 구간을 머신러닝 기법으로 탐지해 근거 기반으로 보고하고, OpenAI API 기반 에이전트와 Streamlit 대시보드로 결과를 전달하는 것이다.

본 문서는 Requirements-First 워크플로의 **Requirements 단계** 산출물이며, 이 단계에서는 설계(design.md)·작업(tasks.md)·구현 코드를 확정하지 않는다. 추적성 표의 설계·작업 칸은 승인 후에 채운다.

### 사실 라벨링 규약

- **[확인]**: 프롬프트 1의 "작업 1" 사전 점검에서 안전한 메타데이터·헤더·샘플·순차 행 수 확인으로 검증된 사실.
- **[미확인]**: 전수 검증을 하지 못해 사실로 확정할 수 없는 항목.
- **[추정]**: 샘플·간접 근거에 기반한 추측으로, 전수 확인되지 않은 항목.
- **[결정 필요]**: 사용자 판단·승인이 필요한 항목. 승인 전에는 구현·평가 기준으로 사용하지 않는다.

### PDF 원문 미확인 고지

프로젝트 폴더의 PDF 3개(`2팀 서비스 기획서 10.10.pdf`, `2팀_서비스기획서_Factory_Energy_Copilot.pdf`, `프로젝트 가이드.pdf`)의 **본문은 이 단계에서 확인하지 못했다 [미확인]**. 따라서 PDF에 담긴 요구사항·범위·비목표·평가 기준은 추측하거나 확정하지 않으며, 충돌 가능성이 있는 항목은 `REQ-OPEN`의 `[결정 필요]`로 남긴다.

## Glossary

- **System (시스템)**: Factory Energy Copilot 전체. 집계·학습·예측·평가·이상탐지·에이전트·대시보드 구성요소를 포함한다.
- **Aggregator (집계기)**: 5초 원본을 시간·설비별로 집계해 artifacts를 만드는 구성요소.
- **Trainer (학습기)**: 베이스라인·후보 모델을 학습·선택하는 구성요소.
- **Predictor (예측기)**: 확정된 모델로 2025년 5월 505개 시점을 예측하는 구성요소.
- **Evaluator (평가기)**: 예측이 고정된 뒤 정답 파일을 읽어 MAE 등을 계산하는 구성요소.
- **Anomaly_Detector (이상탐지기)**: 5초 행 단위 이상 구간/이상 징후를 탐지하는 구성요소.
- **Agent (에이전트)**: OpenAI API function calling 기반으로 artifacts를 근거로 답하는 구성요소.
- **Dashboard (대시보드)**: artifacts만 읽어 결과를 시각화하는 Streamlit 애플리케이션.
- **Artifacts (산출물 저장소)**: `artifacts/` 아래의 작은 파일(CSV/Parquet/JSON). 대시보드·에이전트의 유일한 데이터 소스.
- **Training_CSV (학습 원본)**: `data/rtu_data_full_훈련용.csv` (5초 원본, 읽기 전용).
- **Validation_CSV (검증 정답)**: `data/rtu_ground_truth_may_검증용(5월).csv` (5월 정답, 평가 단계에서만 읽음).
- **localtime**: 한국시간(KST) 기준 시각 컬럼. `YYYYMMDDHHMMSS` 14자리 숫자 [확인].
- **시간별 전력량(hourly_pow)**: Σ(activePower × 5/3600). 시간·설비별로 합한 뒤 전체 설비를 더해 공장 전체값을 만든다.
- **이상 구간/이상 징후**: 삽입된 비정상 관측. "고장"이라는 단정 표현은 사용하지 않는다(고장 이력 데이터 없음).

---

## 공통 요구사항

### REQ-PROB: 문제 정의

**User Story:** As a 2팀(문학적인 기계)의 발주처, I want 공장 전력 데이터로부터 미래 전력량과 비용·탄소, 이상 구간을 근거 기반으로 받는 것, so that 데이터로 설명 가능한 에너지·설비 보고를 할 수 있다.

- **우선순위:** Must
- **근거:** 프롬프트 1 "프로젝트 목적·범위" 및 발주처 요구사항 [확인].
- THE System SHALL 2025년 5월 공장 전체 전력량 예측, 전기요금·탄소 환산, 5초 원본 이상 구간 탐지, 근거 기반 보고라는 문제를 requirements.md에 정의로 기록한다.
- **Acceptance:** 문제 정의와 근거가 문서에 존재하고 발주처 요구사항(예측·이상탐지·에이전트·대시보드)과 일치함을 문서 검토로 확인한다.
- **검증 방법:** 문서 리뷰(정의·근거 존재 여부).

### REQ-GOAL: 목표 정의

**User Story:** As a 프로젝트 팀, I want 측정 가능한 목표, so that 성공 여부를 판정할 수 있다.

- **우선순위:** Must
- **근거:** 프롬프트 1 발주처 요구사항 1~4 [확인].
- THE System SHALL (1) 2025-05-10 00:00 ~ 2025-05-31 00:00 1시간 단위 505개 시점 공장 전체 전력량 예측, (2) 전기요금(180원/kWh)·탄소배출(0.424 kgCO2/kWh) 환산, (3) 평가지표 MAE, (4) 5초 원본 이상 구간 머신러닝 탐지, (5) OpenAI API 근거 에이전트, (6) Streamlit 대시보드를 목표로 기록한다.
- **Acceptance:** 여섯 목표가 각각 문서에 정의·근거와 함께 존재한다.
- **검증 방법:** 문서 리뷰.

### REQ-USER: 사용자 정의

**User Story:** As a 제품 책임자, I want 명확한 사용자 역할, so that 화면·보고가 역할에 맞게 설계된다.

- **우선순위:** Must
- **근거:** 프롬프트 2 Requirements 1) 사용자 설명 [확인].
- THE System SHALL 두 사용자 역할을 정의한다: **설비 관리 담당자**(어느 설비에서 언제 이상 구간이 나왔는지를 표와 상태색(정상/주의/이상)으로 빠르게 확인), **에너지 관리 담당자**(예측 전력량·요금·탄소를 보고 경영진 보고용 요약 작성).
- THE System SHALL 페르소나를 **역할명(설비 관리 담당자 / 에너지 관리 담당자)으로만** 표시하고, 담당자에게 **개인 이름을 부여하거나 이름 단위로 데이터를 조회하지 않는다** [확인: 팀장 피드백 "역할명으로만"]. 역할 기반 필터링·맞춤 화면은 역할명 식별자(role key)만을 조회·분기 기준으로 사용한다.
- **Acceptance:** 두 역할의 정의와 각 역할의 주요 사용 목적이 문서에 존재하고, 화면·도구·조회 분기 어디에도 개인 이름 식별자가 사용되지 않는다(역할명만 사용).
- **검증 방법:** 문서 리뷰.

### REQ-SCOPE: 범위

**User Story:** As a 프로젝트 팀, I want 명확한 범위, so that 작업이 발산하지 않는다.

- **우선순위:** Must
- **근거:** 프롬프트 1 "프로젝트 목적·범위", 프롬프트 2 Requirements 1) [확인].
- THE System SHALL 다음을 범위에 포함한다: 전력량 예측, 전기요금/탄소 환산, 5초 행 단위 이상탐지, OpenAI API 기반 근거 답변 에이전트, Streamlit 대시보드. 데이터 기준 기간과 실제 확인 범위는 작업 1 보고를 근거로 구분한다.
- **Acceptance:** 포함 목록이 문서에 있고 각 항목이 하나 이상의 기능 요구사항(A/B/C/D)과 연결된다.
- **검증 방법:** 추적성 표 검토.

### REQ-NONSCOPE: 비목표

**User Story:** As a 프로젝트 팀, I want 명확한 비목표, so that 범위 밖 작업을 하지 않는다.

- **우선순위:** Must
- **근거:** 프롬프트 1 "비목표" [확인]. PDF 관련 범위는 [미확인].
- THE System SHALL 다음을 비목표로 **확정**한다: 실시간 연동, 설비 제어, 고장 확정 진단, 생산량 대비 효율, 전년 대비 비교, 점검 이력 관리.
- IF PDF 내보내기 또는 PDF에 기인하는 범위가 비목표 후보로 제기되면, THEN THE System SHALL 그 항목을 비목표로 확정하지 않고 `REQ-OPEN`의 `[결정 필요]`로 기록한다(검토 finding P2-001 반영).
- **Acceptance:** 확정 비목표 6개 목록이 존재하고, PDF 관련 범위는 비목표에 포함되지 않으며 `REQ-OPEN`에 질문으로 남아 있다. PDF 원문 확인·사용자 승인 후에만 비목표와 근거·acceptance를 갱신한다.
- **검증 방법:** 문서 리뷰(비목표 목록과 REQ-OPEN의 PDF 항목 상호 확인).

### REQ-SCENARIO: 사용 시나리오

**User Story:** As a 두 사용자 역할, I want 대표 사용 시나리오, so that 요구사항이 실제 흐름을 반영한다.

- **우선순위:** Must
- **근거:** 프롬프트 2 Requirements 2) [확인].
- WHEN 설비 관리 담당자가 기간·설비를 선택하면, THE System SHALL 상태표, 이상 이벤트, 전압·역률 시계열, 방법별 겹침을 제공한다.
- WHEN 에너지 관리 담당자가 예측 화면을 열면, THE System SHALL 505개 예측의 전력·요금·탄소와 MAE·베이스라인 비교를 제공한다.
- WHEN 사용자가 데이터에 없는 숫자나 원인을 질문하면, THE Agent SHALL `데이터에 없음` 또는 관찰된 사실·가능한 원인 후보·현장 확인 항목을 구분해 답한다.
- **Acceptance:** 세 시나리오가 문서에 있고 각 시나리오가 A/B/C/D 요구사항과 연결된다.
- **검증 방법:** 추적성 표 검토.

### REQ-DATA: 데이터 계약

**User Story:** As a 데이터 담당자, I want 명확한 데이터 계약, so that 누수와 원본 훼손을 방지한다.

- **우선순위:** Must
- **근거:** 프롬프트 1 데이터 사용 규칙·R1~R16, 작업 1 보고 [확인] + [미확인] 구분.
- THE System SHALL 학습 원본과 검증 정답을 경로·변수·산출물·로그에서 분리한다:
  - Training_CSV = `data/rtu_data_full_훈련용.csv` — **[확인]** 19개 열(`module(equipment), timestamp, localtime, operation, voltageR, voltageS, voltageT, voltageRS, voltageST, voltageTR, currentR, currentS, currentT, activePower, powerFactorR, powerFactorS, powerFactorT, reactivePowerLagging, accumActiveEnergy`), 헤더 제외 **물리 행 33,696,013개**, 파일 크기 4,909,245,995바이트(십진 약 4.9GB), localtime `YYYYMMDDHHMMSS` 14자리·5초 간격, 시작 샘플 `20241201000000`, 끝 샘플 `20250430000000`.
  - Validation_CSV = `data/rtu_ground_truth_may_검증용(5월).csv` — **[확인]** 5개 열(`id, hourly_pow, may_bill, may_carbon, agg_pow`), 헤더 제외 **505행**, id는 `2025-05-10 00:00:00` ~ `2025-05-31 00:00:00` 1시간 간격.
- THE System SHALL 다음을 **[미확인]**으로 표시한다: 전체 시간 연속성, 전체 설비 목록·설비 수, 모든 행의 결측·중복·operation 값, 모든 시간·설비 조합의 720행 충족, accumActiveEnergy 감소 여부, 이상치 전수 분포. 또한 위 물리 행 수는 CSV 파서 기준 레코드 수가 아니라 **물리 줄 수**이며, 인용 줄바꿈 미파싱이라는 한계를 표시한다 [확인된 한계].
- THE Dashboard AND THE Agent SHALL Artifacts만 읽는다(원본 CSV를 직접 읽지 않는다).
- THE System SHALL 전처리를 **공유 전처리 산출물에 의존하지 않고 각 기능(집계·학습·예측·이상탐지)이 필요에 맞게 개별 수행**하는 것으로 가정한다 [확인: 팀장 피드백 "전처리를 굳이 공유 안 하고 필요한 내용에 맞게 전처리"]. 단, 모든 기능은 공통 데이터 규칙(원본 불변·전체 적재 금지·학습/검증 누수 방지·KST 기준·재현성 로그)을 동일하게 준수하고, 각 기능은 자신이 수행한 전처리의 근거·입력 경로·변환 내용을 기능 설명/로그에 남긴다.
- **Acceptance:** 경로·필드·행 수·읽기 전용 경계·학습/검증 분리가 문서에 기록되고, [확인]/[미확인] 라벨이 각 항목에 붙어 있다. 또한 각 기능이 수행한 전처리의 근거·입력·변환이 기능별로 기록되고, 공통 데이터 규칙 위반이 없음을 확인한다.
- **검증 방법:** 문서 리뷰 + 추적성 표에서 데이터 계약 참조 확인.

### REQ-NFR: 비기능 요구사항

**User Story:** As a 운영·보안 담당자, I want 비기능 제약, so that 대용량·보안·재현성 요건을 충족한다.

- **우선순위:** Must (NFR-5는 Should·[결정 필요])
- **근거:** 프롬프트 1 R1~R16, 작업 1 보고 [확인].
- **NFR-1 (대용량 처리):** THE System SHALL Training_CSV를 전체 메모리에 적재하지 않는다. PowerShell `Get-Content` 전체 로드, `Import-Csv` 전체 로드, pandas `read_csv` 전체 로드를 금지하고, StreamReader 순차 처리 또는 제한 청크만 사용한다. 청크 크기·메모리 사용량·처리 시간·오류를 로그에 남긴다.
  - Acceptance: 처리 로그에 청크 설정·메모리·시간·오류가 기록되고 전체 로드 호출이 코드에 없다. 검증: 로그 검사 + 코드 정적 검사(승인 후 단계).
- **NFR-2 (원본 불변):** THE System SHALL 작업 전후 두 CSV의 `Length`와 `LastWriteTime`(가능하면 해시)을 비교하고, 원본을 수정·이동·이름 변경·덮어쓰기 하지 않는다. 산출물은 `artifacts/` 아래에만 저장한다.
  - Acceptance: 전후 Length·LastWriteTime 비교 기록이 존재하고 불변임이 확인된다. 검증: 메타데이터 비교 로그.
- **NFR-3 (시간 기준):** THE System SHALL localtime(KST)을 시간 기준으로 사용하고 timestamp를 임의로 사용하지 않는다.
  - Acceptance: 시간 처리 코드/설정이 localtime을 쓰고 timestamp 사용이 없음을 확인한다. 검증: 코드 정적 검사(승인 후).
- **NFR-4 (누수 방지):** THE System SHALL Validation_CSV의 `id, hourly_pow, may_bill, may_carbon, agg_pow`를 학습·특성 생성·파라미터 선택·보정·이상탐지 기준 설정에 사용하지 않는다. 예측이 고정된 뒤 평가 단계에서만 Validation_CSV를 읽는다.
  - Acceptance: 학습/예측/이상탐지 코드가 Validation_CSV 경로를 참조하지 않음을 테스트로 확인한다(승인 후). 검증: 경로 참조 테스트.
- **NFR-5 (첫 화면 성능) [Should · 결정 필요]:** THE System SHALL 원본 없이 artifacts만 읽어 첫 화면을 신속히 표시하는 것을 목표로 검토한다. `5초` 수치는 근거·측정 환경(하드웨어·캐시·데이터 크기)·측정 명령·허용 오차를 사용자 승인으로 확정하기 전에는 Must 기준이나 실패 판정으로 사용하지 않는다.
  - Acceptance: 측정 환경·명령·첫 화면 시간을 기록하되 승인된 목표가 없으면 `미승인/측정 필요`로 표시한다. 검증: 측정 로그.
- **NFR-6 (보안·비밀):** THE System SHALL OPENAI_API_KEY 등 키·토큰·비밀번호를 환경변수(`OPENAI_API_KEY`) 또는 시크릿에서만 읽고, 값·부분값·환경 덤프를 코드·로그·커밋·스펙·화면에 남기지 않는다. 개인정보·비밀정보를 수집·출력·커밋하지 않으며, 식별 가능 정보가 발견되면 값 대신 컬럼·처리 방법만 기록한다. 모델명은 환경변수 `OPENAI_MODEL`로 변경 가능하게 한다.
  - Acceptance: 키·비밀 값이 코드·로그·문서·화면에 없음을 검사한다. 검증: 비밀 스캔(승인 후).
- **NFR-7 (재현성):** THE System SHALL 실행 시각, 코드·설정 버전, 입력 경로, 시간 기준, 청크 설정, 난수 시드, 결과 경로, 요약 통계를 기록해 재현 가능하게 한다. 확인하지 못한 값은 확정값처럼 쓰지 않고 `미확인/추정`으로 표시한다.
  - Acceptance: 실행 로그에 위 항목이 기록된다. 검증: 로그 검사.
- **NFR-8 (환산 척도):** THE System SHALL 전기요금·탄소 환산 계수를 config 한 곳에만 두고 `may_bill = hourly_pow × 180`, `may_carbon = hourly_pow × 0.424`(정답 파일과 동일 척도, 1000으로 나누지 않음)을 사용하며 화면에 계산식을 표기한다.
  - Acceptance: 환산 계수가 단일 config에 있고 화면에 계산식이 표시된다. 검증: config 검사 + UI 검증(승인 후).

### REQ-RISK: 위험

**User Story:** As a 프로젝트 팀, I want 위험 식별과 완화, so that 실패를 조기에 관리한다.

- **우선순위:** Must
- **근거:** 프롬프트 2 Requirements 5) [확인].
- THE System SHALL 다음 위험을 영향·완화·잔여 위험·검증 방법과 함께 문서화한다:
  1. **4.9GB 처리 중 메모리/시간 초과** — 영향: 집계 실패. 완화: 제한 청크·순차 스트리밍, 메모리·시간 로그, 한계 초과 시 중단·재설계. 잔여: 환경별 처리 시간 편차. 검증: 처리 로그.
  2. **물리 줄 수 ≠ CSV 레코드 수** — 영향: 행 수 오해석. 완화: CSV 파서 기준 재확인, 한계 명시. 잔여: 인용 줄바꿈 존재 가능성. 검증: 파서 기준 재집계(승인 후).
  3. **시간·설비 결측** — 영향: 집계·학습 편향. 완화: n=720 완전 시간만 학습, 제외 건수 기록. 잔여: 결측 패턴 전수 미확인. 검증: 집계 통계.
  4. **5월 분포 차이** — 영향: 예측 오차 증가. 완화: 베이스라인 비교, 분포 차이 보고(정답 보정 금지). 잔여: 설명 불가 추세. 검증: 평가 비교표.
  5. **이상탐지 규칙을 정답으로 해석하는 평가 편향** — 영향: 잘못된 성능 주장. 완화: 프록시 명명(일치율/포착률), 독립 라벨 승인 전 정답 표시 금지. 잔여: 독립 라벨 부재. 검증: 지표 명명 검사.
  6. **API 키/네트워크 실패** — 영향: 에이전트 중단. 완화: 규칙 기반 fallback·캐시, 예외 억제. 잔여: 품질 저하. 검증: 장애 주입 테스트.
  7. **미확인 PDF 요구사항** — 영향: 범위 누락·충돌. 완화: PDF 본문 확인·사용자 승인 전 `[결정 필요]` 유지. 잔여: 미확인 요구사항. 검증: REQ-OPEN 질문 해결.
  8. **5초 첫 화면 성능 저하** — 영향: 사용성 저하. 완화: artifacts-only, 캐시. 잔여: 하드웨어 편차. 검증: 측정 로그(승인 후).
- **Acceptance:** 각 위험에 영향·완화·잔여·검증이 기록된다.
- **검증 방법:** 문서 리뷰.

### REQ-OPEN: 미결정 사항

**User Story:** As a 사용자, I want 결정이 필요한 항목이 질문으로 남는 것, so that 임의 확정으로 인한 오류를 피한다.

- **우선순위:** Must (해소 전까지 관련 기능은 구현/평가 기준으로 확정 금지)
- **근거:** 프롬프트 2 Requirements 5) [결정 필요], 검토 finding P2-001 [확인].
- THE System SHALL 아래 항목의 상태를 관리한다. **결정됨** 항목은 사용자 승인으로 확정되었고, 나머지는 승인 전 확정하지 않는다:
  - **O-1 (PDF 충돌/범위) [미결정]:** 두 기획서 PDF와 프로젝트 가이드 PDF의 본문·평가 기준이 본 문서의 요구사항과 충돌하는지 원문 확인 후 질문. PDF 내보내기 등 PDF 기인 범위·비목표 포함.
  - **O-2 (이상 영향 정의) [미결정]:** 이상 구간의 전력량·요금 영향 정의와 "잡음 범위" 판정 기준.
  - **O-3 (상태 경계값) [미결정]:** 정상/주의/이상 상태 경계값과 급증 정의.
  - **O-4 (규칙 결과 평가 기준) [미결정]:** 독립 정답 라벨의 출처·생성 시점·학습/튜닝 분리·평가 전용 사용 여부.
  - **O-5 (D3 설비 수) [결정됨 — 사용자 승인]:** 설비 수 **최대 13을 상한/기대값**으로 두되 실제 데이터에서 **발견된 설비만 처리**한다. 발견 수가 13과 다르면 경고·질문으로 남긴다. 13을 하드코딩한 고정 반복은 하지 않는다.
  - **O-6 (D7 5초 측정 환경) [미결정]:** 첫 화면 5초 목표의 근거·측정 환경·명령·허용 오차.
  - **O-7 (예측구간 분위수) [잠정 결정 — 사용자 승인]:** 기본 분위수(하한 0.05 / 상한 0.95)를 **잠정값**으로 사용하고 화면·로그에 "임시/잠정"으로 표시한다. 분위수는 config 단일 상수로 두어 추후 교체. 순서 불변식(하한≤예측≤상한)은 항상 보장.
  - **O-8 (Must/Should 우선순위) [미결정]:** 본 문서 우선순위 지정이 발주처 기준과 일치하는지.
  - **O-9 (B2 규칙 판정식) [결정됨 — 사용자 승인]:** **(a) 3상 동시 판정** 채택 — `(voltageR<210 AND voltageS<210 AND voltageT<210) OR (powerFactorR<85 AND powerFactorS<85 AND powerFactorT<85)`. 임계값(210V, 85%)은 config 단일 상수(아래 B2 참조).
  - **O-10 (A7 임계값) [결정됨 — 사용자 승인]:** MAE 약 89/하한 약 73은 **참고용으로만 기록**하고 경고 임계값·합격/불합격 기준으로 **사용하지 않는다**. 정답 보정 금지(아래 A7 참조).
- **Acceptance:** 각 항목에 상태(결정됨/잠정/미결정)·영향·질문·결정 주체·허용 동작이 기록되고, 관련 요구사항(B2, A7, D3, D7 등)과 상호 참조된다. 결정됨 항목은 해당 요구사항·설계·작업에서 결정 내용으로 반영된다.
- **검증 방법:** 문서 리뷰 + 추적성 표 상호 참조 확인.

---

## Requirements

### 요구사항 A — 예측 (Predictor/Trainer/Evaluator)

### Requirement 1: 공장 전체 전력량 예측과 환산·평가 (A)

**User Story:** As a 에너지 관리 담당자, I want 2025년 5월 공장 전체 전력량 예측과 요금·탄소 환산 및 MAE 평가, so that 경영진 보고용 요약을 근거와 함께 만들 수 있다.

#### Acceptance Criteria

1. **A1 [Must]** THE Trainer SHALL 학습 입력으로 `artifacts/hourly_from_full.parquet`에서 n=720인 완전한 시간만 사용하고 불완전한 시간은 제외한다.
   - 근거: R4·작업 1 집계 규칙 [확인(규칙)] / 완전 시간 분포 [미확인].
   - Acceptance: 학습 입력 검사에서 n=720만 남는 결과와 제외 건수가 입력 경로·필드·로그로 기록된다.
2. **A2 [Must]** THE Trainer SHALL 세 베이스라인(학습 전체 평균 반복, 같은 요일·시간대 과거 평균, 직전 주 같은 시간 값)을 먼저 만든다.
   - 근거: 프롬프트 2 A2 [확인].
   - Acceptance: 세 모델명과 시간순 평가 MAE가 비교표에 있고 계산 입력이 기록된다.
3. **A3 [Must]** THE Trainer SHALL 후보를 달력 특성 선형회귀 후 트리 모델 1종 순으로 비교한다. WHERE 특성 후보에 5월에 알 수 없는 전압·전류·역률 또는 학습 종료 4/30~예측 시작 5/10 사이의 최근값(lag)이 포함되면, THE Trainer SHALL 그 필드를 특성에서 제외한다.
   - 근거: 프롬프트 2 A3 [확인].
   - Acceptance: 특성 목록에 금지 필드가 없고 학습 코드/설정 검사로 경계를 확인한다.
4. **A4 [Must]** THE Trainer SHALL 모델 선택에 학습 구간 안에서 마지막 3주를 검증 구간으로 한 시간순 검증 MAE만 사용하고, 확정 후 전체 기간으로 재학습해 5월을 예측한다.
   - 근거: 프롬프트 2 A4 [확인].
   - Acceptance: 분할 시각·선택 점수·재학습 시점이 로그와 비교표에 있으며 5월 정답은 선택에 참조되지 않는다.
5. **A5 [Must]** THE Predictor SHALL `artifacts/predictions.csv`에 `id, pred_pow, pred_bill, pred_carbon` 505행을 누락·중복 없이 저장한다.
   - 근거: 발주처 요구사항 1·작업 1 검증 id [확인].
   - Acceptance: id가 2025-05-10 00:00부터 2025-05-31 00:00까지 1시간 간격이고 Validation_CSV id와 1:1 일치한다.
6. **A6 [Must]** THE Evaluator SHALL `artifacts/evaluation.json`에 hourly_pow·may_bill·may_carbon의 MAE(504/505 기준), agg_pow 대비 총합 오차, 베이스라인·후보 비교표를 저장한다.
   - 근거: R7·R8·발주처 평가지표 [확인].
   - Acceptance: JSON 필드와 기준별 수치·비교표가 존재하고 화면에도 표시된다.
7. **A7 [Should · O-10 결정됨]** THE Evaluator SHALL 학습 평균 반복 MAE 약 89와 하한 약 73을 **참고용으로만** evaluation·로그에 기록한다. THE Evaluator SHALL 73/89를 경고 임계값·합격/불합격 기준으로 **사용하지 않는다**. THE System SHALL 정답으로 예측을 보정하지 않는다. (사용자 승인: 참고용만)
   - 근거: 프롬프트 2 A7 / O-10 [결정됨 — 참고용만].
   - Acceptance: `reference_note`에 73/89가 `참고용(합격 기준 아님)`으로 기록되고, 73/89 기반 경고·합격 판정 코드가 없으며, 정답 보정 코드가 없다.
8. **A8 [Should · O-7 잠정 결정]** THE Predictor SHALL 검증 구간 잔차 분위수로 예측 상·하한을 만들고 predictions.csv에 `pred_low, pred_high`를 추가한다. 분위수는 **잠정값(하한 0.05 / 상한 0.95)**을 config 단일 상수로 사용하고 화면·로그에 "임시/잠정"으로 표시한다.
   - 근거: 프롬프트 2 A8 / O-7 [잠정 결정].
   - Acceptance: 잠정 분위수·검증 구간·계산식을 기록하고 "임시/잠정" 표시가 있으며, 각 예측 행에 `하한 ≤ 예측 ≤ 상한` 조건을 검사한다.

### 요구사항 B — 이상 탐지 (Anomaly_Detector)

### Requirement 2: 5초 행 단위 이상 구간 탐지와 방법 비교 (B)

**User Story:** As a 설비 관리 담당자, I want 5초 원본의 이상 구간을 여러 방법으로 탐지하고 방법 간 비교·근거를 보는 것, so that 어느 설비에서 언제 이상 징후가 있었는지 신뢰할 수 있게 파악한다.

#### Acceptance Criteria

1. **B1 [Must]** THE Anomaly_Detector SHALL 탐지 단위를 5초 행(module/localtime)으로 하고, 시간별 전력량 기준 탐지를 주 방법으로 사용하지 않는다.
   - 근거: 프롬프트 2 B1·F5 [확인(단위 규칙)].
   - Acceptance: 이벤트 원본 단위가 module/localtime 5초 행이고 hourly 결과는 집계 결과로만 표시된다.
2. **B2 [Must · O-9 결정됨]** THE Anomaly_Detector SHALL 같은 데이터에 세 방법(규칙, 설비별 3σ 또는 robust z-score, Isolation Forest 등 비지도 모델—표본 학습 후 청크 점수화)을 적용한다. 규칙 판정식은 **(a) 3상 동시**로 확정한다(사용자 승인): `(voltageR<210 AND voltageS<210 AND voltageT<210) OR (powerFactorR<85 AND powerFactorS<85 AND powerFactorT<85)`. 임계값(210V, 85%)은 config 단일 상수로 둔다. 후보 특성은 3상 전압 평균, 3상 역률 평균, 3상 전류 평균, activePower, `activePower/Σ(V×I×PF/100)` 비율이다. 새 이상 유형은 관측 근거·필드·판정식·임계값·승인 주체를 먼저 문서화하고 승인 전에는 구현·평가에 추가하지 않는다.
   - 근거: 프롬프트 2 B2·F5 [확인(현상)] / O-9 [결정됨 — 3상 동시].
   - Acceptance: 세 방법의 목록·특성·표본·청크 설정·점수가 기록되고 세 결과가 같은 대상에 매칭된다. 규칙에는 3상 동시 논리식·F5 매핑·임계값(config 참조)이 기록된다. 추가 유형은 승인 기록이 있을 때만 별도 규칙으로 포함된다.
3. **B3 [Must]** THE Anomaly_Detector SHALL 방법별 탐지 건수와 방법 간 겹침을 표로 만들고, 독립 정답 라벨이 없는 동안 규칙 결과를 정답으로 부르지 않는다. 규칙 기준 계산값은 `규칙 대비 일치율`·`규칙 포착률` 같은 프록시로 명명하고 분모·대상 기간·규칙 한계를 함께 표시한다. 독립 라벨 확보는 출처·생성 시점·학습/튜닝 분리·평가 전용 사용을 `[결정 필요]`(O-4)로 제시해 승인받는다.
   - 근거: 프롬프트 2 B3 [확인(규칙)] / 독립 라벨 [결정 필요].
   - Acceptance: 방법별 건수·겹침·프록시의 분모·기준·한계가 표에 있고, 독립 라벨 없는 상태에서 독립 정답 지표로 표시하거나 규칙 결과를 정답으로 확정하지 않는다. 독립 라벨 승인 시에만 별도 평가 지표·출처를 기록한다.
4. **B4 [Must]** THE Anomaly_Detector SHALL `artifacts/anomaly_events.parquet`에 module·localtime·유형·값·정상범위·방법별 탐지 여부·점수를, `artifacts/anomaly_hourly.parquet`에 hour·module·유형별 건수를, `artifacts/module_status.csv`에 설비별 이상 건수·비율·최근 발생 시각·상태를 저장한다.
   - 근거: 프롬프트 2 B4 [확인(계약)].
   - Acceptance: 세 파일의 필드와 경로가 검사되고 원본은 산출물로 덮어쓰지 않는다.
5. **B5 [Must]** THE Anomaly_Detector SHALL 상태 정상/주의/이상을 설비별 이상 행 비율과 평소 시간당 건수 대비 급증 여부로 정하고 기준을 문서화한다. 해당 설비는 거의 매 시간 이상 행이 있으므로 이상이 있었던 시간만으로 구분하지 않는다. (경계값·급증 정의는 O-3 [결정 필요])
   - 근거: 프롬프트 2 B5·F5 [확인(현상)] / 경계값 [결정 필요].
   - Acceptance: 상태 경계값·기준 기간·급증 정의가 문서와 module_status에 있고 임의 확정은 `[결정 필요]`로 표시한다.
6. **B6 [Must]** THE Anomaly_Detector SHALL 이상 구간의 전력량·요금 영향을 측정된 경우에만 표시하고, 잡음 범위면 `영향 없음(잡음 범위)`라고 기록한다. (영향 정의·잡음 판정은 O-2 [결정 필요])
   - 근거: 프롬프트 2 B6·F5 [확인(현상)] / 영향 정의 [결정 필요].
   - Acceptance: 영향 계산의 입력·비교 범위와 측정 불가/잡음 판정이 기록되고 미측정 값을 숫자로 표시하지 않는다.

### 요구사항 C — 에이전트 (Agent)

### Requirement 3: artifacts 근거 기반 OpenAI API 에이전트 (C)

**User Story:** As a 사용자, I want artifacts를 근거로만 답하고 데이터에 없는 것은 없다고 말하는 에이전트, so that 수치가 신뢰할 수 있고 단정적 오진을 피한다.

#### Acceptance Criteria

1. **C1 [Must]** THE Agent SHALL OpenAI API function calling으로 구현하고, `artifacts`만 읽는 Python 함수 `get_forecast_summary(start, end)`, `get_evaluation()`, `get_module_status()`, `get_anomaly_events(module, start, end, type)`, `get_anomaly_trend(module, freq)`를 제공한다.
   - 근거: 프롬프트 2 C1·R10 [확인].
   - Acceptance: 각 함수의 입력·출력 스키마·허용 경로·오류 응답이 문서화되고 artifacts 밖 파일 접근 테스트가 통과한다.
2. **C2 [Must]** THE Agent SHALL 답변 숫자를 도구 결과에서만 가져오며, 결과에 없는 내용은 `데이터에 없음`이라고 답한다(LLM은 숫자를 계산하지 않는다).
   - 근거: R10 [확인].
   - Acceptance: 도구 출력과 답변 숫자 비교 테스트가 있고 임의 계산·추측 숫자가 없다.
3. **C3 [Must]** THE Agent SHALL 기간 브리핑(전력·요금·탄소·MAE·주의 설비·확인 항목·분석 한계), 자연어 질의응답, 경영진용 5줄 이내·에너지 관리자용·현장 담당자용 전환을 제공한다.
   - 근거: 프롬프트 2 C3 [확인].
   - Acceptance: 세 대상 형식과 브리핑 필드가 화면 또는 도구 계약에서 확인된다.
4. **C4 [Must]** THE Agent SHALL `고장`·`위험 확정` 같은 단정을 사용하지 않고 관찰된 사실·가능한 원인 후보·현장 확인 항목을 구분해 답한다.
   - 근거: R9 [확인].
   - Acceptance: 금지 표현 검사와 세 구획 출력 검사가 있다.
5. **C5 [Must]** IF OPENAI_API_KEY가 없거나 API 호출이 실패하면, THEN THE Dashboard SHALL 예외 없이 동작을 유지하고 규칙 기반 요약으로 대체하며 호출 결과를 캐시한다.
   - 근거: 프롬프트 2 C5·R11 [확인].
   - Acceptance: 키 없음·호출 실패 시 예외 없이 fallback과 캐시 동작을 테스트한다.

### 요구사항 D — Streamlit 대시보드 (Dashboard)

### Requirement 4: artifacts만 읽는 Streamlit 대시보드 (D)

**User Story:** As a 두 사용자 역할, I want 예측·이상·AI 결과를 artifacts만으로 보는 대시보드, so that 원본 없이도 결과를 확인하고 보고할 수 있다.

#### Acceptance Criteria

1. **D1 [Must]** THE Dashboard SHALL 상단에 팀명 `문학적인 기계`, 제목 `Factory Energy Copilot`, 한 줄 설명, 데이터 기준 기간을 표시한다.
   - 근거: 발주처 필수 화면 요소·프롬프트 2 D1 [확인].
   - Acceptance: 네 요소가 첫 화면 DOM/스크린샷 또는 UI 테스트에서 확인된다.
2. **D2 [Must]** THE Dashboard SHALL 예측 탭에 시간대별 요금·탄소 예측, 실제값·예측값·예측구간 그래프, 전력량/요금/탄소 MAE 카드, 베이스라인 비교표, 예측 기간 총 전력량·총 요금·총 탄소, 계산식을 표시한다.
   - 근거: 발주처 필수·프롬프트 2 D2 [확인].
   - Acceptance: 필수 시각화·카드·표·계산식의 존재를 UI 검증으로 확인한다.
3. **D3 [Must · O-5 결정됨]** THE Dashboard SHALL 이상 탐지 탭에 설비 상태표(정상/주의/이상 색), 이벤트 표, 선택 설비의 전압·역률 시계열 이상 지점, 세 방법 비교표를 표시한다. 설비는 **최대 13 상한(O-5 결정) 안에서 데이터에서 발견된 설비만** 상태표에 표시하고, 발견 수가 13과 다르면 화면에 안내를 둔다.
   - 근거: 프롬프트 2 D3 [확인(요소)] / O-5 [결정됨 — 13 상한+발견 집합].
   - Acceptance: 상태 행과 네 가지 시각 요소가 artifacts로부터 표시되고, 발견 설비 수가 표시되며 13과 다르면 안내가 보인다.
4. **D4 [Must]** THE Dashboard SHALL AI 탭에 브리핑 생성, 대상별 보고서 전환, 질의 입력을 제공하고 계산 결과와 AI 문장을 구분한다.
   - 근거: 프롬프트 2 D4 [확인].
   - Acceptance: 세 컨트롤과 두 출력 영역이 화면에서 구분된다.
5. **D5 [Should]** THE Dashboard SHALL 사이드바에 날짜 필터와 설비 필터를 둔다.
   - 근거: 발주처 권장·프롬프트 2 D5 [확인].
   - Acceptance: 두 필터가 결과 표·그래프·도구 질의 범위에 적용된다.
6. **D6 [Must]** THE Dashboard SHALL 실제값이 예측구간을 벗어난 시간과 시간당 이상 건수가 급증한 설비를 알림으로 표시한다.
   - 근거: 발주처 권장·프롬프트 2 D6 [확인].
   - Acceptance: 두 경고 조건·근거 수치·데이터 없음 상태가 구분되어 표시된다.
7. **D7 [Should · 결정 필요]** THE Dashboard SHALL PC 큰 화면 기준으로 artifacts만 읽고 첫 화면을 신속하게 표시하는 목표를 검토한다. WHERE `5초` 목표가 근거·측정 환경·측정 명령·허용 오차와 함께 승인된 경우에만, THE Dashboard SHALL 그 수치를 acceptance로 사용한다. IF 승인이 없으면, THEN THE Dashboard SHALL 측정값을 기록하되 합격/실패로 판정하지 않는다(O-6).
   - 근거: 발주처 권장·프롬프트 2 D7 [결정 필요].
   - Acceptance: 원본 없이 실행하고 측정 환경·데이터·명령·첫 화면 시간 로그를 기록하며, 승인된 목표가 없으면 `미승인/측정 필요`로 표시한다.

---

## 성공 기준 (S1~S7)

### Requirement 5: 프로젝트 성공 기준 (S)

**User Story:** As a 발주처, I want 측정 가능한 성공 기준, so that 완료 여부를 테스트·산출물로 판정할 수 있다.

#### Acceptance Criteria

1. **S1 [Must]** THE System SHALL `predictions.csv`가 505행이며 Validation_CSV id와 1:1 일치하게 한다. (연결: A5)
   - Acceptance: 행 수·id 1:1 일치 테스트 명령·출력에 연결된다.
2. **S2 [Must]** THE System SHALL 학습 코드가 Validation_CSV를 읽지 않음을 테스트로 확인한다. (연결: NFR-4)
   - Acceptance: 경로 참조 테스트가 통과한다.
3. **S3 [Must]** THE Dashboard SHALL MAE와 베이스라인 비교표를 화면에 표시한다. (연결: A2, A6, D2)
   - Acceptance: UI 검증에서 MAE·비교표 존재가 확인된다.
4. **S4 [Must]** THE System SHALL 규칙 기반 이상 행을 모델이 얼마나 찾는지 수치(프록시)로 제시한다. (연결: B3)
   - Acceptance: 프록시 수치와 분모·한계가 표에 있다.
5. **S5 [Must]** THE Agent SHALL 답변 숫자가 artifacts 값과 일치하게 한다. (연결: C2)
   - Acceptance: 도구 출력과 답변 비교 테스트가 통과한다.
6. **S6 [Must]** THE Dashboard SHALL 가이드에서 확인된 필수 화면 요소 4가지를 모두 포함한다. (연결: D1, D2, D3, D4)
   - Acceptance: UI 검증에서 네 요소 존재가 확인된다. (필수 4요소의 PDF 가이드 근거는 O-1 확인 전까지 발주처 요구사항 요약 기준)
7. **S7 [Must]** THE Dashboard SHALL 원본 없이 artifacts만으로 실행된다. (연결: REQ-DATA, NFR-1)
   - Acceptance: 원본 미존재 환경에서 실행 성공을 테스트한다.
- **공통 Acceptance:** S1~S7을 각각 테스트 명령·출력·산출물 경로에 연결하고 추적성 표에서 누락을 찾는다.

---

## 추적성 표

열: `요구사항 ID | 근거/우선순위 | 설계 결정/계약 | 작업 ID | acceptance/test | 산출물/근거`
(Requirements 단계이므로 설계·작업 칸은 `승인 후 작성` 또는 `미정`으로 둔다.)

| 요구사항 ID | 근거/우선순위 | 설계 결정/계약 | 작업 ID | acceptance/test | 산출물/근거 |
|---|---|---|---|---|---|
| REQ-PROB | 프롬프트1 목적 / Must | 승인 후 작성 | 미정 | 문제 정의·근거 존재(문서 리뷰) | requirements.md |
| REQ-GOAL | 발주처 1~4 / Must | 승인 후 작성 | 미정 | 여섯 목표 정의 존재 | requirements.md |
| REQ-USER | 프롬프트2 1) / Must | 승인 후 작성 | 미정 | 두 역할 정의 존재 | requirements.md |
| REQ-SCOPE | 프롬프트1 범위 / Must | 승인 후 작성 | 미정 | 포함 목록 ↔ A/B/C/D 연결 | 추적성 표 |
| REQ-NONSCOPE | 프롬프트1 비목표 / Must | 승인 후 작성 | 미정 | 비목표 6개 + PDF는 REQ-OPEN | 문서 리뷰 |
| REQ-SCENARIO | 프롬프트2 2) / Must | 승인 후 작성 | 미정 | 세 시나리오 ↔ 기능 연결 | 추적성 표 |
| REQ-DATA | 프롬프트1 R1~R16·작업1 / Must | 승인 후 작성 | 미정 | 경로·필드·행수·읽기전용 기록 | 작업1 보고 |
| REQ-NFR | 프롬프트1 R1~R16 / Must(일부 Should) | 승인 후 작성 | 미정 | NFR-1~8 acceptance | 로그·config |
| REQ-RISK | 프롬프트2 5) / Must | 승인 후 작성 | 미정 | 위험 8개 영향·완화·검증 | 문서 리뷰 |
| REQ-OPEN | 프롬프트2 5)·P2-001 / Must | 승인 후 작성 | 미정 | O-1~O-10 질문·허용 동작 | 문서 리뷰 |
| A1 | R4·작업1 / Must | 승인 후 작성 | 미정 | n=720만 사용·제외 건수 기록 | hourly_from_full.parquet(예정) |
| A2 | 프롬프트2 A2 / Must | 승인 후 작성 | 미정 | 세 베이스라인 MAE 비교표 | evaluation(예정) |
| A3 | 프롬프트2 A3 / Must | 승인 후 작성 | 미정 | 금지 필드 부재·경계 검사 | 학습 설정(예정) |
| A4 | 프롬프트2 A4 / Must | 승인 후 작성 | 미정 | 시간순 검증·재학습 기록 | 로그(예정) |
| A5 | 발주처1·작업1 / Must | 승인 후 작성 | 미정 | 505행·id 1:1 일치 | predictions.csv(예정) |
| A6 | R7·R8 / Must | 승인 후 작성 | 미정 | MAE 504/505·총합오차·비교표 | evaluation.json(예정) |
| A7 | 프롬프트2 A7 / Should·결정필요 | 승인 후 작성 | 미정 | 근거·승인 또는 미승인 표시, 보정 코드 부재 | O-10 |
| A8 | 프롬프트2 A8 / Should | 승인 후 작성 | 미정 | 하한≤예측≤상한 검사 | predictions.csv(예정)·O-7 |
| B1 | 프롬프트2 B1·F5 / Must | 승인 후 작성 | 미정 | 5초 행 단위·hourly는 집계 | anomaly_events(예정) |
| B2 | 프롬프트2 B2·F5 / Must·결정필요 | 승인 후 작성 | 미정 | 세 방법·판정식 승인 전 [결정 필요] | O-9 |
| B3 | 프롬프트2 B3 / Must | 승인 후 작성 | 미정 | 프록시 명명·독립 라벨 승인 | O-4 |
| B4 | 프롬프트2 B4 / Must | 승인 후 작성 | 미정 | 세 산출물 필드·경로 | anomaly_*.parquet·module_status.csv(예정) |
| B5 | 프롬프트2 B5·F5 / Must | 승인 후 작성 | 미정 | 상태 경계·급증 정의 | module_status.csv(예정)·O-3 |
| B6 | 프롬프트2 B6 / Must | 승인 후 작성 | 미정 | 측정/잡음 판정 기록 | O-2 |
| C1 | 프롬프트2 C1·R10 / Must | 승인 후 작성 | 미정 | 함수 스키마·artifacts-only 테스트 | 도구 계약(예정) |
| C2 | R10 / Must | 승인 후 작성 | 미정 | 숫자 출처 비교 테스트 | 테스트(예정) |
| C3 | 프롬프트2 C3 / Must | 승인 후 작성 | 미정 | 세 대상 형식·브리핑 필드 | UI/도구(예정) |
| C4 | R9 / Must | 승인 후 작성 | 미정 | 금지 표현·세 구획 검사 | 테스트(예정) |
| C5 | 프롬프트2 C5·R11 / Must | 승인 후 작성 | 미정 | 키 없음·실패 fallback·캐시 | 테스트(예정) |
| D1 | 발주처 필수 / Must | 승인 후 작성 | 미정 | 네 요소 표시 | UI(예정) |
| D2 | 발주처 필수 / Must | 승인 후 작성 | 미정 | 시각화·카드·표·계산식 | UI(예정) |
| D3 | 프롬프트2 D3 / Must·결정필요 | 승인 후 작성 | 미정 | 상태표·네 요소, 설비수 질문 | O-5 |
| D4 | 프롬프트2 D4 / Must | 승인 후 작성 | 미정 | 세 컨트롤·두 출력 구분 | UI(예정) |
| D5 | 발주처 권장 / Should | 승인 후 작성 | 미정 | 두 필터 적용 | UI(예정) |
| D6 | 발주처 권장 / Must | 승인 후 작성 | 미정 | 두 경고·근거·데이터 없음 | UI(예정) |
| D7 | 발주처 권장 / Should·결정필요 | 승인 후 작성 | 미정 | 측정 로그, 미승인 시 미판정 | O-6 |
| S1 | 발주처 / Must | 승인 후 작성 | 미정 | 505행·id 일치 테스트 | predictions.csv(예정) |
| S2 | R5·R12 / Must | 승인 후 작성 | 미정 | 정답 경로 미참조 테스트 | 테스트(예정) |
| S3 | 발주처 / Must | 승인 후 작성 | 미정 | MAE·비교표 UI 검증 | UI(예정) |
| S4 | 프롬프트2 S4 / Must | 승인 후 작성 | 미정 | 프록시 수치 제시 | 비교표(예정) |
| S5 | R10 / Must | 승인 후 작성 | 미정 | 답변↔artifacts 일치 테스트 | 테스트(예정) |
| S6 | 발주처 필수 / Must | 승인 후 작성 | 미정 | 필수 4요소 UI 검증 | UI(예정)·O-1 |
| S7 | R2 / Must | 승인 후 작성 | 미정 | 원본 없이 실행 테스트 | 테스트(예정) |

**추적성 종료 점검:** 위 표에 A1~A8, B1~B6, C1~C5, D1~D7, S1~S7과 REQ-PROB/GOAL/USER/SCOPE/NONSCOPE/SCENARIO/DATA/NFR/RISK/OPEN이 각각 한 행 이상 포함되어 있으며, 연결되지 않은 요구사항은 없다. 설계·작업 칸은 승인 후 채운다.

---

## 단계 보고 (Requirements)

- **생성 경로:** `./.kiro/specs/factory-energy-copilot/requirements.md`, `./.kiro/specs/factory-energy-copilot/.config.kiro`
- **수정 경로:** 없음(원본 CSV·PDF·기타 파일 미수정).
- **검증 명령과 결과:**
  - `list_directory`로 `data/` 확인 — 종료 코드 없음(툴 호출). 판정 근거: `rtu_data_full_훈련용.csv`, `rtu_ground_truth_may_검증용(5월).csv` 두 파일 존재 확인. 성공.
  - `.kiro/specs/` 존재 여부 — 세션 시작 파일 트리에 없었고 `fs_write`가 디렉터리를 새로 생성함. 성공.
  - 그 외 분석·학습·테스트 명령은 이 단계에서 실행하지 않음 → **미검증**(설계/작업/구현 승인 후 수행).
- **원본 불변:** 이 단계에서 두 CSV를 읽거나 수정하지 않았다. 파일 목록 조회만 수행했다. (프롬프트 1 작업 1에서 기록된 Length·LastWriteTime 기준값은 그 보고에 있으며, 본 단계에서 재측정하지 않았으므로 재측정 결과는 **미검증**으로 둔다.)
- **Git:** 이 환경의 Git 작업 트리 여부는 본 단계에서 재확인하지 않았다. 저장소가 없으면 `git init`·커밋을 하지 않는다(프롬프트 1 기록 참조). → Git 증거는 **미검증**(검토 finding P2-002에 따라 미검증으로 처리).
- **남은 위험·질문:** REQ-OPEN의 O-1~O-10이 미해소 상태다. 특히 PDF 3개 본문 미확인(O-1), B2 판정식(O-9), A7 임계값(O-10), 설비 13개(O-5), D7 5초(O-6), 예측구간 분위수(O-7)는 사용자 승인 전 확정하지 않는다.

## 다음 단계

이 문서는 Requirements 단계 산출물이다. 사용자 승인을 받은 뒤에만 `design.md`를 작성한다. 승인 전에는 Design·Tasks·구현(애플리케이션/분석/학습/테스트 코드)·분석 산출물을 작성하거나 실행하지 않는다.
