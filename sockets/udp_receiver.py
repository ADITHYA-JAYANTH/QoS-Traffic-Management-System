
import argparse, json, select, socket, struct, time

HDR = struct.Struct("!BId")          # type, seq, timestamp (must match udp_sender.py)
DATA, FIN = 0, 1


def jain(xs):
    """Jain's fairness index: 1.0 = perfectly equal, 1/n = one class gets everything."""
    xs = [x for x in xs if x is not None]
    if not xs or sum(xs) == 0:
        return 0.0
    return sum(xs) ** 2 / (len(xs) * sum(x * x for x in xs))


def main():
    ap = argparse.ArgumentParser(description="UDP multi-class receiver")
    ap.add_argument("--ports", type=int, nargs="+", default=[5001, 5002, 5003])
    ap.add_argument("--labels", nargs="+", default=["HIGH", "MEDIUM", "LOW"])
    ap.add_argument("--idle", type=float, default=5, help="stop after this many silent seconds")
    ap.add_argument("--t0", type=float, help="experiment start time (epoch) for the timeline")
    ap.add_argument("--out", default="results.json")
    a = ap.parse_args()
    if len(a.labels) != len(a.ports):
        raise SystemExit("--labels must have one entry per port")

    socks, st = {}, {}
    for port, label in zip(a.ports, a.labels):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)   # avoid drops in the receiver itself
        s.bind(("0.0.0.0", port))
        socks[s] = port
        st[port] = dict(label=label, rx=0, bytes=0, max_seq=-1, fin_total=None, dsum=0.0,
                        dmax=0.0, jsum=0.0, prev_d=None, first=None, last=None, bins={})
    bad = 0
    t0 = a.t0
    print(f"[UDP receiver] listening on ports {a.ports}", flush=True)

    while True:
        ready, _, _ = select.select(list(socks), [], [], a.idle)
        if not ready:
            break                                        # idle timeout
        for s in ready:
            data, _ = s.recvfrom(4096)
            now = time.time()
            if len(data) < HDR.size:
                bad += 1; continue                       # malformed packet: ignore, keep running
            mtype, seq, ts = HDR.unpack_from(data)
            x = st[socks[s]]
            if mtype == FIN:
                x["fin_total"] = seq; continue
            if mtype != DATA:
                bad += 1; continue
            d = now - ts
            x["rx"] += 1; x["bytes"] += len(data); x["dsum"] += d; x["dmax"] = max(x["dmax"], d)
            if x["prev_d"] is not None:
                x["jsum"] += abs(d - x["prev_d"])
            x["prev_d"] = d
            x["max_seq"] = max(x["max_seq"], seq)
            x["first"] = x["first"] or now; x["last"] = now
            if t0 is None:
                t0 = now
            b = x["bins"].setdefault(int(now - t0), [0, 0.0, 0])  # bytes, delay sum, packets
            b[0] += len(data); b[1] += d; b[2] += 1
        if all(x["fin_total"] is not None for x in st.values()):
            break                                        # every sender said FIN

    classes, timeline = {}, {}
    print(f"{'class':<8}{'port':>6}{'sent':>8}{'rx':>8}{'loss%':>8}{'kbps':>9}{'delay_ms':>10}{'jitter_ms':>11}")
    for port, x in st.items():
        sent = x["fin_total"] if x["fin_total"] is not None else x["max_seq"] + 1
        lost = max(sent - x["rx"], 0)
        dur = (x["last"] - x["first"]) if x["rx"] > 1 else 1
        r = dict(port=port, sent=sent, rx=x["rx"], lost=lost,
                 loss_pct=lost / sent * 100 if sent else 0.0,
                 kbps=x["bytes"] * 8 / dur / 1000,
                 delay_ms=x["dsum"] / x["rx"] * 1000 if x["rx"] else 0.0,
                 max_delay_ms=x["dmax"] * 1000,
                 jitter_ms=x["jsum"] / (x["rx"] - 1) * 1000 if x["rx"] > 1 else 0.0,
                 delivery_ratio=x["rx"] / sent if sent else None)
        classes[x["label"]] = r
        timeline[x["label"]] = [[t, b[0] * 8 / 1000, b[1] / b[2] * 1000] for t, b in sorted(x["bins"].items())]
        print(f"{x['label']:<8}{port:>6}{sent:>8}{x['rx']:>8}{r['loss_pct']:>8.1f}{r['kbps']:>9.0f}"
              f"{r['delay_ms']:>10.1f}{r['jitter_ms']:>11.2f}")

    fair = jain([c["delivery_ratio"] for c in classes.values()])
    total = sum(c["kbps"] for c in classes.values())
    print(f"total throughput {total:.0f} kbps | Jain fairness (delivery ratio) {fair:.3f} | malformed {bad}")
    with open(a.out, "w") as f:
        json.dump(dict(classes=classes, fairness_jain=fair, total_kbps=total, malformed=bad,
                       t0=t0, timeline=timeline), f, indent=2)


if __name__ == "__main__":
    main()
