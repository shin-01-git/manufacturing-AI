"""Colour-system tests (steering A-2 / A-9): theme tokens, contrast, no literals."""

from __future__ import annotations

import pathlib
import re

import pytest

from src.app.ui import components, theme

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "src" / "app"

METRICS = theme.METRIC_KEYS
STATUSES = theme.STATUS_KEYS


def _lum(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    chans = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in chans]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_all_themes_share_the_same_key_set():
    key_sets = [set(t) for t in theme.THEMES.values()]
    assert key_sets and all(ks == key_sets[0] for ks in key_sets)
    assert theme.ACTIVE_THEME in theme.THEMES


@pytest.mark.parametrize("name", list(theme.THEMES))
def test_text_colours_meet_wcag_aa_on_surface(name):
    t = theme.THEMES[name]
    for key in list(METRICS) + list(STATUSES):
        ratio = _contrast(t[f"{key}_text"], t["surface"])
        assert ratio >= 4.5, f"{name}.{key}_text contrast {ratio:.2f} < 4.5"


@pytest.mark.parametrize("name", list(theme.THEMES))
def test_metric_colours_distinct_and_not_status(name):
    t = theme.THEMES[name]
    metric_fills = [t[m].lower() for m in METRICS]
    assert len(metric_fills) == 6 and len(set(metric_fills)) == 6
    status_fills = {t[s].lower() for s in STATUSES}
    assert status_fills.isdisjoint(metric_fills)
    # 3-phase colours must not collide with the status colours either.
    assert status_fills.isdisjoint({t[p].lower() for p in theme.PHASE_KEYS})


def test_backward_compatible_names_read_active_theme():
    t = theme.active()
    assert theme.COLOR["accent"] == t["accent"]
    assert theme.STATUS_COLOR["이상"] == t["alert"]
    assert theme.DEFAULT_STATUS_COLOR == t["unknown"]
    assert theme.status_color("정상") == t["ok"]
    assert theme.metric("power") == t["power"]
    assert theme.metric_text("power") == t["power_text"]


def test_tint_is_metric_colour_with_alpha():
    assert theme.tint("#3B82F6") == "rgba(59,130,246,0.12)"
    assert theme.tint("power") == theme.tint(theme.metric("power"))
    assert theme.tint("power", 0.18) == "rgba(59,130,246,0.18)"


def test_css_variables_include_metric_status_and_text_vars():
    css = theme.css_variables()
    assert css.startswith(":root {")
    for m in METRICS:
        for suffix in ("", "-text", "-tint"):
            assert f"--fec-c-{m}{suffix}:" in css
    for s in STATUSES:
        for suffix in ("", "-text", "-tint"):
            assert f"--fec-{s}{suffix}:" in css
    for p in ("phase-r", "phase-s", "phase-t", "bg", "surface", "accent", "nav"):
        assert f"--fec-{p}:" in css
    assert css in theme.load_css()


def _literal_scan_paths() -> list[pathlib.Path]:
    paths = list((APP_DIR / "tabs").glob("*.py"))
    paths += [
        APP_DIR / "ui" / "charts.py",
        APP_DIR / "ui" / "components.py",
        APP_DIR / "ui" / "style.css",
        APP_DIR / "ui_logic.py",
    ]
    return paths


def test_no_hex_colour_literals_outside_theme():
    pattern = re.compile(r"#[0-9A-Fa-f]{3,8}\b")
    offenders: list[str] = []
    for path in _literal_scan_paths():
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{no}: {line.strip()}")
    assert offenders == [], offenders


def test_kpi_card_tone_classes_and_default_compat():
    for tone in ("power", "bill", "carbon", "accuracy", "anomaly", "unknown", "watch"):
        assert f"fec-kpi-tone-{tone}" in components.kpi_card("t", "1", tone=tone)
    default = components.kpi_card("t", "1", formula="f")
    assert default.startswith('<div class="fec-kpi">')
    assert "fec-kpi-tone" not in default
    assert components.kpi_card("t", "1", formula="f", tone="neutral") == default


def test_chip_and_panel_header_tones():
    for tone in METRICS:
        assert f"fec-chip-{tone}" in components.chip("x", tone=tone)
    assert "fec-ph-tone-anomaly" in components.panel_header("t", icon_name="chart", tone="anomaly")
    assert "fec-ph-tone-" not in components.panel_header("t", icon_name="chart")


def test_plotly_template_uses_active_theme():
    import plotly.io as pio

    theme.register_template()
    layout = pio.templates[theme.FEC_TEMPLATE_NAME].layout
    assert layout.paper_bgcolor == theme.active()["surface"]
    assert layout.colorway[0] == theme.metric("power")
