"""Single number-formatting source for the dashboard (steering A-1/A-6).

Every number shown anywhere — KPI cards, tables, chart hovers, the AI tab's
calc-value table — is routed through one of these functions, so the same value
is always rendered as the same string (prevents the C2/S5 unit-mismatch class of
bug, A-0). Units come from ``config.UNIT_LABELS`` only; the dashboard never
hard-codes "kWh" / "원" because the raw activePower unit is unconfirmed (A-1).

Large magnitudes use a single abbreviation scheme (억/만) so power, bill and
carbon totals never appear with mixed scales (A-0). Missing / non-numeric values
collapse to the shared ``데이터에 없음`` sentinel.
"""

from __future__ import annotations

from typing import Any

from config import config
from src.agent import tools as tools_mod

NO_DATA = tools_mod.NO_DATA

# Korean myriad grouping thresholds (A-0: 큰 수는 억/만 축약 통일).
_EOK = 100_000_000  # 억
_MAN = 10_000       # 만


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def abbreviate(value: Any, *, decimals: int = 2) -> str:
    """Abbreviate a magnitude with a single 억/만 scheme (A-0).

    Values >= 1억 render as ``N.NN억``; >= 1만 as ``N.NN만``; otherwise a plain
    thousands-separated number. Negative values keep their sign. Non-numeric
    input returns the ``데이터에 없음`` sentinel.
    """

    if not _is_number(value):
        return NO_DATA
    num = float(value)
    sign = "-" if num < 0 else ""
    mag = abs(num)
    if mag >= _EOK:
        return f"{sign}{mag / _EOK:,.{decimals}f}억"
    if mag >= _MAN:
        return f"{sign}{mag / _MAN:,.{decimals}f}만"
    return f"{sign}{mag:,.{decimals}f}"


def _with_unit(text: str, unit_key: str | None) -> str:
    if unit_key is None:
        return text
    label = config.UNIT_LABELS.get(unit_key)
    return f"{text} {label}" if label else text


def fmt_pow(value: Any, *, with_unit: bool = False) -> str:
    """Format a power quantity (data scale). Big values abbreviated (억/만)."""

    text = abbreviate(value)
    if text == NO_DATA:
        return text
    return _with_unit(text, "pow") if with_unit else text


def fmt_bill(value: Any, *, with_unit: bool = False) -> str:
    """Format a bill quantity (answer scale, ×180). Big values abbreviated."""

    text = abbreviate(value)
    if text == NO_DATA:
        return text
    return _with_unit(text, "bill") if with_unit else text


def fmt_carbon(value: Any, *, with_unit: bool = False) -> str:
    """Format a carbon quantity (answer scale, ×0.424). Big values abbreviated."""

    text = abbreviate(value)
    if text == NO_DATA:
        return text
    return _with_unit(text, "carbon") if with_unit else text


def fmt_mae(value: Any) -> str:
    """Format a MAE value with one decimal (88.18 -> 88.2); shared by every surface."""

    if not _is_number(value):
        return NO_DATA
    return f"{float(value):,.1f}"


def fmt_pct(value: Any, *, decimals: int = 2, is_ratio: bool = True) -> str:
    """Format a percentage. ``is_ratio=True`` treats input as 0..1 and ×100."""

    if not _is_number(value):
        return NO_DATA
    num = float(value) * 100.0 if is_ratio else float(value)
    return f"{num:,.{decimals}f}%"


def fmt_count(value: Any) -> str:
    """Format an exact integer count with thousands separators — never abbreviated.

    Anomaly row counts (e.g. 60,312) must show in full, so this does NOT use the
    억/만 abbreviation scheme (§7-4). Non-numeric input returns the sentinel.
    """

    if not _is_number(value):
        return NO_DATA
    return f"{int(round(float(value))):,d}"


def fmt_ratio_pct(value: Any, *, decimals: int = 2) -> str:
    """Format a 0..1 ratio as a percentage with ×100 and two decimals (§7-2).

    e.g. 0.0050 -> ``0.50%``. Shared by the KPI/overview text and the status
    table's ProgressColumn label so the ratio reads identically everywhere.
    """

    return fmt_pct(value, decimals=decimals, is_ratio=True)


def unit_label(unit_key: str) -> str:
    """Return the configured unit label for a key (pow/bill/carbon)."""

    return config.UNIT_LABELS.get(unit_key, "")


def unit_footnote() -> str:
    """Return the shared unit footnote caption (A-1)."""

    return config.UNIT_FOOTNOTE


# ---------------------------------------------------------------------------
# Short, human-readable forms used by briefings and the calc-value table.
# ---------------------------------------------------------------------------
def fmt_krw_short(value: Any) -> str:
    """Bill in won, no decimals: 3556690522.53 -> ``35.6억 원``; 4,820만 원; 9,500원."""

    if not _is_number(value):
        return NO_DATA
    v = float(value)
    mag = abs(v)
    sign = "-" if v < 0 else ""
    if mag >= _EOK:
        return f"{sign}{mag / _EOK:,.1f}억 원"
    if mag >= _MAN:
        return f"{sign}{round(mag / _MAN):,d}만 원"
    return f"{sign}{round(mag):,d}원"


def fmt_carbon_short(value: Any) -> str:
    """Carbon in 억/만 with one decimal: 8377982.12 -> ``837.8만`` (unit label is separate)."""

    if not _is_number(value):
        return NO_DATA
    v = float(value)
    mag = abs(v)
    sign = "-" if v < 0 else ""
    if mag >= _EOK:
        return f"{sign}{mag / _EOK:,.1f}억"
    if mag >= _MAN:
        return f"{sign}{mag / _MAN:,.1f}만"
    return f"{sign}{mag:,.1f}"


def fmt_pow_short(value: Any) -> str:
    """Power total: 19733651 -> ``1,973만``; under 100만 stays a plain integer (39130 -> ``39,130``)."""

    if not _is_number(value):
        return NO_DATA
    v = float(value)
    mag = abs(v)
    sign = "-" if v < 0 else ""
    if mag >= _EOK:
        return f"{sign}{mag / _EOK:,.1f}억"
    if mag >= 1_000_000:
        return f"{sign}{round(mag / _MAN):,d}만"
    return f"{sign}{round(mag):,d}"


def fmt_period(start: Any, end: Any) -> str:
    """``2025-05-10 00:00:00`` ~ ``2025-05-31 00:00:00`` -> ``05-10 ~ 05-31 (22일)``."""

    from datetime import datetime

    def _d(value: Any):
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d")
        except ValueError:
            return None

    s, e = _d(start), _d(end)
    if s is None or e is None:
        return NO_DATA
    days = (e - s).days + 1
    return f"{s:%m-%d} ~ {e:%m-%d} ({days}일)"


def fmt_time(value: Any) -> str:
    """``2025-05-14 03:00:00`` (or ISO ``T``) -> ``05-14 03시``."""

    from datetime import datetime

    text = str(value).replace("T", " ")[:19]
    try:
        moment = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            moment = datetime.strptime(text[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            return NO_DATA
    return f"{moment:%m-%d %H}시"


def fmt_count_per_day(value: Any) -> str:
    """Format a daily-average count as a rounded integer + '건': ``312.4 -> "312건"``.

    Used by the 탐지 시간대 panel's '일평균 N건' chip (§4) and the per-module row
    label in the §2.1 small-multiples chart — the one place a non-integer
    (an average) still needs the exact-count '건' suffix.
    """

    if not _is_number(value):
        return NO_DATA
    return f"{int(round(float(value))):,d}건"


def fmt_time_full(value: Any) -> str:
    """``2025-05-14 03:00:12`` (or ISO ``T``) -> ``03:00:12`` (§2.3 event table).

    Unlike ``fmt_time`` this keeps seconds and drops the date — the 일자 상세
    event table already shows one fixed day, so only the time-of-day matters.
    """

    from datetime import datetime

    text = str(value).replace("T", " ")[:19]
    try:
        moment = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return NO_DATA
    return f"{moment:%H:%M:%S}"
