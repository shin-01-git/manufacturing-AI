# Factory Energy Copilot — D 파트

맞춤 정보 에이전트와 공장 에너지 대시보드 구현 파트입니다.

## 담당 범위

- 맞춤 정보 에이전트: 경영진 5줄 / 에너지 관리 담당자 / 설비 관리 담당자 브리핑
- 공장 전체 전력량 예측
- 요금·탄소 환산 및 설정값 기반 예상 탄소비용
- 이상 구간·설비별 이상 비율·이상 유형·탐지 시간대
- 예측구간 이탈·요금 급증·이상 급증 경고
- Streamlit 5페이지 대시보드

## 실행

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run src/app/app.py
```

원본 CSV는 저장소에 포함하지 않습니다. 대시보드는 `artifacts/`의 배치 산출물만 읽습니다.

## Streamlit Community Cloud

- Main file path: `D-part/factory-energy-copilot/src/app/app.py`
- Python: 3.11
- API 키가 없으면 규칙 기반 대체 모드로 실행됩니다.
- API 키를 사용할 때는 Cloud Secrets에만 입력합니다.

```toml
OPENAI_API_KEY = "..."
OPENAI_MODEL = "gpt-4o-mini"
```

`.env`, 원본 CSV, 검증 정답, `anomaly_events_allmethods.parquet`는 커밋하지 않습니다.

## 검증

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

UI 실행 경로는 `src/app/app.py`이며, 숫자는 `src/app/ui/fmt.py` 공용 포매터를 사용합니다.
