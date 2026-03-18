#!/usr/bin/env python3
import os
import pandas as pd
import matplotlib.pyplot as plt

INPUT_CSV = "results/multiflow_fairness_summary.csv"
OUT_DIR = "paper/figures"
os.makedirs(OUT_DIR, exist_ok=True)

df = pd.read_csv(INPUT_CSV)

agg = (
    df.groupby("controller", as_index=False)
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

# Throughput
plt.figure(figsize=(8, 5))
plt.bar(agg["controller"], agg["thr_mean"], yerr=agg["thr_std"], capsize=4)
plt.ylabel("Average Total Throughput (Mbps)")
plt.xlabel("Controller")
plt.title("Multi-flow Throughput by Controller")
plt.xticks(rotation=30)
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/multiflow_throughput.png", dpi=200)
plt.close()

# Fairness
plt.figure(figsize=(8, 5))
plt.bar(agg["controller"], agg["fair_mean"], yerr=agg["fair_std"], capsize=4)
plt.ylabel("Jain Fairness Index")
plt.xlabel("Controller")
plt.title("Multi-flow Fairness by Controller")
plt.ylim(0, 1.05)
plt.xticks(rotation=30)
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/multiflow_fairness.png", dpi=200)
plt.close()

# Queue (log)
plt.figure(figsize=(8, 5))
plt.bar(agg["controller"], agg["queue_mean"], yerr=agg["queue_std"], capsize=4)
plt.yscale("log")
plt.ylabel("Average Queue Bytes (log scale)")
plt.xlabel("Controller")
plt.title("Multi-flow Queue by Controller")
plt.xticks(rotation=30)
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/multiflow_queue_log.png", dpi=200)
plt.close()

print("Saved fairness figures to", OUT_DIR)
print(agg)
