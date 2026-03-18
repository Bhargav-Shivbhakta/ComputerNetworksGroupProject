#!/usr/bin/env python3
"""
ml/eval_rl_vs_baselines.py

Compare:
- FIXED_SR
- EWMA_SR
- ML_PRED
- PPO_RL

using the same offline plant simulator.

Outputs:
- results/rl_vs_baselines_summary.csv
- results/rl_vs_baselines_summary.json
"""

import os
import glob
import json
import argparse
import numpy as np
import pandas as pd
from joblib import load
import xgboost as xgb

from stable_baselines3 import PPO

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

def plant_step(bottleneck: float, send_rate: float, q_bytes: float):
    noise = np.random.normal(0.0, 0.05 * max(1e-6, bottleneck))
    achieved = max(0.0, min(send_rate, bottleneck) + noise)
    excess = send_rate - bottleneck
    q_bytes_delta = excess * 8000.0
    q_bytes = max(0.0, q_bytes + q_bytes_delta)
    q_packets = q_bytes / 1200.0 if q_bytes > 0 else 0.0
    return achieved, q_bytes, q_packets

def build_rl_obs(last_thr, q_bytes, q_packets, send_rate, pred_thr, bottleneck, q_norm_bytes=200000.0):
    q_bytes_norm = q_bytes / q_norm_bytes
    q_pkts_norm = q_packets / (q_norm_bytes / 1200.0)
    return np.array(
        [last_thr, q_bytes_norm, q_pkts_norm, send_rate, pred_thr, bottleneck],
        dtype=np.float32
    )

def simulate_controller(df, controller, alpha=0.6, min_rate=0.5, max_rate=50.0,
                        xgb_bundle=None, ppo_model=None, seed=42):
    np.random.seed(seed)

    required = ["throughput_mbps", "send_rate_mbps", "bottleneck_bw_mbps"]
    for c in required:
        if c not in df.columns:
            raise KeyError(f"Missing required column: {c}")

    sr = float(df["send_rate_mbps"].iloc[0])
    sr = float(np.clip(sr, min_rate, max_rate))
    q_bytes = 0.0
    q_packets = 0.0
    achieved = float(df["throughput_mbps"].iloc[0])
    ewma = achieved

    if controller == "ML_PRED":
        bst = xgb_bundle["model"]
        scaler = xgb_bundle["scaler"]
        feature_names = xgb_bundle["feature_names"]

    rows = []
    for i in range(WINDOW, len(df)):
        t = float(df["t_sec"].iloc[i]) if "t_sec" in df.columns else float(i)
        bottleneck = float(df["bottleneck_bw_mbps"].iloc[i])

        pred = achieved

        if controller == "FIXED_SR":
            pass

        elif controller == "EWMA_SR":
            sr = (1 - alpha) * sr + alpha * ewma

        elif controller == "ML_PRED":
            window = df.iloc[i-WINDOW:i].copy()
            feat = featurize_window(window)
            X = pd.DataFrame([feat])
            for col in feature_names:
                if col not in X.columns:
                    X[col] = 0.0
            X = X[feature_names]
            Xs = scaler.transform(X)
            dmat = xgb.DMatrix(Xs, feature_names=list(X.columns))
            pred = float(bst.predict(dmat)[0])
            pred = float(np.clip(pred, min_rate, max_rate))
            sr = (1 - alpha) * sr + alpha * pred

        elif controller == "PPO_RL":
            # optional predictor feature from XGB if bundle exists
            if xgb_bundle is not None:
                bst = xgb_bundle["model"]
                scaler = xgb_bundle["scaler"]
                feature_names = xgb_bundle["feature_names"]
                window = df.iloc[i-WINDOW:i].copy()
                feat = featurize_window(window)
                X = pd.DataFrame([feat])
                for col in feature_names:
                    if col not in X.columns:
                        X[col] = 0.0
                X = X[feature_names]
                Xs = scaler.transform(X)
                dmat = xgb.DMatrix(Xs, feature_names=list(X.columns))
                pred = float(bst.predict(dmat)[0])

            obs = build_rl_obs(
                last_thr=achieved,
                q_bytes=q_bytes,
                q_packets=q_packets,
                send_rate=sr,
                pred_thr=pred,
                bottleneck=bottleneck
            )
            action, _ = ppo_model.predict(obs, deterministic=True)

            scales = [0.8, 0.9, 1.0, 1.1, 1.2]
            sr = sr * scales[int(action)]

        else:
            raise ValueError(f"Unknown controller: {controller}")

        sr = float(np.clip(sr, min_rate, max_rate))

        achieved, q_bytes, q_packets = plant_step(
            bottleneck=bottleneck,
            send_rate=sr,
            q_bytes=q_bytes
        )

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

def summarize(simdf, scenario, controller):
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
    ap.add_argument("--glob", required=True)
    ap.add_argument("--xgb_model", required=True)
    ap.add_argument("--ppo_model", required=True)
    ap.add_argument("--alpha", type=float, default=0.6)
    ap.add_argument("--min_rate", type=float, default=0.5)
    ap.add_argument("--max_rate", type=float, default=50.0)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    files = sorted(glob.glob(args.glob))
    if not files:
        raise FileNotFoundError(f"No files matched glob: {args.glob}")

    xgb_bundle = load(args.xgb_model)
    ppo_model = PPO.load(args.ppo_model)

    summaries = []
    controllers = ["FIXED_SR", "EWMA_SR", "ML_PRED", "PPO_RL"]

    for f in files:
        scenario = os.path.basename(f)
        df = pd.read_csv(f).replace([np.inf, -np.inf], np.nan).dropna()

        for c in controllers:
            simdf = simulate_controller(
                df=df,
                controller=c,
                alpha=args.alpha,
                min_rate=args.min_rate,
                max_rate=args.max_rate,
                xgb_bundle=xgb_bundle,
                ppo_model=ppo_model,
                seed=args.seed,
            )
            summaries.append(summarize(simdf, scenario, c))

    os.makedirs("results", exist_ok=True)
    out_csv = "results/rl_vs_baselines_summary.csv"
    out_json = "results/rl_vs_baselines_summary.json"

    sdf = pd.DataFrame(summaries)
    sdf.to_csv(out_csv, index=False)
    with open(out_json, "w") as f:
        json.dump(summaries, f, indent=2)

    print("Wrote:")
    print(" -", out_csv)
    print(" -", out_json)

    overall = sdf.groupby("controller")[[
        "avg_throughput",
        "std_throughput",
        "avg_queue_bytes",
        "p95_queue_bytes",
        "queue_nonzero_frac",
        "avg_send_rate"
    ]].mean().reset_index()

    print("\n=== Overall (mean across scenarios) ===")
    print(overall.to_string(index=False))

if __name__ == "__main__":
    main()
