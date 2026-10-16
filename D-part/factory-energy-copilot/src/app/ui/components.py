"""Reusable HTML components for the dashboard (steering A-4).

Each builder returns an HTML string so it is testable without Streamlit; the app
renders them with ``st.markdown(..., unsafe_allow_html=True)``. KPI cards use
this helper instead of ``st.metric`` (A-4). Status chips are the only place
status colours appear in text (A-2). The calc-value / AI-narration split (D4) is
expressed as two distinct card styles.
"""

from __future__ import annotations

import html
from typing import Any

from src.app.ui import icons, theme

NO_DATA_LABEL = "데이터에 없음"

# Status label -> text glyph for the status table (v2: 텍스트 기호).
STATUS_GLYPH = {
    "이상": "■ 이상",
    "주의": "▲ 주의",
    "정상": "● 정상",
    "미확정": "○ 미확정",
}
STATUS_GLYPH_NONE = "— 기록 없음"

# kpi_card tones that add a class (neutral keeps the original, class-less markup).
_KPI_TONE_CLASSES = tuple(t for t in theme.KPI_TONES if t != "neutral") + ("watch", "unknown")


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def panel(*, height: int | None = None, key: str | None = None):
    """A bordered panel container (``st.container(border=True)``) — v2 §A-8.

    Every information block (except KPI cards) lives inside one of these; the
    panel's visual style is CSS-driven, never a markdown div wrapper. Returns the
    Streamlit container so the caller uses it as a ``with`` block.
    """

    import streamlit as st

    kwargs: dict[str, Any] = {"border": True}
    if height is not None:
        kwargs["height"] = height
    if key is not None:
        kwargs["key"] = key
    return st.container(**kwargs)


def panel_header(
    title: str, *, icon_name: str = "", desc: str = "", tone: str = ""
) -> str:
    """First-line header inside a panel (v2 §A-8): title + optional icon/desc.

    Rendered with ``st.markdown(..., unsafe_allow_html=True)`` as the panel's
    first child. Never a free-floating title outside the panel. ``tone`` (one of
    ``theme.METRIC_KEYS``) colours only the icon with that metric colour; the
    default is the accent colour.
    """

    tone_cls = f" fec-ph-tone-{tone}" if tone in theme.METRIC_KEYS else ""
    icon_html = (
        f'<span class="fec-ph-icon{tone_cls}">{icons.icon(icon_name)}</span>'
        if icon_name
        else ""
    )
    desc_html = f'<span class="fec-ph-desc">{_esc(desc)}</span>' if desc else ""
    return (
        '<div class="fec-panel-header">'
        f'<span class="fec-ph-title">{icon_html}{_esc(title)}</span>'
        f"{desc_html}"
        "</div>"
    )


def page_title(title: str, *, desc: str = "") -> str:
    """Page title (20px/700) + one-line muted description (13px) — no st.title."""

    desc_html = f'<div class="fec-page-desc">{_esc(desc)}</div>' if desc else ""
    return f'<div class="fec-page-title">{_esc(title)}</div>{desc_html}'


def breadcrumb(parts: list[str]) -> str:
    """A simple breadcrumb ``A › B › C`` (drill-down headers)."""

    safe = [f'<span>{_esc(p)}</span>' for p in parts if p]
    return '<div class="fec-breadcrumb">' + '<span class="fec-crumb-sep">›</span>'.join(safe) + "</div>"


def chip(text: str, *, tone: str = "muted", icon_name: str = "") -> str:
    """A small pill chip.

    ``tone`` in {accent, muted, ok, watch, alert, power, bill, carbon, accuracy,
    anomaly, info}. Metric tones use the metric tint background + metric text
    colour; status tones are for status/warnings only.
    """

    tone_cls = {
        "accent": "fec-chip",
        "muted": "fec-chip fec-chip-muted",
        "ok": "fec-chip fec-chip-ok",
        "watch": "fec-chip fec-chip-watch",
        "alert": "fec-chip fec-chip-alert",
        **{m: f"fec-chip fec-chip-{m}" for m in theme.METRIC_KEYS},
    }.get(tone, "fec-chip fec-chip-muted")
    icon_html = icons.icon(icon_name) if icon_name else ""
    return f'<span class="{tone_cls}">{icon_html}{_esc(text)}</span>'


def status_text(status: str, *, has_record: bool = True) -> str:
    """Return the text-glyph status label (■/▲/○/—). No colour fill (v2)."""

    if not has_record:
        return STATUS_GLYPH_NONE
    return STATUS_GLYPH.get(status, STATUS_GLYPH["미확정"])


def kpi_card(
    title: str,
    value: str,
    *,
    unit: str = "",
    formula: str = "",
    footnote: str = "",
    interp: str = "",
    tone: str = "neutral",
) -> str:
    """A KPI card (A-4). ``value`` is already formatted via ``fmt`` by the caller.

    ``tone`` picks the 4px top band + big-number colour: one of
    ``theme.KPI_TONES`` (power/bill/carbon/accuracy/anomaly/neutral) or the grey
    ``unknown`` / amber ``watch`` status tones. ``neutral`` (default) renders the
    original markup unchanged.

    ``interp`` is an optional one-line plain-language reading of the number
    (STEP 7-2 ③) placed above the formula; when omitted the card behaves exactly
    as before. Keep it factual, no exaggeration.
    """

    unit_html = f'<span class="fec-kpi-unit">{_esc(unit)}</span>' if unit else ""
    interp_html = f'<div class="fec-kpi-interp">{_esc(interp)}</div>' if interp else ""
    formula_html = f'<div class="fec-kpi-formula">{_esc(formula)}</div>' if formula else ""
    footnote_html = f'<div class="fec-kpi-footnote">{_esc(footnote)}</div>' if footnote else ""
    tone_cls = f" fec-kpi-tone-{tone}" if tone in _KPI_TONE_CLASSES else ""
    return (
        f'<div class="fec-kpi{tone_cls}">'
        f'<div class="fec-kpi-title">{_esc(title)}</div>'
        f'<div class="fec-kpi-value">{_esc(value)}{unit_html}</div>'
        f"{interp_html}{formula_html}{footnote_html}"
        "</div>"
    )


def status_chip(status: str) -> str:
    """A coloured status chip. Grey '미확정' when O-3 boundaries unapproved (A-2)."""

    color = theme.status_color(status)
    return (
        f'<span class="fec-status" style="color:{theme.status_text_color(status)};'
        f'background:{theme.status_tint(status)};">'
        f'<span class="fec-dot" style="background:{color};"></span>{_esc(status)}'
        "</span>"
    )


def llm_status_chip(connected: bool) -> str:
    """LLM status chip top-right (C5): connected vs rule-based fallback mode."""

    if connected:
        return '<span class="fec-llm fec-llm-on">● LLM 연결됨</span>'
    return '<span class="fec-llm fec-llm-off">○ 대체 모드: 규칙 기반 요약</span>'


def period_chip(start: str, end: str, *, mode: str = "") -> str:
    """Data-basis period chip (A-3/D1)."""

    tail = f" · {_esc(mode)}" if mode else ""
    return f'<span class="fec-chip">분석 기간 {_esc(start)} ~ {_esc(end)}{tail}</span>'


def source_chip(label: str, *, found: bool = True) -> str:
    """A source chip next to a number (A-5 copilot). ``found=False`` -> 데이터에 없음."""

    if not found:
        return f'<span class="fec-source fec-source-none">{_esc(NO_DATA_LABEL)}</span>'
    return f'<span class="fec-source">출처: {_esc(label)}</span>'


def alert_item(text: str, *, none: bool = False) -> str:
    """A warning list item. ``none=True`` renders the '해당 없음' calm variant (D6)."""

    cls = "fec-alert-item fec-alert-none" if none else "fec-alert-item"
    return f'<div class="{cls}">{_esc(text)}</div>'


def empty_state(artifact_name: str) -> str:
    """Empty-state card for a missing artifact (A-6): 데이터에 없음: <파일명>."""

    return f'<div class="fec-empty">{_esc(NO_DATA_LABEL)}: {_esc(artifact_name)}</div>'


def section_card(title: str, body_html: str, *, desc: str = "") -> str:
    """A titled section container wrapping already-built body HTML."""

    desc_html = f'<div class="fec-section-desc">{_esc(desc)}</div>' if desc else ""
    return (
        '<div class="fec-section">'
        f"<h3>{_esc(title)}</h3>{desc_html}{body_html}"
        "</div>"
    )


def html_table(
    headers: list[str],
    rows: list[list[Any]],
    *,
    highlight_rows: set[int] | None = None,
) -> str:
    """A static HTML table with optionally highlighted rows (v2: 베이스라인/방법 비교).

    ``highlight_rows`` is a set of 0-based row indices rendered with the accent
    highlight class (selected model / 합계 행). All cells are escaped.
    """

    highlight_rows = highlight_rows or set()
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body_parts: list[str] = []
    for i, row in enumerate(rows):
        cls = ' class="fec-row-hl"' if i in highlight_rows else ""
        cells = "".join(f"<td>{_esc(c)}</td>" for c in row)
        body_parts.append(f"<tr{cls}>{cells}</tr>")
    return (
        '<table class="fec-table"><thead><tr>'
        f"{head}</tr></thead><tbody>{''.join(body_parts)}</tbody></table>"
    )


def kv_table(pairs: list[tuple[str, str]]) -> str:
    """Key/value rows for the calc-value card body (D4)."""

    return "".join(
        '<div class="fec-kv"><span>{}</span><span>{}</span></div>'.format(_esc(k), _esc(v))
        for k, v in pairs
    )


def calc_card(body_html: str, *, tag: str = "Python 계산값") -> str:
    """Grey monospaced calc-value card (D4). ``body_html`` is caller-built."""

    return (
        '<div class="fec-calc">'
        f'<span class="fec-tag fec-tag-calc">{_esc(tag)}</span>'
        f"<div>{body_html}</div>"
        "</div>"
    )


def ai_card(text: str, *, tag: str = "AI 서술") -> str:
    """White AI-narration card (D4). ``text`` is escaped (plain narration)."""

    return (
        '<div class="fec-ai">'
        f'<span class="fec-tag fec-tag-ai">{_esc(tag)}</span>'
        f'<div>{_esc(text)}</div>'
        "</div>"
    )


def ai_card_lines(lines: list[str], *, tag: str = "계산/규칙 기반") -> str:
    """AI-narration card from pre-escaped bullet lines (overview AI 요약 3줄)."""

    body = "<br>".join(f"· {_esc(ln)}" for ln in lines)
    return (
        '<div class="fec-ai">'
        f'<span class="fec-tag fec-tag-ai">{_esc(tag)}</span>'
        f"<div>{body}</div>"
        "</div>"
    )


# ===========================================================================
# v3/v4 components: sidebar brand, page header, KPI tile, alert strip/rows,
# error state, flow step, calc box. Pure HTML builders (rendered in one
# st.markdown call each); interactive widgets stay Streamlit widgets.
# ===========================================================================
ERROR_STATE_TITLE = "데이터를 불러오지 못함"


def brand(title: str, team: str) -> str:
    """Sidebar brand block: logo + product name + team (v4 §2.1)."""

    return (
        '<div class="fec-brand">'
        f'<span class="fec-brand-logo">{icons.icon("pulse", size=18)}</span>'
        '<div class="fec-brand-text">'
        f'<div class="fec-brand-title">{_esc(title)}</div>'
        f'<div class="fec-brand-team">Team: {_esc(team)}</div>'
        "</div></div>"
    )


def period_badge(text: str) -> str:
    """Calendar-icon period chip beside the page title (v4 §1.2)."""

    return f'<span class="fec-period">{icons.icon("calendar", size=14)}{_esc(text)}</span>'


def page_header_left(title: str, *, chip_text: str = "", desc: str = "") -> str:
    """Left half of the page header: title (+ period chip) and muted subtitle."""

    chip_html = period_badge(chip_text) if chip_text else ""
    desc_html = f'<div class="fec-page-sub">{_esc(desc)}</div>' if desc else ""
    return (
        '<div class="fec-page-head">'
        f'<div class="fec-page-h1"><span>{_esc(title)}</span>{chip_html}</div>'
        f"{desc_html}</div>"
    )


def delta_chip(pct: float | None) -> str:
    """Grey ▲/▼ change chip (no good/bad colour meaning). Empty if ``pct`` is None."""

    if pct is None:
        return ""
    if abs(pct) < 0.05:  # ±0.0%로만 보이는 변화는 정보가 없어 칩을 숨긴다
        return '<span class="fec-delta">변화 없음</span>'
    arrow = "▲" if pct >= 0 else "▼"
    return f'<span class="fec-delta">{arrow}{abs(pct):.1f}%</span>'


def kpi_tile(
    label: str,
    value: str,
    *,
    unit: str = "",
    sub: str = "",
    icon_name: str = "",
    tone: str = "accent",
    delta: float | None = None,
    chip_text: str = "",
    disabled: bool = False,
    compact: bool = False,
    icon_tone: str = "",
) -> str:
    """v4 KPI card: optional accent icon circle + label + big number + formula line.

    ``icon_name`` set -> filled circle with white glyph (home KPIs). Without icon the
    card is the plain label+number variant (equipment / anomaly pages). ``disabled``
    renders the faint, non-active variant (탄소비용 미설정). ``chip_text`` adds a small
    chip such as ``설정값 기준``.
    """

    cls = "fec-tile"
    if compact:
        cls += " fec-tile-sm"
    if disabled:
        cls += " fec-tile-off"
    if tone in theme.METRIC_KEYS and not disabled:
        cls += f" fec-tile-{tone}"
    icon_html = (
        f'<span class="fec-tile-icon{" fec-tile-icon-warn" if icon_tone == "warn" else ""}">'
        f'{icons.icon(icon_name, size=20)}</span>' if icon_name else ""
    )
    unit_html = f'<span class="fec-tile-unit">{_esc(unit)}</span>' if unit else ""
    chip_html = f'<span class="fec-tile-chip">{_esc(chip_text)}</span>' if chip_text else ""
    sub_html = f'<div class="fec-tile-sub">{_esc(sub)}</div>' if sub else ""
    return (
        f'<div class="{cls}">'
        f'<div class="fec-tile-top">{icon_html}<span class="fec-tile-label">{_esc(label)}</span>{chip_html}</div>'
        f'<div class="fec-tile-value"><span class="fec-tile-num">{_esc(value)}</span>{unit_html}'
        f"{delta_chip(delta)}</div>"
        f"{sub_html}</div>"
    )


def alert_strip(
    total: int, *, interval_count: int = 0, surge_count: int = 0, pattern_count: int = 0,
    error: bool = False,
) -> str:
    """Warning summary strip (v4 §2.3). Error/empty/active are three distinct states."""

    if error:
        return (
            '<div class="fec-strip fec-strip-error">'
            f'{icons.icon("anomaly", size=20)}'
            "<span>경고 데이터를 일부 불러오지 못함 — 요약 숫자를 표시하지 않습니다</span></div>"
        )
    if total <= 0:
        return (
            '<div class="fec-strip fec-strip-ok">'
            f'{icons.icon("check", size=20)}<span>경고 없음</span></div>'
        )
    parts = []
    if interval_count:
        parts.append(f"예측구간 이탈 {interval_count}시간")
    if pattern_count:
        parts.append(f"이상 6종 탐지 {pattern_count}건")
    if surge_count:
        parts.append(f"이상 급증 설비 {surge_count}대")
    if not parts:
        parts.append(f"경고 {total}건")
    return (
        '<div class="fec-strip fec-strip-warn">'
        f'{icons.icon("anomaly", size=20)}<span>{_esc(" · ".join(parts))}</span></div>'
    )


def alert_row(level: str, title: str, evidence: str, time_text: str = "") -> str:
    """One alert list row: icon tile + title + evidence line + time (v4 §2.5)."""

    is_alert = level == "alert"
    tile_cls = "fec-arow-tile fec-arow-alert" if is_alert else "fec-arow-tile fec-arow-warn"
    glyph = icons.icon("alert_sq" if is_alert else "anomaly", size=18)
    time_html = f'<span class="fec-arow-time">{_esc(time_text)}</span>' if time_text else ""
    return (
        '<div class="fec-arow">'
        f'<span class="{tile_cls}">{glyph}</span>'
        '<div class="fec-arow-body">'
        f'<div class="fec-arow-title">{_esc(title)}</div>'
        f'<div class="fec-arow-evi">{_esc(evidence)}</div>'
        "</div>"
        f"{time_html}</div>"
    )


def error_state(message: str = ERROR_STATE_TITLE, *, artifact: str = "") -> str:
    """Error state (file missing / unreadable) — distinct from an empty state.

    Shows a short message + the artifact file name only; never a raw exception or
    stack text (v3 §8). Summary numbers must not be drawn next to this state.
    """

    file_html = f'<div class="fec-error-file">파일: {_esc(artifact)}</div>' if artifact else ""
    return (
        '<div class="fec-error">'
        f'<div class="fec-error-title">{_esc(message)}</div>{file_html}</div>'
    )


def flow_step(no: str, title: str, value: str, caption: str) -> str:
    """Analysis-flow step card (v4 §2.8)."""

    return (
        '<div class="fec-flow">'
        f'<div class="fec-flow-head"><span class="fec-flow-no">{_esc(no)}</span>'
        f'<span class="fec-flow-title">{_esc(title)}</span></div>'
        f'<div class="fec-flow-value">{_esc(value)}</div>'
        f'<div class="fec-flow-cap">{_esc(caption)}</div></div>'
    )


def calc_box(rows: list[tuple[str, str]]) -> str:
    """Python 계산값 box: warm outer border + white inner mono table (v4 §2.10)."""

    body = "".join(
        f'<div class="fec-calc2-row"><span>{_esc(k)}</span><span>{_esc(v)}</span></div>'
        for k, v in rows
    )
    return (
        '<div class="fec-calc2">'
        '<span class="fec-tag fec-tag-calc">Python 계산값</span>'
        f'<div class="fec-calc2-inner">{body}</div></div>'
    )


def legend(items: list[tuple[str, str, str]]) -> str:
    """HTML legend for a panel header. ``items`` = [(label, colour, kind)] where kind
    is ``line`` / ``dash`` / ``box``. Colours are passed by the caller from theme."""

    parts = []
    for label, color, kind in items:
        mark_cls = {"line": "fec-lg-line", "dash": "fec-lg-dash", "box": "fec-lg-box"}.get(kind, "fec-lg-line")
        parts.append(
            f'<span class="fec-lg"><span class="{mark_cls}" style="--lg:{color};"></span>{_esc(label)}</span>'
        )
    return '<div class="fec-legend">' + "".join(parts) + "</div>"


# ---------------------------------------------------------------------------
# Briefing card (AI Copilot): one HTML block rendered from a structured Briefing
# ---------------------------------------------------------------------------
_BOLD_NUM = None


def _bold_numbers(text: str) -> str:
    """Escape ``text`` and wrap numbers (with their unit suffix) in <b>."""

    import re

    global _BOLD_NUM
    if _BOLD_NUM is None:
        _BOLD_NUM = re.compile(r"(\d[\d,]*(?:\.\d+)?(?:억 원|만 원|억|만|원|%|시간|시|일|대|건|배|V)?)")
    escaped = html.escape(str(text), quote=False)
    return _BOLD_NUM.sub(r"<b>\1</b>", escaped)


def briefing_card(b) -> str:
    """Render a ``Briefing`` (src.agent.briefing) as the readable card (v3 §4)."""

    kpis = "".join(
        f'<div class="fec-brief-kpi"><span class="fec-brief-kl">{_esc(k)}</span>'
        f'<span class="fec-brief-kv">{_esc(v)}</span></div>'
        for k, v in b.headline
    )
    items = []
    for i, ln in enumerate(b.lines, 1):
        tone = f" fec-bl-{ln.tone}" if ln.tone in ("watch", "alert") else ""
        chips = "".join(
            f'<span class="fec-bchip">{_esc(m)} <b>{_esc(r)}</b></span>' for m, r in ln.chips
        )
        items.append(
            f'<li class="fec-bl{tone}"><span class="fec-bl-no">{i}</span>'
            f'<span class="fec-bl-label">{_esc(ln.label)}</span>'
            f'<span class="fec-bl-text">{_bold_numbers(ln.text)}{chips}</span></li>'
        )
    limits = " · ".join(_esc(x) for x in b.limits)
    sources = []
    for ln in b.lines:
        for s in ln.sources:
            if s not in sources:
                sources.append(s)
    src_html = "".join(f'<span class="fec-source">{_esc(s)}</span>' for s in sources)
    return (
        '<div class="fec-brief">'
        f'<div class="fec-brief-head">{_esc(b.title)} · {_esc(b.period_label)}</div>'
        f'<div class="fec-brief-kpis">{kpis}</div>'
        f'<ol class="fec-brief-lines">{"".join(items)}</ol>'
        f'<div class="fec-brief-limits">분석 한계 · {limits}</div>'
        f'<div class="fec-brief-src">출처 {src_html}</div>'
        "</div>"
    )
