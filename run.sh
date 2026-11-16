#!/usr/bin/env bash
# Runs the experiments. Run as a normal user (it calls sudo itself).
#
#   bash run.sh            everything: 3 modes x 3 loads, surge scenario, policy scenario, plots (~10 min)
#   bash run.sh quick      quick check: fifo vs adaptive at heavy load (~2 min)
#   bash run.sh steady <fifo|static|adaptive> [load]   one steady run (load 1.0 = 3000 kbps offered)
#   bash run.sh surge  [fifo|static|adaptive]          challenging scenario (default: all three modes)
#   bash run.sh policy                                 live policy change through the REST API
#   bash run.sh live   [mode]                          start everything and open the Mininet CLI
#   bash run.sh plots                                  regenerate graphs + summary from results/
cd "$(dirname "$0")"
export PATH="$HOME/ryu39/bin:$PATH"
command -v ryu-manager >/dev/null || { echo "Ryu not found. Run: bash setup.sh"; exit 1; }
mkdir -p results

cleanup() {
  [ -n "$RYU_PID" ] && kill "$RYU_PID" 2>/dev/null && wait "$RYU_PID" 2>/dev/null
  RYU_PID=""
  sudo mn -c >/dev/null 2>&1
  sudo ovs-vsctl --all destroy qos >/dev/null 2>&1
  sudo ovs-vsctl --all destroy queue >/dev/null 2>&1
}
trap cleanup EXIT

# run_one <mode> <tag> <topo_run.py args...>
run_one() {
  local mode=$1 tag=$2; shift 2
  cleanup
  rm -f "results/${tag}_controller.csv"
  QOS_MODE=$mode QOS_LOG="results/${tag}_controller.csv" \
    ryu-manager --ofp-tcp-listen-port 6653 controller/qos_controller.py > "results/${tag}_ryu.log" 2>&1 &
  RYU_PID=$!; sleep 5
  sudo /usr/bin/python3 topology/topo_run.py --tag "$tag" "$@" 2>&1 | tee "results/${tag}_output.txt"
  echo "--- controller log ---"; grep -E "mode|CONGESTION|POLICY" "results/${tag}_ryu.log"
  cleanup
}

steady() { local m=$1 l=${2:-1.0}; run_one "$m" "steady_${m}_load${l}" --scenario steady --load "$l"; }
surge()  { run_one "$1" "surge_$1" --scenario surge; }
plots()  { /usr/bin/python3 analysis/plot_results.py; }   # system python (has matplotlib), not the Ryu venv

case "${1:-all}" in
  all)
    for m in fifo static adaptive; do for l in 0.5 1.0 1.5; do steady $m $l; done; done
    for m in fifo static adaptive; do surge $m; done
    run_one adaptive policy_adaptive --scenario policy
    plots ;;
  quick)  steady fifo 1.0; steady adaptive 1.0; plots ;;
  steady) steady "${2:-adaptive}" "${3:-1.0}" ;;
  surge)  if [ -n "$2" ]; then surge "$2"; else for m in fifo static adaptive; do surge $m; done; fi ;;
  policy) run_one adaptive policy_adaptive --scenario policy ;;
  live)   run_one "${2:-adaptive}" "live_${2:-adaptive}" --scenario steady --duration 60 --cli ;;
  plots)  plots ;;
  *) sed -n '2,11p' "$0"; exit 1 ;;
esac
