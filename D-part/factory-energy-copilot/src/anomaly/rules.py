"""Rule-based anomaly candidate detection on 5-second rows (6.1 / B1, B2, O-9).

O-9 (approved) rule — **(a) three-phase simultaneous** only:

    (voltageR < 210 AND voltageS < 210 AND voltageT < 210)
    OR
    (powerFactorR < 85 AND powerFactorS < 85 AND powerFactorT < 85)

Thresholds come from ``config`` (``VOLTAGE_THRESHOLD`` 210V,
``POWER_FACTOR_THRESHOLD`` 85%). The per-phase (b) interpretation is deliberately
NOT implemented (O-9 rejected it).

The scan is bounded and sequential (NFR-1): the source is read row-by-row via
``src.common.streaming_csv`` and never materialized. ``--max-rows`` samples the
first N rows; ``--dry-run`` prints a summary without writing candidate output.
Validation_CSV is never referenced (NFR-4). The source is checked for immutability
before and after (NFR-2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import argparse
import json
from pathlib import Path
from typing import Any, Callable

from config import config
from src.anomaly import _common
from src.common.streaming_csv import scan_csv

# Anomaly type labels (also used downstream by detectors/artifacts).
TYPE_VOLTAGE = "voltage_low_3phase"
TYPE_POWER_FACTOR = "power_factor_low_3phase"


def voltage_rule_hit(row: _common.ParsedRow, threshold: float) -> bool:
    """True when all three phase voltages are below ``threshold`` (O-9 (a))."""

    return (
        row.voltageR < threshold
        and row.voltageS < threshold
        and row.voltageT < threshold
    )


def power_factor_rule_hit(row: _common.ParsedRow, threshold: float) -> bool:
    """True when all three phase power factors are below ``threshold`` (O-9 (a))."""

    return (
        row.powerFactorR < threshold
        and row.powerFactorS < threshold
        and row.powerFactorT < threshold
    )


@dataclass
class RuleCandidate:
    """A single rule candidate event on one 5-second row."""

    module: str
    localtime: datetime
    type: str
    value: float
    normal_range: str
    rule: str

    def key(self) -> tuple[str, str]:
        return _common.event_key(self.module, self.localtime)


def evaluate_row(
    row: _common.ParsedRow,
    *,
    voltage_threshold: float = config.VOLTAGE_THRESHOLD,
    power_factor_threshold: float = config.POWER_FACTOR_THRESHOLD,
) -> list[RuleCandidate]:
    """Return rule candidates (0, 1, or 2) for one parsed 5-second row.

    A voltage candidate and a power-factor candidate are independent OR branches;
    both can fire on the same row.
    """

    candidates: list[RuleCandidate] = []
    if voltage_rule_hit(row, voltage_threshold):
        candidates.append(
            RuleCandidate(
                module=row.module,
                localtime=row.localtime,
                type=TYPE_VOLTAGE,
                value=row.voltage_mean,
                normal_range=f">= {voltage_threshold} V (all 3 phases)",
                rule=f"voltageR/S/T < {voltage_threshold}",
            )
        )
    if power_factor_rule_hit(row, power_factor_threshold):
        candidates.append(
            RuleCandidate(
                module=row.module,
                localtime=row.localtime,
                type=TYPE_POWER_FACTOR,
                value=row.power_factor_mean,
                normal_range=f">= {power_factor_threshold} % (all 3 phases)",
                rule=f"powerFactorR/S/T < {power_factor_threshold}",
            )
        )
    return candidates


@dataclass
class RuleScanResult:
    candidates: list[RuleCandidate]
    rows_scanned: int
    rows_parsed: int
    rows_skipped: int
    voltage_candidate_count: int
    power_factor_candidate_count: int
    partial: bool
    meta: dict[str, Any]


def scan_rules(
    input_path: str | Path = config.TRAINING_CSV,
    *,
    max_rows: int | None = None,
    chunk_size: int = 1024 * 1024,
    on_candidate: Callable[[RuleCandidate], None] | None = None,
    collect: bool = True,
    logger: Any | None = None,
) -> RuleScanResult:
    """Scan the source once and extract O-9 (a) three-phase rule candidates.

    When ``collect`` is True candidates are returned in memory (fine for samples
    and tests). ``on_candidate`` streams each candidate to a sink so a full scan
    need not keep every event in memory.
    """

    source = Path(input_path)
    voltage_threshold = config.VOLTAGE_THRESHOLD
    power_factor_threshold = config.POWER_FACTOR_THRESHOLD

    before = _common.source_metadata(source)
    columns: dict[str, str] = {}
    candidates: list[RuleCandidate] = []
    counters = {
        "rows_parsed": 0,
        "rows_skipped": 0,
        "voltage": 0,
        "power_factor": 0,
    }

    def process_row(raw: dict[str, str]) -> None:
        if not columns:
            columns.update(_common.resolve_columns(list(raw.keys())))
        parsed = _common.parse_row(raw, columns)
        if parsed is None:
            counters["rows_skipped"] += 1
            return
        counters["rows_parsed"] += 1
        for candidate in evaluate_row(
            parsed,
            voltage_threshold=voltage_threshold,
            power_factor_threshold=power_factor_threshold,
        ):
            if candidate.type == TYPE_VOLTAGE:
                counters["voltage"] += 1
            else:
                counters["power_factor"] += 1
            if collect:
                candidates.append(candidate)
            if on_candidate is not None:
                on_candidate(candidate)

    scan_result = scan_csv(
        source,
        max_rows=max_rows,
        chunk_size=chunk_size,
        row_callback=process_row,
    )
    after = _common.source_metadata(source)
    immutability = _common.assert_source_unchanged(before, after)

    meta = _common.reproducibility_meta(
        module_name="rules",
        input_path=source,
        max_rows=max_rows,
        chunk_size=chunk_size,
        extra={
            "rule_mode": config.ANOMALY_RULE_MODE,
            "voltage_threshold": voltage_threshold,
            "power_factor_threshold": power_factor_threshold,
            "thresholds_from_config": True,
            "rows_scanned": scan_result.csv_record_count,
            "rows_parsed": counters["rows_parsed"],
            "rows_skipped": counters["rows_skipped"],
            "voltage_candidate_count": counters["voltage"],
            "power_factor_candidate_count": counters["power_factor"],
            "validation_csv_referenced": False,
            "source_immutability": immutability,
        },
    )
    if logger is not None:
        logger.info(json.dumps({"event": "rules_scan", **meta}, ensure_ascii=False, default=str))

    return RuleScanResult(
        candidates=candidates,
        rows_scanned=scan_result.csv_record_count,
        rows_parsed=counters["rows_parsed"],
        rows_skipped=counters["rows_skipped"],
        voltage_candidate_count=counters["voltage"],
        power_factor_candidate_count=counters["power_factor"],
        partial=scan_result.partial or (max_rows is not None),
        meta=meta,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rule-based (O-9 3-phase simultaneous) anomaly candidate scan over 5-second rows"
    )
    parser.add_argument("--input", type=Path, default=config.TRAINING_CSV)
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Scan only the first N rows (sample). Omit for a full scan.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and summarize without writing candidate output.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional parquet path for candidate events (defaults to no write).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logger = _common.configure_logger("rules", config.ARTIFACTS_DIR)
    logger.info(
        json.dumps(
            {
                "event": "rules_started",
                "input": str(args.input),
                "max_rows": args.max_rows,
                "dry_run": bool(args.dry_run),
                "rule_mode": config.ANOMALY_RULE_MODE,
                "voltage_threshold": config.VOLTAGE_THRESHOLD,
                "power_factor_threshold": config.POWER_FACTOR_THRESHOLD,
            },
            ensure_ascii=False,
        )
    )
    result = scan_rules(args.input, max_rows=args.max_rows, logger=logger)

    if args.dry_run or args.output is None:
        logger.info(
            json.dumps(
                {
                    "event": "rules_dry_run_summary",
                    "rows_scanned": result.rows_scanned,
                    "rows_parsed": result.rows_parsed,
                    "voltage_candidates": result.voltage_candidate_count,
                    "power_factor_candidates": result.power_factor_candidate_count,
                    "candidates_total": len(result.candidates),
                },
                ensure_ascii=False,
            )
        )
        return 0

    # Optional write path (not used by the sample run; artifacts.py owns the
    # final anomaly_events.parquet contract).
    import pyarrow as pa
    import pyarrow.parquet as pq

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "module": pa.array([c.module for c in result.candidates], type=pa.string()),
            "localtime": pa.array([c.localtime for c in result.candidates], type=pa.timestamp("us", tz="Asia/Seoul")),
            "type": pa.array([c.type for c in result.candidates], type=pa.string()),
            "value": pa.array([c.value for c in result.candidates], type=pa.float64()),
            "normal_range": pa.array([c.normal_range for c in result.candidates], type=pa.string()),
        }
    )
    pq.write_table(table, out)
    logger.info(json.dumps({"event": "rules_candidates_written", "path": str(out), "count": len(result.candidates)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
