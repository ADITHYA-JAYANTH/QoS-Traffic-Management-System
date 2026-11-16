# Test cases

Run `bash run.sh` once and every test below gets evidence. Tests **T10–T19** are checked
automatically by `analysis/plot_results.py` and reported as PASS/FAIL at the bottom of
`results/summary.md`. The others are checked by reading the run output (`results/*_output.txt`).

## Functional tests (application + SDN working end to end)

| ID | What is tested | How | Expected result |
|---|---|---|---|
| T01 | Host connectivity through the SDN switch | `pingAll` at the start of every run | `Results: 0% dropped` |
| T02 | TCP socket client/server through the switch | TCP test at the start of every run | `[TCP client] reply: ACK: hello-from-h1` |
| T03 | Controller installs the QoS rules | flow table printed at the end of each run, or `sudo ovs-ofctl -O OpenFlow13 dump-flows s1` | three `priority=100,udp,…,tp_dst=500x actions=set_queue:N,output:4` rules |
| T04 | HTB queues exist on the bottleneck | queue stats printed at the end of each run, or `sudo ovs-vsctl list queue` | 4 queues on port 4; the queues used by that mode count packets |
| T05 | All three UDP classes run concurrently and the receiver serves them at once | any steady run | receiver table has one row per class with `sent` = packets each sender reported |
| T06 | FIN message gives exact loss | compare `[UDP sender] … sent N` lines with the receiver's `sent` column | identical numbers |
| T07 | Receiver and sender survive bad input | outside Mininet: `python3 sockets/udp_receiver.py --ports 5901 --labels TEST --idle 3 &` then `python3 -c "import socket;socket.socket(2,2).sendto(b'xx',('127.0.0.1',5901))"` then `python3 sockets/udp_sender.py --dst 127.0.0.1 --port 5901 --rate 200 --duration 2`; also try `--size 5` and `--profile 2:abc` | receiver prints `malformed 1` and still measures the real flow (0% loss); the sender rejects bad arguments with a clear message |
| T08 | REST status endpoint | `curl http://127.0.0.1:8080/qos/status` while running | JSON with mode, congested, load_kbps, policy |
| T09 | REST rejects invalid input | `curl -X PUT http://127.0.0.1:8080/qos/policy/5003/URGENT` | HTTP 400 and an error message; rules unchanged |

## Performance and dynamic-behaviour tests (automatic)

| ID | Scenario | Pass condition | Why it matters |
|---|---|---|---|
| T10 | steady, 1500 kbps (below capacity), all modes | every class < 1% loss | no congestion → nothing to fix; the system adds no harm |
| T11 | steady, 1500 kbps, adaptive | controller never switches priority ON | adaptive does not react to normal load (no false alarms) |
| T12 | steady, 3000 kbps, FIFO | HIGH loss > 10% | the baseline really does fail to protect important traffic |
| T13 | steady, 3000 kbps, static | HIGH and MEDIUM < 1% loss, LOW > 20% | priority queues work |
| T14 | steady, 3000 kbps, adaptive | HIGH loss < 2% | the dynamic controller protects HIGH under congestion |
| T15 | steady, 4500 kbps, adaptive | HIGH loss < 2% | still works under extreme overload |
| T16 | surge, adaptive | priority switches ON within 3 s after the surge starts (t = 10 s) | controller detects congestion quickly |
| T17 | surge, adaptive | priority switches OFF after the surge ends (t = 25 s) | controller recovers back to normal |
| T18 | surge, adaptive vs FIFO | HIGH delay during the surge < 10% of FIFO's | the challenging scenario is handled |
| T19 | policy change via REST | after the change the promoted flow (5003) gains throughput and its delay drops >10×; the demoted flow (5001) loses throughput and its delay rises >10× | policies can be modified at runtime |

## Manual checks (while traffic is running)

| ID | Action | Expected |
|---|---|---|
| T20 | `bash run.sh live adaptive`, then `curl …/qos/status` twice during traffic | `congested: true`, `load_kbps` ≈ 3200 |
| T21 | `curl -X PUT http://127.0.0.1:8080/qos/policy/5003/HIGH`, then `dump-flows` | `tp_dst=5003 actions=set_queue:1` |
