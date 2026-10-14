"""Anomaly artifacts: events, hourly counts, and module status (6.4 / B4, B5, O-3, O-5).

Produces the three design Data Models outputs:

- ``anomaly_events.parquet``  : module, localtime, type, value, normal_range,
  detected_by, score
- ``anomaly_hourly.parquet``  : hour, module, type, count
- ``module_status.csv``       : module, anomaly_count, anomaly_ratio, last_seen,
  status

Memory model (design: "전체 적재 금지, 메모리 상수"). ``anomaly_events.parquet`` is
written by a streaming :class:`ParquetEventSink` as the detector scans, batch by
batch, so the full event set is never held in memory. ``anomaly_hourly.parquet``
and ``module_status.csv`` are built from the detector's small bounded
:class:`~src.anomaly.detectors.DetectionAggregator`, not from an event list.

Module set: within the **max-13 cap (O-5)**, only modules actually discovered in
the data are used; if the discovered count differs from 13 a warning is recorded.
Status (B5) is based on per-module anomaly ratio / surge, but the boundary values
are **unapproved (O-3)**, so ``status`` is labelled ``미확정`` and never a confident
정상/주의/이상 value.

To honour "never overwrite the existing artifacts", the CLI writes to a ``tmp``
subdirectory by default. A full run that targets the shared ``artifacts/``
contract must pass ``--out-dir`` at the shared root together with
``--allow-shared`` (or the shared files must not yet exist).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from config import config
from src.anomaly import _common
from src.anomaly.detectors import DetectionAggregator, DetectionEvent

# Status is left as this label until O-3 boundary values are approved.
STATUS_UNDECIDED = "미확정"

# Rows per parquet batch flushed by the streaming event sink.
EVENT_WRITE_BATCH = 50_000

_EVENTS_SCHEMA_FIELDS = ("module", "localtime", "type", "value", "normal_range", "detected_by", "score")


@dataclass
class ArtifactPaths:
    events: Path
    hourly: Path
    module_status: Path
    run_meta: Path


def default_tmp_paths(base: Path | None = None) -> ArtifactPaths:
    """Default sample-run output under ``artifacts/tmp`` (never the shared files)."""

    root = Path(base) if base is not None else config.ARTIFACTS_DIR / "tmp"
    return ArtifactPaths(
        events=root / "anomaly_events.parquet",
        hourly=root / "anomaly_hourly.parquet",
        module_status=root / "module_status.csv",
        run_meta=root / "anomaly_artifacts_run.json",
    )


@dataclass
class ModuleSummary:
    module: str
    anomaly_count: int
    total_rows: int
    last_seen: datetime | None

    @property
    def anomaly_ratio(self) -> float:
        return (self.anomaly_count / self.total_rows) if self.total_rows else 0.0


# ---------------------------------------------------------------------------
# Event shaping (shared schema). ``_event_score``/``_primary_type``/``_event_row``
# turn one DetectionEvent into one anomaly_events row.
# ---------------------------------------------------------------------------
def _event_score(event: DetectionEvent) -> float:
    """Pick a single ``score`` per the events schema (unsupervised score)."""

    return float(event.unsupervised_score)


def _primary_type(event: DetectionEvent) -> str:
    return event.primary_type()


def _event_row(event: DetectionEvent) -> dict[str, Any]:
    return {
        "module": event.module,
        "localtime": event.localtime,
        "type": _primary_type(event),
        "value": float(event.value),
        "normal_range": event.normal_range,
        "detected_by": json.dumps(event.detected_by, ensure_ascii=False),
        "score": _event_score(event),
    }


def build_event_rows(events: list[DetectionEvent]) -> list[dict[str, Any]]:
    """Shape detected events into anomaly_events rows (design schema).

    In-memory helper kept for samples/tests; the full-scan path streams rows via
    :class:`ParquetEventSink` instead of materializing this list.
    """

    return [_event_row(event) for event in events]


# ---------------------------------------------------------------------------
# Streaming parquet sink: writes anomaly_events.parquet in bounded batches so a
# full scan never holds all events in memory (design "메모리 상수").
# ---------------------------------------------------------------------------
def _events_pyarrow_schema():
    import pyarrow as pa

    return pa.schema(
        [
            ("module", pa.string()),
            ("localtime", pa.timestamp("us", tz="Asia/Seoul")),
            ("type", pa.string()),
            ("value", pa.float64()),
            ("normal_range", pa.string()),
            ("detected_by", pa.string()),
            ("score", pa.float64()),
        ]
    )


def _events_batch_table(rows: list[dict[str, Any]]):
    import pyarrow as pa

    return pa.table(
        {
            "module": pa.array([r["module"] for r in rows], type=pa.string()),
            "localtime": pa.array([r["localtime"] for r in rows], type=pa.timestamp("us", tz="Asia/Seoul")),
            "type": pa.array([r["type"] for r in rows], type=pa.string()),
            "value": pa.array([r["value"] for r in rows], type=pa.float64()),
            "normal_range": pa.array([r["normal_range"] for r in rows], type=pa.string()),
            "detected_by": pa.array([r["detected_by"] for r in rows], type=pa.string()),
            "score": pa.array([r["score"] for r in rows], type=pa.float64()),
        }
    )


class ParquetEventSink:
    """Append detected events to a parquet file in bounded batches.

    Opens a ``pyarrow.parquet.ParquetWriter`` lazily on the first event so the
    file (with the fixed schema) is always created, even if zero events are
    detected (see :meth:`close`). Only ``batch_size`` rows are buffered at once.
    """

    def __init__(self, path: Path, *, batch_size: int = EVENT_WRITE_BATCH) -> None:
        self.path = Path(path)
        self.batch_size = batch_size
        self._buffer: list[dict[str, Any]] = []
        self._writer = None
        self._schema = None
        self.rows_written = 0

    def _ensure_writer(self):
        if self._writer is None:
            import pyarrow.parquet as pq

            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._schema = _events_pyarrow_schema()
            self._writer = pq.ParquetWriter(str(self.path), self._schema)
        return self._writer

    def _flush(self) -> None:
        if not self._buffer:
            return
        writer = self._ensure_writer()
        table = _events_batch_table(self._buffer).cast(self._schema)
        writer.write_table(table)
        self.rows_written += len(self._buffer)
        self._buffer.clear()

    def emit(self, event: DetectionEvent) -> None:
        self._buffer.append(_event_row(event))
        if len(self._buffer) >= self.batch_size:
            self._flush()

    def close(self) -> None:
        self._flush()
        if self._writer is not None:
            self._writer.close()
            self._writer = None
            return
        # No events were emitted: still create an empty file with the schema so
        # downstream readers see the contract instead of a missing file.
        import pyarrow as pa
        import pyarrow.parquet as pq

        self.path.parent.mkdir(parents=True, exist_ok=True)
        schema = _events_pyarrow_schema()
        pq.write_table(pa.table({f.name: pa.array([], type=f.type) for f in schema}), str(self.path))


# ---------------------------------------------------------------------------
# Hourly / module_status rows from the bounded aggregator.
# ---------------------------------------------------------------------------
def build_hourly_rows_from_aggregator(aggregator: DetectionAggregator) -> list[dict[str, Any]]:
    """Aggregate (hour, module, type) -> count rows from the bounded aggregator."""

    rows: list[dict[str, Any]] = []
    for (hour, module, etype), count in sorted(
        aggregator.hourly.items(), key=lambda i: (i[0][1], i[0][0], i[0][2])
    ):
        rows.append({"hour": hour, "module": module, "type": etype, "count": int(count)})
    return rows


def build_module_status_from_aggregator(aggregator: DetectionAggregator) -> list[dict[str, Any]]:
    """Build module_status rows from the aggregator. ``status`` stays ``미확정``."""

    modules = aggregator.discovered_modules
    rows: list[dict[str, Any]] = []
    for module in modules:
        summary = ModuleSummary(
            module=module,
            anomaly_count=aggregator.module_anomaly_count.get(module, 0),
            total_rows=aggregator.module_rows.get(module, 0),
            last_seen=aggregator.module_last_seen.get(module),
        )
        rows.append(
            {
                "module": summary.module,
                "anomaly_count": summary.anomaly_count,
                "anomaly_ratio": round(summary.anomaly_ratio, 6),
                "last_seen": summary.last_seen.isoformat() if summary.last_seen else "",
                "status": STATUS_UNDECIDED,
            }
        )
    return rows


def build_hourly_rows(events: list[DetectionEvent]) -> list[dict[str, Any]]:
    """Aggregate events into (hour, module, type) -> count rows (in-memory helper)."""

    counter: Counter[tuple[datetime, str, str]] = Counter()
    for event in events:
        hour = event.localtime.replace(minute=0, second=0, microsecond=0)
        counter[(hour, event.module, _primary_type(event))] += 1
    rows: list[dict[str, Any]] = []
    for (hour, module, etype), count in sorted(counter.items(), key=lambda i: (i[0][1], i[0][0], i[0][2])):
        rows.append({"hour": hour, "module": module, "type": etype, "count": int(count)})
    return rows


def build_module_status(
    events: list[DetectionEvent],
    module_rows: dict[str, int],
) -> list[dict[str, Any]]:
    """Build module_status rows from events (in-memory helper). ``status``=``미확정``."""

    anomaly_count: Counter[str] = Counter()
    last_seen: dict[str, datetime] = {}
    for event in events:
        if not event.any_detected():
            continue
        anomaly_count[event.module] += 1
        prev = last_seen.get(event.module)
        if prev is None or event.localtime > prev:
            last_seen[event.module] = event.localtime

    modules = sorted(set(module_rows) | set(anomaly_count))
    rows: list[dict[str, Any]] = []
    for module in modules:
        summary = ModuleSummary(
            module=module,
            anomaly_count=anomaly_count.get(module, 0),
            total_rows=module_rows.get(module, 0),
            last_seen=last_seen.get(module),
        )
        rows.append(
            {
                "module": summary.module,
                "anomaly_count": summary.anomaly_count,
                "anomaly_ratio": round(summary.anomaly_ratio, 6),
                "last_seen": summary.last_seen.isoformat() if summary.last_seen else "",
                "status": STATUS_UNDECIDED,
            }
        )
    return rows


def module_cap_check(discovered_modules: list[str]) -> dict[str, Any]:
    """O-5 check: discovered set must stay within the max-13 cap; warn if != 13."""

    count = len(discovered_modules)
    cap = config.MODULE_COUNT_EXPECTED
    over_cap = count > cap
    warnings: list[str] = []
    if over_cap:
        warnings.append(
            f"발견 설비 수 {count}가 상한 {cap}을 초과했습니다 (O-5). 상한 밖 설비 확인 필요."
        )
    elif count != cap:
        warnings.append(
            f"발견 설비 수 {count}가 기대값 {cap}과 다릅니다 (O-5). 데이터 범위 확인 필요."
        )
    return {
        "expected_cap": cap,
        "discovered_count": count,
        "discovered_modules": discovered_modules,
        "within_cap": not over_cap,
        "matches_expected": count == cap,
        "warnings": warnings,
    }


def _write_events_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    table = _events_batch_table(rows).cast(_events_pyarrow_schema())
    pq.write_table(table, path)


def _write_hourly_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "hour": pa.array([r["hour"] for r in rows], type=pa.timestamp("us", tz="Asia/Seoul")),
            "module": pa.array([r["module"] for r in rows], type=pa.string()),
            "type": pa.array([r["type"] for r in rows], type=pa.string()),
            "count": pa.array([r["count"] for r in rows], type=pa.int64()),
        }
    )
    pq.write_table(table, path)


def _write_module_status_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["module", "anomaly_count", "anomaly_ratio", "last_seen", "status"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _assert_not_overwriting_shared(paths: ArtifactPaths, *, allow_shared: bool) -> None:
    """Guard the shared artifacts contract unless ``allow_shared`` is set.

    Refuses to overwrite the shared ``config.ANOMALY_EVENTS`` /
    ``config.ANOMALY_HOURLY`` / ``config.MODULE_STATUS_CSV`` files to protect the
    existing artifacts (per task constraint). Writing to a shared target is only
    allowed when the caller opts in with ``allow_shared`` or the file is absent.
    """

    shared = {p.resolve() for p in (config.ANOMALY_EVENTS, config.ANOMALY_HOURLY, config.MODULE_STATUS_CSV)}
    for target in (paths.events, paths.hourly, paths.module_status):
        if target.resolve() in shared and target.exists() and not allow_shared:
            raise RuntimeError(
                f"Refusing to overwrite existing shared artifact: {target}. "
                "Write to a tmp path or pass --allow-shared."
            )


def write_artifacts(
    events: list[DetectionEvent],
    module_rows: dict[str, int],
    paths: ArtifactPaths,
    *,
    detector_meta: dict[str, Any] | None = None,
    allow_shared: bool = False,
    logger: Any | None = None,
) -> dict[str, Any]:
    """Write the three artifacts from an in-memory event list (samples/tests).

    Full-scan callers should use :func:`write_artifacts_streaming`, which streams
    events to parquet and builds hourly/module_status from the bounded aggregator.
    """

    _assert_not_overwriting_shared(paths, allow_shared=allow_shared)

    event_rows = build_event_rows(events)
    hourly_rows = build_hourly_rows(events)
    discovered = sorted(set(module_rows) | {e.module for e in events})
    status_rows = build_module_status(events, module_rows)
    cap = module_cap_check(discovered)

    _write_events_parquet(paths.events, event_rows)
    _write_hourly_parquet(paths.hourly, hourly_rows)
    _write_module_status_csv(paths.module_status, status_rows)

    return _write_run_meta(
        paths,
        events_written=len(event_rows),
        hourly_rows_written=len(hourly_rows),
        module_status_rows_written=len(status_rows),
        cap=cap,
        detector_meta=detector_meta,
        logger=logger,
    )


def write_aggregate_artifacts(
    aggregator: DetectionAggregator,
    paths: ArtifactPaths,
    *,
    events_written: int,
    detector_meta: dict[str, Any] | None = None,
    allow_shared: bool = False,
    logger: Any | None = None,
) -> dict[str, Any]:
    """Write hourly + module_status from the bounded aggregator.

    ``anomaly_events.parquet`` is written separately by the streaming
    :class:`ParquetEventSink`; ``events_written`` is its row count for run-meta.
    This path never materializes the full event list, so peak memory stays
    bounded by the aggregator size (modules x hours x types).
    """

    _assert_not_overwriting_shared(paths, allow_shared=allow_shared)

    hourly_rows = build_hourly_rows_from_aggregator(aggregator)
    status_rows = build_module_status_from_aggregator(aggregator)
    cap = module_cap_check(aggregator.discovered_modules)

    _write_hourly_parquet(paths.hourly, hourly_rows)
    _write_module_status_csv(paths.module_status, status_rows)

    return _write_run_meta(
        paths,
        events_written=events_written,
        hourly_rows_written=len(hourly_rows),
        module_status_rows_written=len(status_rows),
        cap=cap,
        detector_meta=detector_meta,
        logger=logger,
    )


def _write_run_meta(
    paths: ArtifactPaths,
    *,
    events_written: int,
    hourly_rows_written: int,
    module_status_rows_written: int,
    cap: dict[str, Any],
    detector_meta: dict[str, Any] | None,
    logger: Any | None,
) -> dict[str, Any]:
    run_meta = {
        "generated_at": datetime.now(tz=None).isoformat(),
        "events_written": events_written,
        "hourly_rows_written": hourly_rows_written,
        "module_status_rows_written": module_status_rows_written,
        "module_cap_check": cap,
        "status_label_policy": f"status='{STATUS_UNDECIDED}' (O-3 미승인)",
        # Memory model note: events are not held in a list on the full-scan path.
        "memory_model": "events streamed to parquet; hourly/module_status from bounded aggregator (메모리 상수)",
        "schema": {
            "anomaly_events": list(_EVENTS_SCHEMA_FIELDS),
            "anomaly_hourly": ["hour", "module", "type", "count"],
            "module_status": ["module", "anomaly_count", "anomaly_ratio", "last_seen", "status"],
        },
        "paths": {
            "events": str(paths.events),
            "hourly": str(paths.hourly),
            "module_status": str(paths.module_status),
        },
        "detector_meta": detector_meta,
    }
    paths.run_meta.parent.mkdir(parents=True, exist_ok=True)
    paths.run_meta.write_text(
        json.dumps(run_meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    if logger is not None:
        for warning in cap["warnings"]:
            logger.warning("module_cap %s", warning)
        logger.info(json.dumps({"event": "artifacts_written", **run_meta}, ensure_ascii=False, default=str))
    return run_meta


def main(argv: list[str] | None = None) -> int:
    import argparse

    from src.anomaly.detectors import run_detectors

    parser = argparse.ArgumentParser(description="Build anomaly_events/anomaly_hourly/module_status artifacts (tmp by default)")
    parser.add_argument("--input", type=Path, default=config.TRAINING_CSV)
    parser.add_argument("--max-rows", type=int, default=None, help="Scan only the first N rows (sample).")
    parser.add_argument("--out-dir", type=Path, default=None, help="Output dir (defaults to artifacts/tmp).")
    parser.add_argument(
        "--allow-shared",
        action="store_true",
        help="Allow writing to the shared artifacts/ contract (overwrites if present).",
    )
    args = parser.parse_args(argv)

    logger = _common.configure_logger("artifacts", config.ARTIFACTS_DIR)
    paths = default_tmp_paths(args.out_dir)

    # Guard before the (potentially long) scan so a shared-overwrite fails fast.
    _assert_not_overwriting_shared(paths, allow_shared=args.allow_shared)

    # Stream detected events straight to parquet; keep only bounded aggregators.
    event_sink = ParquetEventSink(paths.events)
    try:
        result = run_detectors(
            args.input,
            max_rows=args.max_rows,
            sink=event_sink,
            collect_events=False,
            logger=logger,
        )
    finally:
        event_sink.close()

    write_aggregate_artifacts(
        result.aggregator,
        paths,
        events_written=event_sink.rows_written,
        detector_meta=result.meta,
        allow_shared=args.allow_shared,
        logger=logger,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
