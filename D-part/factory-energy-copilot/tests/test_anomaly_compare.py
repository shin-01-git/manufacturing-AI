"""Tests for the proxy comparison table (6.3 / B3, O-4)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.anomaly.compare import build_comparison, build_comparison_from_aggregator
from src.anomaly.detectors import DetectionAggregator, DetectionEvent

KST = ZoneInfo("Asia/Seoul")


def _event(hour, detected_by, rule_types=None):
    return DetectionEvent(
        module="M1",
        localtime=datetime(2024, 12, 1, hour, tzinfo=KST),
        detected_by=detected_by,
        rule_types=rule_types or [],
        value=1.0,
        normal_range="n/a",
        statistical_z=0.0,
        unsupervised_score=0.0,
    )


def _events():
    return [
        _event(0, {"rule": True, "statistical": True, "unsupervised": False}, ["voltage_low_3phase"]),
        _event(1, {"rule": True, "statistical": False, "unsupervised": True}, ["voltage_low_3phase"]),
        _event(2, {"rule": False, "statistical": True, "unsupervised": True}),
    ]


def test_method_counts_and_overlap():
    table = build_comparison(_events())
    assert table.method_counts == {"rule": 2, "statistical": 2, "unsupervised": 2}
    assert table.overlap_counts["rule_and_statistical"] == 1
    assert table.overlap_counts["all_three"] == 0


def test_proxies_are_named_and_not_ground_truth():
    table = build_comparison(_events())
    # Rule result is never called ground truth (O-4 undecided).
    assert table.independent_label_used is False
    for name, proxy in table.proxies.items():
        assert proxy["is_proxy"] is True
        assert proxy["ground_truth"] is False
        # Proxy names use 규칙 대비 일치율 / 규칙 포착률.
        assert ("일치율" in name) or ("포착률" in name)


def test_proxies_record_denominator_and_limitation():
    table = build_comparison(_events())
    for proxy in table.proxies.values():
        assert "denominator" in proxy
        assert "denominator_desc" in proxy
        assert proxy["limitation"]
        assert proxy["period_start"] is not None
        assert proxy["period_end"] is not None
    assert table.limitations


def test_aggregator_comparison_matches_event_comparison():
    """build_comparison_from_aggregator equals build_comparison over same events."""

    events = _events()
    aggregator = DetectionAggregator()
    for event in events:
        aggregator.add_event(event)

    from_events = build_comparison(events).to_dict()
    from_agg = build_comparison_from_aggregator(aggregator).to_dict()

    assert from_agg["method_counts"] == from_events["method_counts"]
    assert from_agg["overlap_counts"] == from_events["overlap_counts"]
    assert from_agg["total_events"] == from_events["total_events"]
    assert from_agg["proxies"] == from_events["proxies"]
    assert from_agg["independent_label_used"] is False


def test_empty_events_gives_none_values_not_crash():
    table = build_comparison([])
    assert table.total_events == 0
    for proxy in table.proxies.values():
        assert proxy["value"] is None
