# QoS Traffic Management System (SDN + UDP) - Deliverable 1

A UDP application generates three classes of traffic (HIGH / MEDIUM / LOW). They are sent through a
Mininet network controlled by an SDN controller (Ryu, OpenFlow 1.3) that maps each class to a priority
queue on a congested link. The receiver measures throughput, delay and packet loss per class.

## Architecture

```
 h1 (HIGH   udp/5001, 500 kbps)  --\
 h2 (MEDIUM udp/5002, 1000 kbps) ---+--> s1 (Open vSwitch) --[2 Mbps bottleneck, 3 queues]--> h4 (receiver)
 h3 (LOW    udp/5003, 1500 kbps) --/         ^
                                              | OpenFlow 1.3
                                        Ryu controller (qos_controller.py)
```

Offered load is 3000 kbps against a 2000 kbps link, so congestion is guaranteed. The controller installs one
flow per class (match UDP dst port, `set_queue`, output to h4). HIGH gets queue 1, MEDIUM queue 2, LOW queue 3.

## Repository layout

| Path | Contents |
|---|---|
| `sockets/` | TCP client/server and UDP sender/receiver (loss, delay, throughput measurement) |
| `controller/qos_controller.py` | Ryu SDN controller: QoS flow rules + learning switch |
| `topology/topo_run.py` | Mininet topology, queue setup on the bottleneck, test harness |
| `setup.sh` | One-time install of Mininet, Open vSwitch and Ryu |
| `run.sh` | Runs the full test (QoS mode, then baseline mode) |
| `results/` | Output logs and JSON written by `run.sh` |
| `docs/` | Design document |

## Requirements

Ubuntu/Debian Linux (native or VM) with `sudo` and internet access. Not supported on plain Windows/macOS or WSL1.

## Build and run

```bash
git clone <this-repo-url> && cd qos-traffic-management
bash setup.sh      # once, takes a few minutes; do not use sudo
bash run.sh        # runs QoS mode, then baseline (FIFO) mode
```

Other options: `bash run.sh qos`, `bash run.sh fifo`. If anything hangs: `sudo mn -c`.

## Expected result

`run.sh` prints, for each mode, a ping check, a TCP socket test, a per-class UDP table
(`rx`, `lost`, `loss%`, `kbps`, `delay_ms`), the OpenFlow flow table and queue statistics.
In `qos` mode the HIGH class should show near-zero loss while LOW absorbs most of the loss;
in `fifo` mode the loss is spread across classes. Results are saved in `results/`.

## Team contributions

| Member | Part | Files |
|---|---|---|
| <Name 1> | TCP/UDP socket implementation and measurement | `sockets/` |
| <Name 2> | SDN controller and QoS policy | `controller/` |
| <Name 3> | Mininet topology, queue setup, integration and testing, scripts | `topology/`, `setup.sh`, `run.sh`, `docs/` |
