"""Statistical and unsupervised anomaly detectors with method matching (6.2 / B2).

Three methods are applied to the **same 5-second rows** and matched by the
``(module, localtime)`` key (B1):

- **rule**: the O-9 (a) three-phase simultaneous rule from ``rules.py``.
- **statistical**: per-module robust z-score (median / MAD, falls back to
  mean/std) computed from streaming per-module statistics in the first pass.
- **unsupervised**: an ``IsolationForest`` trained on a fixed-size Reservoir
  sample in the first pass, then used to score every 5-second row in the second
  pass. This path is explicitly **2-Pass** per the design.

Memory model (design: "전체 적재 금지, 메모리 상수"). The second (scoring) pass
does **not** accumulate the detected events in a Python list. Each detected event
is handed row-by-row to an :class:`EventSink` (e.g. a streaming parquet writer),
and only small bounded aggregators are kept in memory:

- hourly ``(hour, module, primary_type) -> count``,
- per-module ``anomaly_count`` and most-recent localtime,
- per-method counts and pairwise/triple method overlap counters,
- per-module total parsed-row counts (the ``module_status`` denominator).

These aggregators are bounded by ``modules x hours x types`` (roughly 3600 hours
x 13 modules x a handful of types), so peak memory stays constant regardless of
the 4.9GB source size. The per-module row counts are gathered in the same pass,
so the previous separate denominator scan is removed (3-pass -> 2-pass).

Design references: candidate features (3-phase voltage/power-factor/current
means, activePower, ``activePower / Σ(V×I×PF/100)``), Reservoir Sampling with a
default size of 100,000, ``RANDOM_SEED`` fixed, no full-source materialization
(NFR-1), source immutability (NFR-2), Validation_CSV untouched (NFR-4), and a
reproducibility block that records sample size / chunk / seed (NFR-7).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
import argparse
import json
import math
import random
from pathlib import Path
from typing import Any, Protocol

from config import config
from src.anomaly import _common, rules as rules_mod
from src.common.streaming_csv import scan_csv

DEFAULT_RESERVOIR_SIZE = 100_000
# Robust z-score magnitude above which the statistical method flags a feature.
STAT_Z_THRESHOLD = 3.0
TYPE_STATISTICAL = "statistical_outlier"
TYPE_UNSUPERVISED = "unsupervised_outlier"

# Methods matched on the same 5-second row (shared with compare.py).
METHODS = ("rule", "statistical", "unsupervised")

# How often (in parsed rows) each pass emits a progress log line.
PROGRESS_EVERY = 2_000_000


# ---------------------------------------------------------------------------
# Streaming per-module statistics (first pass) for the robust z-score method.
# ---------------------------------------------------------------------------
@dataclass
class _ModuleStat:
    """Running count/mean/M2 (Welford) per module for one feature."""

    count: int = 0
    mean: float = 0.0
    m2: float = 0.0
    values_for_median: list[float] = field(default_factory=list)

    def update(self, value: float, *, keep_sample: bool, sample_cap: int) -> None:
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        self.m2 += delta * (value - self.mean)
        if keep_sample and len(self.values_for_median) < sample_cap:
            self.values_for_median.append(value)

    @property
    def std(self) -> float:
        if self.count < 2:
            return 0.0
        return math.sqrt(self.m2 / (self.count - 1))

    def median_mad(self) -> tuple[float, float]:
        """Return (median, MAD) from the retained bounded sample."""

        sample = sorted(self.values_for_median)
        if not sample:
            return 0.0, 0.0
        median = _median_sorted(sample)
        deviations = sorted(abs(v - median) for v in sample)
        mad = _median_sorted(deviations)
        return median, mad


def _median_sorted(values: list[float]) -> float:
    n = len(values)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2.0


class ModuleStatistics:
    """Per-module statistics for the ``active_power`` feature (robust z-score).

    We track the activePower distribution per module because it is the primary
    load signal; the robust z-score (median/MAD) is more outlier-resistant than
    a plain 3σ, and we fall back to mean/std when MAD is zero.
    """

    def __init__(self, *, median_sample_cap: int = 20_000) -> None:
        self._stats: dict[str, _ModuleStat] = {}
        self._median_sample_cap = median_sample_cap
        # Finalized per-module (median, mad, mean, std); filled lazily on first
        # robust_z call so the median sort runs once per module, not per row.
        self._finalized: dict[str, tuple[float, float, float, float]] | None = None

    def update(self, row: _common.ParsedRow) -> None:
        stat = self._stats.setdefault(row.module, _ModuleStat())
        stat.update(row.active_power, keep_sample=True, sample_cap=self._median_sample_cap)

    def _finalize(self) -> None:
        self._finalized = {}
        for module, stat in self._stats.items():
            median, mad = stat.median_mad()
            self._finalized[module] = (median, mad, stat.mean, stat.std)

    def robust_z(self, module: str, value: float) -> float:
        if self._finalized is None:
            self._finalize()
        assert self._finalized is not None
        finalized = self._finalized.get(module)
        stat = self._stats.get(module)
        if finalized is None or stat is None or stat.count < 2:
            return 0.0
        median, mad, mean, std = finalized
        if mad > 1e-9:
            # 0.6745 scales MAD to be comparable to a standard deviation.
            return 0.6745 * (value - median) / mad
        if std > 1e-9:
            return (value - mean) / std
        return 0.0

    def summary(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for module, stat in self._stats.items():
            median, mad = stat.median_mad()
            out[module] = {
                "count": float(stat.count),
                "mean": stat.mean,
                "std": stat.std,
                "median": median,
                "mad": mad,
            }
        return out

    @property
    def modules(self) -> list[str]:
        return sorted(self._stats)


# ---------------------------------------------------------------------------
# Reservoir sampling (first pass) for the IsolationForest training set.
# ---------------------------------------------------------------------------
class Reservoir:
    """Fixed-size uniform reservoir sample of feature vectors (Algorithm R)."""

    def __init__(self, size: int, *, seed: int) -> None:
        if size <= 0:
            raise ValueError("reservoir size must be positive")
        self.size = size
        self._rng = random.Random(seed)
        self._seen = 0
        self.samples: list[list[float]] = []

    def offer(self, vector: list[float]) -> None:
        self._seen += 1
        if len(self.samples) < self.size:
            self.samples.append(vector)
            return
        j = self._rng.randint(0, self._seen - 1)
        if j < self.size:
            self.samples[j] = vector

    @property
    def seen(self) -> int:
        return self._seen


# ---------------------------------------------------------------------------
# Matched event record (same 5-second row across the three methods).
# ---------------------------------------------------------------------------
@dataclass
class DetectionEvent:
    module: str
    localtime: datetime
    detected_by: dict[str, bool]
    rule_types: list[str]
    value: float
    normal_range: str
    statistical_z: float
    unsupervised_score: float

    def key(self) -> tuple[str, str]:
        return _common.event_key(self.module, self.localtime)

    def any_detected(self) -> bool:
        return any(self.detected_by.values())

    def primary_type(self) -> str:
        """Single ``type`` for the events/hourly schema (rule type wins)."""

        if self.rule_types:
            return self.rule_types[0]
        if self.detected_by.get("statistical"):
            return TYPE_STATISTICAL
        return TYPE_UNSUPERVISED


# ---------------------------------------------------------------------------
# Event sinks: detected events stream through a sink instead of a list so a
# full scan never materializes every event in memory ("메모리 상수" 원칙).
# ---------------------------------------------------------------------------
class EventSink(Protocol):
    """Receives one detected :class:`DetectionEvent` at a time."""

    def emit(self, event: DetectionEvent) -> None: ...

    def close(self) -> None: ...


class MemoryEventSink:
    """Collects events into a list. For samples/tests and ``build_comparison``.

    This is the only sink that keeps events in memory, so it must not be used
    for an unbounded full scan.
    """

    def __init__(self) -> None:
        self.events: list[DetectionEvent] = []

    def emit(self, event: DetectionEvent) -> None:
        self.events.append(event)

    def close(self) -> None:  # noqa: D401 - no resources to release.
        return None


class NullEventSink:
    """Drops events; used when only the aggregates matter (bounded memory)."""

    def emit(self, event: DetectionEvent) -> None:  # noqa: D401
        return None

    def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Bounded aggregators kept in memory during the scoring pass.
# ---------------------------------------------------------------------------
class DetectionAggregator:
    """Small, constant-ish aggregators for hourly / module / method summaries.

    Sizes are bounded by ``modules x hours x types`` (hourly), ``modules``
    (module status) and a fixed set of method-overlap keys, so this stays
    effectively constant regardless of the source row count.
    """

    def __init__(self) -> None:
        # (hour, module, primary_type) -> count
        self.hourly: Counter[tuple[datetime, str, str]] = Counter()
        # module -> anomaly_count / most recent localtime
        self.module_anomaly_count: Counter[str] = Counter()
        self.module_last_seen: dict[str, datetime] = {}
        # module -> total parsed rows (status denominator, folded into pass 2)
        self.module_rows: Counter[str] = Counter()
        # per-method detection counts + method overlaps
        self.method_counts: Counter[str] = Counter()
        self.overlap_counts: Counter[str] = Counter({
            "rule_and_statistical": 0,
            "rule_and_unsupervised": 0,
            "statistical_and_unsupervised": 0,
            "all_three": 0,
            "any": 0,
        })
        # Overall detected-event span (for compare period metadata).
        self.period_start: datetime | None = None
        self.period_end: datetime | None = None
        self.total_detected = 0

    def count_row(self, module: str) -> None:
        """Count every parsed row (status denominator), detected or not."""

        self.module_rows[module] += 1

    def add_event(self, event: DetectionEvent) -> None:
        """Fold one detected event into the bounded aggregators."""

        primary = event.primary_type()
        hour = event.localtime.replace(minute=0, second=0, microsecond=0)
        self.hourly[(hour, event.module, primary)] += 1

        self.module_anomaly_count[event.module] += 1
        prev = self.module_last_seen.get(event.module)
        if prev is None or event.localtime > prev:
            self.module_last_seen[event.module] = event.localtime

        rule = bool(event.detected_by.get("rule"))
        stat = bool(event.detected_by.get("statistical"))
        unsup = bool(event.detected_by.get("unsupervised"))
        if rule:
            self.method_counts["rule"] += 1
        if stat:
            self.method_counts["statistical"] += 1
        if unsup:
            self.method_counts["unsupervised"] += 1
        if rule and stat:
            self.overlap_counts["rule_and_statistical"] += 1
        if rule and unsup:
            self.overlap_counts["rule_and_unsupervised"] += 1
        if stat and unsup:
            self.overlap_counts["statistical_and_unsupervised"] += 1
        if rule and stat and unsup:
            self.overlap_counts["all_three"] += 1
        self.overlap_counts["any"] += 1

        self.total_detected += 1
        if self.period_start is None or event.localtime < self.period_start:
            self.period_start = event.localtime
        if self.period_end is None or event.localtime > self.period_end:
            self.period_end = event.localtime

    @property
    def discovered_modules(self) -> list[str]:
        return sorted(set(self.module_rows) | set(self.module_anomaly_count))


@dataclass
class DetectorResult:
    events: list[DetectionEvent] | None
    aggregator: DetectionAggregator
    rows_parsed: int
    rows_skipped: int
    reservoir_size: int
    reservoir_seen: int
    module_stats: dict[str, dict[str, float]]
    partial: bool
    meta: dict[str, Any]

    @property
    def events_in_memory(self) -> bool:
        return self.events is not None


def _train_isolation_forest(samples: list[list[float]]):
    """Fit an IsolationForest on the reservoir sample (seed fixed). None if empty."""

    if not samples:
        return None
    from sklearn.ensemble import IsolationForest

    model = IsolationForest(random_state=config.RANDOM_SEED, n_estimators=100)
    model.fit(samples)
    return model


def run_detectors(
    input_path: str | Path = config.TRAINING_CSV,
    *,
    max_rows: int | None = None,
    reservoir_size: int = DEFAULT_RESERVOIR_SIZE,
    chunk_size: int = 1024 * 1024,
    only_detected: bool = True,
    sink: EventSink | None = None,
    collect_events: bool | None = None,
    logger: Any | None = None,
) -> DetectorResult:
    """Two-pass detection: build stats+reservoir (pass 1), then score rows (pass 2).

    Pass 1 (sequential): accumulate per-module statistics and a fixed-size
    reservoir sample, then fit the IsolationForest.
    Pass 2 (sequential): recompute rule hits, robust z-score, and model score for
    each 5-second row, match them into one event per ``(module, localtime)``, and
    stream each detected event to ``sink`` while folding it into bounded
    aggregators (hourly, per-module, per-method). Per-module total row counts are
    gathered in this same pass, so no separate denominator scan is needed.

    Event routing:
    - ``sink`` receives each detected event row-by-row. Pass a streaming parquet
      sink for a full scan so events never pile up in memory.
    - ``collect_events`` controls whether a :class:`MemoryEventSink` also keeps
      the events (populating ``result.events``). It defaults to True only when no
      external ``sink`` is given (keeps samples/tests working); pass False to
      guarantee bounded memory on a full scan.

    When ``only_detected`` is True (default) only rows flagged by at least one
    method are emitted/aggregated.
    """

    source = Path(input_path)
    before = _common.source_metadata(source)

    if collect_events is None:
        collect_events = sink is None
    memory_sink = MemoryEventSink() if collect_events else None
    external_sink = sink

    def _emit(event: DetectionEvent) -> None:
        if memory_sink is not None:
            memory_sink.emit(event)
        if external_sink is not None:
            external_sink.emit(event)

    aggregator = DetectionAggregator()

    # ----- Pass 1: statistics + reservoir -----
    columns: dict[str, str] = {}
    stats = ModuleStatistics()
    reservoir = Reservoir(reservoir_size, seed=config.RANDOM_SEED)
    pass1 = {"parsed": 0, "skipped": 0}

    def pass1_row(raw: dict[str, str]) -> None:
        if not columns:
            columns.update(_common.resolve_columns(list(raw.keys())))
        parsed = _common.parse_row(raw, columns)
        if parsed is None:
            pass1["skipped"] += 1
            return
        pass1["parsed"] += 1
        stats.update(parsed)
        reservoir.offer(parsed.feature_vector())
        if logger is not None and pass1["parsed"] % PROGRESS_EVERY == 0:
            logger.info(
                json.dumps(
                    {"event": "detectors_pass1_progress", "rows_parsed": pass1["parsed"]},
                    ensure_ascii=False,
                )
            )

    scan1 = scan_csv(source, max_rows=max_rows, chunk_size=chunk_size, row_callback=pass1_row)
    model = _train_isolation_forest(reservoir.samples)

    # ----- Pass 2: score every row, stream detected events, aggregate -----
    # Rows are buffered and the IsolationForest scores them in batches so a
    # multi-million-row scan does not pay per-row sklearn call overhead. Memory
    # stays bounded because the buffer is flushed every ``score_batch`` rows and
    # detected events go to the sink/aggregators rather than a growing list.
    pass2 = {"parsed": 0, "skipped": 0}
    buffer: list[_common.ParsedRow] = []
    score_batch = 20_000

    def _flush() -> None:
        if not buffer:
            return
        vectors = [row.feature_vector() for row in buffer]
        if model is not None:
            # decision_function: lower = more anomalous. predict: -1 = outlier.
            scores = model.decision_function(vectors)
            preds = model.predict(vectors)
        else:
            scores = [0.0] * len(buffer)
            preds = [1] * len(buffer)
        for parsed, score, pred in zip(buffer, scores, preds):
            rule_candidates = rules_mod.evaluate_row(parsed)
            rule_types = [c.type for c in rule_candidates]
            z = stats.robust_z(parsed.module, parsed.active_power)
            stat_hit = abs(z) >= STAT_Z_THRESHOLD
            unsup_hit = pred == -1

            detected_by = {
                "rule": bool(rule_candidates),
                "statistical": bool(stat_hit),
                "unsupervised": bool(unsup_hit),
            }
            if only_detected and not any(detected_by.values()):
                continue

            if rule_types:
                value = rule_candidates[0].value
                normal_range = rule_candidates[0].normal_range
            elif stat_hit:
                value = parsed.active_power
                normal_range = f"|robust_z| < {STAT_Z_THRESHOLD} (per module activePower)"
            else:
                value = parsed.active_power
                normal_range = "IsolationForest inlier"

            event = DetectionEvent(
                module=parsed.module,
                localtime=parsed.localtime,
                detected_by=detected_by,
                rule_types=rule_types,
                value=value,
                normal_range=normal_range,
                statistical_z=z,
                unsupervised_score=float(score),
            )
            aggregator.add_event(event)
            _emit(event)
        buffer.clear()

    def pass2_row(raw: dict[str, str]) -> None:
        parsed = _common.parse_row(raw, columns)
        if parsed is None:
            pass2["skipped"] += 1
            return
        pass2["parsed"] += 1
        aggregator.count_row(parsed.module)
        buffer.append(parsed)
        if len(buffer) >= score_batch:
            _flush()
        if logger is not None and pass2["parsed"] % PROGRESS_EVERY == 0:
            logger.info(
                json.dumps(
                    {
                        "event": "detectors_pass2_progress",
                        "rows_parsed": pass2["parsed"],
                        "detected_so_far": aggregator.total_detected,
                    },
                    ensure_ascii=False,
                )
            )

    scan2 = scan_csv(source, max_rows=max_rows, chunk_size=chunk_size, row_callback=pass2_row)
    _flush()

    after = _common.source_metadata(source)
    immutability = _common.assert_source_unchanged(before, after)

    meta = _common.reproducibility_meta(
        module_name="detectors",
        input_path=source,
        max_rows=max_rows,
        chunk_size=chunk_size,
        extra={
            "passes": 2,
            "pass1_rows": scan1.csv_record_count,
            "pass2_rows": scan2.csv_record_count,
            "rows_parsed_pass1": pass1["parsed"],
            "rows_parsed_pass2": pass2["parsed"],
            "reservoir_size": reservoir_size,
            "reservoir_seen": reservoir.seen,
            "reservoir_filled": len(reservoir.samples),
            "isolation_forest_trained": model is not None,
            "statistical_z_threshold": STAT_Z_THRESHOLD,
            "feature_names": list(_common.FEATURE_NAMES),
            "random_seed": config.RANDOM_SEED,
            "validation_csv_referenced": False,
            "source_immutability": immutability,
            # Memory model: detected events are streamed to a sink; only bounded
            # aggregators are retained. ``events_in_memory`` is False on a full
            # scan so peak memory stays constant ("메모리 상수" 원칙).
            "events_in_memory": memory_sink is not None,
            "events_detected": aggregator.total_detected,
            "progress_log_every_rows": PROGRESS_EVERY,
        },
    )
    if logger is not None:
        logger.info(json.dumps({"event": "detectors_run", **meta}, ensure_ascii=False, default=str))

    return DetectorResult(
        events=memory_sink.events if memory_sink is not None else None,
        aggregator=aggregator,
        rows_parsed=pass2["parsed"],
        rows_skipped=pass2["skipped"],
        reservoir_size=reservoir_size,
        reservoir_seen=reservoir.seen,
        module_stats=stats.summary(),
        partial=scan2.partial or (max_rows is not None),
        meta=meta,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Statistical + unsupervised (2-pass reservoir) anomaly detection matched to rule hits"
    )
    parser.add_argument("--input", type=Path, default=config.TRAINING_CSV)
    parser.add_argument("--max-rows", type=int, default=None, help="Scan only the first N rows (sample).")
    parser.add_argument("--sample", action="store_true", help="Alias for a small sampled run (uses --max-rows).")
    parser.add_argument("--reservoir-size", type=int, default=DEFAULT_RESERVOIR_SIZE)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logger = _common.configure_logger("detectors", config.ARTIFACTS_DIR)
    logger.info(
        json.dumps(
            {
                "event": "detectors_started",
                "input": str(args.input),
                "max_rows": args.max_rows,
                "reservoir_size": args.reservoir_size,
                "random_seed": config.RANDOM_SEED,
            },
            ensure_ascii=False,
        )
    )
    result = run_detectors(
        args.input,
        max_rows=args.max_rows,
        reservoir_size=args.reservoir_size,
        logger=logger,
    )
    logger.info(
        json.dumps(
            {
                "event": "detectors_summary",
                "rows_parsed": result.rows_parsed,
                "events_detected": result.aggregator.total_detected,
                "events_in_memory": result.events_in_memory,
                "modules": sorted(result.module_stats),
                "reservoir_seen": result.reservoir_seen,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
