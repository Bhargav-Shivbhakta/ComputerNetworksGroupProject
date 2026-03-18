#!/usr/bin/env python3
"""
ml/eval_multiflow_fairness.py

Evaluate controllers in a simple 2-flow shared bottleneck setting.

Controllers:
- EWMA_SR
- GCC_LIKE
- BBR_LIKE
- PPO_RL
- optional ML_PRED

Outputs:
- results/multiflow_fairness_summary.csv
- results/multiflow_fairness_summary.json
"""

import os
import glob
import json
import argparse
from collections import deque

import numpy as np
import pandas as pd
from joblib import load

try:
    from stable_baselines3 import PPO
    HAS_SB3 = True
except Exception:
    HAS_SB3 = False

WINDOW = 10
SIGNALS = ["throughput_mbps", "queue_bytes", "queue_packets", "send_rate_mbps"]


def compute_slope(x: np.ndarray) -> float:
    if len(x) < 2:
        return 0.0
    return float((x[-1] - x[0]) / len(x))


def featurize_window_df(window_df: pd.DataFrame) -> dict:
    feat = {}
    for s in SIGNALS:
        vals = window_df[s].values.astype(float)
        feat[f"{s}_mean"] = float(np.mean(vals))
        feat[f"{s}_std"] = float(np.std(vals))
        feat[f"{s}_min"] = float(np.min(vals))
        feat[f"{s}_max"] = float(np.max(vals))
        feat[f"{s}_last"] = float(vals[-1])
        feat[f"{s}_slope"] = compute_slope(vals)
    return feat


def jain_fairness(xs):
    xs = np.array(xs, dtype=float)
    denom = len(xs) * np.sum(xs ** 2)
    if denom <= 1e-12:
        return 0.0
    return float((np.sum(xs) ** 2) / denom)


def load_xgb_bundle(path):
    bundle = load(path)
    for k in ("model", "scaler", "feature_names"):
        if k not in bundle:
            raise KeyError(f"Missing key in XGB bundle: {k}")
    return bundle


def predict_xgb(bundle, history_rows):
    wdf = pd.DataFrame(history_rows)
    feat = featurize_window_df(wdf)
    feature_names = bundle["feature_names"]
    row_df = pd.DataFrame([{k: feat.get(k, 0.0) for k in feature_names}])
    x_scaled = bundle["scaler"].transform(row_df)

    import xgboost as xgb
    dtest = xgb.DMatrix(x_scaled, feature_names=feature_names)
    pred = float(bundle["model"].predict(dtest)[0])
    return pred


def build_initial_history(init_thr, init_sr):
    row = {
        "throughput_mbps": init_thr,
        "queue_bytes": 0.0,
        "queue_packets": 0.0,
        "send_rate_mbps": init_sr,
    }
    return [row.copy() for _ in range(WINDOW)]


def shared_bottleneck_step(bottleneck, send_rates, q_bytes_total, rng):
    """
    Shared bottleneck for 2 flows.
    Allocate throughput proportional to offered send rate.
    """
    send_rates = np.array(send_rates, dtype=float)
    offered = np.sum(send_rates)

    if offered <= 1e-9:
        throughputs = np.zeros_like(send_rates)
    elif offered <= bottleneck:
        throughputs = send_rates.copy()
    else:
        throughputs = bottleneck * (send_rates / offered)

    noise = rng.normal(0.0, 0.03 * max(1e-6, bottleneck), size=len(send_rates))
    throughputs = np.maximum(0.0, throughputs + noise)

    excess = max(0.0, offered - bottleneck)
    q_bytes_total = max(0.0, q_bytes_total + excess * 8000.0)
    q_packets_total = q_bytes_total / 1200.0 if q_bytes_total > 0 else 0.0

    return throughputs, q_bytes_total, q_packets_total


def controller_next_rate(name, state, min_rate, max_rate, alpha, bundle=None, ppo_model=None):
    send_rate = state["send_rate"]
    q_bytes = state["queue_bytes"]
    last_thr = state["last_thr"]

    if name == "EWMA_SR":
        ewma_thr = state["ewma_thr"]
        send_rate = float(np.clip(ewma_thr, min_rate, max_rate))
        return send_rate

    if name == "GCC_LIKE":
        if q_bytes > 10000.0:
            send_rate *= 0.85
        else:
            util = last_thr / max(send_rate, 1e-6)
            if util > 0.85:
                send_rate += 0.15
            else:
                send_rate += 0.05
        return float(np.clip(send_rate, min_rate, max_rate))

    if name == "BBR_LIKE":
        recent = state["recent_thr"]
        bw_est = max(recent) if recent else last_thr
        phase_cycle = ["probe_up", "drain", "cruise", "cruise"]
        phase = phase_cycle[state["phase_idx"] % len(phase_cycle)]
        state["phase_idx"] += 1

        if phase == "probe_up":
            target = bw_est * 1.05
        elif phase == "drain":
            target = bw_est * 0.95
        else:
            target = bw_est

        if q_bytes > 12000.0:
            target *= 0.92

        send_rate = 0.75 * send_rate + 0.25 * target
        return float(np.clip(send_rate, min_rate, max_rate))

    if name == "ML_PRED":
        pred_thr = predict_xgb(bundle, list(state["history"]))
        target = max(min_rate, pred_thr)
        send_rate = (1.0 - alpha) * send_rate + alpha * target
        return float(np.clip(send_rate, min_rate, max_rate))

    if name == "PPO_RL":
        pred_thr = predict_xgb(bundle, list(state["history"]))
        obs = np.array([
            float(last_thr),
            float(q_bytes),
            float(q_bytes / 1200.0),
            float(send_rate),
            float(pred_thr),
            float(state["bottleneck"]),
        ], dtype=np.float32)

        action, _ = ppo_model.predict(obs, deterministic=True)
        try:
            action_idx = int(np.asarray(action).item())
        except Exception:
            action_idx = int(action)

        action_map = {
            0: -0.20,
            1: -0.10,
            2:  0.00,
            3:  0.10,
            4:  0.20,
        }
        delta = action_map.get(action_idx, 0.0)
        send_rate = send_rate * (1.0 + delta)
        return float(np.clip(send_rate, min_rate, max_rate))

    raise ValueError(f"Unknown controller: {name}")


def run_multiflow_controller(df, controller_name, min_rate, max_rate, alpha, rng, bundle=None, ppo_model=None):
    bottleneck = float(df["bottleneck_bw_mbps"].iloc[0])

    init_sr1 = float(np.clip(df["send_rate_mbps"].iloc[0], min_rate, max_rate))
    init_sr2 = float(np.clip(df["send_rate_mbps"].iloc[0], min_rate, max_rate))

    init_thr = min(init_sr1, bottleneck / 2.0)

    flow1 = {
        "send_rate": init_sr1,
        "last_thr": init_thr,
        "queue_bytes": 0.0,
        "ewma_thr": init_thr,
        "recent_thr": [init_thr],
        "phase_idx": 0,
        "history": deque(build_initial_history(init_thr, init_sr1), maxlen=WINDOW),
        "bottleneck": bottleneck,
    }
    flow2 = {
        "send_rate": init_sr2,
        "last_thr": init_thr,
        "queue_bytes": 0.0,
        "ewma_thr": init_thr,
        "recent_thr": [init_thr],
        "phase_idx": 0,
        "history": deque(build_initial_history(init_thr, init_sr2), maxlen=WINDOW),
        "bottleneck": bottleneck,
    }

    q_total = 0.0
    rows = []

    for i in range(len(df)):
        t_sec = float(df["t_sec"].iloc[i]) if "t_sec" in df.columns else float(i)

        sr1 = controller_next_rate(controller_name, flow1, min_rate, max_rate, alpha, bundle, ppo_model)
        sr2 = controller_next_rate(controller_name, flow2, min_rate, max_rate, alpha, bundle, ppo_model)

        thrs, q_total, q_packets_total = shared_bottleneck_step(
            bottleneck=bottleneck,
            send_rates=[sr1, sr2],
            q_bytes_total=q_total,
            rng=rng
        )
        thr1, thr2 = float(thrs[0]), float(thrs[1])

        flow1["send_rate"] = sr1
        flow2["send_rate"] = sr2
        flow1["last_thr"] = thr1
        flow2["last_thr"] = thr2
        flow1["queue_bytes"] = q_total / 2.0
        flow2["queue_bytes"] = q_total / 2.0

        flow1["ewma_thr"] = (1.0 - alpha) * flow1["ewma_thr"] + alpha * thr1
        flow2["ewma_thr"] = (1.0 - alpha) * flow2["ewma_thr"] + alpha * thr2

        flow1["recent_thr"].append(thr1)
        flow2["recent_thr"].append(thr2)
        flow1["recent_thr"] = flow1["recent_thr"][-8:]
        flow2["recent_thr"] = flow2["recent_thr"][-8:]

        flow1["history"].append({
            "throughput_mbps": thr1,
            "queue_bytes": q_total / 2.0,
            "queue_packets": q_packets_total / 2.0,
            "send_rate_mbps": sr1,
        })
        flow2["history"].append({
            "throughput_mbps": thr2,
            "queue_bytes": q_total / 2.0,
            "queue_packets": q_packets_total / 2.0,
            "send_rate_mbps": sr2,
        })

        rows.append({
            "t_sec": t_sec,
            "flow1_thr": thr1,
            "flow2_thr": thr2,
            "flow1_send": sr1,
            "flow2_send": sr2,
            "total_thr": thr1 + thr2,
            "queue_bytes": q_total,
            "queue_packets": q_packets_total,
            "fairness": jain_fairness([thr1, thr2]),
            "controller": controller_name,
        })

    return pd.DataFrame(rows)


def summarize_multiflow(scenario, controller, simdf):
    return {
        "scenario": scenario,
        "controller": controller,
        "n": int(len(simdf)),
        "avg_total_throughput": float(simdf["total_thr"].mean()),
        "avg_flow1_throughput": float(simdf["flow1_thr"].mean()),
        "avg_flow2_throughput": float(simdf["flow2_thr"].mean()),
        "avg_fairness": float(simdf["fairness"].mean()),
        "avg_queue_bytes": float(simdf["queue_bytes"].mean()),
        "p95_queue_bytes": float(np.percentile(simdf["queue_bytes"], 95)),
        "avg_send_rate": float((simdf["flow1_send"].mean() + simdf["flow2_send"].mean()) / 2.0),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", required=True, help="Glob for processed CSVs")
    ap.add_argument("--xgb_model", required=True)
    ap.add_argument("--ppo_model", default="")
    ap.add_argument("--alpha", type=float, default=0.6)
    ap.add_argument("--min_rate", type=float, default=2.0)
    ap.add_argument("--max_rate", type=float, default=50.0)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    files = sorted(glob.glob(args.glob))
    if not files:
        raise FileNotFoundError(f"No files matched: {args.glob}")

    bundle = load_xgb_bundle(args.xgb_model)
    ppo_model = None
    if args.ppo_model:
        if not HAS_SB3:
            raise ImportError("stable_baselines3 unavailable")
        ppo_model = PPO.load(args.ppo_model, device="cpu")

    controllers = ["EWMA_SR", "GCC_LIKE", "BBR_LIKE", "PPO_RL"]
    summaries = []

    for idx, path in enumerate(files):
        df = pd.read_csv(path).replace([np.inf, -np.inf], np.nan).dropna()

        for c in ["throughput_mbps", "send_rate_mbps", "bottleneck_bw_mbps"]:
            if c not in df.columns:
                raise KeyError(f"{path} missing required column: {c}")

        if "t_sec" not in df.columns:
            df["t_sec"] = np.arange(len(df), dtype=float)

        scenario = os.path.basename(path)

        for j, ctrl in enumerate(controllers):
            simdf = run_multiflow_controller(
                df=df,
                controller_name=ctrl,
                min_rate=args.min_rate,
                max_rate=args.max_rate,
                alpha=args.alpha,
                rng=np.random.default_rng(args.seed + idx * 100 + j),
                bundle=bundle,
                ppo_model=ppo_model
            )
            summaries.append(summarize_multiflow(scenario, ctrl, simdf))

    out_df = pd.DataFrame(summaries)
    os.makedirs("results", exist_ok=True)
    csv_path = "results/multiflow_fairness_summary.csv"
    json_path = "results/multiflow_fairness_summary.json"

    out_df.to_csv(csv_path, index=False)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(out_df.to_dict(orient="records"), f, indent=2)

    print("Wrote:")
    print(" -", csv_path)
    print(" -", json_path)

    overall = (
        out_df.groupby("controller", as_index=False)[
            ["avg_total_throughput", "avg_fairness", "avg_queue_bytes", "p95_queue_bytes", "avg_send_rate"]
        ]
        .mean()
        .sort_values("avg_total_throughput", ascending=False)
    )

    print("\n=== Multi-flow Overall ===")
    print(overall.to_string(index=False))


if __name__ == "__main__":
    main()
