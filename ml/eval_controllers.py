#!/usr/bin/env python3
"""
ml/eval_controllers.py

Evaluate controller strategies offline using a simple closed-loop plant simulator.

Controllers:
- FIXED_SR: constant send rate = initial send_rate_mbps
- EWMA_SR: send_rate tracks EWMA of achieved throughput (feedback)
- ML_PRED: XGBoost prediction -> send_rate control (feedback)

Input:
- processed CSVs: data/processed/*_proc.csv
  Must include at least:
    t_sec, throughput_mbps, queue_bytes, queue_packets, send_rate_mbps, bottleneck_bw_mbps

Outputs:
- results/controller_summary.csv
- results/controller_summary.json
"""

import os
import glob
import json
import argparse
import numpy as np
import pandas as pd
from joblib import load
import xgboost as xgb

WINDOW = 10
SIGNALS = ["throughput_mbps", "queue_bytes", "queue_packets", "send_rate_mbps"]

def compute_slope(x: np.ndarray) -> float:
    if len(x) < 2:
        return 0.0
    return float((x[-1] - x[0]) / len(x))

def featurize_window(window_df: pd.DataFrame) -> dict:
    feat = {}
    for s in SIGNALS:
        vals = window_df[s].values.astype(float)
        feat[f"{s}_mean"] = float(np.mean(vals))
        feat[f"{s}_std"]  = float(np.std(vals))
        feat[f"{s}_min"]  = float(np.min(vals))
        feat[f"{s}_max"]  = float(np.max(vals))
        feat[f"{s}_last"] = float(vals[-1])
        feat[f"{s}_slope"]= compute_slope(vals)
    return feat

def plant_step(bottleneck: float, send_rate: float) -> tuple[float, float, float]:
    """
    Very simple plant:
    - achieved throughput is limited by bottleneck and send_rate (with mild noise)
    - queue grows if send_rate > bottleneck, drains otherwise
    Returns: achieved_throughput, queue_bytes, queue_packets
    """
    # Throughput is min(send_rate, bottleneck) + small noise
    noise = np.random.normal(0.0, 0.05 * max(1e-6, bottleneck))
    achieved = max(0.0, min(send_rate, bottleneck) + noise)

    # Queue model (bytes). Use a scale factor so numbers look like your logs.
    # If send_rate exceeds bottleneck, queue increases. Otherwise it drains.
    excess = send_rate - bottleneck
    q_bytes_delta = excess * 8000.0  # scale factor (tunable)
    return achieved, q_bytes_delta

def simulate_controller(df: pd.DataFrame,
                        controller: str,
                        model_bundle=None,
                        alpha: float = 0.6,
                        min_rate: float = 0.5,
                        max_rate: float = 50.0,
                        seed: int = 42) -> pd.DataFrame:
    np.random.seed(seed)

    required = ["throughput_mbps", "send_rate_mbps", "bottleneck_bw_mbps"]
    for c in required:
        if c not in df.columns:
            raise KeyError(f"Missing required column: {c}")

    # start state
    sr = float(df["send_rate_mbps"].iloc[0])
    sr = float(np.clip(sr, min_rate, max_rate))

    q_bytes = 0.0

    # EWMA state
    ewma = float(df["throughput_mbps"].iloc[0])

    # ML bundle
    if controller == "ML_PRED":
        if model_bundle is None:
            raise ValueError("ML_PRED requires --model_path")
        bst = model_bundle["model"]
        scaler = model_bundle["scaler"]
        feature_names = model_bundle.get("feature_names", None)

    rows = []

    for i in range(WINDOW, len(df)):
        t = float(df["t_sec"].iloc[i]) if "t_sec" in df.columns else float(i)
        bottleneck = float(df["bottleneck_bw_mbps"].iloc[i])

        # --- Controller action update (based on previous feedback state) ---
        if controller == "FIXED_SR":
            # keep sr fixed at initial
            pass

        elif controller == "EWMA_SR":
            # track EWMA of achieved throughput (feedback)
            sr = (1 - alpha) * sr + alpha * ewma

        elif controller == "ML_PRED":
            # build features from the past WINDOW samples (observations)
            window = df.iloc[i-WINDOW:i].copy()

            feat = featurize_window(window)
            X = pd.DataFrame([feat])

            # align to training feature names if present
            if feature_names is not None:
                for col in feature_names:
                    if col not in X.columns:
                        X[col] = 0.0
                X = X[feature_names]

            # predict next-step throughput
            Xs = scaler.transform(X)
            dmat = xgb.DMatrix(Xs, feature_names=list(X.columns))
            pred = float(bst.predict(dmat)[0])

            # IMPORTANT: clamp prediction so it can't drive rate negative
            pred = float(np.clip(pred, min_rate, max_rate))

            # move send rate towards prediction
            sr = (1 - alpha) * sr + alpha * pred

        else:
            raise ValueError(f"Unknown controller: {controller}")

        # clamp send rate
        sr = float(np.clip(sr, min_rate, max_rate))

        # --- Plant step ---
        achieved, q_delta = plant_step(bottleneck=bottleneck, send_rate=sr)

        # queue integrates over time, but never below 0
        q_bytes = max(0.0, q_bytes + q_delta)

        # convert queue bytes -> packets (roughly)
        q_packets = q_bytes / 1200.0 if q_bytes > 0 else 0.0

        # feedback updates
        ewma = (1 - alpha) * ewma + alpha * achieved

        rows.append({
            "t_sec": t,
            "controller": controller,
            "sim_send_rate": sr,
            "sim_throughput": achieved,
            "sim_queue_bytes": q_bytes,
            "sim_queue_packets": q_packets,
            "bottleneck_bw_mbps": bottleneck,
        })

    return pd.DataFrame(rows)

def summarize(simdf: pd.DataFrame, scenario: str, controller: str) -> dict:
    return {
        "scenario": scenario,
        "controller": controller,
        "n": int(len(simdf)),
        "avg_throughput": float(simdf["sim_throughput"].mean()) if len(simdf) else 0.0,
        "std_throughput": float(simdf["sim_throughput"].std(ddof=0)) if len(simdf) else 0.0,
        "avg_send_rate": float(simdf["sim_send_rate"].mean()) if len(simdf) else 0.0,
        "std_send_rate": float(simdf["sim_send_rate"].std(ddof=0)) if len(simdf) else 0.0,
        "avg_queue_bytes": float(simdf["sim_queue_bytes"].mean()) if len(simdf) else 0.0,
        "p95_queue_bytes": float(np.percentile(simdf["sim_queue_bytes"], 95)) if len(simdf) else 0.0,
        "queue_nonzero_frac": float((simdf["sim_queue_bytes"] > 0).mean()) if len(simdf) else 0.0,
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", required=True, help='e.g. "data/processed/*_proc.csv"')
    ap.add_argument("--model_path", default=None, help="XGB joblib bundle (for ML_PRED)")
    ap.add_argument("--alpha", type=float, default=0.6)
    ap.add_argument("--min_rate", type=float, default=0.5)
    ap.add_argument("--max_rate", type=float, default=50.0)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    files = sorted(glob.glob(args.glob))
    if not files:
        raise FileNotFoundError(f"No files matched glob: {args.glob}")

    model_bundle = None
    if args.model_path:
        model_bundle = load(args.model_path)

    os.makedirs("results", exist_ok=True)

    controllers = ["FIXED_SR", "EWMA_SR", "ML_PRED"]

    summaries = []
    for f in files:
        scenario = os.path.basename(f)
        df = pd.read_csv(f).replace([np.inf, -np.inf], np.nan).dropna()

        for c in controllers:
            simdf = simulate_controller(
                df=df,
                controller=c,
                model_bundle=model_bundle if c == "ML_PRED" else None,
                alpha=args.alpha,
                min_rate=args.min_rate,
                max_rate=args.max_rate,
                seed=args.seed,
            )
            summaries.append(summarize(simdf, scenario=scenario, controller=c))

    out_csv = os.path.join("results", "controller_summary.csv")
    out_json = os.path.join("results", "controller_summary.json")

    sdf = pd.DataFrame(summaries)
    sdf.to_csv(out_csv, index=False)
    with open(out_json, "w") as f:
        json.dump(summaries, f, indent=2)

    print("Wrote:")
    print(" -", out_csv)
    print(" -", out_json)

    overall = sdf.groupby("controller")[["avg_throughput","std_throughput","avg_queue_bytes","p95_queue_bytes","queue_nonzero_frac","avg_send_rate"]].mean().reset_index()
    print("\n=== Overall (mean across scenarios) ===")
    print(overall.to_string(index=False))

if __name__ == "__main__":
    main()
