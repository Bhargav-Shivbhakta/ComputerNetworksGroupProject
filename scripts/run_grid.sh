#!/usr/bin/env bash
set -euo pipefail

NS3_DIR="/home/bhargav/CNGP/ns3/ns-allinone-3.39/ns-3.39"
OUT_RAW="/home/bhargav/CNGP/data/raw"
OUT_RES="/home/bhargav/CNGP/results"
LOG_DIR="/home/bhargav/CNGP/logs"

mkdir -p "$OUT_RAW" "$OUT_RES" "$LOG_DIR"

# Grid (edit freely)
BWS=("2Mbps" "5Mbps" "10Mbps")
RTTS_MS=(20 40 80)           # RTT approx = 2*(pDelay + bDelay); we'll vary bDelay and keep pDelay small
QMAX=("25p" "50p" "100p")
SEND=("3Mbps" "6Mbps" "12Mbps")

PDELAY="2ms"
SIMTIME="10"
INTERVAL="0.1"

cd "$NS3_DIR"

# Build once
./ns3 build

TOTAL=0
FAIL=0

for bw in "${BWS[@]}"; do
  for rtt in "${RTTS_MS[@]}"; do
    # Convert RTT to bottleneck delay (rough): RTT ~= 2*(pDelay + bDelay)
    # pDelay=2ms => one-way pDelay=2ms. So RTT contribution from pDelay is ~4ms.
    # target bDelay one-way ~= (RTT/2 - pDelay)
    # Example RTT=40ms => one-way=20ms => bDelay ~= 18ms
    one_way=$((rtt / 2))
    bdelay=$((one_way - 2))
    if (( bdelay < 1 )); then bdelay=1; fi
    BDELAY="${bdelay}ms"

    for q in "${QMAX[@]}"; do
      for sr in "${SEND[@]}"; do
        tag="bw${bw}_rtt${rtt}_q${q}_sr${sr}"
        tag="${tag//./}"          # remove dots if any
        tag="${tag//Mbps/M}"      # simplify

        echo "=== RUN $tag ==="
        TOTAL=$((TOTAL+1))

        # Run and capture stdout/stderr
        set +e
        ./ns3 run "dumbbell-sim --runTag=${tag} --bRate=${bw} --pDelay=${PDELAY} --bDelay=${BDELAY} --qMax=${q} --sendRate=${sr} --simTime=${SIMTIME} --interval=${INTERVAL}" \
          > "${LOG_DIR}/${tag}.out" 2> "${LOG_DIR}/${tag}.err"
        rc=$?
        set -e

        if (( rc != 0 )); then
          echo "FAILED $tag (rc=$rc). See ${LOG_DIR}/${tag}.err"
          FAIL=$((FAIL+1))
        fi
      done
    done
  done
done

echo "DONE. Total runs: $TOTAL, Failed: $FAIL"
echo "Outputs:"
echo "  CSV:   $OUT_RAW"
echo "  Flow:  $OUT_RES"
echo "  Logs:  $LOG_DIR"
