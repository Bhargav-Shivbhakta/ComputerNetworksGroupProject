#!/usr/bin/env python3
"""
ml/controller_gcc_like.py

GCC-inspired delay/queue-reactive controller for offline evaluation.

Simplified behavior:
 - additive increase when not saturated
 - multiplicative decrease when queue grows
 - simple offline plant model used to simulate achieved throughput and queue
"""
import os
import argparse
import numpy as np
import pandas as pd

def plant_step(bottleneck: float, send_rate: float, q_bytes: float):
    """Simple offline plant model (noise + queue accumulation)."""
    noise = np.random.normal(0.0, 0.05 * max(1e-6, bottleneck))
    achieved = max(0.0, min(send_rate, bottleneck) + noise)
    excess = send_rate - bottleneck
    q_bytes_delta = excess * 8000.0  # convert Mbps-excess to bytes per step (approx)
    q_bytes = max(0.0, q_bytes + q_bytes_delta)
    q_packets = q_bytes / 1200.0 if q_bytes > 0 else 0.0
    return achieved, q_bytes, q_packets

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input_csv", required=True, help="Processed CSV input")
    ap.add_argument("--out_csv", default=None, help="Output CSV (optional)")
    ap.add_argument("--min_rate", type=float, default=0.5)
    ap.add_argument("--max_rate", type=float, default=50.0)
    ap.add_argument("--queue_threshold_bytes", type=float, default=10000.0)
    ap.add_argument("--ai_step", type=float, default=0.15)
    ap.add_argument("--md_factor", type=float, default=0.85)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    np.random.seed(args.seed)
    df = pd.read_csv(args.input_csv).replace([np.inf, -np.inf], np.nan).dropna()

    for c in ("throughput_mbps","send_rate_mbps","bottleneck_bw_mbps"):
        if c not in df.columns:
            raise KeyError(f"Missing required column: {c}")

    if "t_sec" not in df.columns:
        df["t_sec"] = np.arange(len(df), dtype=float)

    scenario_name = os.path.basename(args.input_csv).replace(".csv","")
    out_csv = args.out_csv or f"results/gcc_like_report_{scenario_name}.csv"
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)

    send_rate = float(df["send_rate_mbps"].iloc[0])
    send_rate = float(np.clip(send_rate, args.min_rate, args.max_rate))
    q_bytes = 0.0
    achieved = float(df["throughput_mbps"].iloc[0])

    rows = []
    for i in range(len(df)):
        t_sec = float(df["t_sec"].iloc[i])
        bottleneck = float(df["bottleneck_bw_mbps"].iloc[i])

        utilization_ratio = achieved / max(send_rate, 1e-6)

        if q_bytes > args.queue_threshold_bytes:
            send_rate *= args.md_factor
        else:
            if utilization_ratio > 0.85:
                send_rate += args.ai_step
            else:
                send_rate += 0.05

        send_rate = float(np.clip(send_rate, args.min_rate, args.max_rate))

        achieved, q_bytes, q_packets = plant_step(bottleneck, send_rate, q_bytes)

        rows.append({
            "t_sec": t_sec,
            "gcc_send_rate_mbps": send_rate,
            "gcc_achieved_throughput_mbps": achieved,
            "gcc_queue_bytes": q_bytes,
            "gcc_queue_packets": q_packets,
            "bottleneck_bw_mbps": bottleneck,
        })

    out_df = pd.DataFrame(rows)
    out_df.to_csv(out_csv, index=False)
    print(f"Wrote: {out_csv}")
    print("Rows:", len(out_df))
    print("Average throughput:", float(out_df["gcc_achieved_throughput_mbps"].mean()))
    print("Average send rate:", float(out_df["gcc_send_rate_mbps"].mean()))
    print("Average queue bytes:", float(out_df["gcc_queue_bytes"].mean()))
    print("p95 queue bytes:", float(np.percentile(out_df["gcc_queue_bytes"], 95)))

if __name__ == "__main__":
    main()
