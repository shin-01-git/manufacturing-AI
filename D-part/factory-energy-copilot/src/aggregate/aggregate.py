"""Streaming aggregation of 5-second training data by module and KST hour."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import argparse
import json
import logging
import math
import os
from pathlib import Path
import tempfile
import time
from typing import Any
from zoneinfo import ZoneInfo

from config import config
from src.common.io_meta import compare_file_metadata, get_file_metadata
from src.common.streaming_csv import CSVScanResult, scan_csv

KST = ZoneInfo("Asia/Seoul")
_MAX_ERROR_SAMPLES = 20
_DEFAULT_CHUNK_SIZE = 1024 * 1024

_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "module": ("module(equipment)", "module", "equipment"),
    "localtime": ("localtime",),
    "activePower": ("activePower", "active_power"),
}


def _configure_logger(artifacts_dir: Path) -> logging.Logger:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("factory_energy_copilot.aggregate")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = logging.FileHandler(artifacts_dir / "aggregate.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger


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


def _atomic_write_parquet(path: Path, records: list[tuple[str, datetime, float, int]]) -> None:
    """Write the small grouped result and atomically replace the target."""

    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    modules = [record[0] for record in records]
    hours = [record[1] for record in records]
    powers = [record[2] for record in records]
    counts = [record[3] for record in records]
    table = pa.table(
        {
            "module": pa.array(modules, type=pa.string()),
            "hour": pa.array(hours, type=pa.timestamp("us", tz="Asia/Seoul")),
            "hourly_pow": pa.array(powers, type=pa.float64()),
            "n": pa.array(counts, type=pa.int64()),
        }
    )

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


def _normalise_column(name: str) -> str:
    return "".join(name.casefold().split()).replace("_", "")


def _find_column(header: list[str], field: str) -> str | None:
    aliases = {_normalise_column(alias) for alias in _COLUMN_ALIASES[field]}
    for column in header:
        if _normalise_column(column) in aliases:
            return column
    return None


def _parse_localtime(value: str) -> datetime:
    text = value.strip()
    if len(text) != 14 or not text.isdigit():
        raise ValueError("localtime must be a 14-digit YYYYMMDDHHMMSS value")
    try:
        parsed = datetime.strptime(text, "%Y%m%d%H%M%S")
    except ValueError as exc:
        raise ValueError("localtime is not a valid calendar date/time") from exc
    return parsed.replace(tzinfo=KST)


class _ErrorTracker:
    def __init__(self) -> None:
        self.count = 0
        self.non_numeric_rows = 0
        self.invalid_time_rows = 0
        self.empty_value_rows = 0
        self.samples: list[str] = []

    def add(self, message: str, *, kind: str | None = None) -> None:
        self.count += 1
        if kind == "non_numeric":
            self.non_numeric_rows += 1
        elif kind == "invalid_time":
            self.invalid_time_rows += 1
        elif kind == "empty":
            self.empty_value_rows += 1
        if len(self.samples) < _MAX_ERROR_SAMPLES:
            self.samples.append(message)


def _metadata_for_source(path: Path, *, compute_hash: bool | None) -> Any:
    if compute_hash is None:
        # Hash small test/source files, but do not add a second multi-GB scan to
        # the production aggregation path unless explicitly requested.
        compute_hash = path.stat().st_size <= 64 * 1024 * 1024
    return get_file_metadata(path, compute_hash=compute_hash)


def _result_records(
    groups: dict[tuple[str, datetime], list[float | int]],
) -> tuple[list[tuple[str, datetime, float, int]], int, int]:
    total_groups = len(groups)
    excluded_groups = 0
    records: list[tuple[str, datetime, float, int]] = []
    for (module, hour), values in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1])):
        total_power, count = values
        if count != config.SAMPLES_PER_HOUR_COMPLETE:
            excluded_groups += 1
            continue
        records.append((module, hour, float(total_power), int(count)))
    return records, total_groups, excluded_groups


def aggregate_csv(
    input_path: str | Path = config.TRAINING_CSV,
    output_path: str | Path = config.HOURLY_FROM_FULL,
    *,
    max_rows: int | None = None,
    chunk_log_interval: int = 1_000_000,
    compute_hash: bool | None = None,
    run_meta_path: str | Path | None = None,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
) -> dict[str, Any]:
    """Aggregate ``activePower`` in one bounded, row-by-row CSV pass.

    Only one ``(module, hour)`` sum and count is retained per group; source rows
    are never materialized. ``hour`` is explicitly parsed from KST ``localtime``
    and the source ``timestamp`` column, when present, is ignored.
    """

    if max_rows is not None and max_rows < 0:
        raise ValueError("max_rows must be non-negative or None")
    if chunk_log_interval <= 0:
        raise ValueError("chunk_log_interval must be positive")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    source = Path(input_path)
    output = Path(output_path)
    metadata_path = Path(run_meta_path) if run_meta_path is not None else output.parent / "aggregate_run.json"
    logger = _configure_logger(output.parent)
    started = time.perf_counter()
    run_started = datetime.now(timezone.utc).isoformat()
    logger.info(
        json.dumps(
            {
                "event": "aggregate_started",
                "input": str(source),
                "output": str(output),
                "rows_limit": max_rows,
                "chunk_log_interval": chunk_log_interval,
                "chunk_size": chunk_size,
                "time_basis": f"{config.TIME_BASE} (KST)",
                "aggregation_formula": "sum(activePower * SECONDS_PER_SAMPLE / 3600)",
                "activePower_unit": "source activePower scale; no additional 1000 divisor",
            },
            ensure_ascii=False,
        )
    )

    before = _metadata_for_source(source, compute_hash=compute_hash)
    groups: dict[tuple[str, datetime], list[float | int]] = {}
    errors = _ErrorTracker()
    header: list[str] | None = None
    module_column: str | None = None
    localtime_column: str | None = None
    power_column: str | None = None
    valid_rows = 0
    next_log_at = chunk_log_interval

    def process_row(row: dict[str, str]) -> None:
        nonlocal header, module_column, localtime_column, power_column, valid_rows, next_log_at
        if header is None:
            header = list(row.keys())
            module_column = _find_column(header, "module")
            localtime_column = _find_column(header, "localtime")
            power_column = _find_column(header, "activePower")
            missing = [
                field
                for field, column in (
                    ("module", module_column),
                    ("localtime", localtime_column),
                    ("activePower", power_column),
                )
                if column is None
            ]
            if missing:
                raise ValueError(f"Required CSV columns not found by header name: {missing}")

        assert module_column is not None
        assert localtime_column is not None
        assert power_column is not None
        valid_rows += 1
        raw_module = row[module_column]
        raw_time = row[localtime_column]
        raw_power = row[power_column]
        if not raw_module.strip() or not raw_time.strip() or not raw_power.strip():
            errors.add(
                f"record callback {valid_rows}: empty module/localtime/activePower value",
                kind="empty",
            )
            return
        try:
            hour = _parse_localtime(raw_time)
        except ValueError as exc:
            errors.add(f"record callback {valid_rows}: {exc}: {raw_time!r}", kind="invalid_time")
            return
        try:
            active_power = float(raw_power)
        except (TypeError, ValueError):
            errors.add(
                f"record callback {valid_rows}: activePower is non-numeric: {raw_power!r}",
                kind="non_numeric",
            )
            return
        if not math.isfinite(active_power):
            errors.add(
                f"record callback {valid_rows}: activePower is not finite: {raw_power!r}",
                kind="non_numeric",
            )
            return

        key = (raw_module, hour.replace(minute=0, second=0, microsecond=0))
        current = groups.setdefault(key, [0.0, 0])
        current[0] = float(current[0]) + active_power * config.SECONDS_PER_SAMPLE / 3600
        current[1] = int(current[1]) + 1
        if valid_rows >= next_log_at:
            logger.info(
                json.dumps(
                    {
                        "event": "aggregate_progress",
                        "rows_seen": valid_rows,
                        "groups": len(groups),
                    },
                    ensure_ascii=False,
                )
            )
            next_log_at += chunk_log_interval

    try:
        scan_result: CSVScanResult = scan_csv(
            source,
            max_rows=max_rows,
            chunk_size=chunk_size,
            row_callback=process_row,
        )
        after_processing = _metadata_for_source(source, compute_hash=compute_hash)
        immutability = compare_file_metadata(before, after_processing)
        if not immutability["unchanged"]:
            raise RuntimeError("Source CSV changed during aggregation; output was not replaced")

        records, total_groups, excluded_groups = _result_records(groups)
        _atomic_write_parquet(output, records)
        after_output = _metadata_for_source(source, compute_hash=compute_hash)
        immutability = compare_file_metadata(before, after_output)
        if not immutability["unchanged"]:
            output.unlink(missing_ok=True)
            raise RuntimeError("Source CSV changed during aggregation; output was invalidated")

        partial = max_rows is not None
        scanner_errors = list(scan_result.errors)
        all_error_samples = (scanner_errors + errors.samples)[:_MAX_ERROR_SAMPLES]
        run_metadata: dict[str, Any] = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "run_started_at": run_started,
            "elapsed_seconds": time.perf_counter() - started,
            "input_path": str(source),
            "output_path": str(output),
            "rows_limit": max_rows,
            "partial": partial,
            "full_scan": max_rows is None,
            "input_scan_partial": scan_result.partial,
            "rows_processed": scan_result.csv_record_count,
            "valid_rows": valid_rows,
            "encoding": scan_result.encoding,
            "delimiter": scan_result.delimiter,
            "header": scan_result.header,
            "columns_used": {
                "module": module_column,
                "localtime": localtime_column,
                "activePower": power_column,
            },
            "time_basis": f"{config.TIME_BASE} (KST)",
            "timestamp_column_used": False,
            "aggregation_formula": "sum(activePower * SECONDS_PER_SAMPLE / 3600)",
            "seconds_per_sample": config.SECONDS_PER_SAMPLE,
            "activePower_unit": "source activePower scale; no additional 1000 divisor",
            "complete_n": config.SAMPLES_PER_HOUR_COMPLETE,
            "complete_only": True,
            "total_group_count": total_groups,
            "complete_group_count": len(records),
            "excluded_incomplete_group_count": excluded_groups,
            "error_count": scan_result.error_count + errors.count,
            "malformed_row_error_count": scan_result.error_count,
            "non_numeric_row_count": errors.non_numeric_rows,
            "invalid_localtime_row_count": errors.invalid_time_rows,
            "empty_value_row_count": errors.empty_value_rows,
            "error_samples": all_error_samples,
            "source_immutability": immutability,
            "chunk": {
                "size_bytes": chunk_size,
                "log_interval_rows": chunk_log_interval,
                "single_sequential_pass": True,
                "source_rows_not_materialized": True,
            },
            "schema": ["module", "hour", "hourly_pow", "n"],
        }
        _atomic_write_json(metadata_path, run_metadata)
        logger.info(
            json.dumps(
                {
                    "event": "aggregate_finished",
                    "rows_processed": scan_result.csv_record_count,
                    "valid_rows": valid_rows,
                    "total_group_count": total_groups,
                    "complete_group_count": len(records),
                    "excluded_incomplete_group_count": excluded_groups,
                    "error_count": run_metadata["error_count"],
                    "partial": partial,
                    "rows_limit": max_rows,
                    "immutability": "불변 확인",
                },
                ensure_ascii=False,
            )
        )
        for sample in all_error_samples:
            logger.warning("aggregate_row_error %s", sample)
        if excluded_groups:
            logger.info("excluded incomplete groups=%d of total groups=%d", excluded_groups, total_groups)
        return run_metadata
    except Exception:
        logger.exception("aggregate_failed")
        raise


# Descriptive alias for callers that use the task name.
aggregate = aggregate_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stream activePower into complete KST hourly aggregates")
    parser.add_argument("--input", type=Path, default=config.TRAINING_CSV)
    parser.add_argument("--output", type=Path, default=config.HOURLY_FROM_FULL)
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--chunk-log-interval", type=int, default=1_000_000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    aggregate_csv(
        args.input,
        args.output,
        max_rows=args.max_rows,
        chunk_log_interval=args.chunk_log_interval,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
