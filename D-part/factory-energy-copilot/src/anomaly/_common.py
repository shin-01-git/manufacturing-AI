"""Shared helpers for the anomaly modules (column resolution, row parsing, logging).

The anomaly modules (rules, detectors, compare, artifacts, impact) all read the
same 5-second training CSV in a bounded, sequential pass. This module centralizes
the pieces they share so the behaviour stays consistent:

- header column resolution by name (aliases),
- KST ``localtime`` parsing (14-digit ``YYYYMMDDHHMMSS``, timestamp ignored — NFR-3),
- the candidate feature vector used by the statistical/unsupervised detectors,
- a per-run logger and a reproducibility run-metadata block (NFR-7),
- a source-immutability guard built on ``src.common.io_meta`` (NFR-2).

No module in ``src.anomaly`` loads the whole 4.9GB source into memory; all reading
goes through ``src.common.streaming_csv`` row callbacks (NFR-1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import math
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from config import config
from src.common.io_meta import compare_file_metadata, get_file_metadata

KST = ZoneInfo("Asia/Seoul")

# Candidate feature names used by the statistical / unsupervised detectors (6.2).
# Order is fixed so the Isolation Forest sees identical columns in both passes.
FEATURE_NAMES: tuple[str, ...] = (
    "voltage_mean",       # 3상 전압 평균
    "power_factor_mean",  # 3상 역률 평균
    "current_mean",       # 3상 전류 평균
    "active_power",       # activePower
    "power_ratio",        # activePower / Σ(V×I×PF/100)
)

# Column aliases keyed by the logical field name. Matching is whitespace- and
# underscore-insensitive and case-folded (see ``_normalise_column``).
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "module": ("module(equipment)", "module", "equipment"),
    "localtime": ("localtime",),
    "voltageR": ("voltageR",),
    "voltageS": ("voltageS",),
    "voltageT": ("voltageT",),
    "currentR": ("currentR",),
    "currentS": ("currentS",),
    "currentT": ("currentT",),
    "activePower": ("activePower", "active_power"),
    "powerFactorR": ("powerFactorR",),
    "powerFactorS": ("powerFactorS",),
    "powerFactorT": ("powerFactorT",),
}

# Fields every anomaly module needs to resolve from the header.
REQUIRED_FIELDS: tuple[str, ...] = tuple(_COLUMN_ALIASES.keys())


def _normalise_column(name: str) -> str:
    return "".join(name.casefold().split()).replace("_", "")


def find_column(header: list[str], field: str) -> str | None:
    """Return the real header name for ``field`` by alias, or None if absent."""

    aliases = {_normalise_column(alias) for alias in _COLUMN_ALIASES[field]}
    for column in header:
        if _normalise_column(column) in aliases:
            return column
    return None


def resolve_columns(header: list[str]) -> dict[str, str]:
    """Map every required logical field to its real column name.

    Raises ``ValueError`` if any required field is missing so a schema mismatch
    fails loudly instead of silently producing wrong features.
    """

    resolved: dict[str, str] = {}
    missing: list[str] = []
    for field in REQUIRED_FIELDS:
        column = find_column(header, field)
        if column is None:
            missing.append(field)
        else:
            resolved[field] = column
    if missing:
        raise ValueError(f"Required CSV columns not found by header name: {missing}")
    return resolved


def parse_localtime(value: str) -> datetime:
    """Parse a 14-digit KST ``localtime`` into a timezone-aware datetime.

    ``timestamp`` is never used (NFR-3); only ``localtime`` defines the KST hour.
    """

    text = value.strip()
    if len(text) != 14 or not text.isdigit():
        raise ValueError("localtime must be a 14-digit YYYYMMDDHHMMSS value")
    try:
        parsed = datetime.strptime(text, "%Y%m%d%H%M%S")
    except ValueError as exc:
        raise ValueError("localtime is not a valid calendar date/time") from exc
    return parsed.replace(tzinfo=KST)


@dataclass
class ParsedRow:
    """A single 5-second observation with the fields the detectors need."""

    module: str
    localtime: datetime
    voltageR: float
    voltageS: float
    voltageT: float
    currentR: float
    currentS: float
    currentT: float
    active_power: float
    powerFactorR: float
    powerFactorS: float
    powerFactorT: float

    @property
    def voltage_mean(self) -> float:
        return (self.voltageR + self.voltageS + self.voltageT) / 3.0

    @property
    def power_factor_mean(self) -> float:
        return (self.powerFactorR + self.powerFactorS + self.powerFactorT) / 3.0

    @property
    def current_mean(self) -> float:
        return (self.currentR + self.currentS + self.currentT) / 3.0

    @property
    def power_ratio(self) -> float:
        """activePower / Σ(V×I×PF/100). Returns 0.0 when the denominator is ~0."""

        denom = (
            self.voltageR * self.currentR * self.powerFactorR / 100.0
            + self.voltageS * self.currentS * self.powerFactorS / 100.0
            + self.voltageT * self.currentT * self.powerFactorT / 100.0
        )
        if abs(denom) < 1e-9:
            return 0.0
        return self.active_power / denom

    def feature_vector(self) -> list[float]:
        """Return the candidate feature vector in ``FEATURE_NAMES`` order."""

        return [
            self.voltage_mean,
            self.power_factor_mean,
            self.current_mean,
            self.active_power,
            self.power_ratio,
        ]


def parse_row(row: dict[str, str], columns: dict[str, str]) -> ParsedRow | None:
    """Parse one raw CSV record into a ``ParsedRow``.

    Returns None when a required field is empty, the localtime is malformed, or a
    numeric field is non-numeric/non-finite. Callers count these as skipped rows.
    """

    try:
        raw_module = row[columns["module"]]
        raw_time = row[columns["localtime"]]
    except KeyError:
        return None
    if not raw_module.strip() or not raw_time.strip():
        return None
    try:
        localtime = parse_localtime(raw_time)
    except ValueError:
        return None

    numeric: dict[str, float] = {}
    for field in (
        "voltageR",
        "voltageS",
        "voltageT",
        "currentR",
        "currentS",
        "currentT",
        "activePower",
        "powerFactorR",
        "powerFactorS",
        "powerFactorT",
    ):
        raw = row.get(columns[field], "")
        if raw is None or not str(raw).strip():
            return None
        try:
            parsed_value = float(raw)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(parsed_value):
            return None
        numeric[field] = parsed_value

    return ParsedRow(
        module=raw_module.strip(),
        localtime=localtime,
        voltageR=numeric["voltageR"],
        voltageS=numeric["voltageS"],
        voltageT=numeric["voltageT"],
        currentR=numeric["currentR"],
        currentS=numeric["currentS"],
        currentT=numeric["currentT"],
        active_power=numeric["activePower"],
        powerFactorR=numeric["powerFactorR"],
        powerFactorS=numeric["powerFactorS"],
        powerFactorT=numeric["powerFactorT"],
    )


def event_key(module: str, localtime: datetime) -> tuple[str, str]:
    """Stable 5-second event key (module, localtime) used to match methods (B1)."""

    return (module, localtime.strftime("%Y%m%d%H%M%S"))


def configure_logger(name: str, log_dir: Path) -> logging.Logger:
    """Create a console+file logger under ``log_dir`` (mirrors aggregate.py)."""

    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"factory_energy_copilot.anomaly.{name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = logging.FileHandler(log_dir / f"anomaly_{name}.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger


def reproducibility_meta(
    *,
    module_name: str,
    input_path: Path,
    max_rows: int | None,
    chunk_size: int,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a reproducibility log block (NFR-7).

    Records run time, input path, KST time basis, chunk settings, random seed, and
    (for the unsupervised path) sample size via ``extra``. No secrets are included.
    """

    meta: dict[str, Any] = {
        "module": module_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path),
        "time_basis": f"{config.TIME_BASE} (KST)",
        "timestamp_column_used": False,
        "chunk": {"size_bytes": chunk_size, "max_rows": max_rows},
        "random_seed": config.RANDOM_SEED,
        "partial": max_rows is not None,
    }
    if extra:
        meta.update(extra)
    return meta


def source_metadata(path: Path, *, compute_hash: bool | None = None) -> Any:
    """Snapshot the source for an immutability check (NFR-2).

    Hashing a multi-GB source would add a second full scan, so hashing defaults to
    on only for small files unless the caller forces it.
    """

    if compute_hash is None:
        compute_hash = path.stat().st_size <= 64 * 1024 * 1024
    return get_file_metadata(path, compute_hash=compute_hash)


def assert_source_unchanged(before: Any, after: Any) -> dict[str, Any]:
    """Raise if the source changed between two snapshots; else return comparison."""

    comparison = compare_file_metadata(before, after)
    if not comparison["unchanged"]:
        raise RuntimeError("Source CSV changed during anomaly processing")
    return comparison
