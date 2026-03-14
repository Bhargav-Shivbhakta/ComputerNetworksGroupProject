#!/usr/bin/env python3
"""
ml/controller_bbr_like.py

BBR-inspired model-based controller for offline evaluation.

Simplified behavior:
 - estimate bottleneck bandwidth as recent max throughput
 - pace near that estimate (probe up / drain cycle simplified)
 - trim the send rate if queue grows
"""
import os
import argparse
import numpy as np
import pandas as pd

def plant_step(bottleneck: float, send_rate: float, q_bytes: float):
    noise = np.random.normal(0.0, 0.05 * max(1e-6, bottleneck))
    achieved = max(0.0, min(send_rate, bottleneck) + noise)
    excess = send_rate - bottleneck
    q_bytes_delta = excess * 8000.0
    q_bytes = max(0.0, q_bytes + q_bytes_delta)
    q_packets = q_bytes / 1200.0 if q_bytes > 0 else 0.0
    return achieved, q_bytes, q_packets

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input_csv", required=True)
    ap.add_argument("--out_csv", default=None)
    ap.add_argument("--min_rate", type=float, default=0.5)
    ap.add_argument("--max_rate", type=float, default=50.0)
    ap.add_argument("--window", type=int, default=8)
    ap.add_argument("--queue_threshold_bytes", type=float, default=12000.0)
    ap.add_argument("--probe_up_gain", type=float, default=1.05)
    ap.add_argument("--drain_gain", type=float, default=0.95)
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
    out_csv = args.out_csv or f"results/bbr_like_report_{scenario_name}.csv"
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)

    send_rate = float(df["send_rate_mbps"].iloc[0])
    send_rate = float(np.clip(send_rate, args.min_rate, args.max_rate))
    q_bytes = 0.0
    achieved = float(df["throughput_mbps"].iloc[0])
    achieved_hist = [achieved]
    phase_cycle = ["probe_up", "drain", "cruise", "cruise"]
    phase_idx = 0

    rows = []
    for i in range(len(df)):
        t_sec = float(df["t_sec"].iloc[i])
        bottleneck = float(df["bottleneck_bw_mbps"].iloc[i])

        recent = achieved_hist[-args.window:] if len(achieved_hist) >= 1 else [achieved]
        bw_est = max(recent)

        phase = phase_cycle[phase_idx % len(phase_cycle)]
        phase_idx += 1

        if phase == "probe_up":
            target_rate = bw_est * args.probe_up_gain
        elif phase == "drain":
            target_rate = bw_est * args.drain_gain
        else:
            target_rate = bw_est

        if q_bytes > args.queue_threshold_bytes:
            target_rate *= 0.92

        send_rate = 0.75 * send_rate + 0.25 * target_rate
        send_rate = float(np.clip(send_rate, args.min_rate, args.max_rate))

        achieved, q_bytes, q_packets = plant_step(bottleneck, send_rate, q_bytes)
        achieved_hist.append(achieved)

        rows.append({
            "t_sec": t_sec,
            "bbr_send_rate_mbps": send_rate,
            "bbr_achieved_throughput_mbps": achieved,
            "bbr_queue_bytes": q_bytes,
            "bbr_queue_packets": q_packets,
            "bbr_bw_est_mbps": bw_est,
            "bbr_phase": phase,
            "bottleneck_bw_mbps": bottleneck,
        })

    out_df = pd.DataFrame(rows)
    out_df.to_csv(out_csv, index=False)
    print(f"Wrote: {out_csv}")
    print("Rows:", len(out_df))
    print("Average throughput:", float(out_df["bbr_achieved_throughput_mbps"].mean()))
    print("Average send rate:", float(out_df["bbr_send_rate_mbps"].mean()))
    print("Average queue bytes:", float(out_df["bbr_queue_bytes"].mean()))
    print("p95 queue bytes:", float(np.percentile(out_df["bbr_queue_bytes"], 95)))

if __name__ == "__main__":
    main()
