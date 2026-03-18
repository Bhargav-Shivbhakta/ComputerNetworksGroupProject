# Learning-Based Congestion Control (Computer Networks Group Project)

## Overview

This project explores **machine learning and reinforcement learning approaches to congestion control**.  
We develop a hybrid framework that combines:

- **Supervised traffic prediction (XGBoost)**
- **Reinforcement learning rate control (PPO)**
- **Classical networking baselines (EWMA, GCC-like, BBR-like)**

The system is evaluated using **network traces derived from ns-3 simulations** across multiple scenarios including bandwidth variations, RTT differences, and queue configurations.

---

# Project Architecture

Network traces (ns-3)
        ↓
Feature extraction (sliding window)
        ↓
ML predictor (XGBoost)
        ↓
RL controller (PPO)
        ↓
Send-rate adaptation
        ↓
Offline network simulator

---

# Controllers Evaluated

| Controller | Description                                          | 
|------------|------------------------------------------------------| 
| FIXED_SR   | Constant sending rate baseline                       |
| EWMA_SR    | Reactive controller using exponential moving average |
| ML_PRED    | Predictive controller using XGBoost                  |
| GCC_LIKE   | Delay/queue reactive congestion control              |
| BBR_LIKE   | Model-based bandwidth estimation controller          |
| PPO_RL     | Reinforcement learning controller                    |

---

# Evaluation Metrics

The controllers are evaluated using:

- Average throughput
- Queue size
- p95 queue occupancy
- Jain fairness index
- Send rate stability

---

# Experiment Types

### Single-flow experiments
Evaluate throughput vs queue tradeoff.

### Multi-flow fairness experiments
Evaluate fairness under:

- Equal start conditions
- Late joining flows
- Unequal initial sending rates
- RTT mismatch scenarios

---

# Key Results

Typical aggregated results:

| Controller | Throughput | Queue    |
|------------|------------|----------|
| PPO_RL     | Best overall tradeoff |
| BBR_LIKE   | Highest throughput    |
| EWMA_SR    | Lowest queue          |
| GCC_LIKE   | Balanced performance  |

Fairness tests show that:

- All controllers remain fair under symmetric conditions
- RTT mismatch reveals fairness degradation for some classical methods
- PPO and GCC maintain strong fairness

---

# Repository Structure


configs/
data/
docs/
eval/
logs/
ml/
paper/
results/
scripts/


### Important directories


ml/
train_xgb.py
train_ppo.py
controller_predictive.py
controller_gcc_like.py
controller_bbr_like.py
eval_all_baselines.py
eval_multiflow_fairness.py
eval_multiflow_fairness_asym.py

results/
all_baselines_summary.csv
multiflow_fairness_summary.csv
multiflow_fairness_asym_summary.csv

paper/figures/
final_throughput_comparison.png
final_queue_comparison_log.png
final_tradeoff_logqueue.png


---

# Running Experiments

## Train predictor


python3 ml/train_xgb.py


## Train PPO controller


python3 ml/train_ppo.py


## Evaluate controllers


python3 ml/eval_all_baselines.py


## Multi-flow fairness evaluation


python3 ml/eval_multiflow_fairness.py
python3 ml/eval_multiflow_fairness_asym.py


---

# Figures

Plots are generated using scripts in:


scripts/


Examples:


python3 scripts/plot_final_results.py
python3 scripts/plot_multiflow_fairness.py


---

# Technologies Used

- Python
- XGBoost
- Stable-Baselines3 (PPO)
- NumPy / Pandas
- Matplotlib / Seaborn
- ns-3 generated network traces

---

# Authors

Computer Networks Group Project

Graduate Course Project  
Machine Learning for Networking

---

# License

This project is intended for academic research and educational use.
EOF

This command replaces your README with a full professional project description.
