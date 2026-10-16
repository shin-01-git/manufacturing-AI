"""Factory Energy Copilot - 중앙 설정(config)

이 파일은 프로젝트의 단일 설정 출처(single source of truth)다.
환산 계수, 이상탐지 임계값, 예측구간 분위수, 설비 상한, 경로, 역할 enum,
환경변수 로더를 한곳에 모은다. 다른 모듈·테스트는 반드시 이 상수를 참조한다.

사실/결정 근거:
- 환산 계수(NFR-8, R8): may_bill = hourly_pow x 180, may_carbon = hourly_pow x 0.424
- O-9(결정): 이상탐지 규칙은 (a) 3상 동시 판정, 임계값 전압 210V / 역률 85%
- O-7(잠정): 예측구간 분위수 하한 0.05 / 상한 0.95 ("임시/잠정")
- O-5(결정): 설비 수 최대 13 상한 + 발견된 설비만 사용
- NFR-6: OPENAI_API_KEY / OPENAI_MODEL 은 환경변수에서만 읽고 값을 로그에 남기지 않는다
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# 경로 (프로젝트 루트 기준)
# ---------------------------------------------------------------------------
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = PROJECT_ROOT / "data"
ARTIFACTS_DIR: Path = PROJECT_ROOT / "artifacts"

# 원본 데이터 경로. 학습 원본과 검증 정답을 분리한다(NFR-4 누수 방지).
TRAINING_CSV: Path = DATA_DIR / "rtu_data_full_훈련용.csv"
VALIDATION_CSV: Path = DATA_DIR / "rtu_ground_truth_may_검증용(5월).csv"

# 주요 산출물 경로(서비스가 읽는 공유 artifact)
HOURLY_FROM_FULL: Path = ARTIFACTS_DIR / "hourly_from_full.parquet"
PREDICTIONS_CSV: Path = ARTIFACTS_DIR / "predictions.csv"
EVALUATION_JSON: Path = ARTIFACTS_DIR / "evaluation.json"
ANOMALY_EVENTS: Path = ARTIFACTS_DIR / "anomaly_events.parquet"
ANOMALY_HOURLY: Path = ARTIFACTS_DIR / "anomaly_hourly.parquet"
MODULE_STATUS_CSV: Path = ARTIFACTS_DIR / "module_status.csv"

# ---------------------------------------------------------------------------
# 환산 계수 (NFR-8, R8) — 단일 출처
# 정답 파일과 동일 척도. 1000으로 나누지 않는다.
# ---------------------------------------------------------------------------
BILL_RATE: float = 180.0        # 원/kWh
CARBON_RATE: float = 0.424      # kgCO2/kWh

# ---------------------------------------------------------------------------
# 집계 규칙 (A1, R4)
# 시간별 전력량 = sum(activePower * 5 / 3600)
# ---------------------------------------------------------------------------
SECONDS_PER_SAMPLE: int = 5          # 5초 간격
SAMPLES_PER_HOUR_COMPLETE: int = 720  # n=720 완전 시간 기준
TIME_BASE: str = "localtime"          # KST 기준. timestamp 임의 사용 금지(NFR-3)

# ---------------------------------------------------------------------------
# 설비 상한 (O-5 결정) — 최대 13 상한 + 발견 집합
# ---------------------------------------------------------------------------
MODULE_COUNT_EXPECTED: int = 13  # 기대/상한값. 하드코딩 반복 금지, 발견 집합만 처리.

# ---------------------------------------------------------------------------
# 이상탐지 규칙 (O-9 결정: (a) 3상 동시 판정)
#   (voltageR<210 AND voltageS<210 AND voltageT<210)
#   OR (powerFactorR<85 AND powerFactorS<85 AND powerFactorT<85)
# ---------------------------------------------------------------------------
VOLTAGE_THRESHOLD: float = 210.0       # V. 세 상 모두 미만일 때 전압 이상 후보
POWER_FACTOR_THRESHOLD: float = 85.0   # %. 세 상 모두 미만일 때 역률 이상 후보
ANOMALY_RULE_MODE: str = "three_phase_simultaneous"  # (a) 3상 동시 (O-9)

# ---------------------------------------------------------------------------
# 예측구간 분위수 (O-7 잠정 결정) — "임시/잠정" 표시 필요
# ---------------------------------------------------------------------------
PRED_INTERVAL_LOW_Q: float = 0.05   # 잠정 하한 분위수
PRED_INTERVAL_HIGH_Q: float = 0.95  # 잠정 상한 분위수
PRED_INTERVAL_PROVISIONAL: bool = True  # True면 화면/로그에 "임시/잠정" 표시

# ---------------------------------------------------------------------------
# MAE 해석 참고값 (O-10 결정) — 참고용만. 합격/불합격 기준으로 사용 금지.
# ---------------------------------------------------------------------------
MAE_REFERENCE_UPPER: float = 89.0  # 참고용(학습 평균 반복)
MAE_REFERENCE_LOWER: float = 73.0  # 참고용(정답 평균 하한)
MAE_USE_AS_THRESHOLD: bool = False  # 반드시 False — 경고/합격 판정 금지

# ---------------------------------------------------------------------------
# 재현성 (NFR-7)
# ---------------------------------------------------------------------------
RANDOM_SEED: int = 42

# ---------------------------------------------------------------------------
# 역할 페르소나 (REQ-USER / 팀장 피드백 "역할명으로만")
# 개인 이름을 두지 않는다. 역할 enum만 분기 기준으로 사용한다.
# ---------------------------------------------------------------------------
ROLES: dict[str, str] = {
    "facility": "설비 관리 담당자",
    "energy": "에너지 관리 담당자",
    "executive": "경영진",
}
DEFAULT_ROLE: str = "facility"

# 팀/프로젝트 표시 정보 (D1)
TEAM_NAME: str = "문학적인 기계"
PROJECT_TITLE: str = "Factory Energy Copilot"
PROJECT_TAGLINE: str = "공장 전력량 예측·요금/탄소 환산·이상 구간 탐지 근거 기반 대시보드"

# ---------------------------------------------------------------------------
# 대시보드 UI 상수 (steering dashboard-ui A-1, A-2)
# ---------------------------------------------------------------------------
# activePower 단위가 [미확인]이므로 "kWh/원"으로 단정하지 않는다. 요금·탄소는
# 정답 파일과 동일 척도(NFR-8). 팀이 단위를 확정하면 이 한 곳만 바꾼다.
UNIT_LABELS: dict[str, str] = {
    "pow": "kWh",
    "bill": "원",
    "carbon": "kgCO₂",
}
# 교안(session-12 '단위와 표기'): 데이터 전력량은 Wh → 1,000으로 나눠 kWh로 바꾼 뒤
# 요금(180원/kWh)·탄소(0.424kgCO₂/kWh)를 계산한다. artifacts는 Wh 척도 그대로 두고
# 화면·에이전트로 나가는 값만 이 계수로 환산한다(artifacts 불변, 평가 MAE는 Wh 유지).
WH_PER_KWH: float = 1000.0
UNIT_FOOTNOTE: str = "ⓘ 데이터 전력량(Wh) ÷ 1,000 = kWh · 요금 = kWh × 180원 · 탄소 = kWh × 0.424kgCO₂"

# O-3 상태 경계값 (확정). 설비 상태 = 규칙 이상 비율 + 이상 6종(패턴) 탐지 건수
#   10/16 멘토 피드백(거짓 양성 과다)으로 상태는 '급격한 변화' 탐지 건수만 본다.
#   5초 단위 기준 미달(규칙 이벤트)은 거의 매시간 생기는 순간 저하라 상태·경고에서 제외(참고 표시).
#   이상치 = 시간 급변 + C파트 5초 고전력(2.9σ). 이상: 이상치 ≥ 5건 · 주의: 3–4건 · 정상: 0–2건 (설비당 평균 약 4건)
STATUS_THRESHOLDS_APPROVED: bool = True
STATUS_ALERT_PATTERN_COUNT: int = 5
STATUS_WATCH_PATTERN_COUNT: int = 3
# 예상 탄소비용 기본 단가(가정): 국내 배출권 KAU 시세 10,500원/tCO2 (SK증권 탄소배출권 리포트, 2025-10-01 기준).
# 설정 화면에서 바꿀 수 있다. 실제 부과액이 아니라 '배출권으로 환산한 비용' 가정치.
DEFAULT_CARBON_PRICE: float = 10500.0
CARBON_PRICE_SOURCE: str = "KAU 배출권 10,500원/tCO₂ (2025-10-01 시세 가정)"
CPART_Z_OPTIONS: tuple = (2.9, 2.92, 2.88)  # C파트(신승현) 합의: 2.9 또는 2.92 (3.0 이상은 0건)
CPART_Z_DEFAULT: float = 2.9
DEFAULT_ANOMALY_K: float = 3.5  # 급변 탐지 민감도: 전체 평균 ±kσ 밖 + 직전 시간 대비 ±kσ 급변

# 분석 기준 기간(검증 모드) 표시용. 예측 artifacts의 id 범위와 일치해야 한다.
ANALYSIS_PERIOD_START: str = "2025-05-10"
ANALYSIS_PERIOD_END: str = "2025-05-31"

# 데이터 출처 라벨(사이드바 푸터)
DATA_SOURCE_LABEL: str = "데이터 출처: artifacts (batch)"

# ---------------------------------------------------------------------------
# UI v3: 이상 유형 표시 이름 매핑 / 화면 경고 기본 기준 / 야간 시간대
# - 매핑은 표시 이름일 뿐이다. anomaly_events.type 에 실제로 있는 값만 칩으로 만든다.
# - 경고 기준은 "화면 표시 기준"만 바꾼다(배치 탐지 결과는 그대로).
# ---------------------------------------------------------------------------
ANOMALY_TYPE_LABELS: dict[str, str] = {
    "voltage_low_3phase": "3상 저전압",
    "power_factor_low_3phase": "3상 저역률",
    "load_imbalance": "부하 불균형",
    "reactive_power_surge": "무효전력 급증",
    "power_factor_drop": "역률 급감",
    "abnormal_pattern": "비정상 패턴",
    "night_operation": "야간 이상가동",
    "current_only_increase": "전류만 증가",
}
DEFAULT_BILL_SURGE_PCT: float = 20.0       # 예측 요금이 일 평균 대비 +20% 이상이면 경고
DEFAULT_ANOMALY_SURGE_FACTOR: float = 3.0  # 평소 시간당 건수의 3배 이상이면 급증
DEFAULT_NIGHT_START_HOUR: int = 22
DEFAULT_NIGHT_END_HOUR: int = 6


# ---------------------------------------------------------------------------
# 환경변수 로더 (NFR-6) — 값/부분값을 로그에 남기지 않는다.
# ---------------------------------------------------------------------------
def _load_dotenv() -> None:
    """프로젝트 폴더(또는 레포 루트)의 .env에서 OPENAI_API_KEY 등을 읽어 환경변수에 넣는다.

    이미 설정된 환경변수는 덮어쓰지 않는다. .env는 .gitignore 대상(커밋 금지). 값은 로그에 남기지 않는다.
    """

    for path in (PROJECT_ROOT / ".env", PROJECT_ROOT.parent.parent / ".env"):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip().removeprefix("export ").strip()
            v = v.strip().strip('"').strip("'")
            if k and v and not os.environ.get(k):
                os.environ[k] = v


_load_dotenv()


def get_openai_api_key() -> str | None:
    """OPENAI_API_KEY를 환경변수에서만 읽는다. 값을 로그에 남기지 않는다.

    Returns:
        키 문자열 또는 없으면 None.
    """
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    return _secret("OPENAI_API_KEY")


def _secret(name: str) -> str | None:
    """Streamlit Cloud의 Secrets(st.secrets)에서 값만 읽는다. 없거나 Streamlit 밖이면 None."""
    try:
        import streamlit as st

        value = st.secrets.get(name)  # secrets.toml이 없으면 예외 → None
        return str(value) if value else None
    except Exception:  # noqa: BLE001
        return None


# Streamlit Community Cloud는 저장소를 /mount/src 아래에 받는다. 그곳에서는 접속자 모두가 같은
# 파일을 쓰므로 설정 파일 저장을 끄고(접속자별 세션 값만 사용) 화면 문구도 바꾼다.
IS_CLOUD: bool = PROJECT_ROOT.as_posix().startswith("/mount/src") or os.environ.get("FEC_CLOUD") == "1"


def get_openai_model() -> str:
    """OPENAI_MODEL을 환경변수에서 읽고, 없으면 기본값을 쓴다."""
    return os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


def has_openai_key() -> bool:
    """키 존재 여부만 반환한다(값 노출 없음). fallback 분기용."""
    key = get_openai_api_key()
    return bool(key and key.strip())
