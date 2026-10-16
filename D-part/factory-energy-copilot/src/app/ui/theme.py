"""Design tokens + the single Plotly template ``fec_template`` (steering A-2, A-9).

Every colour of the dashboard lives in ``THEMES`` below (and nowhere else). The
active theme (``ACTIVE_THEME``) feeds three consumers:

- ``css_variables()`` -> a ``:root { --fec-*: ... }`` block prepended to
  ``style.css`` by ``load_css()`` (CSS only ever uses ``var(--fec-*)``);
- Python accessors (``metric``, ``metric_text``, ``tint``, ``status_color`` ...)
  used by components/charts/tabs (no hex literals outside this file);
- the Plotly template ``fec_template``.

Adding a dark theme later = adding another entry with the *same key set* to
``THEMES`` and switching ``ACTIVE_THEME`` (see steering A-9). Status colours
(ok/watch/alert/unknown) are reserved for equipment status and warnings — never
chart series colours; data series use the metric colours (A-2).
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Themes — the ONLY place colour literals are allowed (steering A-9).
# Keys per metric: fill colour (lines/bars/bands) and a darker "_text" colour
# for text on a light surface. Light-tint backgrounds are *derived* with
# ``tint()`` (metric colour + alpha), never defined as separate colours.
# ---------------------------------------------------------------------------
THEMES: dict[str, dict] = {
    "light": {
        # neutral / brand
        "bg": "#F5F6F8",
        "surface": "#FFFFFF",
        "border": "#E3E6EB",
        "nav": "#0F1B2D",
        "text": "#1B2430",
        "muted": "#6B7280",
        "accent": "#0E9F8E",
        "accent_bg": "#E3F4F1",
        "actual": "#334155",
        "subtle": "#EEF0F3",
        "subtle_strong": "#DADFE6",
        "on_nav": "#FFFFFF",
        "on_nav_muted": "#AEB7C4",
        # v4 visual tokens (side nav / cards / warning strip / calc box)
        "faint": "#9CA3AF",
        "hover": "#F3F4F6",
        "chip_bg": "#EEF0F3",
        "grid": "#EEF0F3",
        "bar_gray": "#D9DDE3",
        "partial": "#CFEDE8",
        "warn": "#D97706",
        "warn_bg": "#FFF4DE",
        "warn_border": "#F6D58E",
        "alert_bg": "#FDE8E8",
        "alert_strong": "#DC2626",
        "calc_bg": "#FFF8EB",
        "calc_border": "#F5C26B",
        "calc_inner": "#FBFBFC",
        "ok_dot": "#10B981",
        "heat_low": "#F1F5F4",
        "heat_high": "#0B6E63",
        # metric colours (fill) + text colours
        "power": "#3B82F6",
        "power_text": "#1D4ED8",
        "bill": "#8B5CF6",
        "bill_text": "#6D28D9",
        "carbon": "#10B981",
        "carbon_text": "#047857",
        "accuracy": "#6366F1",
        "accuracy_text": "#4338CA",
        "anomaly": "#F43F5E",
        "anomaly_text": "#BE123C",
        "info": "#0EA5E9",
        "info_text": "#0369A1",
        # status colours (equipment status / warnings only) + text colours
        "ok": "#22C55E",
        "ok_text": "#15803D",
        "watch": "#F59E0B",
        "watch_text": "#B45309",
        "alert": "#EF4444",
        "alert_text": "#B91C1C",
        "unknown": "#9CA3AF",
        "unknown_text": "#6B7280",
        # three-phase line colours (distinct from the status colours)
        "phase_r": "#0EA5E9",
        "phase_s": "#A855F7",
        "phase_t": "#14B8A6",
        # tint alpha: metric colour + this alpha = light background
        "tint_alpha": 0.12,
    },
}
ACTIVE_THEME = "light"

METRIC_KEYS: tuple[str, ...] = ("power", "bill", "carbon", "accuracy", "anomaly", "info")
STATUS_KEYS: tuple[str, ...] = ("ok", "watch", "alert", "unknown")
PHASE_KEYS: tuple[str, ...] = ("phase_r", "phase_s", "phase_t")
# Tones accepted by kpi_card (neutral = no metric colour).
KPI_TONES: tuple[str, ...] = ("power", "bill", "carbon", "accuracy", "anomaly", "neutral")


def active() -> dict:
    """The active theme's colour dictionary."""

    return THEMES[ACTIVE_THEME]


def hex_to_rgba(hex_color: str, alpha: float) -> str:
    """``#RRGGBB`` -> ``rgba(r,g,b,alpha)``."""

    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha:g})"


def tint(color: str, alpha: float | None = None) -> str:
    """Light background derived from a colour: ``rgba`` of the colour at ``alpha``.

    ``color`` is either a hex string or a theme key (e.g. ``'power'``, ``'alert'``,
    ``'accent'``). ``alpha`` defaults to the active theme's ``tint_alpha`` so the
    same rule works for every theme (light 0.12 / dark 0.18).
    """

    theme = active()
    base = theme.get(color, color)
    a = theme["tint_alpha"] if alpha is None else alpha
    return hex_to_rgba(base, a)


def metric(name: str) -> str:
    """Fill colour for a metric (power/bill/carbon/accuracy/anomaly/info)."""

    return active()[name]


def metric_text(name: str) -> str:
    """Darker text colour for a metric (readable on the surface)."""

    return active()[f"{name}_text"]


def token(name: str) -> str:
    """Raw colour token from the active theme (neutral / v4 visual tokens)."""

    return active()[name]


def phase(name: str) -> str:
    """Three-phase line colour: ``'R'``, ``'S'`` or ``'T'``."""

    return active()[f"phase_{name.lower()}"]


# ---------------------------------------------------------------------------
# Backward-compatible names — all read from the active theme.
# ---------------------------------------------------------------------------
_T = active()
COLOR: dict[str, str] = {
    "bg": _T["bg"],
    "surface": _T["surface"],
    "border": _T["border"],
    "nav": _T["nav"],
    "text": _T["text"],
    "muted": _T["muted"],
    "accent": _T["accent"],
    "accent_bg": _T["accent_bg"],
    "actual": _T["actual"],
    "band": tint("power"),
    "ok": _T["ok"],
    "watch": _T["watch"],
    "alert": _T["alert"],
    "unknown": _T["unknown"],
}

RADIUS = 8
GAP = 16
CARD_PAD = 20
FONT = 'Pretendard, "Noto Sans KR", -apple-system, "Segoe UI", sans-serif'
NUM_FONT = "tabular-nums"

# Panel height budget (v2 §A-8): chart height = panel height − header − padding.
PANEL_HEADER_H = 44
PANEL_PAD = 32
# Extra vertical space a panel-level st.caption consumes under the chart (버그3).
PANEL_CAPTION_H = 30


def chart_height(panel_height: int, has_caption: bool = False) -> int:
    """Chart height inside a bordered panel.

    = panel height − header − padding, minus an extra allowance when the panel
    also renders an ``st.caption`` under the chart, so the chart + caption fit
    inside the panel without an inner scrollbar (버그3).
    """

    budget = int(panel_height) - PANEL_HEADER_H - PANEL_PAD
    if has_caption:
        budget -= PANEL_CAPTION_H
    return max(120, budget)


# Status label -> colour (equipment status / warnings only, A-2).
STATUS_COLOR: dict[str, str] = {
    "정상": _T["ok"],
    "주의": _T["watch"],
    "이상": _T["alert"],
    "미확정": _T["unknown"],
}
DEFAULT_STATUS_COLOR = _T["unknown"]
# Status label -> theme key (to reach the _text colour / tint).
_STATUS_KEY: dict[str, str] = {
    "정상": "ok",
    "주의": "watch",
    "이상": "alert",
    "미확정": "unknown",
}

# Monochrome sequential scales. Heatmaps use the anomaly scale (white -> anomaly).
ACCENT_COLORSCALE = [
    [0.0, _T["surface"]],
    [0.5, _T["accent_bg"]],
    [1.0, _T["accent"]],
]
ANOMALY_COLORSCALE = [
    [0.0, _T["surface"]],
    [1.0, _T["anomaly"]],
]

FEC_TEMPLATE_NAME = "fec_template"

_STYLE_CSS = Path(__file__).resolve().parent / "style.css"


def _var(name: str) -> str:
    return name.replace("_", "-")


def css_variables() -> str:
    """``:root { --fec-*: ... }`` block generated from the active theme.

    Neutrals: ``--fec-<key>``. Metrics: ``--fec-c-<m>``, ``--fec-c-<m>-text``,
    ``--fec-c-<m>-tint``. Status: ``--fec-<s>``, ``--fec-<s>-text``,
    ``--fec-<s>-tint``. Phases: ``--fec-<phase_r>``. ``style.css`` uses only these.
    """

    t = active()
    lines: list[str] = []
    for key in (
        "bg", "surface", "border", "nav", "text", "muted", "accent", "accent_bg",
        "actual", "subtle", "subtle_strong", "on_nav", "on_nav_muted",
        "faint", "hover", "chip_bg", "grid", "bar_gray", "partial", "warn", "warn_bg",
        "warn_border", "alert_bg", "alert_strong", "calc_bg", "calc_border", "calc_inner",
        "ok_dot", "heat_low", "heat_high",
    ):
        lines.append(f"  --fec-{_var(key)}: {t[key]};")
    lines.append(f"  --fec-accent-tint: {tint('accent')};")
    for m in METRIC_KEYS:
        lines.append(f"  --fec-c-{m}: {t[m]};")
        lines.append(f"  --fec-c-{m}-text: {t[m + '_text']};")
        lines.append(f"  --fec-c-{m}-tint: {tint(m)};")
    for s in STATUS_KEYS:
        lines.append(f"  --fec-{s}: {t[s]};")
        lines.append(f"  --fec-{s}-text: {t[s + '_text']};")
        lines.append(f"  --fec-{s}-tint: {tint(s)};")
    for p in PHASE_KEYS:
        lines.append(f"  --fec-{_var(p)}: {t[p]};")
    return ":root {\n" + "\n".join(lines) + "\n}\n"


def load_css() -> str:
    """Return the CSS-variable block + the single dashboard CSS body (A-4)."""

    try:
        body = _STYLE_CSS.read_text(encoding="utf-8")
    except OSError:
        body = ""
    return css_variables() + body


def status_color(status: str) -> str:
    """Colour for an equipment status label (A-2 status colours)."""

    return STATUS_COLOR.get(status, DEFAULT_STATUS_COLOR)


def status_text_color(status: str) -> str:
    """Darker text colour for a status label (readable on the surface)."""

    key = _STATUS_KEY.get(status, "unknown")
    return active()[f"{key}_text"]


def status_tint(status: str) -> str:
    """Light background (tint) for a status label."""

    return tint(_STATUS_KEY.get(status, "unknown"))


def register_template() -> None:
    """Register the single Plotly template ``fec_template`` (idempotent).

    White background, faint horizontal grid only, legend at the top-right, and a
    shared hover label style. Colours come from the active theme; the colorway is
    the metric colours (status colours are not part of the template palette).
    """

    import plotly.graph_objects as go
    import plotly.io as pio

    if FEC_TEMPLATE_NAME in pio.templates:
        return

    t = active()
    template = go.layout.Template()
    template.layout = go.Layout(
        font=dict(family=FONT, color=t["text"], size=13),
        paper_bgcolor=t["surface"],
        plot_bgcolor=t["surface"],
        colorway=[t["power"], t["bill"], t["carbon"], t["accuracy"], t["anomaly"]],
        # Tighter top/bottom margins free vertical space so charts fit inside
        # panels without an inner scrollbar (버그3); legend is kept.
        margin=dict(l=52, r=20, t=24, b=34),
        xaxis=dict(
            showgrid=False,
            zeroline=False,
            linecolor=t["border"],
            ticks="outside",
            tickcolor=t["border"],
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor=t["grid"],
            zeroline=False,
            linecolor=t["border"],
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1.0,
            bgcolor="rgba(0,0,0,0)",
        ),
        hoverlabel=dict(
            bgcolor=t["surface"],
            bordercolor=t["border"],
            font=dict(family=FONT, color=t["text"], size=12),
        ),
    )
    pio.templates[FEC_TEMPLATE_NAME] = template


# 이상치 타임라인 유형 색(차트·범례 공용)
TIMELINE_PALETTE = ["#0E9F8E", "#D97706", "#6366F1", "#DB2777", "#0EA5E9", "#65A30D", "#9333EA", "#B45309"]
