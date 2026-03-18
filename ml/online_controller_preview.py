#!/usr/bin/env python3
import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from joblib import load

MODEL_PATH = "/home/bhargav/CNGP/ml/models/rf_queuebytes_1s.joblib"
RAW_DIR = "/home/bhargav/CNGP/data/raw"
OUT_FIG = "/home/bhargav/CNGP/paper/figures"

WINDOW_STEPS = 10
HORIZON_STEPS = 10
FEATURE_COLS = ["throughput_kbps", "queue_packets", "queue_bytes"]

def parse_meta_from_tag(tag: str):
    # expects bw10M_rtt40_q50p_sr6M style
    import re
    m = re.match(r"bw(\d+)M_rtt(\d+)_q(\d+)p_sr(\d+)M", tag)
    if not m:
        return np.array([0,0,0,0], dtype=np.float32)
    bw, rtt, q, sr = map(int, m.groups())
    return np.array([bw, rtt, q, sr], dtype=np.float32)

def recommend_rate(prev_rate_mbps: float, q_pred_bytes: float, q_max_bytes: float):
    """
    Simple safe controller:
    - If predicted queue > 80% of max: decrease by 15%
    - If predicted queue < 20% of max: increase by 5%
    - Else: hold
    """
    if q_max_bytes <= 0:
        return prev_rate_mbps
    high = 0.80 * q_max_bytes
    low  = 0.20 * q_max_bytes

    if q_pred_bytes >= high:
        return max(0.1, prev_rate_mbps * 0.85)
    if q_pred_bytes <= low:
        return prev_rate_mbps * 1.05
    return prev_rate_mbps

def main():
    os.makedirs(OUT_FIG, exist_ok=True)

    # Pick one run for demo (you can change this)
    tag = "bw5M_rtt40_q50p_sr6M"
    path = os.path.join(RAW_DIR, f"{tag}.csv")
    if not os.path.exists(path):
        raise SystemExit(f"Missing file: {path}")

    df = pd.read_csv(path, comment="#")
    df = df.sort_values("t_sec").reset_index(drop=True)

    model = load(MODEL_PATH)
    meta = parse_meta_from_tag(tag)

    X = df[FEATURE_COLS].to_numpy(dtype=np.float32)
    q_actual = df["queue_bytes"].to_numpy(dtype=np.float32)
    t = df["t_sec"].to_numpy(dtype=np.float32)

    # estimate max queue bytes from observed max (ok for preview)
    q_max_bytes = float(np.max(q_actual))

    q_pred_series = np.full_like(q_actual, fill_value=np.nan, dtype=np.float32)
    rate_series = np.full_like(q_actual, fill_value=np.nan, dtype=np.float32)

    # controller state
    current_rate = float(meta[3]) if meta[3] > 0 else 6.0  # start at send_Mbps from tag

    for i in range(WINDOW_STEPS, len(df) - HORIZON_STEPS):
        window = X[i-WINDOW_STEPS:i].reshape(-1)
        feats = np.hstack([window, meta]).reshape(1, -1)
        q_pred = float(model.predict(feats)[0])
        q_pred_series[i] = q_pred

        # update rate recommendation
        current_rate = recommend_rate(current_rate, q_pred, q_max_bytes)
        rate_series[i] = current_rate

    # Plot
    plt.figure()
    plt.plot(t, q_actual, label="queue_bytes (actual)")
    plt.plot(t, q_pred_series, label="queue_bytes (predicted, +1s)")
    plt.xlabel("time (s)")
    plt.ylabel("queue bytes")
    plt.title(f"Queue prediction preview: {tag}")
    plt.grid(True, alpha=0.3)
    plt.legend()
    out1 = os.path.join(OUT_FIG, f"controller_preview_queue_{tag}.png")
    plt.savefig(out1, dpi=200, bbox_inches="tight")
    plt.close()

    plt.figure()
    plt.plot(t, rate_series, label="recommended send rate (Mbps)")
    plt.xlabel("time (s)")
    plt.ylabel("Mbps")
    plt.title(f"Controller rate trace: {tag}")
    plt.grid(True, alpha=0.3)
    plt.legend()
    out2 = os.path.join(OUT_FIG, f"controller_preview_rate_{tag}.png")
    plt.savefig(out2, dpi=200, bbox_inches="tight")
    plt.close()

    print("Wrote:")
    print(out1)
    print(out2)

if __name__ == "__main__":
    main()
