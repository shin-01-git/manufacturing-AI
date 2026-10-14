"""규칙(3상 동시) 기준으로 이상탐지 산출물을 재생성한다 (재스캔 없음).

이미 생성된 ``artifacts/anomaly_events.parquet`` (전체 스캔 결과, 734만 건)에서
**규칙(O-9 3상 동시)으로 탐지된 이벤트만** 걸러 F5 사전 분석과 일치하는
설비 상태표·시간별 집계·규칙 이벤트 파일을 다시 만든다.

배경: 전체 스캔에서 IsolationForest(비지도)가 전체의 21.8%를 과탐지해
module_status가 "전 설비 ~21.6%"로 오염됐다. 규칙(3상 동시)은 64,934건으로
3호기·예비건조기·6호기 3개 설비만 탐지해 F5와 정확히 일치한다. 따라서 설비
상태의 주 기준을 규칙으로 바꾼다. 통계·비지도 결과는 "방법 비교/과탐지 한계"
로만 보조 기록한다(B3).

분모(설비별 전체 5초 행 수)는 ``hourly_from_full.parquet``의 설비별 ``n`` 합계를
사용한다(원본 재스캔 불필요). status 경계값은 O-3 미승인이므로 '미확정' 라벨을
유지한다.

이 스크립트는 원본 CSV를 읽지 않고 artifacts만 읽는다.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import argparse
import csv
import json
from pathlib import Path
from typing import Any

from config import config

STATUS_UNDECIDED = "미확정"
RULE_TYPES = ("voltage_low_3phase", "power_factor_low_3phase")


def _module_total_rows() -> dict[str, int]:
    """설비별 전체 5초 행 수를 hourly_from_full.parquet의 n 합계로 구한다."""

    import pyarrow.parquet as pq

    table = pq.read_table(config.HOURLY_FROM_FULL, columns=["module", "n"])
    totals: dict[str, int] = defaultdict(int)
    modules = table.column("module").to_pylist()
    counts = table.column("n").to_pylist()
    for module, n in zip(modules, counts):
        totals[module] += int(n)
    return dict(totals)


def rebuild(
    *,
    events_path: Path = config.ANOMALY_EVENTS,
    hourly_out: Path = config.ANOMALY_HOURLY,
    status_out: Path = config.MODULE_STATUS_CSV,
    rule_events_out: Path | None = None,
) -> dict[str, Any]:
    """규칙 기준으로 module_status / anomaly_hourly / (선택) 규칙 이벤트를 재생성한다."""

    import pyarrow as pa
    import pyarrow.parquet as pq

    events_file = Path(events_path)
    source = pq.ParquetFile(events_file)

    module_totals = _module_total_rows()

    rule_by_module: Counter[str] = Counter()
    rule_by_type: Counter[str] = Counter()
    hourly_counts: dict[tuple[Any, str, str], int] = defaultdict(int)
    last_seen: dict[str, datetime] = {}
    method_counts: Counter[str] = Counter()
    overlap: Counter[str] = Counter()
    total_events = 0

    # 규칙 이벤트만 별도 parquet로도 저장(대시보드 이벤트 표가 규칙만 보도록).
    rule_writer: Any | None = None
    rule_schema = pa.schema(
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
    if rule_events_out is not None:
        Path(rule_events_out).parent.mkdir(parents=True, exist_ok=True)
        rule_writer = pq.ParquetWriter(rule_events_out, rule_schema)

    batch_cols = ["module", "localtime", "type", "value", "normal_range", "detected_by", "score"]
    for batch in source.iter_batches(batch_size=200_000, columns=batch_cols):
        modules = batch.column("module").to_pylist()
        times = batch.column("localtime").to_pylist()
        types = batch.column("type").to_pylist()
        detected = batch.column("detected_by").to_pylist()

        for module, localtime, type_, db in zip(modules, times, types, detected):
            total_events += 1
            flags = json.loads(db)
            if flags.get("rule"):
                method_counts["rule"] += 1
            if flags.get("statistical"):
                method_counts["statistical"] += 1
            if flags.get("unsupervised"):
                method_counts["unsupervised"] += 1
            if flags.get("rule") and flags.get("unsupervised"):
                overlap["rule_and_unsupervised"] += 1
            if flags.get("rule") and flags.get("statistical"):
                overlap["rule_and_statistical"] += 1

            if not flags.get("rule"):
                continue
            rtype = type_
            rule_by_module[module] += 1
            rule_by_type[rtype] += 1
            if localtime is not None:
                hour = localtime.replace(minute=0, second=0, microsecond=0)
                hourly_counts[(hour, module, rtype)] += 1
                prev = last_seen.get(module)
                if prev is None or localtime > prev:
                    last_seen[module] = localtime

        if rule_writer is not None:
            # 효율적으로 규칙 행만 다시 추출해 기록
            mask_idx = [i for i, db in enumerate(detected) if json.loads(db).get("rule")]
            if mask_idx:
                sub = batch.take(pa.array(mask_idx))
                rule_writer.write_table(pa.Table.from_batches([sub]).cast(rule_schema))

    if rule_writer is not None:
        rule_writer.close()

    # module_status: 규칙 기준 이상 행 비율
    status_rows = []
    for module in sorted(module_totals):
        count = rule_by_module.get(module, 0)
        total = module_totals.get(module, 0)
        ratio = (count / total) if total else 0.0
        seen = last_seen.get(module)
        status_rows.append(
            {
                "module": module,
                "anomaly_count": count,
                "anomaly_ratio": round(ratio, 6),
                "last_seen": seen.isoformat() if seen else "",
                "status": STATUS_UNDECIDED,
            }
        )

    Path(status_out).parent.mkdir(parents=True, exist_ok=True)
    with Path(status_out).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["module", "anomaly_count", "anomaly_ratio", "last_seen", "status"]
        )
        writer.writeheader()
        for row in status_rows:
            writer.writerow(row)

    # anomaly_hourly: 규칙 기준 시간·설비·유형별 건수
    hourly_rows = sorted(hourly_counts.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2]))
    hours = [k[0] for k, _ in hourly_rows]
    hmods = [k[1] for k, _ in hourly_rows]
    htypes = [k[2] for k, _ in hourly_rows]
    hcounts = [v for _, v in hourly_rows]
    hourly_table = pa.table(
        {
            "hour": pa.array(hours, type=pa.timestamp("us", tz="Asia/Seoul")),
            "module": pa.array(hmods, type=pa.string()),
            "type": pa.array(htypes, type=pa.string()),
            "count": pa.array(hcounts, type=pa.int64()),
        }
    )
    pq.write_table(hourly_table, hourly_out)

    meta = {
        "generated_at": datetime.now().isoformat(),
        "source_events": str(events_file),
        "total_events_scanned": total_events,
        "method_counts": dict(method_counts),
        "method_overlap": dict(overlap),
        "rule_total": sum(rule_by_module.values()),
        "rule_by_module": dict(rule_by_module),
        "rule_by_type": dict(rule_by_type),
        "status_basis": "rule(3상 동시) anomaly rows / module total 5s rows (from hourly_from_full n)",
        "status_label_policy": f"status='{STATUS_UNDECIDED}' (O-3 미승인)",
        "note": "비지도(IsolationForest)는 과탐지(전체 21.8%)로 설비 상태 기준에서 제외. 규칙을 주 기준으로 사용. 방법 비교는 method_counts/overlap 참조.",
    }
    (config.ARTIFACTS_DIR / "anomaly_rebuild_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rebuild rule-based anomaly artifacts from saved events (no rescan)")
    parser.add_argument("--events", type=Path, default=config.ANOMALY_EVENTS)
    parser.add_argument("--rule-events-out", type=Path, default=None, help="선택: 규칙 이벤트만 담은 parquet 경로")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    meta = rebuild(events_path=args.events, rule_events_out=args.rule_events_out)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
