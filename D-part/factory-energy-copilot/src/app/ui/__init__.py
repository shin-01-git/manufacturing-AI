"""UI rendering layer for the Streamlit dashboard (8.4 / TASK-UI).

Design tokens, CSS, number formatting, HTML components, and Plotly charts live
here. The pure logic in ``src/app/ui_logic.py`` and the artifacts-only tools in
``src/agent/tools.py`` are reused unchanged; this package only renders.

Single source of truth for design tokens: ``theme.py`` + ``.streamlit/config.toml``
(steering dashboard-ui A-2). Single source of truth for number formatting:
``fmt.py`` (A-6).
"""

from __future__ import annotations
