#!/usr/bin/env python3
"""
ml/build_processed_dataset.py

Build processed forecasting datasets from raw ns-3 logs in data/raw/*.csv.

Raw format:
- comment lines start with '#'
- header line then numeric data
Columns seen:
  t_sec,rx_bytes_total,throughput_kbps,queue_packets,queue_bytes

Metadata format example (in comment lines):
  # bRate=5Mbps ... interval=0.1

We produce BOTH:
(1) Throughput-forecast label (1s ahead):
  y_throughput_1s := throughput_mbps shifted forward by horizon_steps

(2) Bottleneck bandwidth label (1s ahead):
  y_bottleneck_1s := bottleneck_bw_mbps shifted forward by horizon_steps
  (currently constant per file if bRate is constant; later becomes time-varying when using traces)

Extra columns:
- throughput_mbps derived from throughput_kbps
- send_rate_mbps derived from filename when available
- bottleneck_bw_mbps parsed from metadata (bRate)

Output:
- data/processed/*_proc.csv contains numeric features + both labels
"""

from __future__ import annotations
import os
import glob
import re
import pandas as pd
import numpy as np

ROOT = os.path.dirname(os.path.dirname(__file__))
RAW_DIR = os.path.join(ROOT, "data", "raw")
PROC_DIR = os.path.join(ROOT, "data", "processed")
os.makedirs(PROC_DIR, exist_ok=True)

INTERVAL_RE = re.compile(r"\binterval=(\d+(?:\.\d+)?)\b")
BRATE_RE = re.compile(r"\bbRate=(\d+(?:\.\d+)?)\s*Mbps\b", re.IGNORECASE)
SRATE_FN_RE = re.compile(r"_sr(\d+(?:\.\d+)?)M", re.IGNORECASE)

def read_first_comment_lines(path: str, max_lines: int = 20) -> list[str]:
    lines: list[str] = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for _ in range(max_lines):
                line = f.readline()
                if not line:
                    break
                if line.startswith("#"):
                    lines.append(line.strip())
                else:
                    # once we hit header/data, stop
                    break
    except Exception:
        pass
    return lines

def extract_interval_s(path: str, default: float = 0.1) -> float:
    for line in read_first_comment_lines(path, max_lines=20):
        m = INTERVAL_RE.search(line)
        if m:
            try:
                v = float(m.group(1))
                return v if v > 0 else default
            except Exception:
                return default
    return default

def extract_brate_mbps(path: str) -> float | None:
    for line in read_first_comment_lines(path, max_lines=20):
        m = BRATE_RE.search(line)
        if m:
            try:
                v = float(m.group(1))
                return v
            except Exception:
                return None
    return None

def read_raw_timeseries(path: str) -> pd.DataFrame:
    # comment="#" skips metadata lines
    return pd.read_csv(path, comment="#", engine="python")

def main() -> None:
    horizon_s = 1.0

    raw_files = sorted(glob.glob(os.path.join(RAW_DIR, "*.csv")))
    if not raw_files:
        raise FileNotFoundError(f"No raw CSV files found in {RAW_DIR}")

    processed = 0
    skipped = 0

    print(f"Found {len(raw_files)} raw CSV files in {RAW_DIR}")
    print(f"Using forecasting horizon: {horizon_s:.1f}s")

    for path in raw_files:
        base = os.path.basename(path)

        try:
            df = read_raw_timeseries(path)
        except Exception as e:
            print(f"[SKIP] {base}: cannot parse CSV ({e})")
            skipped += 1
            continue

        if df.empty:
            print(f"[SKIP] {base}: empty after parsing")
            skipped += 1
            continue

        df_num = df.select_dtypes(include=[np.number]).copy()
        if df_num.empty:
            print(f"[SKIP] {base}: no numeric columns")
            skipped += 1
            continue

        interval_s = extract_interval_s(path, default=0.1)
        horizon_steps = int(round(horizon_s / interval_s)) if interval_s > 0 else 10
        if horizon_steps < 1:
            horizon_steps = 1

        # derive throughput_mbps
        if "throughput_mbps" not in df_num.columns:
            if "throughput_kbps" in df_num.columns:
                df_num["throughput_mbps"] = df_num["throughput_kbps"] / 1000.0
            else:
                print(f"[SKIP] {base}: missing throughput_kbps (cannot derive throughput_mbps)")
                skipped += 1
                continue

        # derive send_rate_mbps from filename if not present
        if "send_rate_mbps" not in df_num.columns:
            m = SRATE_FN_RE.search(base)
            if m:
                try:
                    df_num["send_rate_mbps"] = float(m.group(1))
                except Exception:
                    pass

        # parse bottleneck bandwidth from metadata
        brate = extract_brate_mbps(path)
        if brate is None:
            # fallback: try parse from filename like bw5M_...
            m2 = re.search(r"\bbw(\d+(?:\.\d+)?)M\b", base, re.IGNORECASE)
            if m2:
                try:
                    brate = float(m2.group(1))
                except Exception:
                    brate = None

        if brate is None:
            print(f"[SKIP] {base}: could not extract bottleneck bandwidth (bRate=XMpbs)")
            skipped += 1
            continue

        df_num["bottleneck_bw_mbps"] = float(brate)

        # Labels:
        # 1) future throughput label
        df_num["y_throughput_1s"] = df_num["throughput_mbps"].shift(-horizon_steps)

        # 2) future bottleneck bw label (constant now; becomes time-varying later)
        df_num["y_bottleneck_1s"] = df_num["bottleneck_bw_mbps"].shift(-horizon_steps)

        # Clean
        df_num = df_num.replace([np.inf, -np.inf], np.nan).dropna()

        if len(df_num) < 30:
            print(f"[SKIP] {base}: too few rows after shift/clean ({len(df_num)})")
            skipped += 1
            continue

        out_name = base.replace(".csv", "_proc.csv")
        out_path = os.path.join(PROC_DIR, out_name)
        df_num.to_csv(out_path, index=False)

        processed += 1
        print(f"[OK] {base} -> {out_name} | rows={len(df_num)} | interval={interval_s}s | horizon_steps={horizon_steps} | bRate={brate}Mbps")

    print("\n==== Summary ====")
    print("Processed:", processed)
    print("Skipped:", skipped)
    print("Output dir:", PROC_DIR)

if __name__ == "__main__":
    main()
