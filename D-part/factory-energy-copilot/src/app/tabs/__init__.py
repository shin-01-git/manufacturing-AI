"""Tab renderers for the dashboard (steering A-5): forecast / anomaly / copilot.

Each module exposes a single ``render(...)`` that draws its tab from the loaded
artifacts bundle. Rendering only — all numbers come through ``ui.fmt`` and all
logic through ``ui_logic`` / the artifacts-only tools.
"""

from __future__ import annotations
