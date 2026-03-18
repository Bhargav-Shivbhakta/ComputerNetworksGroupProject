#!/usr/bin/env python3
"""
ml/eval_all_baselines.py

Self-contained evaluator for all controller baselines on processed CSV traces.

Controllers:
- FIXED_SR
- EWMA_SR
- ML_PRED
- PPO_RL
- GCC_LIKE
- BBR_LIKE

Input requirements for processed CSV:
- t_sec
- throughput_mbps
- send_rate_mbps
- bottleneck_bw_mbps

Optional:
- queue_bytes
- queue_packets

Outputs:
- results/all_baselines_summary.csv
- results/all_baselines_summary.json

Example:
python3 ml/eval_all_baselines.py \
  --glob "data/processed/bw5M_rtt40_q50p_sr*_proc.csv" \
  --xgb_model ml/models/xgb_windowed_y_throughput_1s_y_throughput_1s.joblib \
  --ppo_model ml/rl_models/ppo_cc_q10/final_model.zip \
  --alpha 0.6 \
  --min_rate 2.0 \
  --max_rate 50
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


def plant_step(bottleneck: float, send_rate: float, q_bytes: float, seed_noise: float) -> tuple:
    """
    Simple offline plant model.
    """
    achieved = max(0.0, min(send_rate, bottleneck) + seed_noise)
    excess = send_rate - bottleneck
    q_bytes_delta = excess * 8000.0
    q_bytes = max(0.0, q_bytes + q_bytes_delta)
    q_packets = q_bytes / 1200.0 if q_bytes > 0 else 0.0
    return achieved, q_bytes, q_packets


def summarize_run(controller_name: str, scenario_name: str, simdf: pd.DataFrame) -> dict:
    return {
        "scenario": scenario_name,
        "controller": controller_name,
        "n": int(len(simdf)),
        "avg_throughput": float(simdf["achieved_throughput_mbps"].mean()),
        "std_throughput": float(simdf["achieved_throughput_mbps"].std(ddof=0)),
        "avg_send_rate": float(simdf["send_rate_mbps"].mean()),
        "std_send_rate": float(simdf["send_rate_mbps"].std(ddof=0)),
        "avg_queue_bytes": float(simdf["queue_bytes"].mean()),
        "p95_queue_bytes": float(np.percentile(simdf["queue_bytes"], 95)),
        "avg_queue_packets": float(simdf["queue_packets"].mean()),
        "queue_nonzero_frac": float((simdf["queue_bytes"] > 0).mean()),
    }


def load_xgb_bundle(xgb_model_path: str):
    bundle = load(xgb_model_path)
    if not isinstance(bundle, dict):
        raise ValueError("XGB model joblib must contain dict with keys model/scaler/feature_names")
    for k in ("model", "scaler", "feature_names"):
        if k not in bundle:
            raise KeyError(f"XGB bundle missing key: {k}")
    return bundle


def predict_xgb(bundle: dict, window_rows: list) -> float:
    wdf = pd.DataFrame(window_rows)
    feat = featurize_window_df(wdf)
    feature_names = bundle["feature_names"]
    row_df = pd.DataFrame([{k: feat.get(k, 0.0) for k in feature_names}])
    x_scaled = bundle["scaler"].transform(row_df)
    dmatrix_cls = type(bundle["model"]).__module__.startswith("xgboost")
    # joblib stores xgb Booster; use DMatrix if available
    try:
        import xgboost as xgb
        dtest = xgb.DMatrix(x_scaled, feature_names=feature_names)
        pred = float(bundle["model"].predict(dtest)[0])
    except Exception:
        pred = float(bundle["model"].predict(x_scaled)[0])
    return pred


def build_initial_history(df: pd.DataFrame) -> list:
    init_thr = float(df["throughput_mbps"].iloc[0])
    init_qb = float(df["queue_bytes"].iloc[0]) if "queue_bytes" in df.columns else 0.0
    init_qp = float(df["queue_packets"].iloc[0]) if "queue_packets" in df.columns else 0.0
    init_sr = float(df["send_rate_mbps"].iloc[0])
    row = {
        "throughput_mbps": init_thr,
        "queue_bytes": init_qb,
        "queue_packets": init_qp,
        "send_rate_mbps": init_sr,
    }
    return [row.copy() for _ in range(WINDOW)]


def run_fixed(df: pd.DataFrame, min_rate: float, max_rate: float, rng: np.random.Generator) -> pd.DataFrame:
    send_rate = float(np.clip(df["send_rate_mbps"].iloc[0], min_rate, max_rate))
    q_bytes = 0.0
    rows = []

    for i in range(len(df)):
        t_sec = float(df["t_sec"].iloc[i])
        bottleneck = float(df["bottleneck_bw_mbps"].iloc[i])
        noise = rng.normal(0.0, 0.05 * max(1e-6, bottleneck))
        achieved, q_bytes, q_packets = plant_step(bottleneck, send_rate, q_bytes, noise)

        rows.append({
            "t_sec": t_sec,
            "send_rate_mbps": send_rate,
            "achieved_throughput_mbps": achieved,
            "queue_bytes": q_bytes,
            "queue_packets": q_packets,
            "bottleneck_bw_mbps": bottleneck,
        })

    return pd.DataFrame(rows)


def run_ewma(df: pd.DataFrame, alpha: float, min_rate: float, max_rate: float, rng: np.random.Generator) -> pd.DataFrame:
    send_rate = float(np.clip(df["send_rate_mbps"].iloc[0], min_rate, max_rate))
    q_bytes = 0.0
    ewma_thr = float(df["throughput_mbps"].iloc[0])
    rows = []

    for i in range(len(df)):
        t_sec = float(df["t_sec"].iloc[i])
        bottleneck = float(df["bottleneck_bw_mbps"].iloc[i])
        noise = rng.normal(0.0, 0.05 * max(1e-6, bottleneck))
        achieved, q_bytes, q_packets = plant_step(bottleneck, send_rate, q_bytes, noise)

        ewma_thr = (1.0 - alpha) * ewma_thr + alpha * achieved
        send_rate = float(np.clip(ewma_thr, min_rate, max_rate))

        rows.append({
            "t_sec": t_sec,
            "send_rate_mbps": send_rate,
            "achieved_throughput_mbps": achieved,
            "queue_bytes": q_bytes,
            "queue_packets": q_packets,
            "bottleneck_bw_mbps": bottleneck,
        })

    return pd.DataFrame(rows)


def run_ml_pred(df: pd.DataFrame, xgb_bundle: dict, alpha: float, min_rate: float, max_rate: float,
                rng: np.random.Generator) -> pd.DataFrame:
    send_rate = float(np.clip(df["send_rate_mbps"].iloc[0], min_rate, max_rate))
    q_bytes = 0.0
    history = deque(build_initial_history(df), maxlen=WINDOW)
    rows = []

    for i in range(len(df)):
        t_sec = float(df["t_sec"].iloc[i])
        bottleneck = float(df["bottleneck_bw_mbps"].iloc[i])

        pred_thr = predict_xgb(xgb_bundle, list(history))
        target_rate = max(min_rate, pred_thr)
        send_rate = float(np.clip((1.0 - alpha) * send_rate + alpha * target_rate, min_rate, max_rate))

        noise = rng.normal(0.0, 0.05 * max(1e-6, bottleneck))
        achieved, q_bytes, q_packets = plant_step(bottleneck, send_rate, q_bytes, noise)

        history.append({
            "throughput_mbps": achieved,
            "queue_bytes": q_bytes,
            "queue_packets": q_packets,
            "send_rate_mbps": send_rate,
        })

        rows.append({
            "t_sec": t_sec,
            "send_rate_mbps": send_rate,
            "achieved_throughput_mbps": achieved,
            "queue_bytes": q_bytes,
            "queue_packets": q_packets,
            "predicted_throughput_mbps": pred_thr,
            "bottleneck_bw_mbps": bottleneck,
        })

    return pd.DataFrame(rows)


def run_ppo(df: pd.DataFrame, xgb_bundle: dict, ppo_model_path: str, min_rate: float, max_rate: float,
            rng: np.random.Generator) -> pd.DataFrame:
    if not HAS_SB3:
        raise ImportError("stable_baselines3 is not installed or unavailable")

    model = PPO.load(ppo_model_path, device="cpu")
    send_rate = float(np.clip(df["send_rate_mbps"].iloc[0], min_rate, max_rate))
    q_bytes = 0.0
    history = deque(build_initial_history(df), maxlen=WINDOW)
    rows = []

    # assumed action mapping used by your PPO setup
    action_map = {
        0: -0.20,
        1: -0.10,
        2:  0.00,
        3:  0.10,
        4:  0.20,
    }

    for i in range(len(df)):
        t_sec = float(df["t_sec"].iloc[i])
        bottleneck = float(df["bottleneck_bw_mbps"].iloc[i])

        pred_thr = predict_xgb(xgb_bundle, list(history))
        last = history[-1]
        obs = np.array([
            float(last["throughput_mbps"]),
            float(last["queue_bytes"]),
            float(last["queue_packets"]),
            float(last["send_rate_mbps"]),
            float(pred_thr),
            float(bottleneck),
        ], dtype=np.float32)

        action, _ = model.predict(obs, deterministic=True)
        try:
            action_idx = int(np.asarray(action).item())
        except Exception:
            action_idx = int(action)

        delta = action_map.get(action_idx, 0.0)
        send_rate = float(np.clip(send_rate * (1.0 + delta), min_rate, max_rate))

        noise = rng.normal(0.0, 0.05 * max(1e-6, bottleneck))
        achieved, q_bytes, q_packets = plant_step(bottleneck, send_rate, q_bytes, noise)

        history.append({
            "throughput_mbps": achieved,
            "queue_bytes": q_bytes,
            "queue_packets": q_packets,
            "send_rate_mbps": send_rate,
        })

        rows.append({
            "t_sec": t_sec,
            "send_rate_mbps": send_rate,
            "achieved_throughput_mbps": achieved,
            "queue_bytes": q_bytes,
            "queue_packets": q_packets,
            "predicted_throughput_mbps": pred_thr,
            "ppo_action_idx": action_idx,
            "bottleneck_bw_mbps": bottleneck,
        })

    return pd.DataFrame(rows)


def run_gcc_like(df: pd.DataFrame, min_rate: float, max_rate: float, rng: np.random.Generator,
                 queue_threshold_bytes: float = 10000.0, ai_step: float = 0.15,
                 md_factor: float = 0.85) -> pd.DataFrame:
    send_rate = float(np.clip(df["send_rate_mbps"].iloc[0], min_rate, max_rate))
    q_bytes = 0.0
    achieved = float(df["throughput_mbps"].iloc[0])
    rows = []

    for i in range(len(df)):
        t_sec = float(df["t_sec"].iloc[i])
        bottleneck = float(df["bottleneck_bw_mbps"].iloc[i])

        utilization_ratio = achieved / max(send_rate, 1e-6)
        if q_bytes > queue_threshold_bytes:
            send_rate *= md_factor
        else:
            if utilization_ratio > 0.85:
                send_rate += ai_step
            else:
                send_rate += 0.05

        send_rate = float(np.clip(send_rate, min_rate, max_rate))
        noise = rng.normal(0.0, 0.05 * max(1e-6, bottleneck))
        achieved, q_bytes, q_packets = plant_step(bottleneck, send_rate, q_bytes, noise)

        rows.append({
            "t_sec": t_sec,
            "send_rate_mbps": send_rate,
            "achieved_throughput_mbps": achieved,
            "queue_bytes": q_bytes,
            "queue_packets": q_packets,
            "bottleneck_bw_mbps": bottleneck,
        })

    return pd.DataFrame(rows)


def run_bbr_like(df: pd.DataFrame, min_rate: float, max_rate: float, rng: np.random.Generator,
                 window: int = 8, queue_threshold_bytes: float = 12000.0,
                 probe_up_gain: float = 1.05, drain_gain: float = 0.95) -> pd.DataFrame:
    send_rate = float(np.clip(df["send_rate_mbps"].iloc[0], min_rate, max_rate))
    q_bytes = 0.0
    achieved = float(df["throughput_mbps"].iloc[0])
    achieved_hist = [achieved]
    phase_cycle = ["probe_up", "drain", "cruise", "cruise"]
    phase_idx = 0
    rows = []

    for i in range(len(df)):
        t_sec = float(df["t_sec"].iloc[i])
        bottleneck = float(df["bottleneck_bw_mbps"].iloc[i])

        recent = achieved_hist[-window:] if len(achieved_hist) >= 1 else [achieved]
        bw_est = max(recent)
        phase = phase_cycle[phase_idx % len(phase_cycle)]
        phase_idx += 1

        if phase == "probe_up":
            target_rate = bw_est * probe_up_gain
        elif phase == "drain":
            target_rate = bw_est * drain_gain
        else:
            target_rate = bw_est

        if q_bytes > queue_threshold_bytes:
            target_rate *= 0.92

        send_rate = 0.75 * send_rate + 0.25 * target_rate
        send_rate = float(np.clip(send_rate, min_rate, max_rate))

        noise = rng.normal(0.0, 0.05 * max(1e-6, bottleneck))
        achieved, q_bytes, q_packets = plant_step(bottleneck, send_rate, q_bytes, noise)
        achieved_hist.append(achieved)

        rows.append({
            "t_sec": t_sec,
            "send_rate_mbps": send_rate,
            "achieved_throughput_mbps": achieved,
            "queue_bytes": q_bytes,
            "queue_packets": q_packets,
            "bw_est_mbps": bw_est,
            "phase": phase,
            "bottleneck_bw_mbps": bottleneck,
        })

    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", required=True, help="Glob for processed CSVs")
    ap.add_argument("--xgb_model", required=True, help="XGB joblib bundle")
    ap.add_argument("--ppo_model", default="", help="Optional PPO model path")
    ap.add_argument("--alpha", type=float, default=0.6, help="EWMA/ML update strength")
    ap.add_argument("--min_rate", type=float, default=2.0)
    ap.add_argument("--max_rate", type=float, default=50.0)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    files = sorted(glob.glob(args.glob))
    if not files:
        raise FileNotFoundError(f"No files matched glob: {args.glob}")

    xgb_bundle = load_xgb_bundle(args.xgb_model)

    summaries = []
    for idx, path in enumerate(files):
        df = pd.read_csv(path).replace([np.inf, -np.inf], np.nan).dropna()
        required = ["throughput_mbps", "send_rate_mbps", "bottleneck_bw_mbps"]
        for c in required:
            if c not in df.columns:
                raise KeyError(f"{path} missing required column: {c}")

        if "t_sec" not in df.columns:
            df["t_sec"] = np.arange(len(df), dtype=float)
        if "queue_bytes" not in df.columns:
            df["queue_bytes"] = 0.0
        if "queue_packets" not in df.columns:
            df["queue_packets"] = 0.0

        scenario_name = os.path.basename(path)

        # independent RNG per controller per scenario for reproducibility
        sim_fixed = run_fixed(df, args.min_rate, args.max_rate, np.random.default_rng(args.seed + idx * 10 + 1))
        sim_ewma = run_ewma(df, args.alpha, args.min_rate, args.max_rate, np.random.default_rng(args.seed + idx * 10 + 2))
        sim_ml = run_ml_pred(df, xgb_bundle, args.alpha, args.min_rate, args.max_rate, np.random.default_rng(args.seed + idx * 10 + 3))
        sim_gcc = run_gcc_like(df, args.min_rate, args.max_rate, np.random.default_rng(args.seed + idx * 10 + 4))
        sim_bbr = run_bbr_like(df, args.min_rate, args.max_rate, np.random.default_rng(args.seed + idx * 10 + 5))

        summaries.append(summarize_run("FIXED_SR", scenario_name, sim_fixed))
        summaries.append(summarize_run("EWMA_SR", scenario_name, sim_ewma))
        summaries.append(summarize_run("ML_PRED", scenario_name, sim_ml))
        summaries.append(summarize_run("GCC_LIKE", scenario_name, sim_gcc))
        summaries.append(summarize_run("BBR_LIKE", scenario_name, sim_bbr))

        if args.ppo_model:
            try:
                sim_ppo = run_ppo(df, xgb_bundle, args.ppo_model, args.min_rate, args.max_rate,
                                  np.random.default_rng(args.seed + idx * 10 + 6))
                summaries.append(summarize_run("PPO_RL", scenario_name, sim_ppo))
            except Exception as e:
                print(f"[WARN] PPO_RL skipped for {scenario_name}: {e}")

    summary_df = pd.DataFrame(summaries)
    os.makedirs("results", exist_ok=True)

    csv_path = "results/all_baselines_summary.csv"
    json_path = "results/all_baselines_summary.json"

    summary_df.to_csv(csv_path, index=False)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary_df.to_dict(orient="records"), f, indent=2)

    print("Wrote:")
    print(f" - {csv_path}")
    print(f" - {json_path}")

    overall = (
        summary_df.groupby("controller", as_index=False)[
            ["avg_throughput", "std_throughput", "avg_queue_bytes", "p95_queue_bytes", "queue_nonzero_frac", "avg_send_rate"]
        ]
        .mean()
        .sort_values("avg_throughput", ascending=False)
    )

    print("\n=== Overall (mean across scenarios) ===")
    print(overall.to_string(index=False))


if __name__ == "__main__":
    main()
