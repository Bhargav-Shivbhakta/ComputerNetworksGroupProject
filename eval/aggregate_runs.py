#!/usr/bin/env python3
import os
import glob
import re
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

RAW_DIR = "/home/bhargav/CNGP/data/raw"
OUT_TABLE = "/home/bhargav/CNGP/paper/tables/summary.csv"
FIG_DIR = "/home/bhargav/CNGP/paper/figures"

# We want to analyze only the "active sending" window to avoid the post-stop zeros.
# Sender starts at 0.1s, we log from 0.2s. Sender stops at simTime (default 10s).
ACTIVE_T_MIN = 0.2
ACTIVE_T_MAX = 10.0

TAG_RE = re.compile(r"bw(?P<bw>\d+)M_rtt(?P<rtt>\d+)_q(?P<q>\d+)p_sr(?P<sr>\d+)M")

def parse_tag(fname: str):
    base = os.path.basename(fname)
    tag = base.replace(".csv", "")
    m = TAG_RE.match(tag)
    if not m:
        return tag, None
    d = m.groupdict()
    return tag, {
        "bw_Mbps": int(d["bw"]),
        "rtt_ms": int(d["rtt"]),
        "q_packets": int(d["q"]),
        "send_Mbps": int(d["sr"]),
    }

def load_one(path: str):
    # CSV has comment lines starting with '#'
    df = pd.read_csv(path, comment="#")
    # enforce columns
    cols = ["t_sec","rx_bytes_total","throughput_kbps","queue_packets","queue_bytes"]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"{path} missing columns: {missing}")
    return df

def summarize_run(df: pd.DataFrame):
    active = df[(df["t_sec"] >= ACTIVE_T_MIN) & (df["t_sec"] <= ACTIVE_T_MAX)].copy()

    # Throughput in Mbps (kbps -> Mbps)
    thr_mbps = active["throughput_kbps"] / 1000.0

    # Avoid including initial zeros if they exist due to startup
    thr_mbps_nonzero = thr_mbps[thr_mbps > 0]

    summary = {
        "samples": len(active),
        "mean_thr_mbps": float(thr_mbps_nonzero.mean()) if len(thr_mbps_nonzero) else 0.0,
        "p10_thr_mbps": float(thr_mbps_nonzero.quantile(0.10)) if len(thr_mbps_nonzero) else 0.0,
        "p50_thr_mbps": float(thr_mbps_nonzero.quantile(0.50)) if len(thr_mbps_nonzero) else 0.0,
        "p90_thr_mbps": float(thr_mbps_nonzero.quantile(0.90)) if len(thr_mbps_nonzero) else 0.0,
        "p95_q_bytes": float(active["queue_bytes"].quantile(0.95)),
        "max_q_bytes": float(active["queue_bytes"].max()),
        "p95_q_packets": float(active["queue_packets"].quantile(0.95)),
        "max_q_packets": float(active["queue_packets"].max()),
    }

    # time-to-first-queue (when queue_bytes becomes > 0)
    qpos = active[active["queue_bytes"] > 0]
    summary["t_first_queue"] = float(qpos["t_sec"].iloc[0]) if len(qpos) else np.nan

    return summary

def main():
    os.makedirs(FIG_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(OUT_TABLE), exist_ok=True)

    files = sorted(glob.glob(os.path.join(RAW_DIR, "bw*M_rtt*_q*p_sr*M.csv")))
    if not files:
        raise SystemExit(f"No grid CSVs found in {RAW_DIR}")

    rows = []
    for f in files:
        tag, meta = parse_tag(f)
        df = load_one(f)
        s = summarize_run(df)
        row = {"runTag": tag}
        if meta:
            row.update(meta)
        row.update(s)
        rows.append(row)

    summary = pd.DataFrame(rows)

    # Save table for paper
    summary.to_csv(OUT_TABLE, index=False)
    print(f"Wrote: {OUT_TABLE} rows={len(summary)}")

    # ---------- FIGURE 1: Mean throughput vs RTT, grouped by BW ----------
    # Each BW as separate line
    fig1 = summary.pivot_table(index="rtt_ms", columns="bw_Mbps", values="mean_thr_mbps", aggfunc="mean").sort_index()
    plt.figure()
    for bw in fig1.columns:
        plt.plot(fig1.index, fig1[bw], marker="o", label=f"{bw} Mbps bottleneck")
    plt.xlabel("RTT (ms)")
    plt.ylabel("Mean throughput (Mbps)")
    plt.title("Throughput vs RTT (averaged over q and sendRate)")
    plt.legend()
    plt.grid(True, alpha=0.3)
    out1 = os.path.join(FIG_DIR, "fig_throughput_vs_rtt.png")
    plt.savefig(out1, dpi=200, bbox_inches="tight")
    plt.close()
    print(f"Wrote: {out1}")

    # ---------- FIGURE 2: Queue bytes (p95) vs send rate, grouped by BW ----------
    fig2 = summary.pivot_table(index="send_Mbps", columns="bw_Mbps", values="p95_q_bytes", aggfunc="mean").sort_index()
    plt.figure()
    for bw in fig2.columns:
        plt.plot(fig2.index, fig2[bw], marker="o", label=f"{bw} Mbps bottleneck")
    plt.xlabel("Send rate (Mbps)")
    plt.ylabel("P95 queue bytes")
    plt.title("Queue pressure vs send rate (averaged over RTT and q)")
    plt.legend()
    plt.grid(True, alpha=0.3)
    out2 = os.path.join(FIG_DIR, "fig_queue_vs_sendrate.png")
    plt.savefig(out2, dpi=200, bbox_inches="tight")
    plt.close()
    print(f"Wrote: {out2}")

    # ---------- FIGURE 3: Heatmap-like table (mean throughput) for one q setting ----------
    # Pick q=50 as "default-ish" if present
    if "q_packets" in summary.columns and (summary["q_packets"] == 50).any():
        sub = summary[summary["q_packets"] == 50]
        mat = sub.pivot_table(index="rtt_ms", columns="bw_Mbps", values="mean_thr_mbps", aggfunc="mean").sort_index()
        plt.figure()
        plt.imshow(mat.values, aspect="auto", interpolation="nearest")
        plt.yticks(range(len(mat.index)), [str(x) for x in mat.index])
        plt.xticks(range(len(mat.columns)), [str(x) for x in mat.columns])
        plt.xlabel("Bottleneck BW (Mbps)")
        plt.ylabel("RTT (ms)")
        plt.title("Mean throughput (Mbps) heatmap (q=50p)")
        plt.colorbar(label="Mbps")
        out3 = os.path.join(FIG_DIR, "fig_heatmap_thr_q50.png")
        plt.savefig(out3, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"Wrote: {out3}")

if __name__ == "__main__":
    main()
