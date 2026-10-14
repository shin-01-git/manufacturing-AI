"""Shared fixtures: build a tiny model + state in a tmp dir for predict tests."""

from __future__ import annotations

from datetime import datetime, timedelta
import math
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from config import config
from src.preprocess import features as features_mod
from src.train import train as train_mod

KST = ZoneInfo("Asia/Seoul")


def write_small_features(path, n_hours: int = 24 * 60) -> None:
    """Write a deterministic small features.parquet used across tests."""

    start = datetime(2025, 2, 1, 0, tzinfo=KST)
    records = []
    for i in range(n_hours):
        hour = start + timedelta(hours=i)
        calendar = features_mod.make_calendar_features(hour)
        target = 100.0 + 10.0 * math.sin(i / 24.0) + 5.0 * calendar["weekday"]
        records.append({"hour": hour, "target_hourly_pow": target, **calendar})

    table = pa.table(
        {
            "hour": pa.array([r["hour"] for r in records], type=pa.timestamp("us", tz="Asia/Seoul")),
            "target_hourly_pow": pa.array([r["target_hourly_pow"] for r in records], type=pa.float64()),
            "weekday": pa.array([r["weekday"] for r in records], type=pa.int8()),
            "hour_of_day": pa.array([r["hour_of_day"] for r in records], type=pa.int8()),
            "day_of_month": pa.array([r["day_of_month"] for r in records], type=pa.int8()),
            "week_of_year": pa.array([r["week_of_year"] for r in records], type=pa.int16()),
            "is_weekend": pa.array([r["is_weekend"] for r in records], type=pa.bool_()),
            "month": pa.array([r["month"] for r in records], type=pa.int8()),
            "day_of_year": pa.array([r["day_of_year"] for r in records], type=pa.int16()),
        }
    )
    pq.write_table(table, path)


def write_fake_artifacts(root, *, module_count: int = 3, n_pred: int = 5):
    """Write a tiny but schema-correct set of service artifacts under ``root``.

    Used by the service/UI tests so they never read the shared ``artifacts/``
    files (which the background anomaly scan is still producing). Everything
    stays inside the given tmp ``root``.
    """

    from datetime import datetime as _dt
    import csv as _csv
    import json as _json

    root.mkdir(parents=True, exist_ok=True)

    # predictions.csv
    pred_path = root / "predictions.csv"
    with pred_path.open("w", encoding="utf-8", newline="") as handle:
        writer = _csv.writer(handle)
        writer.writerow(["id", "pred_pow", "pred_bill", "pred_carbon", "pred_low", "pred_high"])
        for i in range(n_pred):
            pow_v = 100.0 + i
            writer.writerow(
                [
                    f"2025-05-10 {i:02d}:00:00",
                    pow_v,
                    pow_v * config.BILL_RATE,
                    pow_v * config.CARBON_RATE,
                    max(0.0, pow_v - 10.0),
                    pow_v + 10.0,
                ]
            )

    # evaluation.json
    (root / "evaluation.json").write_text(
        _json.dumps(
            {
                "mae_hourly_pow": {"mae504": 88.1, "mae505": 88.2},
                "mae_may_bill": {"mae504": 15875.0, "mae505": 15872.0},
                "mae_may_carbon": {"mae504": 37.3, "mae505": 37.4},
                "agg_pow_total_error": 28342.9,
                "baseline_comparison": [
                    {"model": "global_mean_repeat", "kind": "baseline", "validation_mae": 78.7},
                ],
                "reference_note": {"use_as_threshold": False, "note": "참고용(합격 기준 아님)"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # module_status.csv
    status_path = root / "module_status.csv"
    with status_path.open("w", encoding="utf-8", newline="") as handle:
        writer = _csv.writer(handle)
        writer.writerow(["module", "anomaly_count", "anomaly_ratio", "last_seen", "status"])
        for i in range(module_count):
            writer.writerow([f"M{i}", i * 3, round(i * 0.01, 6), "2024-12-01T00:00:00", "미확정"])

    # anomaly_events.parquet
    import pyarrow as pa
    import pyarrow.parquet as pq

    ev_time = _dt(2024, 12, 1, 0, 0, 0, tzinfo=KST)
    pq.write_table(
        pa.table(
            {
                "module": pa.array(["M0", "M1"], type=pa.string()),
                "localtime": pa.array([ev_time, ev_time], type=pa.timestamp("us", tz="Asia/Seoul")),
                "type": pa.array(["voltage_low_3phase", "statistical_outlier"], type=pa.string()),
                "value": pa.array([5.0, 6.0], type=pa.float64()),
                "normal_range": pa.array([">=210V", ">=210V"], type=pa.string()),
                "detected_by": pa.array(["{\"rule\": true}", "{\"statistical\": true}"], type=pa.string()),
                "score": pa.array([0.1, 0.2], type=pa.float64()),
            }
        ),
        root / "anomaly_events.parquet",
    )

    # anomaly_hourly.parquet
    h0 = _dt(2024, 12, 1, 0, 0, 0, tzinfo=KST)
    h1 = _dt(2024, 12, 1, 1, 0, 0, tzinfo=KST)
    pq.write_table(
        pa.table(
            {
                "hour": pa.array([h0, h1, h0], type=pa.timestamp("us", tz="Asia/Seoul")),
                "module": pa.array(["M0", "M0", "M1"], type=pa.string()),
                "type": pa.array(["voltage_low_3phase", "voltage_low_3phase", "statistical_outlier"], type=pa.string()),
                "count": pa.array([1, 10, 2], type=pa.int64()),
            }
        ),
        root / "anomaly_hourly.parquet",
    )

    return root


@pytest.fixture
def fake_artifacts(tmp_path):
    """A tmp directory populated with schema-correct fake artifacts."""

    root = tmp_path / "artifacts"
    write_fake_artifacts(root)
    return root


@pytest.fixture
def trained_artifacts(tmp_path):
    """Train a tiny model to a tmp dir; return the artifact paths."""

    features_path = tmp_path / "features.parquet"
    model_path = tmp_path / "model.joblib"
    state_path = tmp_path / "preprocessor_state.json"
    selection_path = tmp_path / "model_selection.json"
    write_small_features(features_path)
    train_mod.train(
        features_path,
        model_path=model_path,
        state_path=state_path,
        selection_path=selection_path,
    )
    return {
        "features": features_path,
        "model": model_path,
        "state": state_path,
        "selection": selection_path,
        "tmp": tmp_path,
    }
