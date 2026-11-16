# Design: QoS Traffic Management System (UDP + SDN)

## 1. Problem

Several applications share one network link. When they send more than the link can carry, the switch
must drop or delay packets. Without QoS every application suffers equally, so a time-critical
application (a voice call, say) breaks just as badly as a bulk download that could easily wait.

**Goal:** generate several classes of UDP traffic, create congestion on purpose, and use an SDN
controller to protect the important classes *only when needed*. Then measure the effect
(throughput, delay, jitter, loss, fairness) and compare it with a baseline that has no QoS.

## 2. Architecture

```
  APPLICATION PLANE            CONTROL PLANE                         DATA PLANE
  (sockets/)                   (controller/qos_controller.py)        (topology/topo_run.py)

  h1 udp_sender  HIGH   5001 ─┐                                      ┌──────────── s1 (Open vSwitch) ─────────────┐
  h2 udp_sender  MEDIUM 5002 ─┼─────── UDP datagrams ───────────────►│ flow table  ──►  port 4: 2 Mbps HTB        │──► h4 udp_receiver
  h3 udp_sender  LOW    5003 ─┘                                      │ (match udp port → set_queue)   q0 best effort │    (measures everything)
                                                                     │                                q1 HIGH        │
                               Ryu controller ◄── port stats (1 s) ──│                                q2 MEDIUM      │
                               (fifo/static/adaptive) ── flow rules ►│                                q3 LOW         │
                                   ▲                                 └────────────────────────────────────────────────┘
                                   │ REST  (GET /qos/status, PUT /qos/policy/<port>/<class>)
                               admin / topo_run.py
```

| Component | File | Role |
|---|---|---|
| UDP sender (client) | `sockets/udp_sender.py` | One process per traffic class. Sends numbered, timestamped datagrams at a fixed rate or following a rate profile; ends with a FIN message. |
| UDP receiver (server) | `sockets/udp_receiver.py` | Listens on all class ports at once with `select()`. Computes loss, throughput, delay, jitter, fairness and a per-second timeline. |
| TCP client/server | `sockets/tcp_*.py` | Connectivity check through the SDN switch. |
| SDN controller | `controller/qos_controller.py` | Ryu app (OpenFlow 1.3): learning switch + QoS rules + load monitor + REST API. |
| Topology and experiments | `topology/topo_run.py` | Builds the Mininet network, creates the HTB queues, runs a scenario, saves results. |
| Automation | `run.sh`, `setup.sh` | Install; run every experiment; restart the controller in the right mode for each run. |
| Analysis | `analysis/plot_results.py` | Graphs, summary table and automatic test verdicts. |

### Topology

One switch `s1` with four hosts (`10.0.0.1`–`10.0.0.4`). Each host link is unlimited, but the switch
port toward the receiver (`s1-eth4`) is shaped to **2 Mbps** with Linux HTB. All three senders
target h4, so their traffic meets at that port. That port is the bottleneck.

| Queue | Used for | HTB priority (lower = served first) | Guaranteed min-rate | Max |
|---|---|---|---|---|
| q0 | best effort (FIFO baseline, and adaptive mode when there is no congestion), plus ARP/ping/TCP | 0 | 100 kbps | 2 Mbps |
| q1 | HIGH | 1 | 100 kbps | 2 Mbps |
| q2 | MEDIUM | 2 | 100 kbps | 2 Mbps |
| q3 | LOW | 3 | 100 kbps | 2 Mbps |

Every queue is guaranteed 100 kbps, so even LOW is never completely starved. Spare bandwidth goes
first to the queue with the best priority.

**Why q0 has the top priority:** q0 is only used when nothing is being prioritised. When the
adaptive controller switches flows from q0 to the class queues, packets that were already waiting
in q0 would otherwise sit behind all the prioritised traffic for several seconds. The same happens
in reverse when it switches back. With top priority, those leftovers drain within about a second.
(With q0 at the lowest priority, testing showed HIGH packets stuck in q0 for several seconds after each switch.)

## 3. Application protocol (UDP)

Every datagram starts with a 13-byte header (network byte order, `struct "!BId"`):

| Field | Size | DATA message | FIN message |
|---|---|---|---|
| type | 1 B | 0 | 1 |
| seq | 4 B | sequence number 0, 1, 2 … | total number of DATA packets sent |
| ts | 8 B | send time (float seconds) | send time |

DATA packets are padded to 500 bytes. After its last DATA packet the sender waits 1 s and sends FIN
three times (UDP is unreliable, so one copy could be lost). The receiver uses FIN's count to compute
loss exactly. If no FIN arrives, it falls back to the highest sequence number seen.

**Why UDP?** It is what real-time traffic uses, and since UDP never retransmits, every drop at the
switch shows up directly as measured loss. With TCP, retransmissions and congestion control would
hide the effect of the queues.

**Concurrency:** the receiver serves three sockets from one thread using `select()`. Each sender is
an independent process, so the three classes are truly concurrent clients.

**Error handling:** argument validation, `OSError` on `sendto` is counted rather than crashing, malformed or
unknown packets are counted and ignored, an idle timeout stops the receiver if a sender dies, and a
1 MB socket receive buffer keeps the receiver itself from dropping packets.

## 4. SDN controller

### Flow rules

| Priority | Match | Actions | Installed when |
|---|---|---|---|
| 100 | `udp, ipv4_dst=10.0.0.4, udp_dst=<class port>` | `set_queue:<q>, output:4` | switch connects; re-installed whenever the decision changes |
| 1 | `in_port, eth_src, eth_dst` | `output:<learned port>` | learning switch, on packet-in (ARP, ping, TCP, …) |
| 0 | everything | send to controller | switch connects (table-miss) |

The class rules are the only thing that changes. Re-installing a rule with the same match and
priority replaces its action, so moving a flow to a different queue is a single flow-mod per class.

### Three modes (environment variable `QOS_MODE`)

| Mode | What `queue_for(class)` returns | Purpose |
|---|---|---|
| `fifo` | always q0 | **Baseline:** one shared queue, no priority |
| `static` | q1/q2/q3 by class, always | Fixed configuration: priority permanently on |
| `adaptive` | q0 while the link is fine, q1/q2/q3 while it is congested | **Proposed system** |

### Dynamic behaviour (adaptive mode)

Every second the controller sends an OpenFlow **port-stats request**. From the change in `rx_bytes`
on ports 1–3 it computes the load being offered to the bottleneck.

```
              load > 90% of 2000 kbps (1 sample)
   ┌──────────────┐ ───────────────────────────────────────► ┌──────────────────┐
   │  NORMAL      │                                           │  CONGESTED       │
   │ all → q0     │ ◄─────────────────────────────────────── │ HIGH→q1 MED→q2   │
   └──────────────┘   load < 75% for 3 samples in a row       │ LOW→q3           │
                                                              └──────────────────┘
```

* **Fast ON, slow OFF (hysteresis).** Congestion hurts immediately, so a single high sample switches
  priority on. Switching off waits for 3 calm samples, and the gap between 90% and 75% stops the
  controller flapping when the load hovers near one threshold.
* **Why not keep priority on all the time (static)?** Static is our "always-on" reference, and in a
  steady overload it is marginally better, because it never has to react. Adaptive gives the same
  protection while leaving the network untouched (one shared queue, every class equal) whenever there
  is no congestion. It pays for that with a reaction time of about 1 s at the start of congestion. Its real
  value is that the controller now *observes* the network and *responds* to it: the same monitor loop
  is where further policies (rate limiting, rerouting, alarms) plug in, and its live view is exposed
  through `/qos/status`.
* Every sample (time, load, state) is appended to `results/<run>_controller.csv`, so the
  controller's decisions can be plotted next to what the receiver measured.

### Runtime policy changes (REST API, port 8080)

| Request | Effect |
|---|---|
| `GET /qos/status` | mode, congested yes/no, measured load, per-port rate, and the policy table with each flow's current queue |
| `PUT /qos/policy/<udp_port>/<HIGH\|MEDIUM\|LOW\|BEST_EFFORT>` | change the class of a flow (or add a new flow) while traffic is running; the rule is re-installed immediately. Invalid input → HTTP 400 |

Example: `curl -X PUT http://127.0.0.1:8080/qos/policy/5003/HIGH`

## 5. Measurement definitions

| Metric | How it is computed (receiver) |
|---|---|
| Throughput | received payload bytes × 8 / (last arrival − first arrival), kbps |
| Packet loss | (sent − received) / sent; `sent` comes from FIN |
| Delay | arrival time − timestamp in the packet (valid because Mininet hosts share one clock) |
| Jitter | mean \|delay(i) − delay(i−1)\| over consecutive packets |
| Fairness | Jain's index J = (Σx)² / (n·Σx²), where x = received/sent for each class. 1.0 = all classes lose the same share; 1/3 = one class gets everything |
| Timeline | per-second throughput and average delay of each class |

## 6. Experiments

| Scenario | Traffic | Modes |
|---|---|---|
| steady, load ×0.5 | 250 / 500 / 750 kbps = 1500 kbps (below capacity) | fifo, static, adaptive |
| steady, load ×1.0 | 500 / 1000 / 1500 kbps = 3000 kbps (1.5× capacity) | fifo, static, adaptive |
| steady, load ×1.5 | 750 / 1500 / 2250 kbps = 4500 kbps (2.25× capacity) | fifo, static, adaptive |
| **surge** (challenging scenario) | HIGH 500 + MEDIUM 500 constant; LOW 200 → **2000** (t = 10–25 s) → 200 | fifo, static, adaptive |
| **policy** | 800 / 400 / 800 kbps for 40 s (congested); at t = 15 s the REST API swaps udp/5003 to HIGH and udp/5001 to LOW | adaptive |

## 7. Assumptions and limitations

* The class of a flow is identified by its UDP destination port (5001/5002/5003). A real deployment
  would more likely use DSCP marks or application signalling.
* One switch and one bottleneck keep the experiment easy to reason about; the controller logic does
  not depend on the number of hosts.
* The adaptive controller reacts within about 1–2 s (the polling interval). Packets queued before it
  reacts are not re-sorted. Likewise, after a policy change the demoted flow waits behind the packets
  already sitting in its new queue for a few seconds.
* Loss is measured end to end; a few packets may also be lost at a sender's socket at very high load
  (reported as send errors).
