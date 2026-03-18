import pandas as pd
import matplotlib.pyplot as plt

# Load PPO reward data
df = pd.read_csv("ppo_training_rewards.csv")

# Smooth reward using rolling average
window = 5
df["reward_smooth"] = df["reward"].rolling(window=window, min_periods=1).mean()

plt.figure(figsize=(6,4))

# Raw reward
plt.plot(df["step"], df["reward"],
         color="lightblue",
         linewidth=1,
         label="Raw Reward")

# Smoothed reward
plt.plot(df["step"], df["reward_smooth"],
         color="blue",
         linewidth=2.5,
         label="Smoothed Reward")

plt.xlabel("Training Steps")
plt.ylabel("Reward")
plt.title("PPO Training Curve")

plt.grid(alpha=0.3)
plt.legend()

plt.tight_layout()
plt.savefig("ppo_training_curve.png", dpi=300)
plt.show()
