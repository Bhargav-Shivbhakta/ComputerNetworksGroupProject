#!/usr/bin/env python3
"""
ml/rl_env.py

Research-grade RL environment for congestion control (offline plant simulator).

- Episodes are drawn from processed scenario CSVs (data/processed/*_proc.csv)
- Agent controls send rate; plant sim produces achieved throughput + queue evolution.
- Optional: ML predictor (XGBoost) included as part of observation.

Obs (default):
  [
    thr_last,            # last achieved throughput (Mbps)
    q_bytes_last_norm,   # queue bytes normalized
    q_pkts_last_norm,    # queue packets normalized
    send_rate_last,      # current send rate (Mbps)
    pred_thr_1s,         # predicted future throughput (Mbps) from XGB
    bottleneck_bw        # bottleneck (Mbps)
  ]

Action space (discrete):
  0: -20%
  1: -10%
  2:  0%
  3: +10%
  4: +20%

Reward (tunable):
  throughput - lambda_q * queue_bytes_norm - lambda_delta * |delta_send_rate|

This is NOT ns-3 closed-loop yet — it's your RL training bridge with reproducibility.

Requires:
  pip install gymnasium stable-baselines3 xgboost joblib pandas numpy scikit-learn
"""

from __future__ import annotations

import os
import glob
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict, Any

import numpy as np
import pandas as pd

import gymnasium as gym
from gymnasium import spaces

from joblib import load


# ----------------------------
# Helpers: window featurization (matches your window dataset)
# ----------------------------
WINDOW = 10
SIGNALS = ["throughput_mbps", "queue_bytes", "queue_packets", "send_rate_mbps"]


def compute_slope(x: np.ndarray) -> float:
    if len(x) < 2:
        return 0.0
    return float((x[-1] - x[0]) / len(x))


def featurize_window(window_df: pd.DataFrame) -> Dict[str, float]:
    feat: Dict[str, float] = {}
    for s in SIGNALS:
        vals = window_df[s].values.astype(float)
        feat[f"{s}_mean"] = float(np.mean(vals))
        feat[f"{s}_std"] = float(np.std(vals))
        feat[f"{s}_min"] = float(np.min(vals))
        feat[f"{s}_max"] = float(np.max(vals))
        feat[f"{s}_last"] = float(vals[-1])
        feat[f"{s}_slope"] = compute_slope(vals)
    return feat


# ----------------------------
# Plant simulator
# ----------------------------
@dataclass
class PlantParams:
    # queue model
    q_capacity_bytes: float = 2_000_000.0  # queue cap (bytes) for clipping
    pkt_size_bytes: float = 1200.0         # assumed packet size
    dt: float = 0.1                        # seconds per step (from your logs)

    # noise / dynamics
    throughput_noise_std: float = 0.05     # relative noise on throughput
    queue_drain_gain: float = 1.0          # how fast queue drains when sending <= bottleneck


def plant_step(
    send_rate_mbps: float,
    bottleneck_mbps: float,
    q_bytes: float,
    params: PlantParams,
    rng: np.random.Generator
) -> Tuple[float, float, float]:
    """
    Simple plant:
    - Achieved throughput is capped by bottleneck and limited by queue behavior.
    - Queue increases when send_rate > bottleneck (excess accumulates).
    - Queue drains when send_rate < bottleneck (spare capacity drains queue).
    """
    # Base achieved throughput tries to be min(send_rate, bottleneck)
    base_thr = min(send_rate_mbps, bottleneck_mbps)

    # Add small noise (multiplicative)
    noise = rng.normal(0.0, params.throughput_noise_std)
    achieved_thr = max(0.0, base_thr * (1.0 + noise))

    # Convert Mbps to bytes per dt
    # Mbps -> bits/s -> bytes/s -> bytes/dt
    send_bytes = (send_rate_mbps * 1e6 / 8.0) * params.dt
    drain_bytes = (bottleneck_mbps * 1e6 / 8.0) * params.dt

    # Queue dynamics: excess accumulates, spare drains
    if send_bytes > drain_bytes:
        q_bytes = q_bytes + (send_bytes - drain_bytes)
    else:
        q_bytes = q_bytes - params.queue_drain_gain * (drain_bytes - send_bytes)

    q_bytes = float(np.clip(q_bytes, 0.0, params.q_capacity_bytes))
    q_pkts = float(q_bytes / params.pkt_size_bytes)

    return float(achieved_thr), q_bytes, q_pkts


# ----------------------------
# RL Environment
# ----------------------------
class CongestionControlEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(
        self,
        scenario_paths: List[str],
        xgb_model_path: Optional[str] = None,
        seed: int = 42,
        alpha_action: Optional[List[float]] = None,
        min_rate: float = 0.5,
        max_rate: float = 50.0,
        reward_lambda_q: float = 0.25,
        reward_lambda_delta: float = 0.05,
        q_norm_bytes: float = 200_000.0,  # normalization scale for queue bytes
        p: Optional[PlantParams] = None,
    ):
        super().__init__()

        if not scenario_paths:
            raise ValueError("scenario_paths is empty")

        self.scenario_paths = scenario_paths
        self.seed_value = seed
        self.rng = np.random.default_rng(seed)

        self.min_rate = float(min_rate)
        self.max_rate = float(max_rate)

        self.reward_lambda_q = float(reward_lambda_q)
        self.reward_lambda_delta = float(reward_lambda_delta)
        self.q_norm_bytes = float(q_norm_bytes)

        self.plant = p or PlantParams()

        # Action mapping (multiplicative updates)
        # defaults: [-20%, -10%, 0, +10%, +20%]
        self.action_scales = alpha_action or [0.8, 0.9, 1.0, 1.1, 1.2]
        self.action_space = spaces.Discrete(len(self.action_scales))

        # Observation: 6 floats
        high = np.array([np.inf] * 6, dtype=np.float32)
        self.observation_space = spaces.Box(-high, high, dtype=np.float32)

        # Optional XGB predictor
        self.predictor = None
        self.scaler = None
        self.feature_names = None
        if xgb_model_path:
            bundle = load(xgb_model_path)
            self.predictor = bundle["model"]
            self.scaler = bundle["scaler"]
            self.feature_names = bundle["feature_names"]

        # Episode state
        self.df: Optional[pd.DataFrame] = None
        self.scenario_name: str = ""
        self.idx: int = 0
        self.send_rate: float = 0.0
        self.q_bytes: float = 0.0
        self.q_pkts: float = 0.0
        self.last_thr: float = 0.0
        self.bw: float = 0.0

    def _load_random_scenario(self) -> None:
        path = self.rng.choice(self.scenario_paths)
        self.scenario_name = os.path.basename(path)

        df = pd.read_csv(path)

        required = ["throughput_mbps", "queue_bytes", "queue_packets", "send_rate_mbps", "bottleneck_bw_mbps"]
        for c in required:
            if c not in df.columns:
                raise KeyError(f"{path} missing required column: {c}")

        # clean
        df = df.replace([np.inf, -np.inf], np.nan).dropna().reset_index(drop=True)
        if len(df) <= WINDOW + 2:
            raise ValueError(f"{path} too short after cleaning: {len(df)} rows")

        self.df = df

    def _predict_thr_1s(self) -> float:
        # If no predictor, return last throughput as a naive "prediction"
        if self.predictor is None:
            return float(self.last_thr)

        assert self.df is not None
        if self.idx < WINDOW:
            return float(self.last_thr)

        window_df = self.df.iloc[self.idx - WINDOW : self.idx]
        feat = featurize_window(window_df)

        # Build row in correct feature order
        x_row = np.array([feat.get(k, 0.0) for k in self.feature_names], dtype=np.float32).reshape(1, -1)

        # keep feature names by using DataFrame (prevents sklearn warning spam)
        x_df = pd.DataFrame(x_row, columns=self.feature_names)
        x_scaled = self.scaler.transform(x_df)

        # predictor is xgboost Booster (trained with DMatrix), handle both cases robustly
        try:
            import xgboost as xgb
            d = xgb.DMatrix(x_scaled, feature_names=self.feature_names)
            pred = float(self.predictor.predict(d)[0])
        except Exception:
            pred = float(self.predictor.predict(x_scaled)[0])

        return pred

    def _get_obs(self) -> np.ndarray:
        pred = self._predict_thr_1s()

        q_bytes_norm = self.q_bytes / self.q_norm_bytes
        q_pkts_norm = self.q_pkts / (self.q_norm_bytes / self.plant.pkt_size_bytes)

        obs = np.array(
            [
                self.last_thr,
                q_bytes_norm,
                q_pkts_norm,
                self.send_rate,
                pred,
                self.bw,
            ],
            dtype=np.float32,
        )
        return obs

    def reset(self, *, seed: Optional[int] = None, options: Optional[Dict[str, Any]] = None):
        if seed is not None:
            self.seed_value = seed
            self.rng = np.random.default_rng(seed)

        self._load_random_scenario()
        assert self.df is not None

        # Start index after we have a window
        self.idx = WINDOW

        # Initialize from scenario
        self.send_rate = float(self.df["send_rate_mbps"].iloc[0])
        self.send_rate = float(np.clip(self.send_rate, self.min_rate, self.max_rate))

        self.bw = float(self.df["bottleneck_bw_mbps"].iloc[0])

        # start with scenario’s observed values (helps realism)
        self.q_bytes = float(self.df["queue_bytes"].iloc[self.idx - 1])
        self.q_pkts = float(self.df["queue_packets"].iloc[self.idx - 1])
        self.last_thr = float(self.df["throughput_mbps"].iloc[self.idx - 1])

        return self._get_obs(), {"scenario": self.scenario_name}

    def step(self, action: int):
        assert self.df is not None

        prev_send = self.send_rate

        # Apply action (multiplicative)
        scale = self.action_scales[int(action)]
        self.send_rate = float(np.clip(prev_send * scale, self.min_rate, self.max_rate))

        # Optionally let bottleneck vary slightly over time using scenario info (or keep constant)
        # Here: keep constant per scenario for stability.
        bw = self.bw

        # Plant step -> achieved throughput + updated queue
        achieved_thr, self.q_bytes, self.q_pkts = plant_step(
            send_rate_mbps=self.send_rate,
            bottleneck_mbps=bw,
            q_bytes=self.q_bytes,
            params=self.plant,
            rng=self.rng,
        )
        self.last_thr = achieved_thr

        # Reward: throughput - queue penalty - rate-change penalty
        q_pen = (self.q_bytes / self.q_norm_bytes)
        delta_pen = abs(self.send_rate - prev_send) / max(1e-6, self.max_rate)

        reward = float(
            achieved_thr
            - self.reward_lambda_q * q_pen
            - self.reward_lambda_delta * delta_pen
        )

        # Advance time; episode ends at end of scenario
        self.idx += 1
        terminated = self.idx >= len(self.df) - 1
        truncated = False

        info = {
            "scenario": self.scenario_name,
            "achieved_thr": achieved_thr,
            "send_rate": self.send_rate,
            "q_bytes": self.q_bytes,
            "q_pkts": self.q_pkts,
            "bw": bw,
        }

        return self._get_obs(), reward, terminated, truncated, info


def list_scenarios(glob_pattern: str) -> List[str]:
    paths = sorted(glob.glob(glob_pattern))
    if not paths:
        raise FileNotFoundError(f"No files matched glob: {glob_pattern}")
    return paths
