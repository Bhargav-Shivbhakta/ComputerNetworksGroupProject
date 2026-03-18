#!/usr/bin/env python3
import os
import pandas as pd
import matplotlib.pyplot as plt

INPUT_CSV = "results/multiflow_fairness_asym_summary.csv"
OUT_DIR = "paper/figures"
os.makedirs(OUT_DIR, exist_ok=True)

df = pd.read_csv(INPUT_CSV)

cases = df["case"].unique()

for case_name in cases:
    sub = df[df["case"] == case_name].copy()

    agg = (
        sub.groupby("controller", as_index=False)
           .agg({
               "avg_total_throughput": ["mean", "std"],
               "avg_fairness": ["mean", "std"],
               "avg_queue_bytes": ["mean", "std"],
           })
    )

    agg.columns = [
        "controller",
        "thr_mean", "thr_std",
        "fair_mean", "fair_std",
        "queue_mean", "queue_std",
    ]

    safe_case = case_name.replace("/", "_")

    # Throughput
    plt.figure(figsize=(8, 5))
    plt.bar(agg["controller"], agg["thr_mean"], yerr=agg["thr_std"], capsize=4)
    plt.ylabel("Average Total Throughput (Mbps)")
    plt.xlabel("Controller")
    plt.title(f"Asymmetric Multi-flow Throughput: {case_name}")
    plt.xticks(rotation=30)
    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/asym_{safe_case}_throughput.png", dpi=200)
    plt.close()

    # Fairness
    plt.figure(figsize=(8, 5))
    plt.bar(agg["controller"], agg["fair_mean"], yerr=agg["fair_std"], capsize=4)
    plt.ylabel("Jain Fairness Index")
    plt.xlabel("Controller")
    plt.title(f"Asymmetric Multi-flow Fairness: {case_name}")
    plt.ylim(0, 1.05)
    plt.xticks(rotation=30)
    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/asym_{safe_case}_fairness.png", dpi=200)
    plt.close()

    # Queue
    plt.figure(figsize=(8, 5))
    plt.bar(agg["controller"], agg["queue_mean"], yerr=agg["queue_std"], capsize=4)
    plt.yscale("log")
    plt.ylabel("Average Queue Bytes (log scale)")
    plt.xlabel("Controller")
    plt.title(f"Asymmetric Multi-flow Queue: {case_name}")
    plt.xticks(rotation=30)
    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/asym_{safe_case}_queue_log.png", dpi=200)
    plt.close()

print("Saved asymmetric fairness figures to", OUT_DIR)
