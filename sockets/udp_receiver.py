import socket, struct, time, select, json, argparse
HDR = struct.Struct("!Id")
ap = argparse.ArgumentParser()
ap.add_argument("--ports", type=int, nargs="+", default=[5001, 5002, 5003])
ap.add_argument("--idle", type=float, default=5)
ap.add_argument("--out", default="results.json")
a = ap.parse_args()
socks, st = {}, {}
for p in a.ports:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", p)); socks[s] = p
    st[p] = dict(rx=0, bytes=0, max_seq=-1, dsum=0.0, first=None, last=None)
while True:
    r, _, _ = select.select(list(socks), [], [], a.idle)
    if not r: break
    for s in r:
        data, _ = s.recvfrom(4096); now = time.time()
        seq, ts = HDR.unpack_from(data); x = st[socks[s]]
        x["rx"] += 1; x["bytes"] += len(data); x["dsum"] += now - ts
        x["max_seq"] = max(x["max_seq"], seq)
        x["first"] = x["first"] or now; x["last"] = now
res = {}
print(f"{'port':<6}{'rx':>8}{'lost':>8}{'loss%':>8}{'kbps':>10}{'delay_ms':>10}")
for p, x in st.items():
    sent = x["max_seq"] + 1; lost = max(sent - x["rx"], 0)
    dur = (x["last"] - x["first"]) if x["rx"] > 1 else 1
    kbps = x["bytes"] * 8 / dur / 1000
    dly = x["dsum"] / x["rx"] * 1000 if x["rx"] else 0
    loss = lost / sent * 100 if sent else 0
    res[p] = dict(rx=x["rx"], lost=lost, loss_pct=loss, kbps=kbps, delay_ms=dly)
    print(f"{p:<6}{x['rx']:>8}{lost:>8}{loss:>8.1f}{kbps:>10.0f}{dly:>10.2f}")
json.dump(res, open(a.out, "w"), indent=2)
