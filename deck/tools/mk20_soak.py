#!/usr/bin/env python3
"""MK20 soak logger (M7). Runs on the Mac, unattended; needs MK20_ADB (MK20-only adb wrapper).

  mk20_soak.py [--hours 24] [--interval 300] [--deep-every 12] [--out DIR]

Every --interval s it appends one JSON line to <out>/soak-<start>.jsonl:
  time, health of the ACTIVE release's mk20d (status, kd, composite, selftest, mem/FAT/data free, uptime, release),
  loader state, KD pid (restart detection), log sizes, bridge listeners + established clients (device netstat),
  ping loss/rtt on 172.30.250.2 and ping on 169.254.7.2 bound to the MK20 interface.
Every --deep-every samples (0 = never) also: a read-only HID check (Mac still enumerates the 1d6b:0104 keyboard).
No TCP connects to the single-client KD bridge (they would evict the deck daemon).
Never changes device state: no reboots, no USB changes, no HID writes, never kicks a connected bridge client.
A sample whose health status is not "ok" also keeps the WARN/FAIL lines of health.selftest.detail
("selftest_warn", at most 8 lines x 160 chars) so a transient degraded sample can be attributed later.
On exit (incl. SIGTERM) writes <out>/soak-<start>.summary.json: pass/fail counts, reboots (uptime drops), KD
restarts, rollbacks newer than the one present at start, composite losses, min memory, min FAT free, and
"selftest_warn": samples per distinct WARN/FAIL message (durations such as "12s" folded to "Ns").
Kernel memory (slow kernel leaks hide under a flat MemAvailable; SLUB without SLUB_DEBUG: no /proc/slabinfo):
every sample keeps "meminfo" = /proc/meminfo MemAvailable, MemFree, Slab, SReclaimable, SUnreclaim, Shmem,
Cached in kB (one short adb shell call; a missing/unparsable field is null). The summary's
"kernel_mem" has first/last/min/max of Slab and SUnreclaim over the run and least-squares slopes in kB/h of
SUnreclaim and MemAvailable since the last reboot (null below 6 samples or 1 h). "kernel_mem_trend":
"growing" = SUnreclaim slope > 50 kB/h (= 1.2 MB/day) over >= 6 h, in the whole span AND in each half of it
(sustained: a one-off step raises only one half); "flat" = >= 6 h of samples without that; "insufficient" = fewer
than 6 samples, under 6 h, or a half with < 3 samples.
Host link liveness (1.6.1, incident 2026-10-23: the bridge hung 51 min on a stalled pty while every sample stayed ok):
every sample keeps "kbd_link" = json_in / jpg_ok / host / host_flushes from the mk20-kbd status string the health
JSON already embeds (no extra adb call; a missing field is null). The Mac sends a heartbeat every 1.2 s, so while a
client is connected json_in must grow: bridge_clients >= 1 in this and the previous sample with json_in not
increased -> "host_link": "stalled" and the sample is NOT ok ("ok": json_in grew; "idle": no client in one of the
two samples; "unknown": no previous sample / missing data / reboot / mk20-kbd restart between them). The summary
counts "host_link_stalls" and keeps "first_host_link_stall".
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
WARN_LINES, WARN_CHARS = 8, 160
# mk20d joins its selftest lines with spaces (health JSON string): a line starts at each level keyword
_LEVEL = re.compile(r"(?:^|(?<=\s))(?=(?:PASS|INFO|WARN|FAIL) )")
MEMINFO_KEYS = ("MemAvailable", "MemFree", "Slab", "SReclaimable", "SUnreclaim", "Shmem", "Cached")
# device side (BusyBox 1.27.2 awk): one line "meminfo=MemFree:41000 MemAvailable:52000 Cached:9000 ..." (kB)
MEMINFO_SH = "echo meminfo=$(awk '/^(" + "|".join(MEMINFO_KEYS) + "):/{print $1 $2}' /proc/meminfo 2>/dev/null)"
_MEMINFO = re.compile(r"(?<![\w(])([A-Za-z]\w*):\s*(\d+)(?!\w)")
SLOPE_MIN_N, SLOPE_MIN_H = 6, 1.0        # a slope needs >= 6 samples spanning >= 1 h
TREND_KB_H, TREND_MIN_H = 50.0, 6.0      # "growing": SUnreclaim > 50 kB/h sustained over >= 6 h


def _stop(*_a):
    global STOP
    STOP = True


def selftest_problems(detail) -> list:
    """The WARN/FAIL lines of health.selftest.detail, bounded to WARN_LINES x WARN_CHARS."""
    out: list = []
    if not isinstance(detail, str):
        return out
    for part in _LEVEL.split(detail):
        line = " ".join(part.split())
        if line.startswith(("WARN ", "FAIL ")):
            out.append(line[:WARN_CHARS])
            if len(out) == WARN_LINES:
                break
    return out


def problem_key(line: str) -> str:
    """Summary bucket: ages/uptimes change every sample ("stale (12s, up 3600s)"), the message does not."""
    return re.sub(r"\b\d+s\b", "Ns", line)


def parse_meminfo(text) -> dict:
    """MEMINFO_KEYS in kB from /proc/meminfo lines ("Slab:   11820 kB") or the one-line MEMINFO_SH form
    ("Slab:11820 SUnreclaim:9000"). A missing or unparsable field is None; never raises."""
    out: dict = dict.fromkeys(MEMINFO_KEYS)
    if isinstance(text, str):
        for m in _MEMINFO.finditer(text):
            if m.group(1) in out and out[m.group(1)] is None:
                out[m.group(1)] = int(m.group(2))
    return out


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _ls_kb_h(pts) -> float | None:
    """Least-squares slope in kB/h of [(epoch_s, kB), ...]; None for < 2 points or no time spread."""
    if len(pts) < 2:
        return None
    xs = [(t - pts[0][0]) / 3600 for t, _ in pts]
    mx, my = sum(xs) / len(xs), sum(v for _, v in pts) / len(pts)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    return round(sum((x - mx) * (v - my) for x, (_, v) in zip(xs, pts)) / sxx, 1)


def _clean(pts) -> list:
    return [(t, v) for t, v in pts if _num(t) and _num(v)]


def slope_kb_h(pts) -> float | None:
    """Least-squares slope (kB/h) of [(epoch_s, kB), ...]; None below SLOPE_MIN_N points or SLOPE_MIN_H span."""
    pts = _clean(pts)
    if len(pts) < SLOPE_MIN_N or pts[-1][0] - pts[0][0] < SLOPE_MIN_H * 3600:
        return None
    return _ls_kb_h(pts)


def half_slopes_kb_h(pts) -> list | None:
    """[first half, second half] slopes (kB/h) of the time span; a half with < 3 points or spanning < 0.5 h is None
    (a few minutes of samples give a noisy slope)."""
    pts = _clean(pts)
    if len(pts) < 2:
        return None
    mid = (pts[0][0] + pts[-1][0]) / 2
    halves = ([p for p in pts if p[0] <= mid], [p for p in pts if p[0] >= mid])
    return [_ls_kb_h(h) if len(h) >= 3 and h[-1][0] - h[0][0] >= 1800 else None for h in halves]


def recent_slope_kb_h(pts, hours: float = TREND_MIN_H) -> float | None:
    """Slope (kB/h) over the last `hours` only — shows a leak that starts late, which the whole-span trend (both
    halves must grow) reports as "flat" for a long time. None until the samples span `hours`."""
    pts = _clean(pts)
    if not pts or pts[-1][0] - pts[0][0] < hours * 3600:
        return None
    return slope_kb_h([p for p in pts if p[0] >= pts[-1][0] - hours * 3600])


def kernel_mem_trend(pts) -> str:
    """SUnreclaim [(epoch_s, kB), ...] -> "growing" | "flat" | "insufficient" (thresholds: module docstring)."""
    pts = _clean(pts)
    if len(pts) < SLOPE_MIN_N or pts[-1][0] - pts[0][0] < TREND_MIN_H * 3600:
        return "insufficient"
    halves = half_slopes_kb_h(pts)
    if None in halves:
        return "insufficient"
    full = slope_kb_h(pts)
    return "growing" if full is not None and full > TREND_KB_H and min(halves) > TREND_KB_H else "flat"


def kernel_mem_summary(stats: dict, pts: dict, since) -> dict:
    """Summary block: Slab/SUnreclaim first/last/min/max over the run (stats), slopes since `since` (last reboot)."""
    su, ma = pts.get("SUnreclaim") or [], pts.get("MemAvailable") or []
    return {"Slab": stats.get("Slab"), "SUnreclaim": stats.get("SUnreclaim"), "slope_since": since,
            "slope_samples": len(su), "slope_hours": round((su[-1][0] - su[0][0]) / 3600, 2) if su else 0,
            "sunreclaim_kb_h": slope_kb_h(su), "sunreclaim_half_kb_h": half_slopes_kb_h(su),
            "sunreclaim_last6h_kb_h": recent_slope_kb_h(su),
            "mem_avail_kb_h": slope_kb_h(ma)}


KBD_LINK_KEYS = ("json_in", "jpg_ok", "host", "host_flushes")


def kbd_link(status) -> dict:
    """json_in / jpg_ok / host / host_flushes of the mk20-kbd status (health "kbd"."status": the kbd.status JSON as a
    string, cut at 700 bytes by mk20d, so parsed field by field). Missing/unparsable -> None; never raises."""
    out: dict = dict.fromkeys(KBD_LINK_KEYS)
    if isinstance(status, dict):
        return {k: status.get(k) if _num(status.get(k)) else None for k in KBD_LINK_KEYS}
    if isinstance(status, str):
        for k in KBD_LINK_KEYS:
            m = re.search(r'"%s"\s*:\s*(\d+)(?![\d.])' % k, status)
            out[k] = int(m.group(1)) if m else None
    return out


def host_link_state(prev, cur) -> str:
    """"stalled" | "ok" | "idle" | "unknown" for sample `cur` against the previous sample `prev` (module docstring)."""
    if not isinstance(prev, dict) or not isinstance(cur, dict):
        return "unknown"
    pc, cc = prev.get("bridge_clients"), cur.get("bridge_clients")
    if not (_num(pc) and _num(cc)):
        return "unknown"
    if pc < 1 or cc < 1:
        return "idle"
    pj, cj = (prev.get("kbd_link") or {}).get("json_in"), (cur.get("kbd_link") or {}).get("json_in")
    ev = cur.get("event") or ""
    if not (_num(pj) and _num(cj)) or "reboot" in ev or "kd-restart" in ev:
        return "unknown"
    pk, ck = prev.get("kbd_pid"), cur.get("kbd_pid")
    if pk and ck and pk != ck:
        return "unknown"   # mk20-kbd restarted (its counters start over)
    return "ok" if cj > pj else "stalled"


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
        k = h.get("kbd") or {}   # mk20-kbd releases (K4, >= 1.3): mode/restarts from mk20d health
        s["health"]["kbd"] = {x: k.get(x) for x in ("enabled", "mode", "running", "restarts", "recent_crashes")}
        s["kbd_link"] = kbd_link(k.get("status"))   # host link liveness: json_in must grow while a client is connected
        if h and h.get("status") != "ok":   # degraded/fail: keep what selftest complained about
            s["selftest_warn"] = selftest_problems((h.get("selftest") or {}).get("detail"))
        s["keyboard_ok"] = acc.keyboard_check(h)[1]   # stock: kd=1; mk20-kbd release: kd=0 + mk20-kbd normal
        _, o = acc.sh("echo kd=$(pgrep -f '^/data/KeyboardDevice' | head -n 1); "
                      # pidof (process name), not `pgrep -f "bin/mk20-kbd --run"`: that also matches this adb sh -c and,
                      # after a pid wrap, reported the shell as a "restart" every sample (10-20/21 overnight soak).
                      "echo kbd=$(for p in $(pidof mk20-kbd); do grep -q -- --run /proc/$p/cmdline && echo $p; done | head -n 1); "
                      "echo logkb=$(du -k /data/mk20/logs /mnt/SDCARD/mk20/logs 2>/dev/null | awk '{print $1}' | tr '\\n' ' '); "
                      "echo bridgelog=$(wc -c < /tmp/mk20/bridge.log 2>/dev/null)")
        f = dict(l.split("=", 1) for l in o.splitlines() if "=" in l)
        # Own short call: appending it to the pid probe above would push that request to ~464 bytes, too close to
        # the 512-byte adbd limit (acc.ADB_SHELL_MAX).
        _, mo = acc.sh(MEMINFO_SH, timeout=10)
        s["meminfo"] = parse_meminfo(dict(l.split("=", 1) for l in mo.splitlines() if "=" in l).get("meminfo"))
        s["kd_pid"] = f.get("kd", "").strip()
        s["kbd_pid"] = f.get("kbd", "").strip()
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
    # No TCP connects at all: the KD bridge serves ONE client and evicts the old one on a new connect.
    if deep and not STOP:
        s["hid"] = acc.hid_enumerated()
    h = s.get("health") or {}
    s["ok"] = bool(s["adb"] and iface and s["route_if"] == iface and h.get("status") == "ok" and s.get("keyboard_ok") is True
                   and s["ping_loss"] == 0 and s.get("ping_ll_loss", 100) == 0
                   and s.get("listen_primary") and s.get("listen_ll")
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
    ap.add_argument("--out", default=os.path.expanduser("~/mk20-backup/soak"))
    a = ap.parse_args()
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
    last_up = last_kd = last_kr = None
    rollbacks: list = []
    first_lr = None
    min_mem = min_fat = None
    first_fail = None
    comp_losses, last_comp = 0, None
    warn_counts: dict = {}
    km_stats: dict = {}                                  # Slab/SUnreclaim first/last/min/max over the run
    km_pts: dict = {"SUnreclaim": [], "MemAvailable": []}   # (host epoch, kB) since the last reboot
    km_since = None
    summary: dict = {"start": stamp, "samples": 0, "kernel_mem": None, "kernel_mem_trend": "insufficient",
                     "host_link_stalls": 0, "first_host_link_stall": None}
    prev = None                                          # previous sample (host link liveness)
    hl_stalls, first_hl = 0, None
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
                km_pts = {k: [] for k in km_pts}     # kernel memory starts over at boot: slopes per boot
                km_since = None
            last_up = up if isinstance(up, int) else last_up
            kr = (h.get("kbd") or {}).get("restarts")   # mk20d's own count of real mk20-kbd exits (authoritative)
            kd = s.get("kbd_pid") or s.get("kd_pid")   # fallback for stock-era releases: the KD pid changed
            if isinstance(kr, int) and isinstance(last_kr, int) and s.get("event") != "reboot":
                if kr > last_kr:
                    kd_restarts += kr - last_kr
                    s["event"] = (s.get("event", "") + " kd-restart").strip()
            elif not isinstance(kr, int) and kd and last_kd and kd != last_kd and s.get("event") != "reboot":
                kd_restarts += 1
                s["event"] = (s.get("event", "") + " kd-restart").strip()
            last_kd = kd or last_kd
            last_kr = kr if isinstance(kr, int) else last_kr
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
            mi = s.get("meminfo") if isinstance(s.get("meminfo"), dict) else {}
            for k in ("Slab", "SUnreclaim"):
                v = mi.get(k)
                if _num(v):
                    st = km_stats.setdefault(k, {"first": v, "last": v, "min": v, "max": v})
                    st.update(last=v, min=min(st["min"], v), max=max(st["max"], v))
            ma = mi.get("MemAvailable") if _num(mi.get("MemAvailable")) else h.get("mem_avail_kb")
            for k, v in (("SUnreclaim", mi.get("SUnreclaim")), ("MemAvailable", ma)):
                if _num(v):
                    km_pts[k].append((t, v))
                    km_since = km_since or s.get("time")
            s["host_link"] = host_link_state(prev, s)
            if s["host_link"] == "stalled":              # connected but nothing reaches mk20-kbd: not ok
                s["ok"] = False
                hl_stalls += 1
                first_hl = first_hl or s.get("time")
            prev = s
            for key in {problem_key(x) for x in s.get("selftest_warn") or [] if isinstance(x, str)}:
                warn_counts[key] = warn_counts.get(key, 0) + 1
            if not s.get("ok"):
                bad += 1
                first_fail = first_fail or s.get("time")
            with open(jl, "a") as f:
                f.write(json.dumps(s) + "\n")
            summary = {"start": stamp, "samples": n, "failed": bad, "first_fail": first_fail, "reboots": reboots,
                       "kd_restarts": kd_restarts, "rollbacks": rollbacks, "composite_losses": comp_losses, "min_mem_kb": min_mem,
                       "min_fat_free_kb": min_fat, "last": s.get("time"), "running": True, "pid": os.getpid(),
                       "selftest_warn": dict(sorted(warn_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
                       "kernel_mem": kernel_mem_summary(km_stats, km_pts, km_since),
                       "kernel_mem_trend": kernel_mem_trend(km_pts["SUnreclaim"]),
                       "host_link_stalls": hl_stalls, "first_host_link_stall": first_hl}
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
