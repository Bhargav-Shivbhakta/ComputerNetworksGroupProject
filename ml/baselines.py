#!/usr/bin/env python3
"""
ml/baselines.py

Classical forecasting baselines for avail_bw_mbps (future throughput label).

Baselines implemented (per-trace, sequential):
1) EWMA on throughput_mbps
2) Naive last-value (predict future = current throughput)
3) Linear trend extrapolation using last K points

These baselines are intentionally simple and interpretable.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Tuple, Optional, List
import numpy as np
import pandas as pd


def rmse_mae(y_true: np.ndarray, y_pred: np.ndarray) -> Tuple[float, float]:
    if len(y_true) == 0:
        return float("nan"), float("nan")
    err = y_true - y_pred
    rmse = float(np.sqrt(np.mean(err ** 2)))
    mae = float(np.mean(np.abs(err)))
    return rmse, mae


@dataclass
class BaseConfig:
    warmup: int = 5
    feature_col: str = "throughput_mbps"
    label_col: str = "avail_bw_mbps"


class NaiveLastValueForecaster:
    """
    Predict yhat_t = x_t (current throughput) for future label y_t.
    """
    def __init__(self, cfg: BaseConfig):
        self.cfg = cfg

    def eval_on_trace_df(self, df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        d = df[[self.cfg.feature_col, self.cfg.label_col]].replace([np.inf, -np.inf], np.nan).dropna()
        if d.empty:
            return np.array([]), np.array([])
        x = d[self.cfg.feature_col].to_numpy(dtype=float)
        y = d[self.cfg.label_col].to_numpy(dtype=float)
        yhat = x.copy()

        w = self.cfg.warmup
        if len(y) <= w:
            return np.array([]), np.array([])
        return y[w:], yhat[w:]


@dataclass
class EwmaConfig(BaseConfig):
    alpha: float = 0.2


class EwmaForecaster:
    """
    EWMA forecaster:
      ewma_t = alpha*x_t + (1-alpha)*ewma_{t-1}
      yhat_t = ewma_t
    """
    def __init__(self, cfg: EwmaConfig):
        if not (0.0 < cfg.alpha <= 1.0):
            raise ValueError("alpha must be in (0,1].")
        self.cfg = cfg

    def predict_series(self, x: np.ndarray) -> np.ndarray:
        if len(x) == 0:
            return np.array([], dtype=float)
        ewma = np.zeros_like(x, dtype=float)
        ewma[0] = float(x[0])
        a = self.cfg.alpha
        for i in range(1, len(x)):
            ewma[i] = a * float(x[i]) + (1.0 - a) * ewma[i - 1]
        return ewma

    def eval_on_trace_df(self, df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        d = df[[self.cfg.feature_col, self.cfg.label_col]].replace([np.inf, -np.inf], np.nan).dropna()
        if d.empty:
            return np.array([]), np.array([])
        x = d[self.cfg.feature_col].to_numpy(dtype=float)
        y = d[self.cfg.label_col].to_numpy(dtype=float)
        yhat = self.predict_series(x)

        w = self.cfg.warmup
        if len(y) <= w:
            return np.array([]), np.array([])
        return y[w:], yhat[w:]


@dataclass
class LinearTrendConfig(BaseConfig):
    k: int = 10  # number of last points to fit trend


class LinearTrendForecaster:
    """
    Fit a simple line to last k samples of x and extrapolate one step:
      x_{t-k+1..t} -> fit slope, intercept
      yhat_t = x_t + slope
    """
    def __init__(self, cfg: LinearTrendConfig):
        if cfg.k < 2:
            raise ValueError("k must be >= 2")
        self.cfg = cfg

    def eval_on_trace_df(self, df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        d = df[[self.cfg.feature_col, self.cfg.label_col]].replace([np.inf, -np.inf], np.nan).dropna()
        if d.empty:
            return np.array([]), np.array([])

        x = d[self.cfg.feature_col].to_numpy(dtype=float)
        y = d[self.cfg.label_col].to_numpy(dtype=float)

        k = self.cfg.k
        yhat = np.zeros_like(y, dtype=float)

        for i in range(len(x)):
            if i < k - 1:
                yhat[i] = x[i]
                continue
            xs = x[i-k+1:i+1]
            t = np.arange(len(xs), dtype=float)
            # least squares slope
            t_mean = t.mean()
            xs_mean = xs.mean()
            denom = np.sum((t - t_mean) ** 2)
            slope = 0.0 if denom == 0 else float(np.sum((t - t_mean) * (xs - xs_mean)) / denom)
            yhat[i] = x[i] + slope  # one-step extrapolation

        w = self.cfg.warmup
        if len(y) <= w:
            return np.array([]), np.array([])
        return y[w:], yhat[w:]


def grid_search_ewma_alpha(
    df_list: List[pd.DataFrame],
    alphas: Optional[List[float]] = None,
    warmup: int = 5,
    feature_col: str = "throughput_mbps",
    label_col: str = "avail_bw_mbps",
) -> dict:
    if alphas is None:
        alphas = [0.05, 0.1, 0.2, 0.3, 0.5, 0.7]

    best = {"alpha": None, "rmse": float("inf"), "mae": None, "n": 0}

    for a in alphas:
        f = EwmaForecaster(EwmaConfig(alpha=a, warmup=warmup, feature_col=feature_col, label_col=label_col))
        y_all = []
        yhat_all = []
        for df in df_list:
            y, yhat = f.eval_on_trace_df(df)
            if len(y) == 0:
                continue
            y_all.append(y)
            yhat_all.append(yhat)

        if not y_all:
            continue

        y_cat = np.concatenate(y_all)
        yhat_cat = np.concatenate(yhat_all)
        rmse, mae = rmse_mae(y_cat, yhat_cat)
        if rmse < best["rmse"]:
            best = {"alpha": a, "rmse": rmse, "mae": mae, "n": int(len(y_cat))}

    return best
