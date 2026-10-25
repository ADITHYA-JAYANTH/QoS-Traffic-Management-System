#!/usr/bin/env bash
# Runs the demo. Usage: bash run.sh [qos|fifo|both]   (default: both). Run as a normal user.
cd "$(dirname "$0")"
MODE="${1:-both}"
export PATH="$HOME/ryu39/bin:$PATH"
command -v ryu-manager >/dev/null || { echo "Ryu not found. Run: bash setup.sh"; exit 1; }
mkdir -p results

cleanup() {
  [ -n "$RYU_PID" ] && kill "$RYU_PID" 2>/dev/null
  sudo mn -c >/dev/null 2>&1
  sudo ovs-vsctl --all destroy qos >/dev/null 2>&1
  sudo ovs-vsctl --all destroy queue >/dev/null 2>&1
}
trap cleanup EXIT

run_mode () {
  echo; echo "################ MODE: $1 ################"
  cleanup
  ryu-manager --ofp-tcp-listen-port 6653 controller/qos_controller.py > "results/ryu_$1.log" 2>&1 &
  RYU_PID=$!; sleep 5
  sudo /usr/bin/python3 topology/topo_run.py --mode "$1" 2>&1 | tee "results/output_$1.txt"
  kill "$RYU_PID" 2>/dev/null; RYU_PID=""
}

case "$MODE" in
  qos)  run_mode qos ;;
  fifo) run_mode fifo ;;
  both) run_mode qos; run_mode fifo ;;
  *) echo "Usage: bash run.sh [qos|fifo|both]"; exit 1 ;;
esac

echo; echo "=== SUMMARY (per-class results) ==="
for m in qos fifo; do
  [ -f "results/output_$m.txt" ] && { echo "--- $m ---"; grep -A4 "^port" "results/output_$m.txt"; }
done
echo; echo "Full logs and JSON are in results/"
