#!/usr/bin/env python3
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

df = pd.read_csv("results/all_baselines_summary.csv")

sns.set(style="whitegrid")

# Throughput comparison
plt.figure(figsize=(8,5))
sns.barplot(data=df, x="controller", y="avg_throughput")
plt.title("Average Throughput by Controller")
plt.ylabel("Throughput (Mbps)")
plt.xticks(rotation=45)
plt.tight_layout()
plt.savefig("paper/figures/throughput_comparison_all.png")

# Queue comparison
plt.figure(figsize=(8,5))
sns.barplot(data=df, x="controller", y="avg_queue_bytes")
plt.title("Average Queue Size by Controller")
plt.ylabel("Queue Bytes")
plt.xticks(rotation=45)
plt.tight_layout()
plt.savefig("paper/figures/queue_comparison_all.png")

# Throughput vs Queue tradeoff
plt.figure(figsize=(7,6))
sns.scatterplot(data=df,
                x="avg_queue_bytes",
                y="avg_throughput",
                hue="controller",
                s=120)
plt.title("Throughput vs Queue Tradeoff")
plt.xlabel("Average Queue Bytes")
plt.ylabel("Average Throughput (Mbps)")
plt.tight_layout()
plt.savefig("paper/figures/tradeoff_throughput_vs_queue_all.png")

print("Saved figures to paper/figures/")
