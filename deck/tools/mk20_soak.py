#!/usr/bin/env python3
"""MK20 soak logger (M7). Runs on the Mac, unattended; needs MK20_ADB (MK20-only adb wrapper).

  mk20_soak.py [--hours 24] [--interval 300] [--deep-every 12] [--out DIR]

Every --interval s it appends one JSON line to <out>/soak-<start>.jsonl:
  time, health of the ACTIVE release's mk20d (status, kd, composite, selftest, mem/FAT/data free, uptime, release),
  loader state, KD pid (restart detection), log sizes, bridge listeners + established clients (device netstat),
  ping loss/rtt on 172.30.250.2 and ping on 169.254.7.2 bound to the MK20 interface.
Every --deep-every samples (0 = never) also: a read-only HID check (Mac still enumerates the 1d6b:0104 keyboard).
With --tcp-probe (off by default: check-then-connect is racy) deep samples also do bound TCP connects + getInfo
when the single-client KD bridge has no client.
Never changes device state: no reboots, no USB changes, no HID writes, never kicks a connected bridge client.
On exit (incl. SIGTERM) writes <out>/soak-<start>.summary.json: pass/fail counts, reboots (uptime drops), KD
restarts, rollbacks newer than the one present at start, composite losses, min memory, min FAT free.
Stop early: kill the process (pid in <out>/soak.pid); the summary is still written.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mk20_accept as acc  # noqa: E402
import tcp_transport as tt  # noqa: E402

STOP = False
TCP_PROBE = False


def _stop(*_a):
    global STOP
    STOP = True


def sample(deep: bool) -> dict:
    s: dict = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "deep": deep}
    rc, o = acc.sh("echo up", timeout=10)
    s["adb"] = rc == 0 and "up" in o
    if s["adb"]:
        st = acc.loader_status()
        s["loader"] = {k: st.get(k, "") for k in ("good", "pending", "current_v", "last_rollback")}
        v = st.get("current_v") or st.get("good")   # the ACTIVE release's mk20d
        h = acc.health(v) if v else {}
        s["health"] = {k: h.get(k) for k in ("status", "version", "uptime_s", "kd", "watchdog", "mem_avail_kb",
                                             "fat_free_kb", "data_free_kb", "shim_mounts", "adbd", "flapmon")}
        s["health"]["composite"] = (h.get("usb") or {}).get("composite")
        s["health"]["selftest"] = (h.get("selftest") or {}).get("pass")
        _, o = acc.sh("echo kd=$(pgrep -f '^/data/KeyboardDevice' | head -n 1); "
                      "echo logkb=$(du -k /data/mk20/logs /mnt/SDCARD/mk20/logs 2>/dev/null | awk '{print $1}' | tr '\\n' ' '); "
                      "echo bridgelog=$(wc -c < /tmp/mk20/bridge.log 2>/dev/null)")
        f = dict(l.split("=", 1) for l in o.splitlines() if "=" in l)
        s["kd_pid"] = f.get("kd", "").strip()
        s["log_kb"] = f.get("logkb", "").strip()
        s["bridge_log_b"] = f.get("bridgelog", "").strip()
        lp, ll = acc.bridge_listening()
        s["listen_primary"], s["listen_ll"] = lp, ll
        s["bridge_clients"] = acc.bridge_clients()
    iface = tt.mk20_interface()
    s["iface"] = iface
    _, r, _ = acc.run_proc(["route", "-n", "get", acc.DEV_IP], 10)
    m = re.search(r"interface:\s*(\S+)", r)
    s["route_if"] = m.group(1) if m else None
    p = ""
    if iface:   # every probe is bound to the MK20 interface (never via TUN)
        _, p, _ = acc.run_proc(["ping", "-b", iface, "-c", "3", "-i", "0.3", "-t", "5", acc.DEV_IP], 15)
    s["ping_loss"] = float((re.search(r"([\d.]+)% packet loss", p) or [None, "100"])[1])
    s["ping_avg_ms"] = (re.search(r"= [\d.]+/([\d.]+)/", p) or [None, None])[1]
    if iface:   # link-local path without touching the bridge (macOS ping -b = bind to interface)
        _, p, _ = acc.run_proc(["ping", "-b", iface, "-c", "2", "-i", "0.3", "-t", "5", acc.LL_IP], 15)
        s["ping_ll_loss"] = float((re.search(r"([\d.]+)% packet loss", p) or [None, "100"])[1])
    # The bridge serves ONE client and drops the old one on a new connect: only open TCP when nobody is connected.
    if TCP_PROBE and deep and s.get("bridge_clients") == 0 and not STOP:
        s["tcp_primary"] = acc.tcp_bound(acc.DEV_IP, iface)[0]
        s["tcp_ll"] = acc.tcp_bound(acc.LL_IP, iface)[0]
        s["getinfo"] = acc.get_info(iface)[0]
    if deep and not STOP:
        s["hid"] = acc.hid_enumerated()
    h = s.get("health") or {}
    s["ok"] = bool(s["adb"] and iface and s["route_if"] == iface and h.get("status") == "ok" and h.get("kd") == 1 and h.get("composite") is True
                   and s["ping_loss"] == 0 and s.get("ping_ll_loss", 100) == 0
                   and s.get("listen_primary") and s.get("listen_ll")
                   and s.get("tcp_primary", True) and s.get("tcp_ll", True) and s.get("getinfo", True)
                   and s.get("hid", True))
    return s


def _write_json(path: str, obj: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--deep-every", type=int, default=12)
    ap.add_argument("--tcp-probe", action="store_true",
                    help="deep samples also connect to the bridge + getInfo when it has no client (racy: off by default)")
    ap.add_argument("--out", default=os.path.expanduser("~/mk20-backup/soak"))
    a = ap.parse_args()
    global TCP_PROBE
    TCP_PROBE = a.tcp_probe
    if not os.environ.get("MK20_ADB"):
        sys.exit("set MK20_ADB (MK20-only adb wrapper, never the watch)")
    os.makedirs(a.out, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    jl, sm = os.path.join(a.out, f"soak-{stamp}.jsonl"), os.path.join(a.out, f"soak-{stamp}.summary.json")
    lockf = open(os.path.join(a.out, "soak.lock"), "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(f"another soak is running in {a.out} (soak.lock held)")
    pidf = os.path.join(a.out, "soak.pid")
    with open(pidf, "w") as f:
        f.write(f"{os.getpid()}\n")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    end = time.time() + a.hours * 3600
    n = bad = reboots = kd_restarts = 0
    last_up = last_kd = None
    rollbacks: list = []
    first_lr = None
    min_mem = min_fat = None
    first_fail = None
    comp_losses, last_comp = 0, None
    summary: dict = {"start": stamp, "samples": 0}
    try:
        while not STOP and time.time() < end:
            t = time.time()
            try:
                s = sample(deep=bool(a.deep_every) and n % a.deep_every == 0)
            except Exception as e:  # noqa: BLE001 - a soak logger must not die
                s = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "ok": False, "error": repr(e)}
            n += 1
            h = s.get("health") or {}
            up = h.get("uptime_s")
            if isinstance(up, int) and last_up is not None and up < last_up:
                reboots += 1
                s["event"] = "reboot"
            last_up = up if isinstance(up, int) else last_up
            kd = s.get("kd_pid")
            if kd and last_kd and kd != last_kd and s.get("event") != "reboot":
                kd_restarts += 1
                s["event"] = (s.get("event", "") + " kd-restart").strip()
            last_kd = kd or last_kd
            lr = (s.get("loader") or {}).get("last_rollback")
            if lr is not None and first_lr is None:
                first_lr = lr                       # pre-existing record, not a soak event
            elif lr and lr != first_lr and (not rollbacks or rollbacks[-1]["record"] != lr):
                rollbacks.append({"time": s.get("time"), "record": lr})
                s["event"] = (s.get("event", "") + " rollback").strip()
            comp = h.get("composite")
            if last_comp is True and comp is False:
                comp_losses += 1
                s["event"] = (s.get("event", "") + " composite-lost").strip()
            last_comp = comp if comp is not None else last_comp
            for k, cur in (("mem_avail_kb", "min_mem"), ("fat_free_kb", "min_fat")):
                v = h.get(k)
                if isinstance(v, int):
                    if cur == "min_mem":
                        min_mem = v if min_mem is None else min(min_mem, v)
                    else:
                        min_fat = v if min_fat is None else min(min_fat, v)
            if not s.get("ok"):
                bad += 1
                first_fail = first_fail or s.get("time")
            with open(jl, "a") as f:
                f.write(json.dumps(s) + "\n")
            summary = {"start": stamp, "samples": n, "failed": bad, "first_fail": first_fail, "reboots": reboots,
                       "kd_restarts": kd_restarts, "rollbacks": rollbacks, "composite_losses": comp_losses, "min_mem_kb": min_mem,
                       "min_fat_free_kb": min_fat, "last": s.get("time"), "running": True, "pid": os.getpid()}
            _write_json(sm, summary)
            while not STOP and time.time() < min(end, t + a.interval):
                time.sleep(1)
    finally:
        summary["running"] = False
        summary["ended"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        _write_json(sm, summary)
        try:
            with open(pidf) as f:
                if f.read().strip() == str(os.getpid()):
                    os.remove(pidf)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
