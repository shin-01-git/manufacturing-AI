"""에너지 관리 담당자 AI 보고서: 숫자는 Python 계산, LLM 문장은 검증 후에만 사용."""

from src.agent import report

ROWS = [{"id": f"2025-05-{10 + h // 24:02d} {h % 24:02d}:00:00", "pred_pow": 39.0 + (h % 24) * 0.001}
        for h in range(48)]
SETTINGS = {"bill_rate": 180.0, "carbon_rate": 0.424, "carbon_price": 10500.0}
KW = dict(status_rows=[{"module": "12(4호기)", "status": "이상", "total": 5, "main_type": "고전력"}],
          anomaly_sources={"5초 고전력": 24, "시간 급변": 27})


def test_facts_totals_and_units():
    f = report.compute_facts(ROWS, SETTINGS, **KW)
    total = sum(r["pred_pow"] for r in ROWS)
    assert abs(f["kwh"] - total) < 1e-9
    assert abs(f["bill"] - total * 180) < 1e-6
    assert abs(f["carbon_cost"] - total * 0.424 / 1000 * 10500) < 1e-6
    assert len(f["daily"]) == 2 and f["hour_hi"][0] == 23


def test_rule_report_html_has_sections_and_no_forbidden_words():
    html = report.build_energy_report(ROWS, SETTINGS, use_llm=False, **KW)
    for h in ("1. 요약", "2. 검토 의견", "3. 일별 예측", "4. 시간대 분석", "5. 예측 근거", "6. 이상치 요약", "7. 기준과 한계"):
        assert h in html
    assert "규칙 기반 작성" in html
    for w in ("고장", "Fault", "LIVE", "실시간", "탄소세", "Traceback"):
        assert w not in html


def test_llm_text_used_only_when_numbers_exist_in_facts():
    f = report.compute_facts(ROWS, SETTINGS, **KW)
    ok = lambda s, u: {"summary": "부하가 일정합니다.", "recs": ["기저 부하를 줄이세요.", "점검 일정을 맞추세요."]}
    new_number = lambda s, u: {"summary": "요금이 12,345원 늘어납니다.", "recs": ["확인하세요."]}
    forbidden = lambda s, u: {"summary": "고장입니다.", "recs": ["확인하세요."]}

    def boom(s, u):
        raise RuntimeError("network")

    assert report.write_text(f, ok)["mode"] == "llm"
    assert report.write_text(f, new_number)["mode"] == "rule"
    assert report.write_text(f, forbidden)["mode"] == "rule"
    assert report.write_text(f, boom)["mode"] == "rule"


def test_empty_rows():
    assert "예측 데이터가 없습니다" in report.build_energy_report([], SETTINGS, use_llm=False)
