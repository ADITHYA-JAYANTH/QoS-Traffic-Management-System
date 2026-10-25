#!/usr/bin/env python3
import argparse, os, subprocess, sys, time
from functools import partial
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.log import setLogLevel
from mininet.cli import CLI

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOCK = os.path.join(ROOT, "sockets")      # socket programs
RES = os.path.join(ROOT, "results")       # JSON results
os.makedirs(RES, exist_ok=True)
PY = sys.executable

BOTTLENECK = 2000000                       # 2 Mbps at s1-eth4 (toward h4)
CLASSES = [("h1", "HIGH", 5001, 500), ("h2", "MEDIUM", 5002, 1000), ("h3", "LOW", 5003, 1500)]

def setup_queues(mode):
    prio = {0: 5, 1: 1, 2: 2, 3: 3} if mode == "qos" else {0: 1, 1: 1, 2: 1, 3: 1}
    cmd = ["ovs-vsctl", "set", "port", "s1-eth4", "qos=@newqos", "--",
           "--id=@newqos", "create", "qos", "type=linux-htb",
           f"other-config:max-rate={BOTTLENECK}"] + [f"queues:{i}=@q{i}" for i in range(4)]
    for i in range(4):
        cmd += ["--", f"--id=@q{i}", "create", "queue", "other-config:min-rate=100000",
                f"other-config:max-rate={BOTTLENECK}", f"other-config:priority={prio[i]}"]
    subprocess.run(cmd, check=True)

ap = argparse.ArgumentParser()
ap.add_argument("--mode", choices=["qos", "fifo"], default="qos")
ap.add_argument("--duration", type=int, default=20)
ap.add_argument("--cli", action="store_true")
a = ap.parse_args()

setLogLevel("info")
net = Mininet(controller=None, switch=partial(OVSSwitch, protocols="OpenFlow13"))
net.addController(RemoteController("c0", ip="127.0.0.1", port=6653))
s1 = net.addSwitch("s1")
hosts = [net.addHost(f"h{i}", ip=f"10.0.0.{i}/24") for i in range(1, 5)]
for h in hosts: net.addLink(h, s1)          # h1..h4 -> s1 ports 1..4
net.start(); time.sleep(3)
setup_queues(a.mode)
print("\n=== Connectivity ===");  net.pingAll()

print("\n=== TCP socket test (h1 -> h4) ===")
h1, h2, h3, h4 = hosts
srv = h4.popen([PY, f"{SOCK}/tcp_server.py", "6000"]); time.sleep(1)
print(h1.cmd(f"{PY} {SOCK}/tcp_client.py 10.0.0.4 6000 hello-from-h1"))
srv.wait(timeout=10)

print(f"\n=== UDP traffic classes, mode={a.mode}, {a.duration}s (offered 3000 kbps vs 2000 kbps link) ===")
rx = h4.popen([PY, f"{SOCK}/udp_receiver.py", "--out", f"{RES}/results_{a.mode}.json"]); time.sleep(1)
tx = [net.get(h).popen([PY, f"{SOCK}/udp_sender.py", "--dst", "10.0.0.4", "--port", str(p),
      "--rate", str(r), "--duration", str(a.duration)]) for h, n, p, r in CLASSES]
for t in tx: t.wait()
rx.wait(timeout=30)

print("\n=== Flow table ===")
subprocess.run(["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", "s1"])
print("\n=== Queue stats ===")
subprocess.run(["ovs-ofctl", "-O", "OpenFlow13", "queue-stats", "s1"])
if a.cli: CLI(net)
net.stop()
