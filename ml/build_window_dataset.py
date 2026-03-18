#!/usr/bin/env python3
"""
ml/build_window_dataset.py

Build sliding-window forecasting dataset from data/processed/*.csv

Input:
- data/processed/*.csv
  contains signals like:
    throughput_mbps, queue_bytes, queue_packets, send_rate_mbps
  and labels like:
    y_throughput_1s, y_bottleneck_1s (depending on build_processed_dataset.py)

Output:
- data/processed_windowed/<OUT_NAME>

Config via env vars:
- LABEL_COL (default: y_throughput_1s)
- OUT_NAME  (default: windowed_<LABEL_COL>.csv)
- WINDOW    (default: 10)
"""

import os
import glob
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(__file__))
PROC_DIR = os.path.join(ROOT, "data", "processed")
OUT_DIR = os.path.join(ROOT, "data", "processed_windowed")
os.makedirs(OUT_DIR, exist_ok=True)

WINDOW = int(os.environ.get("WINDOW", "10"))

SIGNALS = [
    "throughput_mbps",
    "queue_bytes",
    "queue_packets",
    "send_rate_mbps",
]

LABEL = os.environ.get("LABEL_COL", "y_throughput_1s")
OUT_NAME = os.environ.get("OUT_NAME", f"windowed_{LABEL}.csv")


def compute_slope(x: np.ndarray) -> float:
    if len(x) < 2:
        return 0.0
    return float((x[-1] - x[0]) / (len(x) - 1))


def build_window_df(df: pd.DataFrame) -> pd.DataFrame:
    rows = []

    for i in range(WINDOW, len(df)):
        window = df.iloc[i - WINDOW : i]
        target = df.iloc[i][LABEL]

        feat = {}
        for s in SIGNALS:
            if s not in df.columns:
                continue

            values = window[s].to_numpy(dtype=float)

            feat[f"{s}_mean"] = float(np.mean(values))
            feat[f"{s}_std"] = float(np.std(values))
            feat[f"{s}_min"] = float(np.min(values))
            feat[f"{s}_max"] = float(np.max(values))
            feat[f"{s}_last"] = float(values[-1])
            feat[f"{s}_slope"] = compute_slope(values)

        feat[LABEL] = float(target)
        rows.append(feat)

    return pd.DataFrame(rows)


def main():
    files = sorted(glob.glob(os.path.join(PROC_DIR, "*.csv")))
    if not files:
        raise FileNotFoundError(f"No processed CSVs found in {PROC_DIR}")

    print("Building windowed dataset...")
    print(f"- PROC_DIR: {PROC_DIR}")
    print(f"- WINDOW: {WINDOW}")
    print(f"- LABEL: {LABEL}")
    print(f"- SIGNALS: {SIGNALS}")

    all_dfs = []

    for f in files:
        df = pd.read_csv(f)
        if LABEL not in df.columns:
            continue

        df = df.select_dtypes(include=[np.number]).replace([np.inf, -np.inf], np.nan).dropna()
        if len(df) <= WINDOW:
            continue

        wdf = build_window_df(df)
        if len(wdf) > 0:
            all_dfs.append(wdf)

    if not all_dfs:
        raise RuntimeError(f"No windowed rows produced. Check LABEL_COL={LABEL} exists in processed CSVs.")

    final_df = pd.concat(all_dfs, ignore_index=True)

    out_path = os.path.join(OUT_DIR, OUT_NAME)
    final_df.to_csv(out_path, index=False)

    print("Windowed dataset shape:", final_df.shape)
    print("Saved to:", out_path)


if __name__ == "__main__":
    main()
