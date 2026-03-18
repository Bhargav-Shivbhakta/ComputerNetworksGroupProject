"""
Feature extraction helpers for SPRCC dataset.
Input: CSV timeseries from ns-3 logs (time-res, metrics).
Output: sliding-window features + future bandwidth label.
"""
import pandas as pd
import numpy as np

def load_trace(csv_path):
    return pd.read_csv(csv_path)

def compute_features(df, window_ms=1000, res_ms=100):
    # simple sliding window aggregator
    steps = int(window_ms / res_ms)
    cols = ['sent_bytes','acked_bytes','rtt_ms','loss_rate','queue_len_pkts','send_rate_mbps']
    out = []
    for i in range(steps, len(df)):
        w = df.iloc[i-steps:i]
        feat = {}
        feat['sent_bytes_mean'] = w['sent_bytes'].mean()
        feat['acked_bytes_mean'] = w['acked_bytes'].mean()
        feat['rtt_mean'] = w['rtt_ms'].mean()
        feat['delay_grad'] = w['rtt_ms'].diff().mean()
        feat['loss_mean'] = w['loss_rate'].mean()
        feat['queue_mean'] = w['queue_len_pkts'].mean()
        feat['send_rate'] = w['send_rate_mbps'].iloc[-1]
        out.append(feat)
    return pd.DataFrame(out)
