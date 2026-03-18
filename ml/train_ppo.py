#!/usr/bin/env python3
"""
ml/train_ppo.py

Research-grade PPO training for congestion control RL.

Features:
- Scenario-level train/test split (no leakage)
- Fixed seeds for reproducibility
- EvalCallback saves best model
- TensorBoard logging
- Records basic rollout stats

Run example:
  python3 ml/train_ppo.py \
    --glob "data/processed/*_proc.csv" \
    --xgb_model "ml/models/xgb_windowed_y_throughput_1s_y_throughput_1s.joblib" \
    --total_timesteps 300000

Requires:
  pip install stable-baselines3 gymnasium tensorboard
"""

from __future__ import annotations

import os
import argparse
import random
from typing import List, Tuple

import numpy as np

from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv
from stable_baselines3.common.callbacks import EvalCallback
from stable_baselines3.common.monitor import Monitor

from ml.rl_env import CongestionControlEnv, list_scenarios


def set_seeds(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)


def split_scenarios(paths: List[str], test_frac: float, seed: int) -> Tuple[List[str], List[str]]:
    rng = np.random.default_rng(seed)
    idx = np.arange(len(paths))
    rng.shuffle(idx)

    n_test = max(1, int(round(len(paths) * test_frac)))
    test_idx = set(idx[:n_test].tolist())
    train = [p for i, p in enumerate(paths) if i not in test_idx]
    test = [p for i, p in enumerate(paths) if i in test_idx]

    return train, test


def make_env(paths: List[str], xgb_model: str, seed: int, **env_kwargs):
    def _thunk():
        env = CongestionControlEnv(
            scenario_paths=paths,
            xgb_model_path=xgb_model if xgb_model else None,
            seed=seed,
            **env_kwargs
        )
        return Monitor(env)
    return _thunk


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument("--glob", required=True, help='Scenario glob e.g. "data/processed/*_proc.csv"')
    ap.add_argument("--xgb_model", default="", help="Optional XGB joblib model used inside observation")

    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--test_frac", type=float, default=0.2)

    ap.add_argument("--total_timesteps", type=int, default=300_000)
    ap.add_argument("--n_steps", type=int, default=2048)
    ap.add_argument("--batch_size", type=int, default=256)
    ap.add_argument("--gamma", type=float, default=0.99)
    ap.add_argument("--learning_rate", type=float, default=3e-4)

    # env knobs
    ap.add_argument("--alpha_q", type=float, default=0.25, help="queue penalty weight")
    ap.add_argument("--alpha_delta", type=float, default=0.05, help="send-rate change penalty weight")
    ap.add_argument("--min_rate", type=float, default=0.5)
    ap.add_argument("--max_rate", type=float, default=50.0)
    ap.add_argument("--q_norm_bytes", type=float, default=200_000.0)

    # output
    ap.add_argument("--out_dir", default="ml/rl_models")
    ap.add_argument("--run_name", default="ppo_cc")

    args = ap.parse_args()

    set_seeds(args.seed)
    os.makedirs(args.out_dir, exist_ok=True)

    # Load scenario list
    all_paths = list_scenarios(args.glob)
    train_paths, test_paths = split_scenarios(all_paths, args.test_frac, args.seed)

    print(f"Total scenarios: {len(all_paths)}")
    print(f"Train scenarios: {len(train_paths)}")
    print(f"Test scenarios : {len(test_paths)}")

    # Log directories
    tb_dir = os.path.join(args.out_dir, "tb")
    log_dir = os.path.join(args.out_dir, args.run_name)
    os.makedirs(tb_dir, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)

    env_kwargs = dict(
        reward_lambda_q=args.alpha_q,
        reward_lambda_delta=args.alpha_delta,
        min_rate=args.min_rate,
        max_rate=args.max_rate,
        q_norm_bytes=args.q_norm_bytes,
    )

    # Vec envs
    train_env = DummyVecEnv([make_env(train_paths, args.xgb_model, args.seed, **env_kwargs)])
    eval_env = DummyVecEnv([make_env(test_paths, args.xgb_model, args.seed + 999, **env_kwargs)])

    # Eval callback
    eval_cb = EvalCallback(
        eval_env,
        best_model_save_path=log_dir,
        log_path=log_dir,
        eval_freq=10_000,     # every 10k steps
        n_eval_episodes=10,
        deterministic=True,
        render=False
    )

    model = PPO(
        "MlpPolicy",
        train_env,
        verbose=1,
        seed=args.seed,
        tensorboard_log=tb_dir,
        n_steps=args.n_steps,
        batch_size=args.batch_size,
        gamma=args.gamma,
        learning_rate=args.learning_rate,
    )

    print("\nTraining PPO...")
    model.learn(
        total_timesteps=args.total_timesteps,
        callback=eval_cb,
        tb_log_name=args.run_name,
        progress_bar=True
    )

    final_path = os.path.join(log_dir, "final_model.zip")
    model.save(final_path)
    print(f"\nSaved final model: {final_path}")
    print(f"Best model (if improved) saved under: {log_dir}")


if __name__ == "__main__":
    main()
