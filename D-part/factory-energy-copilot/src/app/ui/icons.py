"""Inline SVG icons (Lucide-style) for the dashboard (v2 §6).

No external CDN or font-icon dependency (steering: 외부 CDN 금지). Each function
returns a self-contained ``<svg>`` string sized to ``size`` px that inherits the
surrounding text colour via ``stroke="currentColor"``. The paths are simple
Lucide-style line glyphs redrawn here so nothing is fetched at runtime.
"""

from __future__ import annotations

_STROKE = (
    'fill="none" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round"'
)

# Each entry is the inner markup of a 24×24 viewBox icon.
_PATHS: dict[str, str] = {
    # layout-dashboard
    "overview": '<rect x="3" y="3" width="7" height="9" rx="1"/>'
    '<rect x="14" y="3" width="7" height="5" rx="1"/>'
    '<rect x="14" y="12" width="7" height="9" rx="1"/>'
    '<rect x="3" y="16" width="7" height="5" rx="1"/>',
    # trending-up
    "forecast": '<polyline points="3 17 9 11 13 15 21 7"/>'
    '<polyline points="15 7 21 7 21 13"/>',
    # alert-triangle
    "anomaly": '<path d="M10.3 3.6 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3'
    'L13.7 3.6a2 2 0 0 0-3.4 0Z"/><line x1="12" y1="9" x2="12" y2="13"/>'
    '<line x1="12" y1="17" x2="12.01" y2="17"/>',
    # message-square
    "copilot": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z"/>',
    # bar-chart
    "chart": '<line x1="12" y1="20" x2="12" y2="10"/>'
    '<line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/>',
    # activity
    "pulse": '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
    # bell
    "bell": '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/>'
    '<path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
    # cpu / chip (AI)
    "cpu": '<rect x="4" y="4" width="16" height="16" rx="2"/>'
    '<rect x="9" y="9" width="6" height="6"/>'
    '<line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/>'
    '<line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/>'
    '<line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/>'
    '<line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/>',
    # grid / modules
    "grid": '<rect x="3" y="3" width="7" height="7" rx="1"/>'
    '<rect x="14" y="3" width="7" height="7" rx="1"/>'
    '<rect x="14" y="14" width="7" height="7" rx="1"/>'
    '<rect x="3" y="14" width="7" height="7" rx="1"/>',
    # clock
    "clock": '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/>',
    # check-circle
    "check": '<circle cx="12" cy="12" r="9"/><polyline points="8 12 11 15 16 9"/>',
    # lock
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/>'
    '<path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    # calendar
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/>'
    '<line x1="3" y1="10" x2="21" y2="10"/><line x1="8" y1="3" x2="8" y2="7"/>'
    '<line x1="16" y1="3" x2="16" y2="7"/>',
    # zap (power)
    "zap": '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    # wallet (bill)
    "wallet": '<path d="M3 7a2 2 0 0 1 2-2h13v4"/><path d="M3 7v11a2 2 0 0 0 2 2h14a1 1 0 0 0 1-1V10a1 1 0 0 0-1-1H5a2 2 0 0 1-2-2Z"/>'
    '<circle cx="16.5" cy="14.5" r="1"/>',
    # leaf (carbon)
    "leaf": '<path d="M11 20A7 7 0 0 1 4 13c0-5 6-9 16-9 0 10-4 16-9 16Z"/>'
    '<path d="M4 21c3-6 6-9 10-11"/>',
    # target (accuracy)
    "target": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    # square-alert
    "alert_sq": '<rect x="4" y="4" width="16" height="16" rx="2"/>'
    '<line x1="12" y1="8" x2="12" y2="13"/><line x1="12" y1="16" x2="12.01" y2="16"/>',
    # chevron-right
    "chevron": '<polyline points="9 6 15 12 9 18"/>',
    # sparkles (AI summary)
    "sparkles": '<path d="M12 3l1.8 4.2L18 9l-4.2 1.8L12 15l-1.8-4.2L6 9l4.2-1.8Z"/>'
    '<path d="M19 15l.8 1.9L21.5 18l-1.7.8L19 21l-.8-1.9L16.5 18l1.7-.8Z"/>',
}


def icon(name: str, *, size: int = 16) -> str:
    """Return an inline ``<svg>`` for ``name`` (empty string for unknown names)."""

    inner = _PATHS.get(name)
    if inner is None:
        return ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 24 24" {_STROKE} aria-hidden="true" '
        f'style="vertical-align:-2px;flex:none;">{inner}</svg>'
    )
