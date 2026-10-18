#!/usr/bin/env python3
"""MK20 key-latency measurement (Mac side driver).  NOT run automatically — see
tools/KEY-LATENCY.md.  Runs only after the soak test, with the user present to press keys.

Legs (all in nanoseconds of the T113 CLOCK_MONOTONIC unless noted):
  A  GD32 report on the T113 bus  → router receive     (usbmon completion → trace "rx")   [if usbmon available]
  B  router receive               → HID report written (trace "rx" → trace "hid")
  C  HID report written           → Mac receives report (trace "hid" → Mac hidapi read, offset-corrected)
  E2E = A+B+C (or B+C when usbmon is unavailable)

Inputs on the T113 (read-only for this tool):
  * key trace written by the router when MK20_KEYTRACE=1:  "<mono_ns> rx <col> <row> <0|1>" and
    "<mono_ns> hid <col> <row> <0|1>" lines in /tmp/mk20/keytrace.log (tmpfs; never /data).
  * optional usbmon text log  /sys/kernel/debug/usb/usbmon/<bus>u  (debugfs; read only).
Clock offset Mac↔T113: min-RTT sampling of "now at N nsecs" from /proc/timer_list over adb.

Safety: uses ~/mk20-backup/mkadb.sh for every device command; never touches GD32 flash, never sends
legacy commands, never writes /data. Synthetic load only in /tmp (tmpfs) and is killed by a
trap + hard timeout.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time

MKADB = os.path.expanduser("~/mk20-backup/mkadb.sh")
TRACE = "/tmp/mk20/keytrace.log"
LOAD_SH = r"""trap 'kill 0' EXIT INT TERM
( sleep {secs}; kill 0 ) &
i=0; while [ $i -lt {cpu} ]; do ( while :; do :; done ) & i=$((i+1)); done
mkdir -p /tmp/mk20 && ( while :; do dd if=/dev/urandom of=/tmp/mk20/lat.io bs=64k count=64 2>/dev/null; sync; done ) &
wait"""


def adb(*args: str, timeout: float = 20) -> str:
    r = subprocess.run([MKADB, "shell", *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"mkadb failed: {r.stderr.strip()}")
    return r.stdout


def t113_now_ns() -> int:
    m = re.search(r"now at (\d+) nsecs", adb("head -3 /proc/timer_list"))
    if not m:
        raise RuntimeError("cannot read /proc/timer_list")
    return int(m.group(1))


def clock_offset(samples: int = 15) -> tuple[int, int]:
    """Return (offset_ns, rtt_ns) with t113 = mac_mono_ns + offset, from the min-RTT sample."""
    best = None
    for _ in range(samples):
        t0 = time.monotonic_ns()
        dev = t113_now_ns()
        t1 = time.monotonic_ns()
        rtt = t1 - t0
        if best is None or rtt < best[1]:
            best = (dev - (t0 + rtt // 2), rtt)
    return best  # type: ignore[return-value]


def pct(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    q = lambda p: s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]  # noqa: E731
    return {"n": len(s), "min": s[0], "p50": q(50), "p90": q(90), "p95": q(95), "p99": q(99), "max": s[-1],
            "mean": round(statistics.fmean(s), 3)}


def capture_mac_hid(vid: int, pid: int, n: int, timeout_s: float) -> list[int]:
    import hid  # pip install hidapi (Mac)
    h = hid.device()
    h.open(vid, pid)
    h.set_nonblocking(False)
    out, end = [], time.monotonic() + timeout_s
    while len(out) < 2 * n and time.monotonic() < end:  # down + up reports
        if h.read(64, 200):
            out.append(time.monotonic_ns())
    h.close()
    return out


def pair(trace: list[tuple[int, str, int, int, int]], mac: list[int], offset: int, usb: list[int]) -> dict:
    rx = [t for t, k, *_ in trace if k == "rx"]
    tx = [t for t, k, *_ in trace if k == "hid"]
    legA, legB, legC = [], [], []
    for r, h in zip(rx, tx):
        legB.append((h - r) / 1e6)
        prev = [u for u in usb if u <= r]
        if prev:
            legA.append((r - prev[-1]) / 1e6)
    for h, m in zip(tx, mac):
        legC.append((m + offset - h) / 1e6)
    e2e = [b + c + (a if legA else 0) for a, b, c in zip(legA or [0] * len(legB), legB, legC)]
    return {"A_gd32_to_rx_ms": pct(legA), "B_rx_to_hid_ms": pct(legB), "C_hid_to_mac_ms": pct(legC),
            "E2E_ms": pct(e2e)}


def run_phase(name: str, a, load: bool) -> dict:
    adb(f"mkdir -p /tmp/mk20 && : > {TRACE}")
    loader = None
    if load:
        loader = subprocess.Popen([MKADB, "shell", LOAD_SH.format(secs=a.max_wait + 30, cpu=a.cpu)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
    offset, rtt = clock_offset()
    print(f"[{name}] clock offset {offset} ns (rtt {rtt / 1e6:.2f} ms). Press key {a.key} {a.n} times, "
          f"~0.5 s apart …", flush=True)
    try:
        mac = capture_mac_hid(int(a.vid, 16), int(a.pid, 16), a.n, a.max_wait)
    finally:
        if loader:
            loader.terminate()
            adb("pkill -f lat.io; rm -f /tmp/mk20/lat.io; true")
    trace = []
    for line in adb(f"cat {TRACE}").splitlines():
        p = line.split()
        if len(p) == 5 and p[1] in ("rx", "hid"):
            trace.append((int(p[0]), p[1], int(p[2]), int(p[3]), int(p[4])))
    usb = []
    if a.usbmon:
        for line in adb(f"cat /tmp/mk20/usbmon.txt 2>/dev/null || true").splitlines():
            p = line.split()
            if len(p) > 3 and p[2] == "C" and p[3].startswith("Ii"):
                usb.append(int(p[1]) * 1000)  # usbmon µs timestamp → ns (same monotonic base on 5.4)
    res = pair(trace, mac[::2], offset, usb)  # key-down reports only
    res["phase"], res["load"], res["clock_rtt_ms"] = name, load, rtt / 1e6
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=50, help="key presses per phase")
    ap.add_argument("--key", default="A1", help="which key the user presses (informational)")
    ap.add_argument("--vid", default="1d6b", help="T113 HID gadget VID (hex) as seen on the Mac")
    ap.add_argument("--pid", default="0104", help="T113 HID gadget PID (hex)")
    ap.add_argument("--cpu", type=int, default=2, help="busy-loop processes for the load phase")
    ap.add_argument("--max-wait", type=float, default=120, help="seconds per phase")
    ap.add_argument("--usbmon", action="store_true", help="also compute leg A from /tmp/mk20/usbmon.txt")
    ap.add_argument("--phases", default="idle,load")
    ap.add_argument("--out", default="key-latency.json")
    ap.add_argument("--yes-after-soak", action="store_true", help="required: confirms the soak test is finished")
    a = ap.parse_args()
    if not a.yes_after_soak:
        print("refusing: pass --yes-after-soak once the 24h soak has finished (see tools/KEY-LATENCY.md)")
        return 2
    if not os.access(MKADB, os.X_OK):
        print(f"missing {MKADB}")
        return 2
    results = [run_phase(p, a, p == "load") for p in a.phases.split(",")]
    with open(a.out, "w") as f:
        json.dump(results, f, indent=1)
    for r in results:
        print(r["phase"], json.dumps({k: v for k, v in r.items() if k.endswith("_ms") and isinstance(v, dict)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
