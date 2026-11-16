#!/usr/bin/env python3
"""
Mininet topology + experiment runner (run as root, with the Ryu controller already running).

Topology: h1, h2, h3 (senders) and h4 (receiver) on one OVS switch s1.
The link s1 -> h4 (port s1-eth4) is the 2 Mbps bottleneck with 4 HTB queues:
    q0 best effort (shared), q1 HIGH, q2 MEDIUM, q3 LOW
The controller decides which queue each flow uses; this script only builds the network,
generates traffic and collects the results.

Scenarios:
    steady  constant traffic. --load scales the class rates (1.0 = 500/1000/1500 kbps = 3000 kbps)
    surge   challenging scenario: light traffic, then the LOW class suddenly surges to 2000 kbps
            for 15 s (link overloaded), then calms down again
    policy  congestion (800/400/800 kbps); at t=15 s the policy is changed live through the
            controller's REST API: the LOW flow is promoted to HIGH, the HIGH flow demoted to LOW
"""
import argparse, json, os, subprocess, sys, time, urllib.request
from functools import partial
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.log import setLogLevel
from mininet.cli import CLI

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOCK = os.path.join(ROOT, "sockets")
RES = os.path.join(ROOT, "results")
os.makedirs(RES, exist_ok=True)
PY = sys.executable
REST = "http://127.0.0.1:8080"

BOTTLENECK = 2000000                     # 2 Mbps at s1-eth4 (toward h4)
# host, class label, udp port, base rate (kbps)
CLASSES = [("h1", "HIGH", 5001, 500), ("h2", "MEDIUM", 5002, 1000), ("h3", "LOW", 5003, 1500)]
SURGE = {"HIGH": "40:500", "MEDIUM": "40:500", "LOW": "10:200,15:2000,15:200"}   # duration:kbps phases
POLICY = {"HIGH": 800, "MEDIUM": 400, "LOW": 800}     # kbps; 2000 offered = congested
POLICY_DURATION, POLICY_CHANGE_AT = 40, 15


def rest(method, path):
    """Talk to the controller's REST API. Returns the decoded JSON, or None if unreachable."""
    try:
        req = urllib.request.Request(REST + path, method=method)
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[REST] {method} {path} failed: {e}")
        return None


def setup_queues():
    """Create the HTB QoS on the bottleneck port. Lower priority number = served first
    when queues compete for spare bandwidth; every queue is guaranteed min-rate.
    q0 (shared best-effort queue) gets the top priority: it only carries traffic when
    nothing is being prioritised, and this way packets left in it when the controller
    switches to the class queues (or back) drain at once instead of being stuck."""
    prio = {0: 0, 1: 1, 2: 2, 3: 3}
    cmd = ["ovs-vsctl", "set", "port", "s1-eth4", "qos=@newqos", "--",
           "--id=@newqos", "create", "qos", "type=linux-htb",
           f"other-config:max-rate={BOTTLENECK}"] + [f"queues:{i}=@q{i}" for i in range(4)]
    for i in range(4):
        cmd += ["--", f"--id=@q{i}", "create", "queue", "other-config:min-rate=100000",
                f"other-config:max-rate={BOTTLENECK}", f"other-config:priority={prio[i]}"]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)


def main():
    sys.stdout.reconfigure(line_buffering=True)      # keep our prints in order with tool output
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=["steady", "surge", "policy"], default="steady")
    ap.add_argument("--load", type=float, default=1.0, help="steady: multiply all class rates")
    ap.add_argument("--duration", type=int, default=20, help="steady: seconds of traffic")
    ap.add_argument("--tag", help="result file name (default derived from scenario/mode)")
    ap.add_argument("--cli", action="store_true", help="open the Mininet CLI at the end")
    a = ap.parse_args()

    setLogLevel("warning")
    net = Mininet(controller=None, switch=partial(OVSSwitch, protocols="OpenFlow13"))
    net.addController(RemoteController("c0", ip="127.0.0.1", port=6653))
    s1 = net.addSwitch("s1")
    hosts = [net.addHost(f"h{i}", ip=f"10.0.0.{i}/24") for i in range(1, 5)]
    for h in hosts:
        net.addLink(h, s1)                  # h1..h4 -> s1 ports 1..4
    net.start(); time.sleep(3)
    setup_queues()
    h1, h2, h3, h4 = hosts

    st = rest("GET", "/qos/status")
    mode = st["mode"] if st else "unknown"
    tag = a.tag or (f"steady_{mode}_load{a.load:g}" if a.scenario == "steady" else f"{a.scenario}_{mode}")
    print(f"\n######## scenario={a.scenario} mode={mode} tag={tag} ########")

    print("\n=== Connectivity ===")
    loss = net.pingAll()
    print("\n=== TCP socket test (h1 -> h4) ===")
    srv = h4.popen([PY, f"{SOCK}/tcp_server.py", "6000"]); time.sleep(1)
    print(h1.cmd(f"{PY} {SOCK}/tcp_client.py 10.0.0.4 6000 hello-from-h1").strip())
    srv.wait(timeout=10)

    # ---------------- traffic
    out = os.path.join(RES, f"{tag}.json")
    events = []
    t0 = time.time() + 1.5
    rx = h4.popen([PY, f"{SOCK}/udp_receiver.py", "--out", out, "--t0", str(t0)])
    time.sleep(t0 - time.time())
    senders = []
    for host, label, port, base in CLASSES:
        if a.scenario == "steady":
            shape = ["--rate", str(base * a.load), "--duration", str(a.duration)]
        elif a.scenario == "surge":
            shape = ["--profile", SURGE[label]]
        else:
            shape = ["--rate", str(POLICY[label]), "--duration", str(POLICY_DURATION)]
        senders.append(net.get(host).popen([PY, f"{SOCK}/udp_sender.py", "--dst", "10.0.0.4",
                                            "--port", str(port)] + shape))
    print(f"\n=== UDP traffic running ({a.scenario}) ... ===")

    if a.scenario == "policy":
        time.sleep(POLICY_CHANGE_AT)
        print(f"[t={POLICY_CHANGE_AT}s] REST: promote udp/5003 to HIGH, demote udp/5001 to LOW")
        rest("PUT", "/qos/policy/5003/HIGH")
        rest("PUT", "/qos/policy/5001/LOW")
        events.append([round(time.time() - t0, 1), "policy change via REST"])

    for s in senders:
        print(s.communicate()[0].decode().strip())
    print("\n=== Receiver results ===")
    print(rx.communicate(timeout=60)[0].decode().strip())

    print("\n=== Controller status (REST) ===")
    st_end = rest("GET", "/qos/status")
    print(json.dumps(st_end, indent=2))
    print("\n=== Flow table (QoS rules) ===")
    subprocess.run("ovs-ofctl -O OpenFlow13 dump-flows s1 | grep -E 'udp|priority=0'", shell=True)
    print("\n=== Queue stats on the bottleneck port ===")
    subprocess.run(["ovs-ofctl", "-O", "OpenFlow13", "queue-stats", "s1", "4"])

    # add experiment metadata to the receiver's JSON
    with open(out) as f:
        res = json.load(f)
    duration = {"steady": a.duration, "surge": 40, "policy": POLICY_DURATION}[a.scenario]
    res.update(scenario=a.scenario, mode=mode, load=a.load if a.scenario == "steady" else None, duration=duration,
               ping_loss_pct=loss, events=events)
    with open(out, "w") as f:
        json.dump(res, f, indent=2)
    print(f"\nSaved {out}")

    if a.cli:
        CLI(net)
    net.stop()


if __name__ == "__main__":
    main()
