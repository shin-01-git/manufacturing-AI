# Factory Energy Copilot — D 파트 (맞춤 정보 · AI 에이전트)

> 2팀 「문학적인 기계」 / 담당 파트: D (맞춤 정보 화면 + AI 점검 리포트)

공장 13개 설비의 시간별 전력 데이터를 기반으로 사용량·비용·탄소배출량을
계산하고, 설비별 기준선에서 벗어난 이상 사용을 찾아 근거 중심의 AI 리포트를
제공한다. **수치 계산은 코드(pandas)가, 설명·요약은 AI가** 담당한다.

## 파일 구성
| 파일 | 역할 |
|---|---|
| `d_preprocess.py` | 원시 데이터(4.57GB)를 청크로 읽어 집계본(`d_aggregated.csv`) 생성 |
| `d_queries.py` | 데이터 조회·계산 함수 모음 (전력량·요금·탄소·이상치) |
| `d_roles.py` | 역할 정의 (설비 담당 / 에너지 담당) + 요금·탄소 환산 계수 |
| `d_ai.py` | OpenAI function calling. AI가 조회 함수를 골라 호출하고 설명 생성 |
| `d_screens.py` | Streamlit 화면 (맞춤 정보 탭 + AI 상담 탭). **실행 진입점** |
| `.kiro/specs/` | 요구사항·설계 문서 |

## 실행 방법

### 1) 필요한 라이브러리 설치
```
pip install streamlit pandas plotly openai
```

### 2) OpenAI API 키 설정
`.streamlit/secrets.toml.example` 파일을 복사해서 `secrets.toml` 로 이름을 바꾸고,
안에 본인의 OpenAI 키를 넣는다. (키 발급: https://platform.openai.com/api-keys)
> secrets.toml 은 깃허브에 올라가지 않는다(.gitignore 처리). 각자 본인 키를 넣어야 한다.

### 3) 데이터 준비
- 집계본 `d_aggregated.csv` 가 있으면 바로 실행 가능.
- 없으면, 원시 데이터 `traing-dataset/rtu_data_full.csv` 를 두고 아래를 먼저 실행:
```
python d_preprocess.py
```
> 원시 데이터(약 4.57GB)는 용량이 커서 깃허브에 올리지 않는다. 팀 내에서 별도로 공유한다.

### 4) 앱 실행
```
streamlit run d_screens.py
```
브라우저에서 http://localhost:8501 로 열린다.

## 핵심 계산식 (검증됨)
- kWh 환산: `activePower / 720` (= activePower × 5초/3600초)
- 요금(원) = kWh 합계 × 180
- 탄소(kgCO₂) = kWh 합계 × 0.424

## 주의
- 원본 데이터는 수정하지 않는다 (읽기 전용).
- AI는 숫자를 지어내지 않는다. 조회 함수가 계산한 값만 사용한다.
- 현재 데이터만으로 설비 '고장'을 확정하지 않는다. '이상 징후'와 '점검 우선순위'를 제공한다.
