"""
UDP traffic generator (one traffic class = one sender process).

Message format (application-level protocol, network byte order):
    type  (1 byte)   0 = DATA, 1 = FIN
    seq   (4 bytes)  DATA: packet sequence number   FIN: total DATA packets sent
    ts    (8 bytes)  send time (seconds since epoch, float)
    ...padding up to --size bytes

The FIN message tells the receiver exactly how many packets were sent, so loss
can be computed even if the last few DATA packets were dropped.

Examples:
    python3 udp_sender.py --dst 10.0.0.4 --port 5001 --rate 500 --duration 20
    python3 udp_sender.py --dst 10.0.0.4 --port 5003 --profile "10:300,15:2000,10:300"
        (profile = list of duration_s:rate_kbps phases, used for the surge scenario)
"""
import argparse, random, socket, struct, sys, time

HDR = struct.Struct("!BId")          # type, seq, timestamp
DATA, FIN = 0, 1


def parse_profile(text):
    """'10:300,15:2000' -> [(10.0, 300.0), (15.0, 2000.0)]"""
    phases = []
    for part in text.split(","):
        dur, rate = part.split(":")
        phases.append((float(dur), float(rate)))
    return phases


def main():
    ap = argparse.ArgumentParser(description="UDP traffic class sender")
    ap.add_argument("--dst", required=True, help="receiver IP")
    ap.add_argument("--port", type=int, required=True, help="receiver UDP port (identifies the class)")
    ap.add_argument("--rate", type=float, default=500, help="sending rate in kbps")
    ap.add_argument("--size", type=int, default=500, help="UDP payload bytes per packet")
    ap.add_argument("--duration", type=float, default=20, help="seconds to send")
    ap.add_argument("--profile", help="phases 'dur:kbps,dur:kbps,...' (overrides --rate/--duration)")
    a = ap.parse_args()

    if a.size < HDR.size:
        sys.exit(f"--size must be at least {HDR.size} bytes")
    try:
        phases = parse_profile(a.profile) if a.profile else [(a.duration, a.rate)]
    except ValueError:
        sys.exit("--profile must look like '10:300,15:2000' (seconds:kbps,...)")
    if any(d <= 0 or r <= 0 for d, r in phases):
        sys.exit("durations and rates must be > 0")

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Big send buffer: when the switch queue is full, packets still held there keep counting
    # against this socket's buffer. With the small default buffer, sendto() would block and
    # slow the sender down, so packets would be delayed instead of dropped and loss would
    # read 0%. SO_SNDBUFFORCE (Linux, needs root) ignores the wmem_max cap; fall back to SO_SNDBUF.
    try:
        s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_SNDBUFFORCE", 32), 8 << 20)
    except OSError:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8 << 20)
    seq, errors = 0, 0
    try:
        for dur, rate in phases:
            interval = a.size * 8 / (rate * 1000)      # seconds between packets
            nxt = time.time(); end = nxt + dur
            while time.time() < end:
                pkt = HDR.pack(DATA, seq, time.time()).ljust(a.size, b"x")
                try:
                    s.sendto(pkt, (a.dst, a.port))
                    seq += 1
                except OSError:                    # e.g. ENOBUFS / network unreachable
                    errors += 1
                # random spacing (average stays = interval) so the classes don't fall into
                # lock-step with each other; real traffic is never perfectly periodic
                nxt += interval * random.uniform(0.5, 1.5)
                d = nxt - time.time()
                if d > 0:
                    time.sleep(d)
    except KeyboardInterrupt:
        pass

    # FIN: tell the receiver how many packets were sent. Sent 3 times because UDP
    # gives no delivery guarantee; the wait lets the switch queues drain first.
    time.sleep(1.0)
    for _ in range(3):
        try:
            s.sendto(HDR.pack(FIN, seq, time.time()), (a.dst, a.port))
        except OSError:
            pass
        time.sleep(0.3)
    s.close()
    print(f"[UDP sender] port {a.port}: sent {seq} packets, send errors {errors}")


if __name__ == "__main__":
    main()
