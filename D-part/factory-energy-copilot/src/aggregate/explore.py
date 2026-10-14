"""Streaming metadata and distribution exploration for the two source CSVs."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import argparse
import json
import logging
from pathlib import Path
import re
import time
from typing import Any

from config import config
from src.common.io_meta import FileMetadata, compare_file_metadata, get_file_metadata
from src.common.streaming_csv import CSVScanResult, scan_csv


EXPECTED_COLUMNS = {"Training_CSV": 19, "Validation_CSV": 5}
TRAIN_MODULE_COLUMNS = ("module", "module(equipment)", "equipment")
LOCALTIME_COLUMNS = ("localtime", "id")


def _configure_logger(artifacts_dir: Path) -> logging.Logger:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("factory_energy_copilot.explore")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return logger
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = logging.FileHandler(artifacts_dir / "explore.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger


def _json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Not JSON serializable: {type(value)!r}")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _time_columns(header: list[str]) -> list[str]:
    return [column for column in header if column in LOCALTIME_COLUMNS or "localtime" in column.lower()]


def _time_clues(result: CSVScanResult) -> dict[str, dict[str, str | None]]:
    columns = _time_columns(result.header)
    clues: dict[str, dict[str, str | None]] = {}
    for column in columns:
        clues[column] = {
            "first": result.head_samples[0].get(column) if result.head_samples else None,
            "last": result.tail_samples[-1].get(column) if result.tail_samples else None,
        }
    return clues


def _source_file_entry(
    role: str,
    path: Path,
    before: FileMetadata,
    after: FileMetadata,
    result: CSVScanResult,
) -> dict[str, Any]:
    expected = EXPECTED_COLUMNS[role]
    comparison = compare_file_metadata(before, after)
    return {
        "role": role,
        "path": str(path),
        "length": after.length,
        "last_write_time": after.last_write_time,
        "sha256": after.sha256,
        "metadata_before": before.to_dict(),
        "metadata_after": after.to_dict(),
        "immutability": comparison,
        "header": result.header,
        "column_count": result.column_count,
        "schema_column_count": {
            "expected": expected,
            "matches": result.column_count == expected,
            "fact_status": "[확인]",
        },
        "encoding": result.encoding,
        "delimiter": result.delimiter,
        "head_samples": result.head_samples,
        "tail_samples": result.tail_samples,
        "time_clues": _time_clues(result),
        "csv_record_count": result.csv_record_count,
        "physical_row_count": result.physical_row_count,
        "physical_rows_scanned": result.physical_rows_scanned,
        "partial": result.partial,
        "sample_config": {
            "head_size": result.sample_size,
            "tail_size": result.tail_size,
            "max_rows": result.max_rows,
        },
        "errors": result.errors,
        "error_count": result.error_count,
        "physical_vs_csv_note": (
            "물리 줄 수와 CSV 레코드 수는 인용된 줄바꿈 등으로 다를 수 있다. "
            "physical_row_count는 파서가 소비한 물리 CSV 줄 기준이며 전수 품질을 추정하지 않는다."
        ),
        "unverified_quality": {
            "time_continuity": "[미확인]",
            "module_count_and_full_module_list": "[미확인]",
            "missing_values": "[미확인]",
            "duplicates": "[미확인]",
            "all_module_hour_720_counts": "[미확인]",
        },
    }


def _find_column(row: dict[str, str], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in row:
            return candidate
    return None


def _hour_key(value: str) -> str:
    text = value.strip()
    if re.fullmatch(r"\d{14}", text):
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]} {text[8:10]}:00:00"
    normalized = text.replace("T", " ")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return text[:13] if len(text) >= 13 else text
    return parsed.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:00:00")


class DistributionAccumulator:
    """Bounded-by-unique-hours distribution state built in one row pass."""

    def __init__(self) -> None:
        self.module_hours: dict[str, Counter[str]] = defaultdict(Counter)
        self.invalid_rows = 0
        self.rows_seen = 0

    def update(self, row: dict[str, str]) -> None:
        self.rows_seen += 1
        module_column = _find_column(row, TRAIN_MODULE_COLUMNS)
        localtime_column = _find_column(row, LOCALTIME_COLUMNS)
        if module_column is None or localtime_column is None:
            self.invalid_rows += 1
            return
        module = row[module_column].strip()
        localtime = row[localtime_column].strip()
        if not module or not localtime:
            self.invalid_rows += 1
            return
        self.module_hours[module][_hour_key(localtime)] += 1

    def to_dict(self, *, partial: bool, expected_modules: int) -> dict[str, Any]:
        n_distribution: Counter[str] = Counter()
        complete = 0
        incomplete = 0
        for counts in self.module_hours.values():
            for row_count in counts.values():
                n_distribution[str(row_count)] += 1
                if row_count == config.SAMPLES_PER_HOUR_COMPLETE:
                    complete += 1
                else:
                    incomplete += 1
        modules = sorted(self.module_hours)
        module_hour_counts = {
            module: dict(sorted(counts.items()))
            for module, counts in sorted(self.module_hours.items())
        }
        return {
            "scan": {
                "rows_processed": self.rows_seen,
                "partial": partial,
                "time_basis": "localtime (KST)",
                "single_sequential_pass": True,
            },
            "expected_module_count": expected_modules,
            "discovered_modules": modules,
            "discovered_module_count": len(modules),
            "module_count_matches_expected": len(modules) == expected_modules,
            "module_count_warning": (
                None
                if len(modules) == expected_modules
                else f"발견 설비 수 {len(modules)}개가 기대/상한 {expected_modules}개와 다릅니다. 확인이 필요합니다."
            ),
            "module_hour_counts": module_hour_counts,
            "n_720_summary": {
                "complete_module_hour_count": complete,
                "incomplete_module_hour_count": incomplete,
                "total_module_hour_count": complete + incomplete,
                "n_distribution": dict(sorted(n_distribution.items(), key=lambda item: int(item[0]))),
                "complete_n": config.SAMPLES_PER_HOUR_COMPLETE,
            },
            "invalid_rows": self.invalid_rows,
            "quality_note": "분포는 실제로 스캔한 행의 전수 결과이며, partial 실행에서는 미스캔 구간을 포함하지 않는다.",
        }


def explore_sources(
    training_path: str | Path = config.TRAINING_CSV,
    validation_path: str | Path = config.VALIDATION_CSV,
    *,
    artifacts_dir: str | Path = config.ARTIFACTS_DIR,
    max_rows: int | None = None,
    sample_size: int = 5,
    tail_size: int = 5,
    chunk_size: int = 1024 * 1024,
    compute_hash: bool = False,
    include_distribution: bool = False,
) -> dict[str, Any]:
    """Explore both sources without loading either file into memory."""

    if max_rows is not None and max_rows < 0:
        raise ValueError("max_rows must be non-negative or None")
    training = Path(training_path)
    validation = Path(validation_path)
    output_dir = Path(artifacts_dir)
    logger = _configure_logger(output_dir)
    started = time.perf_counter()
    run_started = datetime.now(timezone.utc).isoformat()
    logger.info(
        json.dumps(
            {
                "event": "explore_started",
                "run_started_at": run_started,
                "input_paths": [str(training), str(validation)],
                "max_rows": max_rows,
                "sample_size": sample_size,
                "tail_size": tail_size,
                "chunk_size": chunk_size,
                "hash_requested": compute_hash,
                "time_basis": "localtime (KST)",
            },
            ensure_ascii=False,
        )
    )

    before = {
        "Training_CSV": get_file_metadata(training, compute_hash=compute_hash),
        "Validation_CSV": get_file_metadata(validation, compute_hash=compute_hash),
    }
    distribution = DistributionAccumulator() if include_distribution else None
    try:
        training_result = scan_csv(
            training,
            max_rows=max_rows,
            sample_size=sample_size,
            tail_size=tail_size,
            chunk_size=chunk_size,
            row_callback=distribution.update if distribution else None,
        )
        validation_result = scan_csv(
            validation,
            max_rows=max_rows,
            sample_size=sample_size,
            tail_size=tail_size,
            chunk_size=chunk_size,
        )
        after = {
            "Training_CSV": get_file_metadata(training, compute_hash=compute_hash),
            "Validation_CSV": get_file_metadata(validation, compute_hash=compute_hash),
        }
        files = [
            _source_file_entry("Training_CSV", training, before["Training_CSV"], after["Training_CSV"], training_result),
            _source_file_entry("Validation_CSV", validation, before["Validation_CSV"], after["Validation_CSV"], validation_result),
        ]
        immutable = all(file_entry["immutability"]["unchanged"] for file_entry in files)
        elapsed = time.perf_counter() - started
        source_meta: dict[str, Any] = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "run_started_at": run_started,
            "elapsed_seconds": elapsed,
            "time_basis": "localtime (KST)",
            "scan_config": {
                "max_rows": max_rows,
                "sample_size": sample_size,
                "tail_size": tail_size,
                "chunk_size": chunk_size,
                "compute_hash": compute_hash,
                "full_scan": max_rows is None,
            },
            "source_immutability": {
                "unchanged": immutable,
                "message": "불변 확인" if immutable else "원본 변경 감지",
                "files": {entry["role"]: entry["immutability"] for entry in files},
            },
            "files": files,
            "physical_vs_csv_note": "물리 줄 수와 CSV 레코드 수가 다를 수 있음을 명시한다.",
        }
        _write_json(output_dir / "source_meta.json", source_meta)
        if distribution is not None:
            dist = distribution.to_dict(
                partial=training_result.partial,
                expected_modules=config.MODULE_COUNT_EXPECTED,
            )
            dist.update(
                {
                    "schema_version": 1,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "input_path": str(training),
                    "max_rows": max_rows,
                    "sample_size": sample_size,
                    "tail_size": tail_size,
                    "chunk_size": chunk_size,
                    "source_immutability": source_meta["source_immutability"],
                }
            )
            _write_json(output_dir / "explore_dist.json", dist)
            if dist["module_count_warning"]:
                logger.warning(dist["module_count_warning"])
        logger.info(
            json.dumps(
                {
                    "event": "explore_finished",
                    "processed_rows": {
                        "training": training_result.csv_record_count,
                        "validation": validation_result.csv_record_count,
                    },
                    "elapsed_seconds": elapsed,
                    "errors": {
                        "training": training_result.error_count,
                        "validation": validation_result.error_count,
                    },
                    "immutability": "불변 확인" if immutable else "원본 변경 감지",
                    "partial": bool(training_result.partial or validation_result.partial),
                },
                ensure_ascii=False,
            )
        )
        if not immutable:
            raise RuntimeError("Source CSV changed during exploration; artifacts are invalid")
        return source_meta
    except Exception as exc:
        logger.exception("explore_failed error=%s", exc)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stream source CSV metadata and distributions")
    parser.add_argument("--dist", action="store_true", help="also write the training module/hour distribution")
    parser.add_argument("--max-rows", type=int, default=None, help="limit processed data records; default is full scan")
    parser.add_argument("--sample-size", type=int, default=5)
    parser.add_argument("--tail-size", type=int, default=5)
    parser.add_argument("--chunk-size", type=int, default=1024 * 1024)
    parser.add_argument("--hash", action="store_true", help="also calculate streaming SHA-256 hashes")
    parser.add_argument("--training", type=Path, default=config.TRAINING_CSV)
    parser.add_argument("--validation", type=Path, default=config.VALIDATION_CSV)
    parser.add_argument("--artifacts-dir", type=Path, default=config.ARTIFACTS_DIR)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    explore_sources(
        args.training,
        args.validation,
        artifacts_dir=args.artifacts_dir,
        max_rows=args.max_rows,
        sample_size=args.sample_size,
        tail_size=args.tail_size,
        chunk_size=args.chunk_size,
        compute_hash=args.hash,
        include_distribution=args.dist,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
