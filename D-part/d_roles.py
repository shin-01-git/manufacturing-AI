# ============================================================
# D 파트 - 역할 정의 (기초 틀)
# "설비 담당 / 에너지 담당"이 각각 무엇에 관심있는지 적어둔 설정 파일.
# 데이터를 담는 게 아니라, '역할이 뭔지'만 정의한다. (임의 데이터 아님)
# 담당 설비는 데이터에 구분이 없으므로 모두 '전체 13개'로 둔다. (추후 변경 가능)
# ============================================================

# 역할별 설정을 하나의 딕셔너리(사전)로 모아둔다.
# key = 역할 ID, value = 그 역할의 상세 정보
ROLES = {
    "equipment_manager": {
        "name": "설비 관리 담당자",
        "age": 47,
        # 이 역할이 주로 보는 데이터 항목 (관심 지표)
        "metrics": ["activePower", "voltage", "current", "powerFactor", "operation"],
        "metrics_kr": ["전력", "전압", "전류", "역률", "가동상태"],
        # 담당 설비: 전체 (데이터에 담당자-설비 구분이 없음)
        "equipment_scope": "all",
        # 주로 보는 관점
        "focus": "설비 상태·이상 (어느 설비가 언제 이상한가)",
        "unit": "설비별 · 시간/기간별",
    },
    "energy_manager": {
        "name": "에너지 관리 담당자",
        "age": 34,
        "metrics": ["energy", "bill", "carbon"],
        "metrics_kr": ["전력량", "요금", "탄소"],
        "equipment_scope": "all",
        "focus": "비용·탄소 (얼마고 어떻게 줄이나)",
        "unit": "전사 합계 · 월별/일별 추세",
    },
}

# 요금/탄소 환산 계수 (ground_truth 분석에서 확인된 값)
BILL_RATE = 180      # 요금  = 전력량 × 180
CARBON_RATE = 0.424  # 탄소 = 전력량 × 0.424

# 탄소 → 나무 환산 계수
#   기준: 산림청 — 30년생 소나무 1그루가 1년간 흡수하는 CO2 약 6.6kg
#   (탄소 배출량을 '소나무 몇 그루가 1년간 흡수하는 양'으로 환산해 체감도를 높인다)
TREE_ABSORB_KG = 6.6              # 소나무 1그루 연간 CO2 흡수량(kg)
TREE_SOURCE = "산림청 기준(30년생 소나무 1그루 연간 약 6.6kg CO₂ 흡수)"


# ---- 간단한 도우미 함수 ----
def get_role(role_id):
    """역할 ID로 그 역할의 설정을 꺼낸다. 없는 ID면 None."""
    return ROLES.get(role_id)


def list_roles():
    """사용 가능한 역할 (ID, 이름) 목록을 돌려준다. 화면 드롭다운용."""
    return [(rid, info["name"]) for rid, info in ROLES.items()]


# ---- 이 파일을 직접 실행하면 내용 확인 (테스트용) ----
if __name__ == "__main__":
    print("정의된 역할:")
    for rid, name in list_roles():
        info = get_role(rid)
        print(f"- {name} ({rid}) : 관심지표 {info['metrics_kr']}")
    print(f"\n요금 계수 {BILL_RATE}, 탄소 계수 {CARBON_RATE}")
