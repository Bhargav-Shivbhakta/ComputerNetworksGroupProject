#!/usr/bin/env python3
"""
ml/controller_predictive.py

Offline closed-loop predictive controller simulator:
- Reads a processed CSV (data/processed/*.csv)
- Uses a trained XGBoost model (joblib bundle: {model, scaler, feature_names})
- Builds sliding-window features over *simulated* state
- Updates controlled send rate each step
- Simulates a simple network "plant" using bottleneck_bw_mbps and a queue model

Output:
- results/controller_report_<scenario>.csv

Expected processed CSV columns (from your pipeline):
t_sec, throughput_mbps, queue_bytes, queue_packets, send_rate_mbps, bottleneck_bw_mbps, ...
"""

import os
import argparse
import numpy as np
import pandas as pd
from joblib import load
import xgboost as xgb

WINDOW = 10
SIGNALS = ["throughput_mbps", "queue_bytes", "queue_packets", "send_rate_mbps"]

PKT_SIZE_BYTES_DEFAULT = 1200
QMAX_PACKETS_DEFAULT = 50
DT_DEFAULT = 0.1

def infer_dt(df: pd.DataFrame, default=DT_DEFAULT) -> float:
    if "t_sec" not in df.columns or len(df) < 3:
        return default
    dts = np.diff(df["t_sec"].values.astype(float))
    dts = dts[(dts > 0) & (dts < 10)]
    if len(dts) == 0:
        return default
    return float(np.median(dts))

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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input_csv", required=True, help="Processed CSV from data/processed/*.csv")
    ap.add_argument("--model_path", required=True, help="Trained XGB joblib model (bundle)")
    ap.add_argument("--out_csv", default=None, help="Output report CSV path")
    ap.add_argument("--alpha", type=float, default=0.6, help="How aggressively to move send_rate towards predicted throughput")
    ap.add_argument("--min_rate", type=float, default=0.5, help="Mbps")
    ap.add_argument("--max_rate", type=float, default=50.0, help="Mbps")
    ap.add_argument("--pkt_size", type=float, default=PKT_SIZE_BYTES_DEFAULT, help="Bytes")
    ap.add_argument("--qmax_packets", type=float, default=QMAX_PACKETS_DEFAULT, help="Packets")
    args = ap.parse_args()

    df = pd.read_csv(args.input_csv).replace([np.inf, -np.inf], np.nan).dropna().reset_index(drop=True)

    required = ["throughput_mbps", "queue_bytes", "queue_packets", "send_rate_mbps"]
    for c in required:
        if c not in df.columns:
            raise KeyError(f"Missing required column '{c}' in {args.input_csv}")

    if "bottleneck_bw_mbps" not in df.columns:
        raise KeyError("Missing 'bottleneck_bw_mbps'. Your processed file should include it.")

    dt = infer_dt(df)
    qmax_bytes = float(args.qmax_packets * args.pkt_size)

    bundle = load(args.model_path)
    bst = bundle["model"]
    scaler = bundle["scaler"]
    feature_names = bundle["feature_names"]

    # Output path
    os.makedirs("results", exist_ok=True)
    if args.out_csv is None:
        base = os.path.basename(args.input_csv).replace(".csv", "")
        args.out_csv = os.path.join("results", f"controller_report_{base}.csv")

    # ---- Closed-loop simulation state ----
    cap_series = df["bottleneck_bw_mbps"].values.astype(float)

    # seed history with first WINDOW rows from *logged* values (reasonable warm-start)
    hist = df.loc[:WINDOW-1, ["throughput_mbps","queue_bytes","queue_packets","send_rate_mbps"]].copy()

    sr = float(hist["send_rate_mbps"].iloc[-1])
    q_bytes = float(hist["queue_bytes"].iloc[-1])

    rows = []
    for i in range(WINDOW, len(df)):
        t = float(df["t_sec"].iloc[i]) if "t_sec" in df.columns else float(i)
        cap = float(cap_series[i])

        # featurize from simulated history
        feat = featurize_window(hist.iloc[-WINDOW:])

        # IMPORTANT: keep feature names -> no warnings
        Xrow = pd.DataFrame([feat], columns=feature_names).fillna(0.0)
        Xs = scaler.transform(Xrow)
        dmat = xgb.DMatrix(Xs, feature_names=feature_names)
        pred = float(bst.predict(dmat)[0])

        # control: move sr toward predicted future throughput
        sr = (1 - args.alpha) * sr + args.alpha * pred
        sr = float(np.clip(sr, args.min_rate, args.max_rate))

        # plant: achieved throughput limited by cap
        achieved = min(sr, cap)

        # queue update from mismatch
        excess_mbps = max(0.0, sr - achieved)
        q_bytes += excess_mbps * 1e6 / 8.0 * dt
        q_bytes = max(0.0, min(q_bytes, qmax_bytes))
        q_pkts = q_bytes / float(args.pkt_size)

        rows.append({
            "t_sec": t,
            "pred_throughput_1s": pred,
            "controlled_send_rate": sr,
            "sim_throughput_mbps": achieved,
            "sim_queue_bytes": q_bytes,
            "sim_queue_packets": q_pkts,
            "bottleneck_bw_mbps": cap
        })

        # push new simulated observation into history
        hist = pd.concat([hist, pd.DataFrame([{
            "throughput_mbps": achieved,
            "queue_bytes": q_bytes,
            "queue_packets": q_pkts,
            "send_rate_mbps": sr
        }])], ignore_index=True)

    out = pd.DataFrame(rows)
    out.to_csv(args.out_csv, index=False)

    print(f"Wrote: {args.out_csv}")
    print("Report shape:", out.shape)
    print("Avg achieved throughput:", float(out["sim_throughput_mbps"].mean()))
    print("Avg controlled send rate:", float(out["controlled_send_rate"].mean()))

if __name__ == "__main__":
    main()
