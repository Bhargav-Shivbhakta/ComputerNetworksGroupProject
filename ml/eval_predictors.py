#!/usr/bin/env python3
"""
ml/eval_predictors.py

Evaluate prediction models and export a paper-friendly summary.

Evaluates:
Baselines (per-trace, sequential):
1) Naive last-value
2) Linear trend
3) EWMA (tuned alpha)

ML:
4) XGB snapshot model (ml/models/xgb_availbw.joblib) on data/processed/*.csv
5) XGB windowed model (ml/models/xgb_windowed_availbw.joblib) on data/processed_windowed/windowed_dataset.csv

Outputs:
- results/predictor_summary.json
- results/predictor_summary.csv
"""

from __future__ import annotations
import os
import glob
import json
import numpy as np
import pandas as pd
from joblib import load

from ml.baselines import (
    rmse_mae,
    grid_search_ewma_alpha,
    EwmaForecaster,
    EwmaConfig,
    NaiveLastValueForecaster,
    BaseConfig,
    LinearTrendForecaster,
    LinearTrendConfig,
)

ROOT = os.path.dirname(os.path.dirname(__file__))

PROCESSED_DIR = os.path.join(ROOT, "data", "processed")
WINDOWED_PATH = os.path.join(ROOT, "data", "processed_windowed", "windowed_dataset.csv")

MODEL_SNAPSHOT_PATH = os.path.join(ROOT, "ml", "models", "xgb_availbw.joblib")
MODEL_WINDOWED_PATH = os.path.join(ROOT, "ml", "models", "xgb_windowed_availbw.joblib")

RESULTS_DIR = os.path.join(ROOT, "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

LABEL = "avail_bw_mbps"


def load_processed_traces() -> list[pd.DataFrame]:
    files = sorted(glob.glob(os.path.join(PROCESSED_DIR, "*.csv")))
    if not files:
        raise FileNotFoundError(f"No processed CSVs in {PROCESSED_DIR}")
    dfs = []
    for f in files:
        df = pd.read_csv(f)
        if LABEL not in df.columns:
            continue
        df["_trace"] = os.path.basename(f)
        dfs.append(df)
    if not dfs:
        raise FileNotFoundError(f"Processed CSVs found but none contain label '{LABEL}'.")
    return dfs


def eval_baseline(name: str, forecaster, traces: list[pd.DataFrame]) -> dict:
    y_all, yhat_all = [], []
    for df in traces:
        y, yhat = forecaster.eval_on_trace_df(df)
        if len(y) == 0:
            continue
        y_all.append(y)
        yhat_all.append(yhat)

    y_cat = np.concatenate(y_all) if y_all else np.array([])
    yhat_cat = np.concatenate(yhat_all) if yhat_all else np.array([])
    rmse, mae = rmse_mae(y_cat, yhat_cat)

    return {
        "model": name,
        "dataset": "data/processed/*.csv (per-trace)",
        "n_eval": int(len(y_cat)),
        "rmse": rmse,
        "mae": mae,
    }


def eval_ewma(traces: list[pd.DataFrame]) -> dict:
    best = grid_search_ewma_alpha(
        traces,
        alphas=[0.05, 0.1, 0.2, 0.3, 0.5, 0.7],
        warmup=5,
        feature_col="throughput_mbps",
        label_col=LABEL,
    )
    alpha = best["alpha"] if best["alpha"] is not None else 0.2

    forecaster = EwmaForecaster(EwmaConfig(alpha=float(alpha), warmup=5))
    out = eval_baseline("EWMA", forecaster, traces)
    out["alpha"] = float(alpha)
    out["warmup"] = 5
    return out


def eval_xgb_snapshot(traces: list[pd.DataFrame]) -> dict:
    if not os.path.exists(MODEL_SNAPSHOT_PATH):
        return {"model": "XGB_SNAPSHOT", "status": "missing_model", "path": MODEL_SNAPSHOT_PATH}

    bundle = load(MODEL_SNAPSHOT_PATH)
    bst = bundle["model"]
    scaler = bundle["scaler"]
    feature_names = bundle["feature_names"]

    df = pd.concat(traces, ignore_index=True)
    df = df.select_dtypes(include=[np.number]).replace([np.inf, -np.inf], np.nan).dropna()

    if LABEL not in df.columns:
        raise KeyError("Label missing in concatenated processed dataset.")

    missing = [c for c in feature_names if c not in df.columns]
    if missing:
        raise KeyError(f"Processed dataset missing features expected by snapshot model: {missing}")

    X = df[feature_names].copy()
    y = df[LABEL].to_numpy(dtype=float)

    from sklearn.model_selection import train_test_split
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    X_test_s = scaler.transform(X_test)

    import xgboost as xgb
    dtest = xgb.DMatrix(X_test_s, label=y_test, feature_names=feature_names)
    y_pred = bst.predict(dtest)

    rmse, mae = rmse_mae(y_test, y_pred)

    return {
        "model": "XGB_SNAPSHOT",
        "dataset": "data/processed/*.csv (random split)",
        "n_total": int(len(df)),
        "n_test": int(len(y_test)),
        "rmse": rmse,
        "mae": mae,
        "model_path": MODEL_SNAPSHOT_PATH,
    }


def eval_xgb_windowed() -> dict:
    if not os.path.exists(WINDOWED_PATH):
        raise FileNotFoundError(f"Windowed dataset missing: {WINDOWED_PATH}")

    if not os.path.exists(MODEL_WINDOWED_PATH):
        return {"model": "XGB_WINDOWED", "status": "missing_model", "path": MODEL_WINDOWED_PATH}

    df = pd.read_csv(WINDOWED_PATH)
    df = df.select_dtypes(include=[np.number]).replace([np.inf, -np.inf], np.nan).dropna()

    if LABEL not in df.columns:
        raise KeyError(f"Label '{LABEL}' missing in windowed dataset.")

    bundle = load(MODEL_WINDOWED_PATH)
    bst = bundle["model"]
    scaler = bundle["scaler"]
    feature_names = bundle["feature_names"]

    missing = [c for c in feature_names if c not in df.columns]
    if missing:
        raise KeyError(f"Windowed dataset missing features expected by windowed model: {missing}")

    X = df[feature_names].copy()
    y = df[LABEL].to_numpy(dtype=float)

    from sklearn.model_selection import train_test_split
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    X_test_s = scaler.transform(X_test)

    import xgboost as xgb
    dtest = xgb.DMatrix(X_test_s, label=y_test, feature_names=feature_names)
    y_pred = bst.predict(dtest)

    rmse, mae = rmse_mae(y_test, y_pred)

    return {
        "model": "XGB_WINDOWED",
        "dataset": "data/processed_windowed/windowed_dataset.csv (random split)",
        "n_total": int(len(df)),
        "n_test": int(len(y_test)),
        "rmse": rmse,
        "mae": mae,
        "model_path": MODEL_WINDOWED_PATH,
    }


def main():
    traces = load_processed_traces()

    results = []

    # stronger baselines
    results.append(eval_baseline("NAIVE_LAST", NaiveLastValueForecaster(BaseConfig(warmup=5)), traces))
    results.append(eval_baseline("LINEAR_TREND", LinearTrendForecaster(LinearTrendConfig(warmup=5, k=10)), traces))
    results.append(eval_ewma(traces))

    # ML models
    results.append(eval_xgb_snapshot(traces))
    results.append(eval_xgb_windowed())

    print("\n=== Predictor Summary ===")
    for r in results:
        name = r.get("model")
        status = r.get("status", "ok")
        rmse = r.get("rmse", None)
        mae = r.get("mae", None)
        n = r.get("n_test", r.get("n_eval", r.get("n_total", None)))

        if status != "ok":
            print(f"- {name}: status={status} ({r.get('path','')})")
        else:
            print(f"- {name}: rmse={rmse:.6f} mae={mae:.6f} n={n}")

    out_json = os.path.join(RESULTS_DIR, "predictor_summary.json")
    out_csv = os.path.join(RESULTS_DIR, "predictor_summary.csv")

    with open(out_json, "w") as f:
        json.dump(results, f, indent=2)

    pd.DataFrame(results).to_csv(out_csv, index=False)

    print("\nWrote:")
    print(" -", out_json)
    print(" -", out_csv)


if __name__ == "__main__":
    main()
