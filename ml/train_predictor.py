#!/usr/bin/env python3
import os, glob, re
import numpy as np
import pandas as pd

from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.ensemble import RandomForestRegressor
from joblib import dump

RAW_DIR = "/home/bhargav/CNGP/data/raw"
MODEL_OUT = "/home/bhargav/CNGP/ml/models"
os.makedirs(MODEL_OUT, exist_ok=True)

TAG_RE = re.compile(r"bw(?P<bw>\d+)M_rtt(?P<rtt>\d+)_q(?P<q>\d+)p_sr(?P<sr>\d+)M")

INTERVAL = 0.1
WINDOW_STEPS = 10
HORIZON_STEPS = 10
T_MIN = 0.2
T_MAX = 10.0

FEATURE_COLS = ["throughput_kbps", "queue_packets", "queue_bytes"]
TARGET_COL = "queue_bytes"


def parse_meta(tag: str):
    m = TAG_RE.match(tag)
    if not m:
        return {}
    d = m.groupdict()
    return {
        "bw_Mbps": int(d["bw"]),
        "rtt_ms": int(d["rtt"]),
        "q_packets_cfg": int(d["q"]),
        "send_Mbps": int(d["sr"]),
    }


def make_supervised(df: pd.DataFrame):
    df = df[(df["t_sec"] >= T_MIN) & (df["t_sec"] <= T_MAX)].copy()
    df = df.sort_values("t_sec")

    X_list, y_list = [], []
    vals = df[FEATURE_COLS].to_numpy(dtype=np.float32)
    target = df[TARGET_COL].to_numpy(dtype=np.float32)

    n = len(df)
    for i in range(WINDOW_STEPS, n - HORIZON_STEPS):
        window = vals[i - WINDOW_STEPS:i].reshape(-1)
        y = target[i + HORIZON_STEPS]
        X_list.append(window)
        y_list.append(y)

    if not X_list:
        return None, None

    return np.vstack(X_list), np.array(y_list, dtype=np.float32)


def main():
    files = sorted(glob.glob(os.path.join(RAW_DIR, "bw*M_rtt*_q*p_sr*M.csv")))
    if not files:
        raise SystemExit(f"No grid CSVs found in {RAW_DIR}")

    X_all, y_all = [], []

    for path in files:
        tag = os.path.basename(path).replace(".csv", "")
        meta = parse_meta(tag)

        df = pd.read_csv(path, comment="#")
        needed = set(["t_sec"] + FEATURE_COLS + [TARGET_COL])
        if not needed.issubset(df.columns):
            continue

        X, y = make_supervised(df)
        if X is None:
            continue

        meta_vec = np.array(
            [meta.get("bw_Mbps", 0), meta.get("rtt_ms", 0),
             meta.get("q_packets_cfg", 0), meta.get("send_Mbps", 0)],
            dtype=np.float32,
        )
        meta_mat = np.repeat(meta_vec.reshape(1, -1), repeats=X.shape[0], axis=0)

        X_all.append(np.hstack([X, meta_mat]))
        y_all.append(y)

    X = np.vstack(X_all)
    y = np.concatenate(y_all)

    print("Dataset samples:", X.shape[0], "features:", X.shape[1])

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    model = RandomForestRegressor(
        n_estimators=300,
        random_state=42,
        n_jobs=-1,
        max_depth=None,
        min_samples_leaf=2,
    )
    model.fit(X_train, y_train)

    pred = model.predict(X_test)

    mae = mean_absolute_error(y_test, pred)
    mse = mean_squared_error(y_test, pred)
    rmse = float(np.sqrt(mse))
    r2 = r2_score(y_test, pred)

    print(f"MAE={mae:.2f} bytes  RMSE={rmse:.2f} bytes  R2={r2:.4f}")

    out_path = os.path.join(MODEL_OUT, "rf_queuebytes_1s.joblib")
    dump(model, out_path)
    print("Saved model:", out_path)


if __name__ == "__main__":
    main()
