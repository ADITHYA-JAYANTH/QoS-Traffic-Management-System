#!/usr/bin/env python3
"""
Turns the JSON/CSV files in results/ into graphs and a summary table.
    python3 analysis/plot_results.py
Outputs (in results/):
    plots/loss_by_class.png       packet loss per class, 3 modes x 3 load levels
    plots/delay_by_class.png      average delay per class, 3 modes x 3 load levels
    plots/high_and_fairness.png   HIGH-class loss and Jain fairness vs offered load
    plots/surge_timeline.png      challenging scenario: what the controller saw and did over time
    plots/policy_timeline.png     live policy change through the REST API
    summary.md / summary.csv      all numbers in table form
"""
import csv, glob, json, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
PLOTS = os.path.join(RES, "plots")
MODES = ["fifo", "static", "adaptive"]
MODE_NAME = {"fifo": "FIFO baseline", "static": "Static QoS", "adaptive": "Adaptive SDN QoS"}
MODE_COLOR = {"fifo": "#2a78d6", "static": "#eb6834", "adaptive": "#1baf7a"}
CLASSES = ["HIGH", "MEDIUM", "LOW"]
CLASS_COLOR = {"HIGH": "#6250d6", "MEDIUM": "#008300", "LOW": "#e34948"}
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
CAPACITY, ON_LEVEL, OFF_LEVEL = 2000, 0.90, 0.75          # must match controller

plt.rcParams.update({"font.size": 10, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                     "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.grid": True, "grid.color": GRID,
                     "axes.axisbelow": True, "legend.frameon": False})


def load(path):
    with open(path) as f:
        return json.load(f)


def controller_log(tag, t0):
    path = os.path.join(RES, f"{tag}_controller.csv")
    rows = []
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                t, ld, c = line.strip().split(",")
                rows.append((float(t) - t0, float(ld), int(c)))
    return rows


def steady_runs():
    runs = {}
    for p in glob.glob(os.path.join(RES, "steady_*.json")):
        d = load(p)
        if d.get("mode") in MODES and d.get("load") is not None:
            runs[(d["mode"], d["load"])] = d
    return runs


def offered_kbps(load_factor):
    return 3000 * load_factor


def grouped_bars(runs, loads, metric, ylabel, fname, title, log=False, fmt="{:.0f}"):
    fig, axes = plt.subplots(1, len(loads), figsize=(4.2 * len(loads), 3.8), sharey=True)
    axes = axes if len(loads) > 1 else [axes]
    w = 0.26
    for ax, ld in zip(axes, loads):
        for i, m in enumerate(MODES):
            d = runs.get((m, ld))
            if not d:
                continue
            vals = [d["classes"][c][metric] for c in CLASSES]
            xs = [k + (i - 1) * (w + 0.02) for k in range(len(CLASSES))]
            bars = ax.bar(xs, vals, w, color=MODE_COLOR[m], label=MODE_NAME[m])
            for b, v in zip(bars, vals):
                ax.annotate(fmt.format(v), (b.get_x() + b.get_width() / 2, max(b.get_height(), 0.01)),
                            ha="center", va="bottom", fontsize=7, color=MUTED, xytext=(0, 1),
                            textcoords="offset points")
        ax.set_xticks(range(len(CLASSES)), CLASSES)
        ax.set_title(f"offered {offered_kbps(ld):.0f} kbps (load x{ld:g})", fontsize=10, color=INK)
        ax.grid(axis="x", visible=False)
        if log:
            ax.set_yscale("log")
    axes[0].set_ylabel(ylabel)
    axes[0].legend(loc="upper left", fontsize=8)
    fig.suptitle(title, color=INK)
    fig.tight_layout()
    fig.savefig(os.path.join(PLOTS, fname), dpi=150)
    plt.close(fig)


def high_and_fairness(runs, loads):
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.8))
    for m in MODES:
        pts = [(offered_kbps(ld), runs[(m, ld)]) for ld in loads if (m, ld) in runs]
        if not pts:
            continue
        x = [p[0] for p in pts]
        a1.plot(x, [p[1]["classes"]["HIGH"]["loss_pct"] for p in pts], "-o", lw=2, ms=6,
                color=MODE_COLOR[m], label=MODE_NAME[m])
        a2.plot(x, [p[1]["fairness_jain"] for p in pts], "-o", lw=2, ms=6, color=MODE_COLOR[m],
                label=MODE_NAME[m])
    for ax in (a1, a2):
        ax.axvline(CAPACITY, color=MUTED, lw=1, ls="--")
        ax.set_xlabel("offered load (kbps)   - - link capacity 2000 kbps")
    a1.set_ylabel("HIGH class packet loss (%)"); a1.set_title("Is the important traffic protected?", color=INK)
    a2.set_ylabel("Jain fairness index"); a2.set_title("How evenly is the loss shared?", color=INK)
    a2.set_ylim(0, 1.05)
    a1.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(PLOTS, "high_and_fairness.png"), dpi=150)
    plt.close(fig)


def surge_timeline():
    runs = {m: load(p) for m in MODES for p in [os.path.join(RES, f"surge_{m}.json")] if os.path.exists(p)}
    if not runs:
        return {}
    fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(10, 9), sharex=True)
    ad = runs.get("adaptive")
    if ad:
        log = controller_log("surge_adaptive", ad["t0"])
        if log:
            t = [r[0] for r in log]; ld = [r[1] for r in log]
            a1.plot(t, ld, color=MODE_COLOR["adaptive"], lw=2, label="load measured by controller")
            on = False; start = 0
            for ti, _, c in log + [(t[-1], 0, 0)]:
                if c and not on: on, start = True, ti
                if not c and on:
                    a1.axvspan(start, ti, color="#f5d9cc", alpha=0.6, lw=0)
                    a1.text((start + ti) / 2, 0.04, "priority queues ON", ha="center", fontsize=8,
                            color=INK, transform=a1.get_xaxis_transform())
                    on = False
        a1.axhline(CAPACITY, color=INK, lw=1)
        a1.axhline(ON_LEVEL * CAPACITY, color=MUTED, lw=1, ls="--")
        a1.axhline(OFF_LEVEL * CAPACITY, color=MUTED, lw=1, ls=":")
        a1.text(0.5, CAPACITY + 40, "capacity", fontsize=8, color=INK)
        a1.text(0.5, ON_LEVEL * CAPACITY - 120, "ON at 90%", fontsize=8, color=MUTED)
        a1.text(0.5, OFF_LEVEL * CAPACITY - 120, "OFF below 75%", fontsize=8, color=MUTED)
        a1.set_ylabel("offered load (kbps)")
        a1.set_title("Adaptive controller: load seen at the switch and its decisions", color=INK)
    for m, d in runs.items():
        a2.plot(*series(d, "HIGH", 2), lw=2, color=MODE_COLOR[m], label=MODE_NAME[m])
    a2.set_yscale("log"); a2.set_ylabel("HIGH class delay (ms, log)")
    a2.set_title("HIGH-priority delay during the surge", color=INK); a2.legend(fontsize=8, loc="upper right")
    if ad:
        for c in CLASSES:
            a3.plot(*series(ad, c, 1), lw=2, color=CLASS_COLOR[c], label=c)
        a3.set_ylabel("throughput (kbps)"); a3.legend(fontsize=8, loc="upper left", ncol=3)
        a3.set_title("Adaptive mode: throughput per class (HIGH and MEDIUM both send 500 kbps, so their lines overlap)", color=INK)
    a3.set_xlabel("time (s)   surge of the LOW class from t=10 s to t=25 s")
    for ax in (a2, a3):
        ax.axvspan(10, 25, color=GRID, alpha=0.35, lw=0)          # the surge period
    a3.set_xlim(-1, 44)
    fig.tight_layout()
    fig.savefig(os.path.join(PLOTS, "surge_timeline.png"), dpi=150)
    plt.close(fig)
    return runs


def policy_timeline():
    p = os.path.join(RES, "policy_adaptive.json")
    if not os.path.exists(p):
        return None
    d = load(p)
    fig, ax = plt.subplots(figsize=(10, 3.8))
    names = {"HIGH": "h1 udp/5001, 800 kbps (HIGH -> LOW)", "MEDIUM": "h2 udp/5002, 400 kbps (MEDIUM)",
             "LOW": "h3 udp/5003, 800 kbps (LOW -> HIGH)"}
    for c in CLASSES:
        ax.plot(*series(d, c, 1), lw=2, color=CLASS_COLOR[c], label=names[c])
    for t, text in d.get("events", []):
        ax.axvline(t, color=INK, lw=1, ls="--")
        ax.text(t + 0.3, ax.get_ylim()[1] * 0.92, text, fontsize=8, color=INK)
    ax.set_xlabel("time (s)"); ax.set_ylabel("throughput (kbps)")
    ax.set_title("Changing the policy while traffic is running (REST API)", color=INK)
    ax.legend(fontsize=8, loc="lower left")
    fig.tight_layout()
    fig.savefig(os.path.join(PLOTS, "policy_timeline.png"), dpi=150)
    plt.close(fig)
    return d


def series(d, cls, col):
    """Timeline as a full 1-second series: seconds with no packets count as 0 kbps / no delay."""
    rows = {r[0]: r for r in d["timeline"][cls]}
    end = int(d.get("duration") or max(rows, default=0))
    ts = list(range(0, end))                       # stop when the senders stop
    if col == 1:
        return ts, [rows[t][1] if t in rows else 0.0 for t in ts]
    return ts, [rows[t][2] if t in rows else float("nan") for t in ts]


def window(d, cls, t_from, t_to):
    """Average throughput/delay of one class between two times, from the 1 s timeline."""
    rows = [r for r in d["timeline"][cls] if t_from <= r[0] < t_to]
    if not rows:
        return 0.0, float("nan")                         # nothing received in this window
    return sum(r[1] for r in rows) / (t_to - t_from), sum(r[2] for r in rows) / len(rows)


def write_summary(runs, loads, surge, policy):
    rows = []
    for ld in loads:
        for m in MODES:
            d = runs.get((m, ld))
            if not d:
                continue
            for c in CLASSES:
                x = d["classes"][c]
                rows.append(dict(scenario="steady", mode=m, offered_kbps=offered_kbps(ld), cls=c,
                                 sent=x["sent"], rx=x["rx"], loss_pct=round(x["loss_pct"], 1),
                                 kbps=round(x["kbps"]), delay_ms=round(x["delay_ms"], 1),
                                 jitter_ms=round(x["jitter_ms"], 2), jain=round(d["fairness_jain"], 3)))
    for m, d in surge.items():
        for c in CLASSES:
            x = d["classes"][c]
            rows.append(dict(scenario="surge", mode=m, offered_kbps="", cls=c, sent=x["sent"], rx=x["rx"],
                             loss_pct=round(x["loss_pct"], 1), kbps=round(x["kbps"]),
                             delay_ms=round(x["delay_ms"], 1), jitter_ms=round(x["jitter_ms"], 2),
                             jain=round(d["fairness_jain"], 3)))
    if rows:
        with open(os.path.join(RES, "summary.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

    L = ["# Results summary (generated by analysis/plot_results.py)", ""]
    L += ["## Steady load: 3 modes x 3 load levels", "",
          "| offered kbps | mode | class | loss % | throughput kbps | avg delay ms | jitter ms | Jain |",
          "|---|---|---|---|---|---|---|---|"]
    for r in [r for r in rows if r["scenario"] == "steady"]:
        L.append(f"| {r['offered_kbps']:.0f} | {r['mode']} | {r['cls']} | {r['loss_pct']} | {r['kbps']} | "
                 f"{r['delay_ms']} | {r['jitter_ms']} | {r['jain']} |")
    if surge:
        L += ["", "## Surge scenario (LOW surges to 2000 kbps between t=10 s and t=25 s)", "",
              "| mode | HIGH loss % | HIGH avg delay ms (whole run) | HIGH avg delay ms (during surge) | LOW loss % |",
              "|---|---|---|---|---|"]
        for m in MODES:
            if m in surge:
                d = surge[m]; h = d["classes"]["HIGH"]
                _, dly = window(d, "HIGH", 10, 25)
                L.append(f"| {m} | {h['loss_pct']:.1f} | {h['delay_ms']:.1f} | {dly:.1f} | "
                         f"{d['classes']['LOW']['loss_pct']:.1f} |")
        ad = surge.get("adaptive")
        if ad:
            log = controller_log("surge_adaptive", ad["t0"])
            ons = [t for (t, _, c), prev in zip(log, [(0, 0, 0)] + log) if c and not prev[2]]
            offs = [t for (t, _, c), prev in zip(log, [(0, 0, 0)] + log) if not c and prev[2]]
            L += ["", f"Adaptive controller: priority ON at t = {', '.join(f'{t:.1f}' for t in ons) or '-'} s, "
                      f"OFF at t = {', '.join(f'{t:.1f}' for t in offs) or '-'} s (surge was 10 s - 25 s)."]
    if policy:
        t = policy["events"][0][0] if policy.get("events") else 15
        L += ["", f"## Live policy change (REST call at t = {t:.0f} s)", "",
              "Before = from t = 3 s to the change; after = from 8 s after the change to the end "
              "(once the packets already queued under the old policy have drained).", "",
              "| flow | throughput before (kbps) | throughput after (kbps) | delay before (ms) | delay after (ms) |",
              "|---|---|---|---|---|"]
        for c, name in [("HIGH", "h1 udp/5001 HIGH->LOW"), ("MEDIUM", "h2 udp/5002 MEDIUM"),
                        ("LOW", "h3 udp/5003 LOW->HIGH")]:
            k1, d1 = window(policy, c, 3, t)
            k2, d2 = window(policy, c, t + 8, policy.get("duration", 40))
            L.append(f"| {name} | {k1:.0f} | {k2:.0f} | {d1:.1f} | {d2:.1f} |".replace("nan", "-"))
    L += ["", "## Automatic checks (test cases in docs/TEST_CASES.md)", "", "| test | check | result |", "|---|---|---|"]
    for tid, text, ok in checks(runs, surge, policy):
        L.append(f"| {tid} | {text} | {'n/a (run missing)' if ok is None else ('PASS' if ok else 'FAIL')} |")
    with open(os.path.join(RES, "summary.md"), "w") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


def checks(runs, surge, policy):
    """Pass/fail verdicts for the performance test cases. None = the run is missing."""
    def loss(m, ld, c):
        return runs[(m, ld)]["classes"][c]["loss_pct"] if (m, ld) in runs else None
    out = []
    light = [runs.get((m, 0.5)) for m in MODES]
    out.append(("T10", "light load (1500 kbps): every class < 1% loss in every mode",
                None if None in light else all(d["classes"][c]["loss_pct"] < 1 for d in light for c in CLASSES)))
    ad_light = runs.get(("adaptive", 0.5))
    if ad_light:
        log = controller_log(f"steady_adaptive_load0.5", ad_light["t0"])
        out.append(("T11", "light load: adaptive controller never switches priority ON",
                    bool(log) and not any(c for _, _, c in log)))
    else:
        out.append(("T11", "light load: adaptive controller never switches priority ON", None))
    v = loss("fifo", 1.0, "HIGH")
    out.append(("T12", "FIFO under congestion: HIGH class is NOT protected (loss > 10%)", None if v is None else v > 10))
    h, mdm, lo = loss("static", 1.0, "HIGH"), loss("static", 1.0, "MEDIUM"), loss("static", 1.0, "LOW")
    out.append(("T13", "static QoS under congestion: HIGH and MEDIUM < 1% loss, LOW takes the loss",
                None if h is None else (h < 1 and mdm < 1 and lo > 20)))
    for tid, ld in (("T14", 1.0), ("T15", 1.5)):
        v = loss("adaptive", ld, "HIGH")
        out.append((tid, f"adaptive QoS at {offered_kbps(ld):.0f} kbps: HIGH loss < 2%", None if v is None else v < 2))
    ad = surge.get("adaptive")
    if ad:
        log = controller_log("surge_adaptive", ad["t0"])
        ons = [t for (t, _, c), prev in zip(log, [(0, 0, 0)] + log) if c and not prev[2]]
        offs = [t for (t, _, c), prev in zip(log, [(0, 0, 0)] + log) if not c and prev[2]]
        out.append(("T16", "surge: controller switches ON within 3 s of the surge (t=10 s)",
                    bool(ons) and 10 <= ons[0] <= 13))
        out.append(("T17", "surge: controller switches back OFF after the surge ends (t=25 s)",
                    bool(offs) and offs[-1] > 25))
    else:
        out += [("T16", "surge: controller switches ON within 3 s of the surge", None),
                ("T17", "surge: controller switches OFF after the surge", None)]
    if ad and "fifo" in surge:
        _, d_ad = window(ad, "HIGH", 12, 25)
        _, d_ff = window(surge["fifo"], "HIGH", 12, 25)
        out.append(("T18", "surge: HIGH delay with adaptive QoS < 10% of FIFO's", d_ad < 0.1 * d_ff))
    else:
        out.append(("T18", "surge: HIGH delay with adaptive QoS < 10% of FIFO's", None))
    if policy:
        t = policy["events"][0][0] if policy.get("events") else 15
        end = policy.get("duration", 40)
        up_b, upd_b = window(policy, "LOW", 3, t); up_a, upd_a = window(policy, "LOW", t + 8, end)
        dn_b, dnd_b = window(policy, "HIGH", 3, t); dn_a, dnd_a = window(policy, "HIGH", t + 8, end)
        out.append(("T19", "REST policy change: promoted flow (5003) gains throughput and its delay drops >10x; "
                           "demoted flow (5001) loses throughput and its delay rises >10x",
                    up_a > up_b and dn_a < dn_b and upd_a < upd_b / 10 and dnd_a > dnd_b * 10))
    else:
        out.append(("T19", "REST policy change takes effect", None))
    pings = [d.get("ping_loss_pct") for d in list(runs.values()) + list(surge.values()) + ([policy] if policy else [])]
    out.append(("T01", "pingall 0% loss in every run", None if not pings else all(p == 0 for p in pings)))
    return out


def main():
    os.makedirs(PLOTS, exist_ok=True)
    runs = steady_runs()
    loads = sorted({ld for (_, ld) in runs})
    if runs:
        grouped_bars(runs, loads, "loss_pct", "packet loss (%)", "loss_by_class.png",
                     "Packet loss per traffic class")
        grouped_bars(runs, loads, "delay_ms", "average one-way delay (ms, log scale)", "delay_by_class.png",
                     "Average delay per traffic class", log=True)
        high_and_fairness(runs, loads)
    surge = surge_timeline()
    policy = policy_timeline()
    write_summary(runs, loads, surge, policy)
    print(f"\nPlots written to {PLOTS}")


if __name__ == "__main__":
    main()
