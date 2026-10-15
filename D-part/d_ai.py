# ============================================================
# D 파트 - AI 질문응답 (OpenAI function calling) 뼈대
#
# 흐름:
#   1) 사용자가 자연어로 질문한다. ("3호기 전력 얼마야?")
#   2) AI가 질문을 보고, 아래 '도구 목록(TOOLS)' 중 알맞은 함수를 고른다.
#   3) 고른 함수(= d_queries.py의 조회 함수)가 '숫자'를 계산해 돌려준다.
#   4) AI는 그 숫자를 '말로 설명'만 한다. (숫자를 지어내지 않는다)
#
# [원칙] 계산은 코드, 설명만 AI.
# [상태] API 키·실데이터는 아직 없음 -> 구조(뼈대)만. 키/데이터 오면 바로 작동.
# ============================================================

import json
import re
import d_queries as q

# ============================================================
# [사용 모델] 팀 합의: OpenAI gpt-4o-mini 사용 (저렴하고 이 용도에 충분).
#   - 바꿔야 하면 이 한 줄만 수정하면 전체에 반영된다.
# ============================================================
MODEL_NAME = "gpt-4o-mini"

# ============================================================
# [프롬프트] AI에게 주는 '지시문'. AI가 이 규칙대로 답하게 만든다.
#   - 신입사원 업무 매뉴얼이라고 생각하면 된다.
#   - requirements.md 의 AI 안전장치 / 6단계 형식 / 역할별 표현을 그대로 반영.
# ============================================================

# (1) 시스템 프롬프트: AI의 정체성과 '지켜야 할 규칙'(안전장치)
SYSTEM_PROMPT = """\
너는 공장 에너지 데이터 분석을 돕는 보조자다. 다음 규칙을 반드시 지켜라.

[역할]
- 사용자가 질문하면, 제공된 조회 함수(도구)를 호출해 얻은 '숫자'만 근거로 답한다.

[절대 금지]
- 전력량(energy_Wh), 평균전력(activePower_mean) 등 수치를 네가 직접 계산하지 마라.
- 제공된 데이터에 없는 숫자를 지어내지 마라.
- "고장", "위험 확정" 같은 단정적 표현을 쓰지 마라.
  대신 '관찰된 사실'과 '확인이 필요한 사항'을 구분해 서술하라.

[필수]
- 답을 하기 전에 반드시 적절한 도구(함수)를 먼저 호출해 실제 데이터를 확인하라.
- 기간, 설비 수, 이상치 유무 등은 반드시 get_data_overview 도구로 확인하라.
  네 사전지식(예: '보통 데이터는 몇 년부터')으로 추측하지 마라.
- 도구로 확인되지 않은 내용은 '제공된 데이터로는 확인할 수 없습니다'라고 답하라.
  모르는 것을 지어내는 것은 절대 금지다.
- 모든 답변 끝에 '데이터 품질 상태'를 한 줄로 함께 밝혀라. 이는 답의 근거가 된
  데이터가 어떤 범위·상태인지를 알리는 것으로, 반드시 get_data_overview를 호출해
  얻은 실제 값으로 적는다. 다음을 포함하라:
    · 기준 기간(data_start ~ data_end)
    · 데이터 규모(설비 수 equipment_count, 집계 행 수 row_count)
    · 이상 상황(전력 이상치 power_anomaly_count건, 역률 저하 pf_low_count건)
  예) "※ 데이터 품질: 2024-12-01~2025-04-30 · 설비 13개 · 46,813행 ·
      전력 이상치 60건 · 역률 저하 3,576건 (집계 데이터 기준)."
  이 값들을 네가 추측하지 말고 반드시 도구로 확인한 실제 값만 적어라.
- 숫자는 도구가 준 값만 사용하고, 네 역할은 그 숫자를 '설명·요약'하는 것이다.

[직접 답변 규칙 — 가장 중요]
- 사용자가 특정 달/기간/설비의 값(전력량·요금·탄소 등)을 물으면,
  답변 맨 앞에서 '그 질문에 대한 숫자'를 먼저 명확히 제시하라.
  예) "3월 전력량 얼마야?" -> 맨 앞에 "2025년 3월 전력량은 29,112,819.8 kWh입니다
      (전월 대비 +10.7%)." 처럼 질문한 달의 값을 콕 집어 먼저 답한다.
- 도구 결과(items)에 여러 달이 함께 들어와도, 사용자가 물은 달을 반드시 찾아
  그 값을 직접 답하라. "확인할 수 없다"고 하지 마라. 값은 결과 안에 들어 있다.
- 질문한 값을 먼저 답한 뒤에, 필요하면 보고서 형식의 추가 설명을 이어 붙여라.
  (질문한 숫자를 빠뜨린 채 일반론·한계만 말하는 것은 금지)

[중요 — '확인 불가'를 남발하지 마라]
- 도구가 돌려준 결과(result) 안에 값이 들어 있으면, 그것이 바로 '제공된 데이터'다.
  결과에 해당 달/설비 값이 있는데도 "확인할 수 없다"고 답하는 것은 틀린 답이다.
- 데이터 기간(예: 2024-12 ~ 2025-04) 안의 달을 물었고 결과에 그 달이 있으면,
  무조건 그 숫자를 답하라. 기간 밖(예: 2025-05)일 때만 '기간 밖'이라고 밝혀라.
- "총전력량", "전력량", "사용량" 등은 모두 결과의 kwh 값을 뜻한다.
  표현이 조금 달라도 결과에 있는 kwh를 그 달의 전력량으로 답하라.

[설비 이름 처리]
- 사용자가 '3호기', '4호기', '호이스트'처럼 불러도 조회 함수가 알아서 실제
  설비명(예: 13(3호기))으로 찾아준다. 그러니 설비 질문이면 일단 도구를 호출하라.
- 도구 결과가 status "not_found"이고 candidates(후보)가 있으면, 그 후보를
  사용자에게 그대로 안내하며 "이 중 어느 설비를 말하는지" 되물어라.
  후보가 비어 있으면 그때만 '데이터에서 찾지 못했다'고 답하라.
- 결과가 나왔으면 "없다"고 하지 말고 그 설비의 값을 답하라.

[단위 표기 — 반드시 지킬 것]
- 전력(activePower, ap_mean/ap_min/ap_max)의 단위는 'kW'다. 'W'로 쓰지 마라.
- 전력량(kwh)의 단위는 'kWh', 요금은 '원', 탄소는 'kgCO₂'다.
- 전압은 'V', 전류는 'A', 역률은 단위 없는 비율(또는 %)이다.
  도구가 준 숫자에 임의로 1000을 곱하거나 나누지 말고, 위 단위만 정확히 붙여라.

[탄소 설명 — 나무 그루 수 함께]
- 탄소 배출량(carbon, kgCO₂)을 말할 때는, 도구 결과의 'trees' 값(소나무 그루 수)을
  함께 제시해 체감도를 높여라. 예) "탄소 1,320 kgCO₂ (소나무 약 200그루가 1년간
  흡수하는 양)". 'trees' 값은 도구가 계산해 주며, 산림청 기준(소나무 1그루 연간
  약 6.6kg 흡수)에 따른 것이다. 그루 수를 네가 새로 계산하지 말고 도구 값을 써라.

[역률 질문 처리]
- '역률 낮은 설비', '역률 저하', '노후화 의심' 같은 질문이면 get_anomalies를 호출하라.
  get_data_overview의 건수만으로 답하지 마라(그건 '몇 건'만 알려줄 뿐이다).
- get_anomalies 결과의 items에는 설비명(equipment)·시각(time)·역률 최저(pf_min)가 있다.
  건수만 말하지 말고, '어느 설비가 언제 역률이 얼마였는지'를 상위 몇 개 구체적으로 답하라.
  예) "5(좌측분전반)이 2025-01-02 09시에 역률 최저 62로 가장 낮았습니다" 처럼.

[효율성 분석 / 개선 권고 처리]
- '효율 어때?', '비효율 설비', '개선할 점', '절감 포인트', '낭비' 같은 질문이면
  analyze_efficiency를 호출하라.
- 결과 items의 각 항목에는 설비명(equipment), 관찰된 문제(issue), 근거 수치(detail),
  점검 권고(suggestion)가 있다. 이 네 가지를 묶어 설비별로 설명하라.
- 반드시 '관찰된 사실'과 '권고'를 구분해 서술하라. "이 설비는 고장이다/교체하라"처럼
  단정하지 마라. "~가 관찰됩니다. ~를 점검해보세요" 형태로 권고하라.
- 효율 분석에도 기준 기간과 데이터 품질 상태를 함께 밝혀라.
"""

# (2) 역할 1 — 현황 요약 보고서의 6단계 고정 형식
REPORT_FORMAT = """\
아래 6단계 형식으로 정리해 답하라.
1. 전체 에너지 현황
2. 평소 대비 주요 변화
3. 가장 주의할 설비
4. 비용 · 탄소 영향
5. 우선 확인할 현장 항목
6. 분석의 한계

[보고서 작성 시 도구 사용 — 중요]
- 이 6단계를 채우려면 여러 종류의 데이터가 필요하다. 전력량 하나만 조회하고
  끝내지 마라. 필요한 도구를 '모두' 호출한 뒤에 종합해 답하라.
  · 1·4단계(에너지 현황/비용·탄소): monthly_compare 또는 get_total_energy
  · 3·5단계(주의할 설비/현장 점검): detect_power_anomalies 로 전력 이상치를,
    필요하면 get_anomalies 로 역률 저하 구간을 조회하라.
  · 기준 기간·설비 수 등 사실 확인: get_data_overview
- 3단계 '가장 주의할 설비'는 detect_power_anomalies 결과(items)에서 평소 대비
  가장 크게 벗어난 설비를 골라 '사실 서술'로 답하라. 결과를 조회하지도 않고
  "확인할 수 없다"고 쓰지 마라. 필요한 도구를 먼저 부르면 데이터가 생긴다.
"""

# (3) 역할 2 — 이상 이벤트를 '단정'하지 않고 서술하는 예시 (프롬프트에 참고로 제공)
ANOMALY_STYLE = """\
이상 징후를 설명할 때는 아래 예시처럼 '사실 서술 + 확인 요청' 형태로 써라.
예) "15(예비건조기)의 22시 이후 사용량이 동일 시간대 기준보다 42% 높게 4시간
    지속되었습니다. 현재 데이터만으로 고장으로 판단할 수 없습니다. 먼저 실제
    가동 여부, 생산 작업 종료 여부, 대기 상태 전력 사용 여부를 확인하고
    전류·역률 변화와 계측 데이터 품질을 함께 점검하세요."
"""

# (4) 역할 3 — 사용자(역할)별로 표현 방식을 다르게
ROLE_STYLE = {
    "energy_manager": "에너지 관리자용: 설비 순위·비용 영향·기간 비교를 중심으로 설명하라.",
    "equipment_manager": "현장 담당자용: 발생 시각·설비·점검 항목을 중심으로 설명하라.",
    "executive": (
        "경영진용: get_executive_summary를 먼저 호출하라. 결론과 의사결정에 필요한 핵심 지표를 "
        "맨 앞 3~5줄로 제시하라. 전월 대비 전력량·요금·탄소 증감을 먼저 말하되, 총량 증감과 "
        "일평균 증감을 함께 밝히고 날짜 수 차이로 생긴 증감을 사용량 변화로 해석하지 마라. "
        "이어서 탄소 최다 배출 설비 순위·비중과 감축 우선 검토 대상, 이상 징후 설비의 "
        "에너지 낭비·탄소 증가 가능성과 점검 권고를 구분해 설명하라. 세부 시각 나열은 줄이고, "
        "전년 대비는 데이터가 없다고 밝혀라."
    ),
}


# (2-b) 단순 조회(QA) 모드 — 보고서 형식 없이 질문한 값만 간결하게
QA_FORMAT = """\
지금은 '단순 조회' 질문이다. 6단계 보고서 형식을 쓰지 마라.
- 사용자가 물은 값(해당 달/설비의 전력량·요금·탄소 등)을 2~4문장으로 간결하게 답하라.
- 맨 앞에 질문한 숫자를 먼저 제시하고, 필요하면 전월 대비 증감만 덧붙여라.
- 묻지 않은 설비 이상·현장 점검·분석의 한계 같은 항목을 억지로 채우지 마라.
- 끝에 기준 기간만 한 줄로 밝혀라.
"""


# (2-c) 경영진(페르소나 3) 보고서 형식 — 결론 먼저, 근거는 get_executive_summary 값만
EXEC_FORMAT = """\
경영진 보고서다. 아래 형식으로, 반드시 get_executive_summary 결과에 있는 값만 써서 답하라.
1. 결론 (3줄 이내): 보고 월의 전력량·요금·탄소 전월 대비 증감(총량)과 일평균 증감을 함께 쓰고,
   총량 증감 중 날짜 수 차이로 생긴 부분은 사용량 변화가 아니라고 한 문장으로 밝혀라.
2. 탄소 배출 순위·비중: 상위 설비(reduction_candidates)와 비중, 설비 간 비중 차이(share_spread_pctp)를 밝혀라.
3. 증가 원인 설비: increase_driver가 있으면 그 설비와 일평균 증가량, 없으면 '늘어난 설비 없음'.
4. 점검 권고: watch_equipment의 설비별 값만 사용하라. 초과 전력량(excess_kwh)이 있는 설비는
   에너지 낭비·탄소 증가 가능성으로, pf_low_hours가 0보다 큰 설비만 역률 저하로 설명하라.
   pf_low_hours가 0보다 큰 설비는 빠뜨리지 말고 반드시 포함하라.
   고장이라고 단정하지 말고 점검 권고로 써라.
5. 한계: 전년 대비는 데이터가 없다는 점과 이상 징후는 관찰 사실이라는 점.
- 결과에 없는 연도·설비·수치를 만들어 쓰지 마라. '전년도' 분석은 존재하지 않는다.
"""

# 경영진 단순 조회(qa)에도 지키는 규칙
EXEC_QA_RULE = """\
[경영진 비교 규칙] 전월 대비를 말할 때는 반드시 총량 증감과 일평균 증감(change.daily_pct)을 함께 써라.
두 값이 크게 다르면 총량 증감은 날짜 수 차이(예: 2월은 28일, 3월은 31일) 때문이라고 밝히고,
사용량이 실제로 줄었다/늘었다거나 긍정적·부정적 변화라고 해석하지 마라.
"""


def build_prompt(role_id=None, mode="summary"):
    """역할·모드에 맞는 최종 지시문을 조립해 돌려준다.
    mode="summary"면 6단계 보고서 형식(경영진은 EXEC_FORMAT), mode="qa"면 간결 조회 형식."""
    parts = [SYSTEM_PROMPT]
    if mode == "summary" and role_id == "executive":
        parts.append(EXEC_FORMAT)
    elif mode == "summary":
        parts.append(REPORT_FORMAT)
    elif mode == "qa":
        parts.append(QA_FORMAT)
        if role_id == "executive":
            parts.append(EXEC_QA_RULE)
    parts.append(ANOMALY_STYLE)
    if role_id and role_id in ROLE_STYLE:
        parts.append(ROLE_STYLE[role_id])
    return "\n".join(parts)


# ------------------------------------------------------------
# 질문이 '종합 보고서 요청'인지 '단순 조회'인지 자동 판단 (A안)
#   - 보고서/요약/현황/정리/리포트/점검 등의 말이 있으면 보고서(summary)
#   - 그 외에는 단순 조회(qa)
# ------------------------------------------------------------
_REPORT_HINTS = ["보고서", "요약", "현황", "정리", "리포트", "report",
                 "종합", "점검", "브리핑", "분석", "전반", "전체적으로",
                 "효율", "개선", "권고", "절감", "낭비", "진단"]


def decide_mode(question):
    """질문 문자열을 보고 'summary'(보고서) 또는 'qa'(단순 조회)를 돌려준다."""
    q_lower = (question or "").lower()
    for h in _REPORT_HINTS:
        if h in q_lower:
            return "summary"
    return "qa"


# ------------------------------------------------------------
# 1) AI에게 "이런 함수들을 쓸 수 있다"고 알려줄 도구 목록(스키마)
#    - OpenAI function calling 규격: 함수 이름 / 설명 / 입력 파라미터
#    - 여기 적힌 설명을 보고 AI가 어떤 함수를 부를지 판단한다.
# ------------------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_equipment_power",
            "description": "특정 설비의 전력(activePower)을 기간 범위에서 조회한다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "equipment": {"type": "string", "description": "설비명 (예: 3호기, 1(PM-3))"},
                    "start": {"type": "string", "description": "조회 시작 (예: 2025-05-10)"},
                    "end": {"type": "string", "description": "조회 끝 (예: 2025-05-11)"},
                },
                "required": ["equipment"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_equipment_energy",
            "description": "특정 설비의 전력량 합계와 요금·탄소 환산값을 조회한다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "equipment": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                },
                "required": ["equipment"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_total_energy",
            "description": "전체 설비의 전력량 합계와 요금·탄소를 조회한다 (에너지 담당용).",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_anomalies",
            "description": ("역률(powerFactor)이 낮았던 구간을 설비명·발생시각·역률값과 "
                            "함께 조회한다. '역률 낮은 설비', '역률 저하', '노후화 의심 설비'처럼 "
                            "역률 관련 질문이면 반드시 이 함수를 호출하라. "
                            "(get_data_overview는 '건수'만 주므로, 어느 설비인지 답하려면 "
                            "이 함수를 써야 한다.)"),
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rank_carbon_by_equipment",
            "description": "설비별 탄소 배출량 순위를 조회한다 (감축 대상 선정용).",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "top": {"type": "integer", "description": "상위 몇 개 (기본 5)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_data_overview",
            "description": ("데이터의 실제 기간(시작/끝 날짜), 설비 수, 행 수, "
                            "전력 이상치 건수, 역률 저하 건수를 조회한다. "
                            "기간이나 이상치 유무를 답하기 전에 반드시 이 함수를 먼저 호출하라."),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_power_anomalies",
            "description": ("전력 이상치(설비별 평소 평균에서 3σ 벗어난 구간)를 조회한다. "
                            "이상치가 있는 설비/시각을 물으면 이 함수를 호출하라."),
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "top": {"type": "integer", "description": "상위 몇 개 (기본 20)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_efficiency",
            "description": ("운영 효율성 분석(역률·가동률 기반). 역률이 낮아 전기 효율이 "
                            "떨어지는 설비, 가동률 대비 전력이 과다한(대기전력 낭비 의심) "
                            "설비를 '관찰된 사실 + 점검 권고'로 돌려준다. "
                            "'효율 어때?', '비효율 설비', '개선/절감할 점', '낭비' 같은 "
                            "질문이면 이 함수를 호출하라."),
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "monthly_compare",
            "description": ("월별(예: 2024-12, 2025-01, 2025-02, 2025-03, 2025-04) "
                            "전력량/요금/탄소와 전월 대비 증감을 조회한다. "
                            "'2월 전력량', '특정 월의 사용량', '월별 비교'처럼 "
                            "월 단위 질문이면 반드시 이 함수를 호출하라. "
                            "데이터 기간(2024-12 ~ 2025-04) 안의 모든 월을 돌려준다."),
            "parameters": {
                "type": "object",
                "properties": {
                    "equipment": {"type": "string",
                                  "description": "특정 설비만 보려면 설비명. 전체면 생략."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_executive_summary",
            "description": ("경영진용 월간 요약. 지정한 달의 전력량·요금·탄소와 전월 대비 증감"
                            "(총량·일평균), 설비별 탄소 배출 순위·비중, 감축 우선 검토 대상, "
                            "전월 대비 증가 기여 설비, 이상 징후 설비의 초과 전력량·요금·탄소를 돌려준다. "
                            "경영 보고, ESG 보고, '이번 달 요약', '전월 대비' 질문이면 호출하라."),
            "parameters": {
                "type": "object",
                "properties": {
                    "month": {"type": "string",
                              "description": "보고 월 (예: 2025-04). 생략하면 데이터의 마지막 달."},
                },
            },
        },
    },
]


# ------------------------------------------------------------
# (방법2 보조) 보고서용 '가장 주의할 설비' 사실을 코드가 미리 조회한다.
#   - 전체 기간 기준(맞춤 정보 화면 신호등과 동일)으로 설비 이상치를 집계해
#     설비별 이상 건수 순위를 만든다. AI가 '주의할 설비'를 지어내거나
#     "확인 불가"로 넘기지 않도록, 확정된 사실을 미리 쥐여주기 위함.
#   - 반환: {"text": AI에게 줄 요약문, "raw": 근거용 원본} 또는 None
# ------------------------------------------------------------
# ------------------------------------------------------------
# (A안 보조) 질문 문장에서 '언제'를 읽어 집계본 기간(YYYYMMDDhh)으로 바꾼다.
#   - "1월", "2025년 3월", "2025-02" 같은 표현 -> 그 달의 (시작hour, 끝hour)
#   - 연도가 없으면 데이터에 그 달이 있는 해를 집계본에서 찾아 맞춘다.
#   - 월 표현이 없으면 (None, None) = 전체 기간.
# ------------------------------------------------------------
def _extract_period_from_question(question, df=None):
    if not question:
        return (None, None)
    text = question
    year = None
    month = None
    m = re.search(r"(20\d{2})\s*[-/.년]?\s*(\d{1,2})\s*월?", text)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
    else:
        m2 = re.search(r"(?<!\d)(\d{1,2})\s*월", text)
        if m2:
            month = int(m2.group(1))
    if not month or month < 1 or month > 12:
        return (None, None)

    # 연도 추정: 질문에 없으면, 집계본에 'YYYYMM'이 있는 해를 찾는다
    if year is None and df is not None:
        months_in_df = set(df["hour"].astype(str).str[:6].unique())
        for cand in sorted({h[:4] for h in months_in_df}):
            if f"{cand}{month:02d}" in months_in_df:
                year = int(cand)
                break
    if year is None:
        return (None, None)

    start = f"{year}{month:02d}0100"   # 그 달 1일 00시
    end = f"{year}{month:02d}3123"     # 그 달 31일 23시 (문자열 비교라 안전)
    return (start, end)


def _prefetch_equipment_facts(df, top=5, start=None, end=None):
    try:
        an = q.detect_power_anomalies(df, start=start, end=end, sigma=3.0, top=100000)
    except Exception:
        return None
    if an.get("status") != "ok":
        return None

    # 설비별 이상 건수 집계
    by_eq = {}
    for it in an.get("items", []):
        eq = it.get("equipment")
        if eq is None:
            continue
        d = by_eq.setdefault(eq, {"count": 0, "max_ratio": 0, "sample": it})
        d["count"] += 1
        if abs(it.get("ratio", 1) - 1) > abs(d["max_ratio"] - 1):
            d["max_ratio"] = it.get("ratio", 1)
            d["sample"] = it
    ranked = sorted(by_eq.items(), key=lambda kv: kv[1]["count"], reverse=True)[:top]

    # 역률 저하(노후화 의심) 건수도 함께 (같은 기간 기준)
    try:
        pf = q.get_anomalies(df, start=start, end=end)
        pf_count = pf.get("count", 0)
    except Exception:
        pf_count = 0

    period_label = "전체 기간"
    if start and end:
        # YYYYMMDDhh -> 'YYYY-MM' 로 보기 좋게
        period_label = f"{start[:4]}-{start[4:6]} 기간"

    lines = [
        f"[코드가 미리 조회한 '{period_label}' 설비 사실 — 보고서의 '가장 주의할 설비'에 "
        "반드시 이 데이터를 사용하라. 이 값은 확정된 사실이며 '확인할 수 없다'고 "
        "답하지 마라. 사용자가 특정 달을 물었으면 그 달 기준이다.]",
        f"- 전력 이상치(3σ) 총 {an.get('count', 0):,}건, 역률 저하(70 미만) {pf_count:,}건.",
        "- 이상 건수 상위 설비:",
    ]
    for eq, d in ranked:
        s = d["sample"]
        lines.append(
            f"  · {eq}: 이상 {d['count']:,}건, 대표 사례 {s.get('time')} "
            f"(전력 {s.get('power')}kW, 평소 {s.get('normal_mean')}kW의 "
            f"{s.get('ratio')}배 {s.get('direction')})"
        )
    if not ranked:
        lines.append("  · (전체 기간에 3σ 이상치가 집계되지 않음)")

    return {
        "text": "\n".join(lines),
        "raw": {"anomaly_total": an.get("count", 0),
                "pf_low_count": pf_count,
                "top_equipment": [{"equipment": eq, "count": d["count"],
                                   "sample": d["sample"]} for eq, d in ranked]},
    }


# ------------------------------------------------------------
# 2) AI가 "이 함수 불러줘"라고 하면, 실제 조회 함수로 연결하는 다리
#    - df: A가 준 집계 데이터 (없으면 조회 함수가 "데이터 없음" 반환)
# ------------------------------------------------------------
def run_tool(name, args, df=None):
    """AI가 고른 함수 이름(name)과 인자(args)를 받아 실제 조회 함수를 실행한다."""
    if name == "get_equipment_power":
        return q.get_equipment_power(df, args.get("equipment"), args.get("start"), args.get("end"))
    if name == "get_equipment_energy":
        return q.get_equipment_energy(df, args.get("equipment"), args.get("start"), args.get("end"))
    if name == "get_total_energy":
        return q.get_total_energy(df, args.get("start"), args.get("end"))
    if name == "get_anomalies":
        return q.get_anomalies(df, args.get("start"), args.get("end"))
    if name == "rank_carbon_by_equipment":
        return q.rank_carbon_by_equipment(df, args.get("start"), args.get("end"), args.get("top", 5))
    if name == "get_data_overview":
        return q.get_data_overview(df)
    if name == "detect_power_anomalies":
        return q.detect_power_anomalies(df, args.get("start"), args.get("end"),
                                        sigma=args.get("sigma", 3.0), top=args.get("top", 20))
    if name == "monthly_compare":
        return q.monthly_compare(df, equipment=args.get("equipment"))
    if name == "analyze_efficiency":
        return q.analyze_efficiency(df, args.get("start"), args.get("end"))
    if name == "get_executive_summary":
        return q.executive_summary(df, args.get("month"))
    return {"status": "error", "message": f"알 수 없는 함수: {name}"}


# ------------------------------------------------------------
# 3) 질문 -> AI -> 함수 호출 -> AI 설명  (전체 흐름)
#    [상태] OpenAI 호출 부분은 API 키가 있어야 작동. 지금은 뼈대만.
# ------------------------------------------------------------
def ask(question, role_id=None, df=None, api_key=None, model=MODEL_NAME,
        mode="auto", max_tool_rounds=4):
    """사용자 질문을 받아 AI가 조회 함수를 호출하고 답을 돌려준다.

    흐름:
      1) mode="auto"면 질문을 보고 보고서(summary)/단순조회(qa)를 자동 선택한다. (A안)
      2) build_prompt로 역할·안전장치가 담긴 지시문을 만든다.
      3) OpenAI에 질문 + 도구목록(TOOLS)을 보낸다.
      4) AI가 도구를 고르면 run_tool로 '코드가' 숫자를 계산해 돌려준다.
         AI가 '더 조회할 게 있다'고 하면 여러 번 반복해 모두 조회한다. (B안)
      5) 도구가 준 숫자를 바탕으로 AI가 '설명'만 생성한다 (숫자 생성 금지).

    api_key가 없으면 실제 호출 없이 '준비됨' 상태만 돌려준다.
    """
    # (A안) 질문에 맞는 모드 자동 선택
    if mode == "auto":
        mode = decide_mode(question)

    if not api_key:
        return {
            "status": "no_api_key",
            "message": "OpenAI API 키가 없습니다. secrets.toml에 넣고 전달하면 작동합니다.",
            "question": question,
            "role": role_id,
            "mode": mode,
            "available_tools": [t["function"]["name"] for t in TOOLS],
            "prompt_preview": build_prompt(role_id, mode),  # 지시문 미리보기(디버그용)
        }

    try:
        from openai import OpenAI
    except ImportError:
        return {"status": "error",
                "message": "openai 라이브러리가 설치되지 않았습니다. (pip install openai)"}

    # 키 앞뒤 공백/개행만 제거 (글자 자체는 건드리지 않는다)
    if api_key:
        api_key = api_key.strip()

    client = OpenAI(api_key=api_key)
    system_prompt = build_prompt(role_id, mode)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]

    # (방법2) 보고서 모드일 때, '가장 주의할 설비' 데이터를 코드가 먼저 조회해
    #         AI에게 미리 쥐여준다. AI가 함수를 잘못 부르거나 기간을 좁게 넣어
    #         결과가 비는 것을 막는다. (맞춤 정보 화면 신호등과 같은 전체 기간 기준)
    used = []

    # (A안) 질문에서 기간(특정 달)을 읽는다. 없으면 전체 기간.
    q_start, q_end = _extract_period_from_question(question, df)

    # (품질상태) 모드와 무관하게, 코드가 '데이터 품질 상태'를 미리 조회해 쥐여준다.
    #   AI가 get_data_overview를 안 불러도 모든 답변에 품질 상태가 들어가도록 보장.
    if df is not None:
        ov = q.get_data_overview(df)
        if isinstance(ov, dict) and ov.get("status") == "ok":
            quality = (
                "[데이터 품질 상태 — 답변 끝에 반드시 이 내용을 한 줄로 포함하라. "
                "이 값은 코드가 집계 데이터에서 확인한 사실이다.]\n"
                f"※ 데이터 품질: {ov['data_start']}~{ov['data_end']} · "
                f"설비 {ov['equipment_count']}개 · {ov['row_count']:,}행 · "
                f"전력 이상치 {ov['power_anomaly_count']:,}건 · "
                f"역률 저하 {ov['pf_low_count']:,}건 (집계 데이터 기준)."
            )
            messages.append({"role": "system", "content": quality})
            used.append({"name": "get_data_overview(prefetch)",
                         "args": {}, "result": ov})

    # (방법2) 보고서 모드일 때, '가장 주의할 설비' 사실도 미리 조회해 쥐여준다.
    #   질문에 특정 달이 있으면 그 달 기준으로, 없으면 전체 기간 기준으로 조회한다.
    prefetch = None
    if role_id == "executive" and df is not None:
        # 경영진: 월간 요약을 코드가 먼저 조회해 쥐여준다 (질문에 달이 있으면 그 달, 없으면 마지막 달)
        ex = q.executive_summary(df, q_start[:6] if q_start else None)
        if isinstance(ex, dict) and ex.get("status") == "ok":
            messages.append({"role": "system", "content":
                             "[get_executive_summary 결과 — 코드가 계산한 사실. 이 값만 사용하라]\n"
                             + json.dumps(ex, ensure_ascii=False)})
            used.append({"name": "get_executive_summary(prefetch)",
                         "args": {"month": ex["month"]}, "result": ex})
    elif mode == "summary" and df is not None:
        prefetch = _prefetch_equipment_facts(df, start=q_start, end=q_end)
        if prefetch:
            messages.append({"role": "system", "content": prefetch["text"]})
            used.append({"name": "detect_power_anomalies(prefetch)",
                         "args": {"start": q_start, "end": q_end,
                                  "기준": "질문 기간" if q_start else "전체 기간"},
                         "result": prefetch["raw"]})

    # (B안) AI가 도구를 그만 부를 때까지 여러 번 반복 호출한다.
    #   - 보고서 작성 시 monthly_compare + detect_power_anomalies 처럼
    #     여러 함수를 이어서 부를 수 있게 한다. (최대 max_tool_rounds회)
    answer = None
    for _ in range(max_tool_rounds):
        resp = client.chat.completions.create(
            model=model, messages=messages, tools=TOOLS,
        )
        msg = resp.choices[0].message

        # 더 이상 도구를 안 부르면 그게 최종 답
        if not getattr(msg, "tool_calls", None):
            answer = msg.content
            break

        # AI가 고른 도구들을 '코드가' 실행해서 숫자를 얻는다
        messages.append(msg)
        for call in msg.tool_calls:
            name = call.function.name
            args = json.loads(call.function.arguments or "{}")
            result = run_tool(name, args, df=df)   # ← 숫자는 여기서(코드) 나온다
            used.append({"name": name, "args": args, "result": result})
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, ensure_ascii=False),
            })
    else:
        # 반복 한도까지 도구만 부르고 최종 답을 못 받았을 때:
        # 도구 없이 한 번 더 불러 지금까지 결과로 답을 마무리한다.
        final = client.chat.completions.create(model=model, messages=messages)
        answer = final.choices[0].message.content

    return {"status": "ok", "answer": answer, "used_tools": used, "mode": mode}


# ------------------------------------------------------------
# 혼자 테스트: 키 없이도 '도구 목록'과 '함수 연결'이 되는지 확인
# ------------------------------------------------------------
if __name__ == "__main__":
    print("등록된 AI 도구(함수) 목록:")
    for t in TOOLS:
        print(f"  - {t['function']['name']}: {t['function']['description']}")

    print("\n함수 연결 테스트 (데이터 없을 때):")
    print(" ", run_tool("get_total_energy", {}, df=None))

    print("\n조립된 프롬프트 미리보기 (현장 담당자용, 요약 모드):")
    print(build_prompt("equipment_manager", "summary"))

    print("\n질문 테스트 (API 키 없을 때 - 준비 상태 확인):")
    res = ask("3호기 전력 얼마야?", role_id="equipment_manager")
    print("  status:", res["status"])
    print("  available_tools:", res.get("available_tools"))
