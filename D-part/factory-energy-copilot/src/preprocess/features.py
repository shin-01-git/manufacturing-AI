"""Create training features from the hourly aggregation artifact.

This module is a callable preprocessing utility for training.  It does not
make the generated feature file a required input for other project features.
All calendar values are derived from the KST ``hour`` field; source timestamp
columns are not used.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import argparse
import json
import logging
import os
from pathlib import Path
import re
import tempfile
from typing import Any
import warnings
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from config import config

KST = ZoneInfo("Asia/Seoul")
FEATURES_VERSION = "1.0.0"
MODULE_LIMIT = config.MODULE_COUNT_EXPECTED

# These are intentionally name-based rules so feature selection can be tested
# without reading data or consulting any external source.
_FORBIDDEN_FEATURE_PATTERNS = (
    re.compile(r"voltage", re.IGNORECASE),
    re.compile(r"current", re.IGNORECASE),
    re.compile(r"power\s*factor", re.IGNORECASE),
    re.compile(r"(?:^|[_-])lag(?:$|[_-])", re.IGNORECASE),
    re.compile(r"(?:^|[_-])recent(?:$|[_-])", re.IGNORECASE),
    re.compile(r"last[_-]?value", re.IGNORECASE),
)


def feature_is_forbidden(name: str) -> bool:
    """Return whether a candidate feature is unavailable at prediction time."""

    return any(pattern.search(name) for pattern in _FORBIDDEN_FEATURE_PATTERNS)


def get_forbidden_features(candidates: Iterable[str]) -> list[str]:
    """Return the forbidden candidates, preserving their original order."""

    return [name for name in candidates if feature_is_forbidden(name)]


def select_allowed_features(candidates: Iterable[str]) -> list[str]:
    """Select only features available without May sensor values or recent lag."""

    return [name for name in candidates if not feature_is_forbidden(name)]


# Descriptive aliases for callers that use the task terminology.
forbidden_features = get_forbidden_features
select_features = select_allowed_features


def _as_kst(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=KST)
    return value.astimezone(KST)


def make_calendar_features(value: datetime) -> dict[str, Any]:
    """Build deterministic calendar fields from one KST hour."""

    hour = _as_kst(value)
    iso_calendar = hour.isocalendar()
    return {
        "weekday": hour.weekday(),
        "hour_of_day": hour.hour,
        "day_of_month": hour.day,
        "week_of_year": iso_calendar.week,
        "is_weekend": hour.weekday() >= 5,
        "month": hour.month,
        "day_of_year": hour.timetuple().tm_yday,
    }


def aggregate_factory_hourly(
    rows: Iterable[Mapping[str, Any]],
    *,
    module_limit: int = MODULE_LIMIT,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Sum module hourly power by KST hour without limiting discovered modules.

    The discovered module set is the source of truth.  When it exceeds the
    configured expectation, all discovered modules remain in the calculation
    and a warning is returned in metadata rather than silently dropping data.
    """

    grouped: dict[datetime, float] = {}
    modules: set[str] = set()
    input_rows = 0
    for row in rows:
        input_rows += 1
        module = str(row["module"])
        hour = _as_kst(row["hour"])
        power = float(row["hourly_pow"])
        modules.add(module)
        grouped[hour] = grouped.get(hour, 0.0) + power

    discovered_modules = sorted(modules)
    exceeds_limit = len(discovered_modules) > module_limit
    warning_message = (
        f"Discovered {len(discovered_modules)} modules, exceeding the "
        f"configured maximum of {module_limit}; all discovered modules were used."
    )
    metadata = {
        "input_row_count": input_rows,
        "discovered_modules": discovered_modules,
        "module_count": len(discovered_modules),
        "module_limit": module_limit,
        "module_count_exceeds_limit": exceeds_limit,
        "warnings": [warning_message] if exceeds_limit else [],
    }
    records = [
        {
            "hour": hour,
            "target_hourly_pow": total_power,
            **make_calendar_features(hour),
        }
        for hour, total_power in sorted(grouped.items())
    ]
    return records, metadata


def build_feature_rows(rows: Iterable[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build factory targets and calendar features from hourly module rows."""

    return aggregate_factory_hourly(rows)


def _load_input_run_metadata(input_path: Path) -> tuple[dict[str, Any] | None, Path]:
    metadata_path = input_path.with_name("aggregate_run.json")
    if not metadata_path.exists():
        return None, metadata_path
    with metadata_path.open("r", encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError(f"Input metadata must be a JSON object: {metadata_path}")
    return value, metadata_path


def _json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Not JSON serializable: {type(value)!r}")


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(value, temporary, ensure_ascii=False, indent=2, default=_json_default)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _atomic_write_parquet(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = {
        "hour": pa.array([record["hour"] for record in records], type=pa.timestamp("us", tz="Asia/Seoul")),
        "target_hourly_pow": pa.array(
            [record["target_hourly_pow"] for record in records], type=pa.float64()
        ),
        "weekday": pa.array([record["weekday"] for record in records], type=pa.int8()),
        "hour_of_day": pa.array([record["hour_of_day"] for record in records], type=pa.int8()),
        "day_of_month": pa.array([record["day_of_month"] for record in records], type=pa.int8()),
        "week_of_year": pa.array([record["week_of_year"] for record in records], type=pa.int16()),
        "is_weekend": pa.array([record["is_weekend"] for record in records], type=pa.bool_()),
        "month": pa.array([record["month"] for record in records], type=pa.int8()),
        "day_of_year": pa.array([record["day_of_year"] for record in records], type=pa.int16()),
    }
    table = pa.table(columns)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        pq.write_table(table, temporary_path)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _configure_logger(artifacts_dir: Path) -> logging.Logger:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("factory_energy_copilot.features")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = logging.FileHandler(artifacts_dir / "features.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger


def create_features(
    input_path: str | Path = config.HOURLY_FROM_FULL,
    output_path: str | Path = config.ARTIFACTS_DIR / "features.parquet",
    *,
    max_rows: int | None = None,
    run_meta_path: str | Path | None = None,
) -> dict[str, Any]:
    """Create the training feature table from a bounded artifact read."""

    if max_rows is not None and max_rows < 0:
        raise ValueError("max_rows must be non-negative or None")

    input_file = Path(input_path)
    output_file = Path(output_path)
    run_file = Path(run_meta_path) if run_meta_path is not None else output_file.parent / "features_run.json"
    logger = _configure_logger(output_file.parent)

    input_run_metadata, input_metadata_path = _load_input_run_metadata(input_file)
    input_table = pq.read_table(input_file)
    rows_available = input_table.num_rows
    if max_rows is not None:
        input_table = input_table.slice(0, max_rows)
    selected_rows = input_table.to_pylist()
    records, aggregation_metadata = build_feature_rows(selected_rows)

    if aggregation_metadata["module_count_exceeds_limit"]:
        warnings.warn(aggregation_metadata["warnings"][0], RuntimeWarning, stacklevel=2)
        logger.warning(aggregation_metadata["warnings"][0])

    input_partial = bool(
        input_run_metadata
        and (input_run_metadata.get("partial") or input_run_metadata.get("input_scan_partial"))
    )
    partial = bool(max_rows is not None or input_partial)
    feature_columns = [
        "hour",
        "target_hourly_pow",
        "weekday",
        "hour_of_day",
        "day_of_month",
        "week_of_year",
        "is_weekend",
        "month",
        "day_of_year",
    ]
    metadata: dict[str, Any] = {
        "schema_version": 1,
        "features_version": FEATURES_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_file),
        "output_path": str(output_file),
        "input_metadata_path": str(input_metadata_path),
        "input_run_metadata": input_run_metadata,
        "input_rows_available": rows_available,
        "rows_processed": len(selected_rows),
        "rows_output": len(records),
        "rows_limit": max_rows,
        "partial": partial,
        "input_partial": input_partial,
        "full_input_read": max_rows is None,
        "time_basis": "localtime (KST)",
        "timestamp_column_used": False,
        "preprocessing": {
            "purpose": "training-only factory hourly power and calendar features",
            "rationale": "Use module hourly power and KST calendar fields available at prediction time.",
            "input": "hourly_from_full.parquet fields module, hour, hourly_pow",
            "transformations": [
                "sum hourly_pow across the discovered module set for each hour",
                "derive weekday, hour_of_day, day_of_month, ISO week_of_year, is_weekend from KST hour",
                "exclude voltage, current, powerFactor, and recent lag candidates",
            ],
            "version": FEATURES_VERSION,
        },
        "feature_columns": feature_columns,
        "allowed_feature_columns": select_allowed_features(feature_columns),
        "forbidden_feature_policy": [
            "voltage*",
            "current*",
            "powerFactor*",
            "recent/lag values between 2025-04-30 and 2025-05-10",
        ],
        "aggregation": aggregation_metadata,
        "module_count": aggregation_metadata["module_count"],
        "module_limit": MODULE_LIMIT,
        "module_count_exceeds_limit": aggregation_metadata["module_count_exceeds_limit"],
        "warnings": aggregation_metadata["warnings"],
    }

    _atomic_write_parquet(output_file, records)
    _atomic_write_json(run_file, metadata)
    logger.info(
        json.dumps(
            {
                "event": "features_finished",
                "input": str(input_file),
                "output": str(output_file),
                "features_version": FEATURES_VERSION,
                "rows_processed": len(selected_rows),
                "rows_output": len(records),
                "partial": partial,
                "module_count": aggregation_metadata["module_count"],
                "preprocessing_rationale": metadata["preprocessing"]["rationale"],
                "transformations": metadata["preprocessing"]["transformations"],
            },
            ensure_ascii=False,
        )
    )
    return metadata


# Alias used by callers that prefer a verb matching the task title.
make_features = create_features


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build KST factory power and calendar training features")
    parser.add_argument("--input", type=Path, default=config.HOURLY_FROM_FULL)
    parser.add_argument("--output", type=Path, default=config.ARTIFACTS_DIR / "features.parquet")
    parser.add_argument("--max-rows", type=int, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    create_features(args.input, args.output, max_rows=args.max_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
