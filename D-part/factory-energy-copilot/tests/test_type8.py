"""이상 유형 8종(10/17 팀 기준표): 전류만 증가 제외, 저전압·저역률·고전력 포함."""

from src.app import page_logic


def test_type8_labels():
    labels = [lab for lab, _, _ in page_logic.TYPE8]
    assert labels == ["부하 불균형", "무효전력 급증", "역률 급감", "비정상 패턴", "야간 이상가동", "저전압", "저역률", "고전력"]
    assert all(r[0] != "current_only_increase" for r in page_logic.SUDDEN_RULES)


def test_rule_condition_counts_hours_and_filters():
    evs = {"events": [
        {"module": "A", "type": "voltage_low_3phase", "localtime": "2025-01-01T00:10:00+09:00"},
        {"module": "A", "type": "voltage_low_3phase", "localtime": "2025-01-01T00:20:00+09:00"},
        {"module": "A", "type": "voltage_low_3phase", "localtime": "2025-01-01T01:05:00+09:00"},
        {"module": "B", "type": "power_factor_low_3phase", "localtime": "2025-02-01T03:00:00+09:00"},
    ]}
    out = page_logic.rule_condition_counts(evs)
    assert out["A"]["저전압"] == {"rows": 3, "hours": 2}
    assert out["B"]["저역률"] == {"rows": 1, "hours": 1}
    assert "B" not in page_logic.rule_condition_counts(evs, end="2025-01-31")
    assert page_logic.rule_condition_counts({"error": "x"}) == {}
