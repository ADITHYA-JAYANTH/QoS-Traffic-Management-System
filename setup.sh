#!/usr/bin/env bash
# One-time setup: installs Mininet, Open vSwitch and Ryu. Run as a normal user (not sudo).
set -e
if [ "$EUID" -eq 0 ]; then echo "Run as a normal user: bash setup.sh"; exit 1; fi

echo "=== Installing system packages ==="
sudo apt update
sudo apt install -y mininet openvswitch-switch python3-pip python3-venv iperf net-tools
sudo systemctl enable --now openvswitch-switch || sudo service openvswitch-switch start

# Ryu needs old Python (3.9) + eventlet 0.30.2; newer system Pythons break it.
# uv downloads a private Python 3.9 into ~/ryu39 (nothing system-wide is changed).
RYU_ENV="$HOME/ryu39"
if [ ! -x "$RYU_ENV/bin/ryu-manager" ]; then
  echo "=== Installing Ryu in a private Python 3.9 environment ==="
  rm -rf "$HOME/uv-tool" "$RYU_ENV"
  python3 -m venv "$HOME/uv-tool"
  "$HOME/uv-tool/bin/pip" install --quiet uv
  "$HOME/uv-tool/bin/uv" venv --python 3.9 "$RYU_ENV"
  "$HOME/uv-tool/bin/uv" pip install --python "$RYU_ENV/bin/python" "setuptools<66" wheel pbr
  "$HOME/uv-tool/bin/uv" pip install --python "$RYU_ENV/bin/python" --no-build-isolation "eventlet==0.30.2" ryu
fi
"$RYU_ENV/bin/ryu-manager" --version
echo "=== Setup complete. Now run: bash run.sh ==="
