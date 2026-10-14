from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.base import clone
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

BASE_DIR = Path(__file__).resolve().parent
RAW_PATH = BASE_DIR / "rtu_data_full.csv"
TRUTH_PATH = BASE_DIR / "rtu_ground_truth_may.csv"
OUT_DIR = BASE_DIR / "forecast_outputs"
OUT_DIR.mkdir(exist_ok=True)
LAGS = [1, 24, 168]
ROLLING_WINDOWS = [24, 168]
HOURS_PER_SAMPLE = 5.0 / 3600.0
WON_PER_KWH = 180.0
KG_CO2_PER_KWH = 0.424
WH_PER_KWH = 1000.0
VALIDATION_HOURS = 24 * 30


def aggregate_hourly_energy(raw_path: Path) -> tuple[pd.DataFrame, dict]:
    """Aggregate synchronized equipment samples into hourly Wh using localtime."""
    hourly_parts = []
    modules: set[str] = set()
    duplicate_rows = 0
    last_time_by_module: dict[str, pd.Timestamp] = {}
    for chunk in pd.read_csv(
        raw_path,
        usecols=["module(equipment)", "localtime", "activePower", "operation"],
        chunksize=500_000,
        dtype={"module(equipment)": "string", "localtime": "string"},
    ):
        chunk = chunk.dropna(subset=["localtime"]).copy()
        chunk["localtime"] = pd.to_datetime(chunk["localtime"].astype(str), format="%Y%m%d%H%M%S", errors="coerce")
        chunk = chunk.dropna(subset=["localtime"]).copy()
        chunk = chunk[chunk["localtime"] < "2025-05-01"].copy()
        if chunk.empty:
            continue

        chunk["activePower"] = pd.to_numeric(chunk["activePower"], errors="coerce")
        chunk = chunk.dropna(subset=["activePower"])
        modules.update(chunk["module(equipment)"].dropna().astype(str).unique())
        for module, group in chunk.groupby("module(equipment)", sort=False):
            previous_time = last_time_by_module.get(str(module))
            if previous_time is not None and not group.empty and group["localtime"].iloc[0] == previous_time:
                chunk = chunk.drop(index=group.index[0])
                duplicate_rows += 1
            last_time_by_module[str(module)] = group["localtime"].iloc[-1]
        duplicate_mask = chunk.duplicated(["module(equipment)", "localtime"], keep="first")
        duplicate_rows += int(duplicate_mask.sum())
        chunk = chunk.loc[~duplicate_mask].copy()
        chunk["energy_wh"] = chunk["activePower"] * (5.0 / 3600.0)

        hourly = (
            chunk.assign(hour=chunk["localtime"].dt.floor("h"))
            .groupby("hour", as_index=False)
            .agg(energy_wh=("energy_wh", "sum"), sample_count=("energy_wh", "size"))
            .sort_values("hour")
        )
        hourly_parts.append(hourly)

    if not hourly_parts:
        raise ValueError("학습용 데이터가 비어 있습니다.")

    hourly = pd.concat(hourly_parts, ignore_index=True).groupby("hour", as_index=False).sum()
    hourly = hourly.sort_values("hour").reset_index(drop=True)
    expected_samples = len(modules) * 720
    hourly["sample_coverage"] = hourly["sample_count"] / expected_samples
    hourly["hourly_pow"] = np.where(
        hourly["sample_coverage"] >= 0.95,
        hourly["energy_wh"] / hourly["sample_coverage"],
        np.nan,
    )

    quality = {
        "equipment_count": len(modules),
        "duplicate_module_timestamps_dropped": duplicate_rows,
        "expected_samples_per_hour": expected_samples,
        "low_coverage_hours": int((hourly["sample_coverage"] < 0.95).sum()),
        "minimum_sample_coverage": float(hourly["sample_coverage"].min()),
        "maximum_sample_coverage": float(hourly["sample_coverage"].max()),
    }
    return hourly[["hour", "hourly_pow", "sample_coverage"]], quality


def make_calendar_features(df: pd.DataFrame) -> pd.DataFrame:
    index = pd.DatetimeIndex(df["hour"])
    calendar = pd.DataFrame(
        {
            "hour": index.hour,
            "day_of_week": index.dayofweek,
            "is_weekend": (index.dayofweek >= 5).astype(int),
            "month": index.month,
            "day": index.day,
            "sin_hour": np.sin(2 * np.pi * index.hour / 24.0),
            "cos_hour": np.cos(2 * np.pi * index.hour / 24.0),
            "sin_dow": np.sin(2 * np.pi * index.dayofweek / 7.0),
            "cos_dow": np.cos(2 * np.pi * index.dayofweek / 7.0),
        },
        index=index,
    )
    return calendar


def add_lag_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values("hour").copy()
    for lag in LAGS:
        df[f"lag_{lag}"] = df["hourly_pow"].shift(lag)
    return df


def make_feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    history = df.set_index("hour")["hourly_pow"].sort_index()
    calendar = make_calendar_features(df).reset_index(drop=True)
    feature_df = calendar.copy()
    timestamps = pd.DatetimeIndex(df["hour"])
    for lag in LAGS:
        feature_df[f"lag_{lag}"] = history.reindex(timestamps - pd.Timedelta(hours=lag)).to_numpy()
    for window in ROLLING_WINDOWS:
        rolling = history.rolling(window=window, min_periods=window).mean().shift(1)
        feature_df[f"rolling_mean_{window}"] = rolling.reindex(timestamps).to_numpy()
    return feature_df


def make_model_candidates() -> dict:
    return {
        "seasonal_naive_168": None,
        "ridge_100": make_pipeline(StandardScaler(), Ridge(alpha=100.0)),
        "ridge_1000": make_pipeline(StandardScaler(), Ridge(alpha=1000.0)),
        "hist_gbdt": HistGradientBoostingRegressor(
            loss="absolute_error",
            max_depth=6,
            max_leaf_nodes=25,
            min_samples_leaf=24,
            l2_regularization=1.0,
            random_state=42,
            early_stopping=False,
        ),
        "xgboost": xgb.XGBRegressor(
            objective="reg:squarederror",
            n_estimators=600,
            max_depth=8,
            learning_rate=0.05,
            subsample=0.9,
            colsample_bytree=0.9,
            random_state=42,
            n_jobs=-1,
        ),
        "lightgbm": lgb.LGBMRegressor(
            objective="regression_l1",
            n_estimators=600,
            learning_rate=0.05,
            num_leaves=31,
            subsample=0.9,
            colsample_bytree=0.9,
            random_state=42,
            verbose=-1,
        ),
    }

def predict_one(model_name: str, model, timestamp: pd.Timestamp, history: pd.Series) -> float:
    row = pd.DataFrame({"hour": [timestamp]})
    calendar = make_calendar_features(row).reset_index(drop=True)
    for lag in LAGS:
        lookup = timestamp - pd.Timedelta(hours=lag)
        calendar[f"lag_{lag}"] = history.get(lookup, np.nan)
    for window in ROLLING_WINDOWS:
        window_hours = pd.date_range(
            timestamp - pd.Timedelta(hours=window),
            timestamp - pd.Timedelta(hours=1),
            freq="h",
        )
        window_values = history.reindex(window_hours)
        calendar[f"rolling_mean_{window}"] = window_values.mean() if window_values.notna().all() else np.nan

    if calendar.isna().any().any():
        raise ValueError(f"예측 feature가 부족합니다: {timestamp}")
    if model_name == "seasonal_naive_168":
        prediction = float(calendar["lag_168"].iloc[0])
    else:
        prediction = float(model.predict(calendar)[0])
    return max(prediction, 0.0)


def predict_future(model_name: str, model, future_dates: pd.DatetimeIndex, history_df: pd.DataFrame) -> pd.Series:
    history = history_df.set_index("hour")["hourly_pow"].sort_index()
    predictions = []
    for timestamp in future_dates:
        prediction = predict_one(model_name, model, timestamp, history)
        predictions.append(prediction)
        history.loc[timestamp] = prediction
    return pd.Series(predictions, index=future_dates, name="pred_hourly_pow")


def fit_model_candidates(train_df: pd.DataFrame) -> tuple[dict, str, dict, pd.DataFrame]:
    X_all = make_feature_frame(train_df)
    target = train_df["hourly_pow"].reset_index(drop=True)
    hours = train_df["hour"].reset_index(drop=True)
    valid_rows = X_all.notna().all(axis=1) & target.notna()
    X = X_all.loc[valid_rows].reset_index(drop=True)
    y = target.loc[valid_rows].to_numpy(dtype=float)
    valid_hours = hours.loc[valid_rows].reset_index(drop=True)
    if len(X) <= VALIDATION_HOURS * 3:
        raise ValueError("3개의 30일 순차 검증 구간을 만들 데이터가 부족합니다.")

    candidates = make_model_candidates()
    splitter = TimeSeriesSplit(n_splits=3, test_size=VALIDATION_HOURS)
    cv_rows = []
    for name, template in candidates.items():
        fold_scores = []
        for train_idx, valid_idx in splitter.split(X):
            model = None if template is None else clone(template)
            if model is not None:
                model.fit(X.iloc[train_idx], y[train_idx])

            valid_start = valid_hours.iloc[valid_idx[0]]
            valid_end = valid_hours.iloc[valid_idx[-1]]
            history = train_df[train_df["hour"] < valid_start].copy()
            rollout_dates = pd.date_range(history["hour"].max() + pd.Timedelta(hours=1), valid_end, freq="h")
            predictions = predict_future(name, model, rollout_dates, history)
            actual = pd.Series(y[valid_idx], index=pd.DatetimeIndex(valid_hours.iloc[valid_idx]))
            matched = pd.concat([actual.rename("actual"), predictions.rename("pred")], axis=1).dropna()
            fold_scores.append(mean_absolute_error(matched["actual"], matched["pred"]))
        cv_rows.append({"model": name, "CV_MAE_hourly_pow": float(np.mean(fold_scores))})

    cv_results = pd.DataFrame(cv_rows).sort_values("CV_MAE_hourly_pow").reset_index(drop=True)
    best_model_name = str(cv_results.iloc[0]["model"])
    fitted_models = {}
    for name, template in candidates.items():
        model = None if template is None else clone(template)
        if model is not None:
            model.fit(X, y)
        fitted_models[name] = model
    return fitted_models, best_model_name, dict(zip(cv_results["model"], cv_results["CV_MAE_hourly_pow"])), cv_results


def compare_all_models(models: dict, cv_scores: dict, train_df: pd.DataFrame, future_dates: pd.DatetimeIndex, truth_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    comparison_rows = []
    all_rows = []
    for model_name, model in models.items():
        pred_series = predict_future(model_name, model, future_dates, train_df)
        pred_df = pred_series.reset_index().rename(columns={"index": "hour", "pred_hourly_pow": "pred_hourly_pow"})
        merged = pred_df.merge(
            truth_df[["hour", "hourly_pow"]],
            on="hour",
            how="inner",
        )
        merged["actual_bill_krw"] = merged["hourly_pow"] / WH_PER_KWH * WON_PER_KWH
        merged["actual_carbon_kg"] = merged["hourly_pow"] / WH_PER_KWH * KG_CO2_PER_KWH
        merged["pred_bill_krw"] = merged["pred_hourly_pow"] / WH_PER_KWH * WON_PER_KWH
        merged["pred_carbon_kg"] = merged["pred_hourly_pow"] / WH_PER_KWH * KG_CO2_PER_KWH
        merged["model"] = model_name
        all_rows.append(merged[["model", "hour", "pred_hourly_pow", "hourly_pow", "actual_bill_krw", "pred_bill_krw", "actual_carbon_kg", "pred_carbon_kg"]])

        comparison_rows.append({
            "model": model_name,
            "CV_MAE_hourly_pow": cv_scores[model_name],
            "MAE_hourly_pow": mean_absolute_error(merged["hourly_pow"], merged["pred_hourly_pow"]),
            "MAE_bill_krw": mean_absolute_error(merged["actual_bill_krw"], merged["pred_bill_krw"]),
            "MAE_carbon_kg": mean_absolute_error(merged["actual_carbon_kg"], merged["pred_carbon_kg"]),
        })

    comparison = pd.DataFrame(comparison_rows).sort_values("CV_MAE_hourly_pow", ascending=True).reset_index(drop=True)
    comparison.to_csv(OUT_DIR / "model_comparison.csv", index=False)

    all_model_df = pd.concat(all_rows, ignore_index=True)
    all_model_df.to_csv(OUT_DIR / "model_predictions_all.csv", index=False)
    return comparison, all_model_df


def build_truth_frame() -> pd.DataFrame:
    truth = pd.read_csv(TRUTH_PATH)
    truth["id"] = pd.to_datetime(truth["id"], errors="coerce")
    truth = truth.dropna(subset=["id"]).copy()
    truth = truth.rename(columns={"id": "hour"})
    return truth[["hour", "hourly_pow", "may_bill", "may_carbon", "agg_pow"]]


def evaluate_predictions(pred_df: pd.DataFrame, truth_df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    merged = pred_df.merge(
        truth_df[["hour", "hourly_pow"]],
        on="hour",
        how="inner",
    )
    merged["actual_bill_krw"] = merged["hourly_pow"] / WH_PER_KWH * WON_PER_KWH
    merged["actual_carbon_kg"] = merged["hourly_pow"] / WH_PER_KWH * KG_CO2_PER_KWH
    merged["pred_bill_krw"] = merged["pred_hourly_pow"] / WH_PER_KWH * WON_PER_KWH
    merged["pred_carbon_kg"] = merged["pred_hourly_pow"] / WH_PER_KWH * KG_CO2_PER_KWH

    metrics = {
        "MAE_hourly_pow": mean_absolute_error(merged["hourly_pow"], merged["pred_hourly_pow"]),
        "MAE_bill_krw": mean_absolute_error(merged["actual_bill_krw"], merged["pred_bill_krw"]),
        "MAE_carbon_kg": mean_absolute_error(merged["actual_carbon_kg"], merged["pred_carbon_kg"]),
    }
    return merged, metrics


def save_plot(df: pd.DataFrame, save_path: Path) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(14, 11), sharex=True)
    axes[0].plot(df["hour"], df["hourly_pow"], label="Actual", linewidth=1.5)
    axes[0].plot(df["hour"], df["pred_hourly_pow"], label="Prediction", linewidth=1.5)
    axes[0].set_ylabel("Power (Wh)")
    axes[0].grid(alpha=0.25)
    axes[0].legend()

    axes[1].plot(df["hour"], df["actual_bill_krw"], label="Actual", linewidth=1.5)
    axes[1].plot(df["hour"], df["pred_bill_krw"], label="Prediction", linewidth=1.5)
    axes[1].set_ylabel("Bill (KRW)")
    axes[1].grid(alpha=0.25)
    axes[1].legend()

    axes[2].plot(df["hour"], df["actual_carbon_kg"], label="Actual", linewidth=1.5)
    axes[2].plot(df["hour"], df["pred_carbon_kg"], label="Prediction", linewidth=1.5)
    axes[2].set_ylabel("Carbon (kgCO₂)")
    axes[2].grid(alpha=0.25)
    axes[2].legend()

    fig.suptitle("2025-05 Load Forecast vs Actual")
    fig.tight_layout()
    fig.savefig(save_path, dpi=150)
    plt.close(fig)


def save_model_comparison_plot(comparison: pd.DataFrame, save_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 5))
    ordered = comparison.sort_values("MAE_hourly_pow")
    labels = ordered["model"]
    mae_values = ordered["MAE_hourly_pow"]
    colors = ["#168C83" if model == labels.iloc[0] else "#76827F" for model in labels]
    ax.bar(labels, mae_values, color=colors)
    ax.set_title("May holdout: model comparison by power MAE")
    ax.set_ylabel("MAE (Wh)")
    ax.grid(axis="y", alpha=0.25)
    plt.xticks(rotation=20)
    fig.tight_layout()
    fig.savefig(save_path, dpi=150)
    plt.close(fig)


def main() -> None:
    hourly, data_quality = aggregate_hourly_energy(RAW_PATH)
    hourly["hour"] = pd.to_datetime(hourly["hour"])
    full_hour_index = pd.date_range(hourly["hour"].min(), hourly["hour"].max(), freq="h")
    hourly = hourly.set_index("hour").reindex(full_hour_index).rename_axis("hour").reset_index()
    hourly["sample_coverage"] = hourly["sample_coverage"].fillna(0.0)
    train_df = hourly[hourly["hour"] < "2025-05-01"].copy().reset_index(drop=True)
    if train_df.empty:
        raise ValueError("학습 데이터가 충분하지 않습니다.")

    truth_df = build_truth_frame()
    truth_df = truth_df.sort_values("hour").reset_index(drop=True)
    models, cv_best_model_name, cv_scores, cv_results = fit_model_candidates(train_df)
    last_observed = train_df.loc[train_df["hourly_pow"].notna(), "hour"].max()
    next_month_start = (last_observed.to_period("M") + 1).start_time
    next_month_end = next_month_start + pd.offsets.MonthBegin(1) - pd.Timedelta(hours=1)
    forecast_end = max(next_month_end, truth_df["hour"].max())
    future_dates = pd.date_range(last_observed + pd.Timedelta(hours=1), forecast_end, freq="h")

    comparison, all_model_df = compare_all_models(models, cv_scores, train_df, future_dates, truth_df)
    best_model_name = str(comparison.sort_values("MAE_hourly_pow").iloc[0]["model"])
    best_holdout = all_model_df[all_model_df["model"] == best_model_name].copy()
    best_predictions = best_holdout[["hour", "pred_hourly_pow"]]
    merged, may_metrics = evaluate_predictions(best_predictions, truth_df)

    future_model = models[best_model_name]
    future_series = predict_future(best_model_name, future_model, future_dates, train_df)
    future_df = future_series.rename_axis("hour").reset_index()
    future_df["pred_bill_krw"] = future_df["pred_hourly_pow"] / WH_PER_KWH * WON_PER_KWH
    future_df["pred_carbon_kg"] = future_df["pred_hourly_pow"] / WH_PER_KWH * KG_CO2_PER_KWH
    future_df["horizon_day"] = future_df["hour"].between(last_observed + pd.Timedelta(hours=1), last_observed + pd.Timedelta(hours=24))
    future_df["horizon_week"] = future_df["hour"].between(last_observed + pd.Timedelta(hours=1), last_observed + pd.Timedelta(hours=168))
    future_df["horizon_month"] = future_df["hour"].between(next_month_start, next_month_end)

    analysis_dir = OUT_DIR / "analysis"
    analysis_dir.mkdir(exist_ok=True)

    hourly.to_csv(OUT_DIR / "hourly_power_processed.csv", index=False)
    future_df.to_csv(OUT_DIR / "future_forecasts.csv", index=False)
    merged.to_csv(OUT_DIR / "pred_vs_truth.csv", index=False)
    save_plot(merged, OUT_DIR / "forecast_vs_truth.png")
    save_model_comparison_plot(comparison, analysis_dir / "model_comparison.png")

    stored_bill = truth_df["may_bill"]
    stored_carbon = truth_df["may_carbon"]
    calculated_bill = truth_df["hourly_pow"] / WH_PER_KWH * WON_PER_KWH
    calculated_carbon = truth_df["hourly_pow"] / WH_PER_KWH * KG_CO2_PER_KWH
    bill_ratio = float((stored_bill / calculated_bill).median())
    carbon_ratio = float((stored_carbon / calculated_carbon).median())

    summary = {
        "best_model": best_model_name,
        "selection_metric": "May validation hourly-power MAE; May is used for selection and is not an independent final test",
        "cv_best_model": cv_best_model_name,
        "cv_results": cv_results.to_dict(orient="records"),
        "may_holdout_metrics": may_metrics,
        "model_comparison": comparison.to_dict(orient="records"),
        "train_range": {
            "start": str(train_df.loc[train_df["hourly_pow"].notna(), "hour"].min()),
            "end": str(last_observed),
            "valid_hour_rows": int(train_df["hourly_pow"].notna().sum()),
        },
        "may_holdout_range": {"start": str(truth_df["hour"].min()), "end": str(truth_df["hour"].max()), "rows": len(truth_df)},
        "dashboard_forecast_range": {"start": str(future_dates.min()), "end": str(future_dates.max())},
        "data_quality": data_quality,
        "unit_assumptions": {
            "activePower": "W",
            "hourly_pow": "Wh",
            "electricity_rate_won_per_kwh": WON_PER_KWH,
            "carbon_factor_kg_per_kwh": KG_CO2_PER_KWH,
            "truth_may_bill_to_calculated_bill_median_ratio": bill_ratio,
            "truth_may_carbon_to_calculated_carbon_median_ratio": carbon_ratio,
            "truth_columns_warning": "Truth may_bill/may_carbon are approximately 1000x energy-converted values; derived dashboard estimates use Wh-to-kWh conversion.",
        },
        "forecast_note": "Recursive forecasts use predicted values as future lags; uncertainty grows with horizon.",
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== Training and data quality ===")
    print(f"Train range: {summary['train_range']['start']} .. {summary['train_range']['end']}")
    print(f"Valid hourly rows: {summary['train_range']['valid_hour_rows']}")
    print(data_quality)
    print("\n=== May holdout MAE by model ===")
    for row in comparison.to_dict(orient="records"):
        print(f"{row['model']}: CV {row['CV_MAE_hourly_pow']:.3f} Wh; May {row['MAE_hourly_pow']:.3f} Wh")
    print(f"Selected by May validation MAE: {best_model_name} (CV leader: {cv_best_model_name})")
    print(f"May validation metrics for selected model: {may_metrics}")
    print(f"Truth bill/carbon formula ratios: {bill_ratio:.1f}x, {carbon_ratio:.1f}x")
    print(f"Outputs: {OUT_DIR}")


if __name__ == "__main__":
    main()
