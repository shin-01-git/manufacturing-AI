"""Method comparison and proxy metrics for anomaly detection (6.3 / B3, O-4).

Builds a per-method detection count and overlap table over the detected events.
Because there is **no independent ground-truth label** yet (O-4 is undecided), the
rule result is NOT called "ground truth". Rule-referenced numbers are named as
**proxies** — ``규칙_대비_일치율`` (agreement with rule) and ``규칙_포착률``
(rule recall proxy) — and each proxy records its denominator, target period, and
limitation. No independent-label metric is produced before O-4 approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from src.anomaly.detectors import DetectionAggregator, DetectionEvent

METHODS = ("rule", "statistical", "unsupervised")

# Limitation note attached to every proxy so downstream UI/agent cannot mistake
# a rule-referenced proxy for an evaluation against an independent label.
PROXY_LIMITATION = (
    "규칙은 독립 정답이 아님(O-4 미결정). 규칙 자체의 한계(3상 동시 조건)로 "
    "미포착 이상이 있을 수 있어 아래 값은 참고용 프록시다."
)


@dataclass
class ComparisonTable:
    period_start: str | None
    period_end: str | None
    total_events: int
    method_counts: dict[str, int]
    overlap_counts: dict[str, int]
    proxies: dict[str, dict[str, Any]]
    limitations: list[str] = field(default_factory=list)
    independent_label_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "period_start": self.period_start,
            "period_end": self.period_end,
            "total_events": self.total_events,
            "method_counts": self.method_counts,
            "overlap_counts": self.overlap_counts,
            "proxies": self.proxies,
            "limitations": self.limitations,
            "independent_label_used": self.independent_label_used,
        }


def _period(events: Iterable[DetectionEvent]) -> tuple[str | None, str | None]:
    times: list[datetime] = [e.localtime for e in events]
    if not times:
        return None, None
    return min(times).isoformat(), max(times).isoformat()


def build_comparison(events: list[DetectionEvent]) -> ComparisonTable:
    """Build the method comparison table and rule-referenced proxy metrics (B3)."""

    method_counts = {m: 0 for m in METHODS}
    for event in events:
        for method in METHODS:
            if event.detected_by.get(method):
                method_counts[method] += 1

    # Pairwise and triple overlaps.
    def hit(e: DetectionEvent, m: str) -> bool:
        return bool(e.detected_by.get(m))

    overlap_counts = {
        "rule_and_statistical": sum(1 for e in events if hit(e, "rule") and hit(e, "statistical")),
        "rule_and_unsupervised": sum(1 for e in events if hit(e, "rule") and hit(e, "unsupervised")),
        "statistical_and_unsupervised": sum(
            1 for e in events if hit(e, "statistical") and hit(e, "unsupervised")
        ),
        "all_three": sum(
            1 for e in events if hit(e, "rule") and hit(e, "statistical") and hit(e, "unsupervised")
        ),
        "any": sum(1 for e in events if e.any_detected()),
    }

    period_start, period_end = _period(events)
    rule_count = method_counts["rule"]

    proxies: dict[str, dict[str, Any]] = {}
    for method in ("statistical", "unsupervised"):
        agree = sum(1 for e in events if hit(e, "rule") and hit(e, method))
        # 규칙_대비_일치율: of this method's detections, how many the rule also flagged.
        method_total = method_counts[method]
        proxies[f"규칙_대비_일치율__{method}"] = {
            "value": (agree / method_total) if method_total else None,
            "numerator": agree,
            "denominator": method_total,
            "denominator_desc": f"{method} 방법 탐지 건수",
            "period_start": period_start,
            "period_end": period_end,
            "limitation": PROXY_LIMITATION,
            "is_proxy": True,
            "ground_truth": False,
        }
        # 규칙_포착률: of rule-flagged rows, how many this method also flagged.
        proxies[f"규칙_포착률__{method}"] = {
            "value": (agree / rule_count) if rule_count else None,
            "numerator": agree,
            "denominator": rule_count,
            "denominator_desc": "규칙(O-9 3상 동시) 후보 건수 — 정답 아님",
            "period_start": period_start,
            "period_end": period_end,
            "limitation": PROXY_LIMITATION,
            "is_proxy": True,
            "ground_truth": False,
        }

    return ComparisonTable(
        period_start=period_start,
        period_end=period_end,
        total_events=len(events),
        method_counts=method_counts,
        overlap_counts=overlap_counts,
        proxies=proxies,
        limitations=[PROXY_LIMITATION, "O-4 독립 라벨 미승인: 독립 정답 지표 미생성."],
        independent_label_used=False,
    )


def _build_proxies(
    method_counts: dict[str, int],
    overlap_counts: dict[str, int],
    period_start: str | None,
    period_end: str | None,
) -> dict[str, dict[str, Any]]:
    """Build the rule-referenced proxy metrics from counts/overlaps (shared)."""

    rule_count = method_counts.get("rule", 0)
    proxies: dict[str, dict[str, Any]] = {}
    for method in ("statistical", "unsupervised"):
        agree = overlap_counts.get(f"rule_and_{method}", 0)
        method_total = method_counts.get(method, 0)
        # 규칙_대비_일치율: of this method's detections, how many the rule also flagged.
        proxies[f"규칙_대비_일치율__{method}"] = {
            "value": (agree / method_total) if method_total else None,
            "numerator": agree,
            "denominator": method_total,
            "denominator_desc": f"{method} 방법 탐지 건수",
            "period_start": period_start,
            "period_end": period_end,
            "limitation": PROXY_LIMITATION,
            "is_proxy": True,
            "ground_truth": False,
        }
        # 규칙_포착률: of rule-flagged rows, how many this method also flagged.
        proxies[f"규칙_포착률__{method}"] = {
            "value": (agree / rule_count) if rule_count else None,
            "numerator": agree,
            "denominator": rule_count,
            "denominator_desc": "규칙(O-9 3상 동시) 후보 건수 — 정답 아님",
            "period_start": period_start,
            "period_end": period_end,
            "limitation": PROXY_LIMITATION,
            "is_proxy": True,
            "ground_truth": False,
        }
    return proxies


def build_comparison_from_aggregator(aggregator: DetectionAggregator) -> ComparisonTable:
    """Build the comparison table from the detector's bounded aggregator (B3).

    Uses the method counts and method-overlap counters accumulated during the
    scoring pass, so no second pass over an event list is needed. This is the
    full-scan path; results match :func:`build_comparison` over the same events.
    """

    method_counts = {m: aggregator.method_counts.get(m, 0) for m in METHODS}
    overlap_counts = {
        "rule_and_statistical": aggregator.overlap_counts.get("rule_and_statistical", 0),
        "rule_and_unsupervised": aggregator.overlap_counts.get("rule_and_unsupervised", 0),
        "statistical_and_unsupervised": aggregator.overlap_counts.get("statistical_and_unsupervised", 0),
        "all_three": aggregator.overlap_counts.get("all_three", 0),
        "any": aggregator.overlap_counts.get("any", 0),
    }
    period_start = aggregator.period_start.isoformat() if aggregator.period_start else None
    period_end = aggregator.period_end.isoformat() if aggregator.period_end else None

    return ComparisonTable(
        period_start=period_start,
        period_end=period_end,
        total_events=aggregator.total_detected,
        method_counts=method_counts,
        overlap_counts=overlap_counts,
        proxies=_build_proxies(method_counts, overlap_counts, period_start, period_end),
        limitations=[PROXY_LIMITATION, "O-4 독립 라벨 미승인: 독립 정답 지표 미생성."],
        independent_label_used=False,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    from pathlib import Path

    from config import config
    from src.anomaly import _common
    from src.anomaly.detectors import run_detectors

    parser = argparse.ArgumentParser(description="Compare rule/statistical/unsupervised detections (proxy metrics only)")
    parser.add_argument("--input", type=Path, default=config.TRAINING_CSV)
    parser.add_argument("--max-rows", type=int, default=None, help="Scan only the first N rows (sample).")
    args = parser.parse_args(argv)

    logger = _common.configure_logger("compare", config.ARTIFACTS_DIR)
    # Bounded memory: do not collect events; use the aggregator's method counts.
    result = run_detectors(args.input, max_rows=args.max_rows, collect_events=False, logger=logger)
    table = build_comparison_from_aggregator(result.aggregator)
    logger.info(
        json.dumps(
            {"event": "compare_table", **table.to_dict()},
            ensure_ascii=False,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
