"""Structured briefing tests: line counts, formats, forbidden words, number provenance, LLM validation."""
from __future__ import annotations

import re

import pytest

from config import config
from src.agent import agent as agent_mod
from src.agent import briefing as bm
from src.app.ui import components, fmt

REAL = config.ARTIFACTS_DIR
needs_real = pytest.mark.skipif(not (REAL / "predictions.csv").exists(), reason="shared artifacts not built")
AUDIENCES = [bm.EXECUTIVE, bm.ENERGY, bm.FACILITY]
FORBID = ("고장", "위험 확정", "실시간")


def _all(b):
    return bm.to_plaintext(b)


@needs_real
@pytest.mark.parametrize("audience,lo,hi", [(bm.EXECUTIVE, 5, 5), (bm.ENERGY, 6, 7), (bm.FACILITY, 6, 8)])
def test_line_counts_per_audience(audience, lo, hi):
    b = bm.build_briefing(audience, "2025-05-10", "2025-05-31", artifacts_dir=REAL)
    assert lo <= len(b.lines) <= hi
    assert len(b.headline) == 3
    assert b.limits and b.mode == "rule"


@needs_real
@pytest.mark.parametrize("audience", AUDIENCES)
def test_no_forbidden_words_or_raw_number_formats(audience):
    b = bm.build_briefing(audience, "2025-05-10", "2025-05-31", artifacts_dir=REAL)
    text = _all(b) + bm.to_markdown(b)
    for word in FORBID:
        assert word not in text
    assert not re.search(r"\.\d\d원", text), "won with decimals"
    assert not re.search(r"\d{2}:\d{2}:\d{2}", text), "seconds in timestamps"
    assert not re.search(r"(?<![\d,.])\d{7,}(?![\d,])", text), "raw 7+ digit number"
    assert "참고용" not in text and "합격 기준" not in text, "MAE must not carry the 73/89 reference caveat"
    assert "수치는 artifacts 계산값이며" not in text


@needs_real
def test_watch_modules_are_capped_and_nonzero():
    ex = bm.build_briefing(bm.EXECUTIVE, artifacts_dir=REAL)
    fa = bm.build_briefing(bm.FACILITY, artifacts_dir=REAL)
    assert 0 < len(ex.watch_modules) <= 3
    assert len(fa.watch_modules) <= 5
    from src.agent import tools

    status = tools.get_module_status(artifacts_dir=REAL)
    zero = {r["module"] for r in status["modules"] if not r["anomaly_count"]}
    assert not zero & {m for m, _ in ex.watch_modules + fa.watch_modules}
    assert "13개" not in _all(ex)


@needs_real
@pytest.mark.parametrize("audience", AUDIENCES)
def test_every_number_in_text_exists_in_calc_table(audience):
    b = bm.build_briefing(audience, "2025-05-10", "2025-05-31", artifacts_dir=REAL)
    pool = " ".join([b.period_label] + [f"{a} {v}" for a, v, _ in b.calc] + [f"{m} {r}" for m, r in b.watch_modules] + b.limits)
    allowed = bm._numbers(pool)
    for ln in b.lines:
        assert bm._numbers(ln.text) <= allowed, (ln.label, ln.text)
    for _, value in b.headline:
        assert bm._numbers(value) <= allowed, value


@needs_real
def test_executive_follows_plan_labels():
    """기획서 7: 경영진 = 총 비용 · 증감 · 주요 위험 (MAE는 에너지 담당자용)."""
    b = bm.build_briefing(bm.EXECUTIVE, "2025-05-10", "2025-05-31", artifacts_dir=REAL)
    assert [ln.label for ln in b.lines] == ["총 비용", "증감", "주요 위험", "우선 조치", "기준·한계"]
    assert "%" in b.lines[1].text and "정답 척도" not in _all(b)
    assert "MAE" not in " ".join(ln.text for ln in b.lines)


def test_missing_artifacts_give_five_no_data_lines(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    empty = tmp_path / "e"
    empty.mkdir()
    b = bm.build_briefing(bm.EXECUTIVE, artifacts_dir=empty)
    assert len(b.lines) == 5
    assert all(bm.NO_DATA in ln.text for ln in b.lines[:3])
    assert b.limits


def test_agent_briefing_rule_without_key(fake_artifacts, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    agent = agent_mod.Agent(artifacts_dir=fake_artifacts)
    for aud in AUDIENCES:
        r = agent.briefing(aud)
        assert r.source == "rule" and r.data["briefing"].mode == "rule"
    assert agent_mod.AUDIENCE_FIELD == bm.FACILITY
    assert bm.normalize_audience("field") == bm.FACILITY


# -- LLM validation --------------------------------------------------------
@needs_real
def test_llm_lines_accepted_when_numbers_unchanged():
    rule = bm.build_briefing(bm.EXECUTIVE, "2025-05-10", "2025-05-31", artifacts_dir=REAL)
    payload = {"lines": [{"label": ln.label, "text": ln.text + " 참고하세요." } for ln in rule.lines]}
    out = bm.narrate_with_llm(rule, call=lambda s, u: payload)
    assert out.mode == "llm" and len(out.lines) == 5
    assert out.calc == rule.calc


@needs_real
@pytest.mark.parametrize("mutate", ["new_number", "bad_label", "forbidden", "short", "none", "raise"])
def test_llm_rejected_falls_back_to_rule(mutate):
    rule = bm.build_briefing(bm.EXECUTIVE, "2025-05-10", "2025-05-31", artifacts_dir=REAL)
    lines = [{"label": ln.label, "text": ln.text} for ln in rule.lines]
    if mutate == "new_number":
        lines[0]["text"] += " 약 9999억 원입니다."
    elif mutate == "bad_label":
        lines[0]["label"] = "다른 라벨"
    elif mutate == "forbidden":
        lines[1]["text"] += " 설비 고장 가능."
    elif mutate == "short":
        lines = lines[:3]

    def call(s, u):
        if mutate == "raise":
            raise RuntimeError("boom")
        return None if mutate == "none" else {"lines": lines}

    out = bm.narrate_with_llm(rule, call=call)
    assert out is rule and out.mode == "rule"


# -- fmt + card ------------------------------------------------------------
def test_fmt_short_forms():
    assert fmt.fmt_krw_short(3556690522.53) == "35.6억 원"
    assert fmt.fmt_krw_short(48200000) == "4,820만 원"
    assert fmt.fmt_krw_short(9500) == "9,500원"
    assert fmt.fmt_carbon_short(8377982.12) == "837.8만"
    assert fmt.fmt_pow_short(19733651) == "1,973만"
    assert fmt.fmt_pow_short(39130.4) == "39,130"
    assert fmt.fmt_mae(88.18) == "88.2"
    assert fmt.fmt_period("2025-05-10 00:00:00", "2025-05-31 00:00:00") == "05-10 ~ 05-31 (21일)"
    assert fmt.fmt_time("2025-05-14 03:00:00") == "05-14 03시"
    assert fmt.fmt_pct(88.2 / 39130, decimals=2) == "0.23%"
    assert fmt.fmt_krw_short(None) == fmt.NO_DATA


@needs_real
@pytest.mark.parametrize("audience", AUDIENCES)
def test_briefing_card_is_balanced_escaped_html(audience):
    b = bm.build_briefing(audience, "2025-05-10", "2025-05-31", artifacts_dir=REAL)
    b.lines[0].text = "<script>alert(1)</script> 35.6억 원"
    html = components.briefing_card(b)
    assert html.count("<div") == html.count("</div>")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert html.count('<li class="fec-bl') == len(b.lines)
    assert "<b>35.6억 원</b>" in html
