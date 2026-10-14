"""Static leak checks: train/predict modules never reference the answer file."""

from __future__ import annotations

import inspect

from src.predict import interval as interval_mod
from src.predict import predict as predict_mod
from src.preprocess import features as features_mod
from src.preprocess import state as state_mod
from src.train import baselines as baselines_mod
from src.train import train as train_mod

# Modules that must not read or name the May answer file (NFR-4, S2).
_TRAIN_PREDICT_MODULES = (
    baselines_mod,
    train_mod,
    predict_mod,
    interval_mod,
    features_mod,
    state_mod,
)

_FORBIDDEN_TOKENS = (
    "VALIDATION_CSV",
    "rtu_ground_truth",
    "검증용",
    "may_bill",
    "may_carbon",
    "agg_pow",
)


def test_train_predict_modules_do_not_reference_validation_path():
    for module in _TRAIN_PREDICT_MODULES:
        source = inspect.getsource(module)
        for token in _FORBIDDEN_TOKENS:
            assert token not in source, f"{module.__name__} references forbidden token {token!r}"
        assert "validation_csv" not in source.casefold(), (
            f"{module.__name__} references validation_csv"
        )


def test_train_predict_modules_do_not_import_evaluate():
    """Train/predict must not import the evaluator (the only answer reader)."""

    for module in _TRAIN_PREDICT_MODULES:
        source = inspect.getsource(module)
        assert "src.evaluate" not in source, f"{module.__name__} imports evaluate"
