#!/usr/bin/env python3
"""
ml/train_xgb.py

Train an XGBoost regressor on a windowed dataset.

Config via environment variables:

DATASET_PATH (required or defaults)
LABEL_COL    (default: y_throughput_1s)

Example:
DATASET_PATH=data/processed_windowed/windowed_y_throughput_1s.csv \
LABEL_COL=y_throughput_1s \
python3 ml/train_xgb.py
"""

import os
import json
import numpy as np
import pandas as pd
from joblib import dump
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_squared_error, mean_absolute_error
import xgboost as xgb


ROOT = os.path.dirname(os.path.dirname(__file__))
OUT_DIR = os.path.join(ROOT, "ml", "models")
os.makedirs(OUT_DIR, exist_ok=True)

DATASET_PATH = os.environ.get(
    "DATASET_PATH",
    os.path.join(ROOT, "data", "processed_windowed", "windowed_y_throughput_1s.csv"),
)

LABEL_COL = os.environ.get("LABEL_COL", "y_throughput_1s")


def main():

    if not os.path.exists(DATASET_PATH):
        raise FileNotFoundError(f"Dataset not found: {DATASET_PATH}")

    print("Loading dataset:", DATASET_PATH)
    print("Label column:", LABEL_COL)

    df = pd.read_csv(DATASET_PATH)
    print("Loaded shape:", df.shape)

    df = df.select_dtypes(include=[np.number]).replace([np.inf, -np.inf], np.nan).dropna()
    print("Clean numeric shape:", df.shape)

    if LABEL_COL not in df.columns:
        raise KeyError(f"Label column '{LABEL_COL}' not found in dataset.")

    X = df.drop(columns=[LABEL_COL])
    y = df[LABEL_COL].values

    y_std = float(np.std(y))
    print(f"Label std-dev: {y_std:.6f}")

    if y_std < 1e-8:
        raise ValueError("Label variance too small. Check label definition.")

    print("Number of features:", X.shape[1])

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)

    dtrain = xgb.DMatrix(X_train_s, label=y_train, feature_names=X.columns.tolist())
    dtest = xgb.DMatrix(X_test_s, label=y_test, feature_names=X.columns.tolist())

    params = {
        "objective": "reg:squarederror",
        "eval_metric": "rmse",
        "tree_method": "hist",
        "eta": 0.05,
        "max_depth": 6,
        "subsample": 0.85,
        "colsample_bytree": 0.85,
        "seed": 42,
    }

    print("Training XGBoost...")
    bst = xgb.train(
        params,
        dtrain,
        num_boost_round=1000,
        evals=[(dtrain, "train"), (dtest, "eval")],
        early_stopping_rounds=30,
        verbose_eval=50,
    )

    y_pred = bst.predict(dtest)

    mse = mean_squared_error(y_test, y_pred)
    rmse = float(np.sqrt(mse))
    mae = float(mean_absolute_error(y_test, y_pred))

    metrics = {
        "rmse": rmse,
        "mae": mae,
        "n_total": int(len(df)),
        "n_train": int(len(y_train)),
        "n_test": int(len(y_test)),
        "label_std": y_std,
        "num_features": int(X.shape[1]),
        "dataset": DATASET_PATH,
        "label_col": LABEL_COL,
    }

    dataset_base = os.path.basename(DATASET_PATH).replace(".csv", "")
    safe_label = LABEL_COL.replace("/", "_")

    model_path = os.path.join(OUT_DIR, f"xgb_{dataset_base}_{safe_label}.joblib")
    metrics_path = os.path.join(
        OUT_DIR, f"xgb_{dataset_base}_{safe_label}_metrics.json"
    )

    dump(
        {"model": bst, "scaler": scaler, "feature_names": X.columns.tolist()},
        model_path,
    )

    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)

    print("\nSaved model to:", model_path)
    print("Saved metrics to:", metrics_path)
    print("Eval metrics:", metrics)

    fmap = bst.get_score(importance_type="gain")
    feat_imp = sorted(fmap.items(), key=lambda kv: kv[1], reverse=True)

    print("\nTop 20 features (gain):")
    for k, v in feat_imp[:20]:
        print(f"  {k}: {v:.4f}")


if __name__ == "__main__":
    main()
