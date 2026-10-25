import socket, struct, time, argparse
HDR = struct.Struct("!Id")          # seq, send timestamp
ap = argparse.ArgumentParser()
ap.add_argument("--dst", required=True); ap.add_argument("--port", type=int, required=True)
ap.add_argument("--rate", type=float, default=500, help="kbps")
ap.add_argument("--size", type=int, default=500, help="bytes per packet")
ap.add_argument("--duration", type=float, default=20)
a = ap.parse_args()
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
interval = a.size * 8 / (a.rate * 1000)
nxt = time.time(); end = nxt + a.duration; seq = 0
while time.time() < end:
    s.sendto(HDR.pack(seq, time.time()).ljust(a.size, b"x"), (a.dst, a.port))
    seq += 1; nxt += interval
    d = nxt - time.time()
    if d > 0: time.sleep(d)
print(f"[UDP sender] port {a.port}: sent {seq} packets")
