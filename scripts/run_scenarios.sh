#!/usr/bin/env bash
# Run ns-3 scenarios listed in configs/scenario_grid.yaml
# Usage: ./scripts/run_scenarios.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCENARIO_YAML="${ROOT_DIR}/configs/scenario_grid.yaml"
NS3_DIR="${ROOT_DIR}/ns3/ns-allinone-3.39/ns-3.39"
OUT_DIR="${ROOT_DIR}/logs"
mkdir -p "${OUT_DIR}"

# require yq for YAML parsing; fall back to python if not available
if ! command -v yq >/dev/null 2>&1; then
  echo "yq not found. Using python YAML parser. Install yq for convenience."
fi

python3 - <<PY
import yaml, subprocess, os, sys
ROOT="${ROOT_DIR}"
with open("${SCENARIO_YAML}") as f:
    cfg=yaml.safe_load(f)
for s in cfg['scenarios']:
    name=s['name']
    out_prefix=os.path.join(ROOT, "logs", name)
    print("Running", name)
    # Customize the ns-3 invocation to run your scratch simulation (sprcc-sim)
    cmd = [
        "${NS3_DIR}/waf", "--run",
        "scratch/sprcc-sim --bw_mbps=%(bw)s --rtt_ms=%(rtt)s --queue_pct=%(q)s --sender_rate=%(sr)s --trace_type=%(tt)s --out_prefix=%(op)s" % {
            "bw": s['bw_mbps'], "rtt": s['rtt_ms'], "q": s['queue_pkts_pct'],
            "sr": s['sender_rate_mbps'], "tt": s['trace_type'], "op": out_prefix
        }
    ]
    print("CMD:", " ".join(cmd))
    subprocess.run(" ".join(cmd), shell=True, check=True)
PY
