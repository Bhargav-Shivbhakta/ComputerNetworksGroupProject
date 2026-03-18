#!/usr/bin/env python3
"""
scripts/generate_results_plots.py

Generate final plots for the congestion control project.

Reads:
- results/rl_vs_baselines_summary.csv
Optionally reads TensorBoard event logs for PPO training curve.

Outputs PNGs into:
- paper/figures/

Usage examples:

python3 scripts/generate_results_plots.py \
  --summary_csv results/rl_vs_baselines_summary.csv \
  --out_dir paper/figures

python3 scripts/generate_results_plots.py \
  --summary_csv results/rl_vs_baselines_summary.csv \
  --tb_logdir ml/rl_models/tb \
  --out_dir paper/figures
"""

import os
import argparse
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def save_bar_plot(df, x_col, y_col, title, ylabel, out_path, rotation=0):
    plt.figure(figsize=(8, 5))
    plt.bar(df[x_col], df[y_col])
    plt.title(title)
    plt.xlabel(x_col)
    plt.ylabel(ylabel)
    plt.xticks(rotation=rotation)
    plt.tight_layout()
    plt.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close()


def save_grouped_bar_plot(df, category_col, value_col, title, ylabel, out_path):
    """
    Aggregates by category_col and plots the mean of value_col.
    """
    agg = df.groupby(category_col, as_index=False)[value_col].mean()
    plt.figure(figsize=(8, 5))
    plt.bar(agg[category_col], agg[value_col])
    plt.title(title)
    plt.xlabel(category_col)
    plt.ylabel(ylabel)
    plt.tight_layout()
    plt.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close()


def save_tradeoff_plot(df, out_path):
    """
    Scatter: avg_queue_bytes vs avg_throughput
    """
    agg = df.groupby("controller", as_index=False)[["avg_throughput", "avg_queue_bytes"]].mean()

    plt.figure(figsize=(7, 5))
    plt.scatter(agg["avg_queue_bytes"], agg["avg_throughput"], s=120)

    for _, row in agg.iterrows():
        plt.annotate(
            row["controller"],
            (row["avg_queue_bytes"], row["avg_throughput"]),
            xytext=(6, 4),
            textcoords="offset points"
        )

    plt.title("Throughput vs Queue Tradeoff")
    plt.xlabel("Average Queue Bytes")
    plt.ylabel("Average Throughput (Mbps)")
    plt.tight_layout()
    plt.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close()


def save_per_scenario_plot(df, metric_col, title, ylabel, out_path):
    """
    Grouped bar chart by scenario and controller.
    """
    pivot = df.pivot(index="scenario", columns="controller", values=metric_col)
    pivot = pivot.sort_index()

    ax = pivot.plot(kind="bar", figsize=(11, 6))
    ax.set_title(title)
    ax.set_xlabel("Scenario")
    ax.set_ylabel(ylabel)
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    plt.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close()


def try_plot_tensorboard(tb_logdir: str, out_path: str) -> bool:
    """
    Attempts to read TensorBoard scalar logs and plot PPO reward curve.
    Looks for scalar tags commonly used by SB3.
    """
    try:
        from tensorboard.backend.event_processing.event_accumulator import EventAccumulator
    except Exception:
        return False

    if not os.path.exists(tb_logdir):
        return False

    event_files = []
    for root, _, files in os.walk(tb_logdir):
        for f in files:
            if "events.out.tfevents" in f:
                event_files.append(os.path.join(root, f))

    if not event_files:
        return False

    # Use the newest event file
    event_files.sort(key=os.path.getmtime, reverse=True)
    ev_path = event_files[0]

    try:
        acc = EventAccumulator(ev_path)
        acc.Reload()
    except Exception:
        return False

    possible_tags = [
        "eval/mean_reward",
        "rollout/ep_rew_mean",
    ]

    chosen_tag = None
    for tag in possible_tags:
        if tag in acc.Tags().get("scalars", []):
            chosen_tag = tag
            break

    if chosen_tag is None:
        return False

    scalars = acc.Scalars(chosen_tag)
    if not scalars:
        return False

    steps = [s.step for s in scalars]
    values = [s.value for s in scalars]

    plt.figure(figsize=(8, 5))
    plt.plot(steps, values)
    plt.title(f"PPO Training Curve ({chosen_tag})")
    plt.xlabel("Training Step")
    plt.ylabel("Reward")
    plt.tight_layout()
    plt.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close()

    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary_csv", required=True, help="Path to rl_vs_baselines_summary.csv")
    parser.add_argument("--tb_logdir", default="", help="Optional TensorBoard logdir for PPO training curve")
    parser.add_argument("--out_dir", default="paper/figures", help="Output directory for plots")
    args = parser.parse_args()

    ensure_dir(args.out_dir)

    if not os.path.exists(args.summary_csv):
        raise FileNotFoundError(f"Summary CSV not found: {args.summary_csv}")

    df = pd.read_csv(args.summary_csv)

    required_cols = [
        "controller",
        "avg_throughput",
        "avg_queue_bytes",
        "p95_queue_bytes",
        "queue_nonzero_frac",
        "avg_send_rate",
    ]
    for c in required_cols:
        if c not in df.columns:
            raise KeyError(f"Missing required column in summary CSV: {c}")

    # Overall mean by controller
    overall = df.groupby("controller", as_index=False)[[
        "avg_throughput",
        "avg_queue_bytes",
        "p95_queue_bytes",
        "queue_nonzero_frac",
        "avg_send_rate"
    ]].mean()

    # 1) Throughput comparison
    save_bar_plot(
        overall,
        x_col="controller",
        y_col="avg_throughput",
        title="Average Throughput by Controller",
        ylabel="Average Throughput (Mbps)",
        out_path=os.path.join(args.out_dir, "throughput_comparison.png"),
    )

    # 2) Queue comparison
    save_bar_plot(
        overall,
        x_col="controller",
        y_col="avg_queue_bytes",
        title="Average Queue Bytes by Controller",
        ylabel="Average Queue Bytes",
        out_path=os.path.join(args.out_dir, "queue_comparison.png"),
    )

    # 3) Tradeoff plot
    save_tradeoff_plot(
        overall,
        out_path=os.path.join(args.out_dir, "tradeoff_throughput_vs_queue.png"),
    )

    # 4) Queue nonzero fraction
    save_bar_plot(
        overall,
        x_col="controller",
        y_col="queue_nonzero_frac",
        title="Queue Nonzero Fraction by Controller",
        ylabel="Queue Nonzero Fraction",
        out_path=os.path.join(args.out_dir, "queue_nonzero_fraction.png"),
    )

    # 5) Send rate comparison
    save_bar_plot(
        overall,
        x_col="controller",
        y_col="avg_send_rate",
        title="Average Send Rate by Controller",
        ylabel="Average Send Rate (Mbps)",
        out_path=os.path.join(args.out_dir, "send_rate_comparison.png"),
    )

    # Optional per-scenario plots if scenario column exists
    if "scenario" in df.columns:
        save_per_scenario_plot(
            df,
            metric_col="avg_throughput",
            title="Per-Scenario Throughput Comparison",
            ylabel="Average Throughput (Mbps)",
            out_path=os.path.join(args.out_dir, "per_scenario_throughput.png"),
        )

        save_per_scenario_plot(
            df,
            metric_col="avg_queue_bytes",
            title="Per-Scenario Queue Comparison",
            ylabel="Average Queue Bytes",
            out_path=os.path.join(args.out_dir, "per_scenario_queue.png"),
        )

    # Optional PPO training curve from TensorBoard
    if args.tb_logdir:
        ok = try_plot_tensorboard(
            tb_logdir=args.tb_logdir,
            out_path=os.path.join(args.out_dir, "ppo_training_curve.png"),
        )
        if not ok:
            print("Warning: could not generate PPO training curve from TensorBoard logs.")

    print(f"Plots saved in: {args.out_dir}")


if __name__ == "__main__":
    main()
