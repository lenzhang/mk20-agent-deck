#!/usr/bin/env python3
"""MK20 hardware acceptance (M7). Runs on the Mac; needs MK20_ADB (MK20-only adb wrapper).

  mk20_accept.py [--reboot] [--json OUT] [--no-hid]

Checks (each PASS/FAIL, exit 1 if any FAIL):
  adb shell; loader good==current, nothing pending; mk20d health status ok / composite / kd=1 / watchdog / selftest;
  ping 172.30.250.2 via the MK20 interface; TCP to the bridge on the primary and on link-local 169.254.7.2 (both
  bound to the MK20 interface); Protocol A getInfo over TCP (deviceVersion); HID: Mac sees the 1d6b:0104 keyboard
  and two all-released reports written to /dev/hidg0 are consumed by the host within 5 s (detached, never killed); Mac route to the device not via a
  TUN; FAT/mem headroom. --reboot first does one watcher hardreset (WDT) and checks hardreset.last fat_ro=y.
Read-only apart from --reboot and the HID probe (two all-released keyboard reports; presses nothing).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tcp_transport as tt  # noqa: E402

DEV_IP = os.environ.get("MK20_HOST", "172.30.250.2")
LL_IP = "169.254.7.2"
PORT = int(os.environ.get("MK20_BRIDGE_PORT", "17890"))


class NoAdb(RuntimeError):
    pass


def adb_cmd() -> list[str]:
    a = os.environ.get("MK20_ADB")
    if not a:
        raise NoAdb("set MK20_ADB (MK20-only adb wrapper, never the watch)")
    return shlex.split(os.path.expanduser(a))


def run_proc(cmd: list[str], timeout: float, env: dict | None = None) -> tuple[int, str, str]:
    """subprocess with its own process group; on timeout the WHOLE group is killed (uv's python, mkadb pipelines)."""
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                             start_new_session=True)
    except OSError as e:
        return 127, "", str(e)
    try:
        o, e = p.communicate(timeout=timeout)
        return p.returncode, o, e
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        p.communicate()
        return 124, "", "timeout"


def sh(cmd: str, timeout: float = 20) -> tuple[int, str]:
    rc, o, _ = run_proc(adb_cmd() + ["shell", cmd], timeout)
    return rc, o


def bridge_clients() -> int | None:
    """Established TCP clients of the KD bridge (it serves ONE client and drops the old one on a new connect).
    None = unknown (adb/netstat failed): callers must then NOT open a TCP connection."""
    rc, o = sh("netstat -tn 2>/dev/null && echo NETSTAT_RC=0", timeout=10)
    if rc != 0 or "NETSTAT_RC=0" not in o or "Proto" not in o:
        return None
    return sum(1 for l in o.splitlines() if l.startswith("tcp") and "ESTABLISHED" in l
               and l.split()[3].endswith(f":{PORT}"))


def bridge_listening() -> tuple[bool, bool]:
    _, o = sh("netstat -ltn 2>/dev/null | grep ':%d '" % PORT, timeout=10)
    return f"{DEV_IP}:{PORT}" in o, f"{LL_IP}:{PORT}" in o


class Run:
    def __init__(self, quiet: bool = False):
        self.results: list[dict] = []
        self.quiet = quiet

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append({"check": name, "pass": bool(ok), "detail": detail})
        if not self.quiet:
            print(f"{'PASS' if ok else 'FAIL'} {name}{(' - ' + detail) if detail else ''}", flush=True)
        return ok

    @property
    def ok(self) -> bool:
        return all(r["pass"] for r in self.results)


def loader_status() -> dict:
    _, out = sh("sh /data/mk20/rescue/loader.sh status")
    d = dict(re.findall(r"(\w+)=(\S*)", out))
    m = re.match(r"current=\S+ \d+ (\S+)", out)
    d["current_v"] = m.group(1) if m else ""
    d["raw"] = out.strip()
    return d


def health(v: str) -> dict:
    _, out = sh(f"/data/mk20/releases/{v}/sbin/mk20d health", timeout=40)
    try:
        return json.loads(out.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        return {}


def wait_adb(present: bool, limit: float) -> bool:
    end = time.time() + limit
    while time.time() < end:
        rc, out = sh("echo up", timeout=5)
        if (rc == 0 and "up" in out) == present:
            return True
        time.sleep(3)
    return False


def do_reboot(run: Run) -> None:
    _, up0 = sh("cut -d. -f1 /proc/uptime")
    sh("touch /mnt/SDCARD/cmd-reboot && sync")
    t0 = time.time()
    run.check("reboot: device left USB", wait_adb(False, 120), f"{time.time() - t0:.0f}s")
    run.check("reboot: adb back", wait_adb(True, 240), f"{time.time() - t0:.0f}s")
    # wait for THIS boot's mk20d to confirm (the log still holds the previous boot's lines until the new "boot" line
    # is written ~20 s in) and for the post-boot USB settling (adb re-add) to finish
    end, done = time.time() + 300, False
    probe = ("u=$(cut -d. -f1 /proc/uptime); c=$(awk '/mk20d boot /{c=0} /mk20d confirm /{c=1} END{print c+0}' "
             "/data/mk20/logs/mk20d.log 2>/dev/null); echo \"$u $c\"")
    while time.time() < end:
        rc, o = sh(probe, timeout=10)
        f = o.split()
        if rc == 0 and len(f) == 2 and f[0].isdigit() and int(f[0]) >= 100 and f[1] == "1":
            done = True
            break
        time.sleep(5)
    run.check("reboot: mk20d confirmed this boot", done)
    _, up1 = sh("cut -d. -f1 /proc/uptime")
    run.check("reboot: fresh boot", up1.strip().isdigit() and up0.strip().isdigit() and int(up1) < int(up0),
              f"uptime {up0.strip()} -> {up1.strip()}")
    _, hr = sh("cat /data/mk20/state/hardreset.last")
    run.check("reboot: clean FAT ro remount", "fat_ro=y" in hr and "mount_hung=n" in hr, hr.strip())


def tcp_bound(host: str, iface: str | None) -> tuple[bool, str]:
    if not iface:
        return False, "no MK20 interface (refusing an unbound connect that could go via TUN)"
    try:
        t = tt.TcpTransport(host, PORT, 3, bound_if=iface)
        src = t._s.getsockname()[0]
        t.close()
        return True, f"src {src}"
    except OSError as e:
        return False, str(e)


def get_info(iface: str | None) -> tuple[bool, str]:
    if not iface:
        return False, "no MK20 interface (refusing an unbound connect)"
    env = dict(os.environ, MK20_PORT=f"bound:{DEV_IP}:{PORT}:{iface}")
    rc, o, e = run_proc(["uv", "run", "-q", "--with", "pyserial", "python", os.path.join(HERE, "mk20ctl.py"),
                         "info"], 40, env=env)
    m = re.search(r'"deviceVersion":\s*"([^"]+)"', o)
    return bool(m), (m.group(1) if m else (o + e)[-200:] or f"rc={rc}")


def hid_enumerated() -> bool:
    _, out, _ = run_proc(["hidutil", "list"], 10)
    return any(re.search(r"0x1d6b\s+0x104\s+\S+\s+1\s+6\s", l) for l in out.splitlines())


def hid(run: Run) -> None:
    run.check("hid: Mac enumerates 1d6b:0104 keyboard", hid_enumerated())
    # Two ALL-RELEASED keyboard reports (report ID 1). The first write() returns once queued; the second completes only
    # after the host read the first, so finishing proves the host polls the HID endpoint. Nothing is ever pressed, so a
    # report left queued (host not polling) can't latch a key on the Mac. The writer runs detached and is never killed.
    # [ -c ]: never create a regular file /dev/hidg0 when the HID function is absent (it would mask the real node).
    # BusyBox here has no setsid/nohup: a subshell that ignores SIGHUP, with stdio detached, outlives the adb shell.
    z = "'\\001\\000\\000\\000\\000\\000\\000\\000\\000'"
    tag = f"{os.getpid()}-{int(time.time())}"
    cmd = ("[ -c /dev/hidg0 ] || { echo NOHIDG; exit 0; }; "
           "grep -qx configured /sys/class/udc/*/state || { echo NOTCONF; exit 0; }; "
           "mkdir -p /tmp/mk20; mkdir /tmp/mk20/hidtap.busy 2>/dev/null || { echo HIDBUSY; exit 0; }; "
           f"D=/tmp/mk20/hidtap.{tag}; "
           f"( trap '' HUP; printf {z} > /dev/hidg0 && printf {z} > /dev/hidg0 && touch $D; rmdir /tmp/mk20/hidtap.busy ) "
           "</dev/null >/dev/null 2>&1 & "
           "i=0; while [ $i -lt 5 ] && [ ! -f $D ]; do sleep 1; i=$((i+1)); done; "
           "[ -f $D ] && { rm -f $D; echo HIDOK; } || echo HIDPENDING")
    _, o = sh(cmd, timeout=20)
    det = next((k for k in ("HIDOK", "NOHIDG", "NOTCONF", "HIDBUSY", "HIDPENDING") if k in o), o.strip()[-80:])
    run.check("hid: host consumes keyboard reports (all keys released)", det == "HIDOK", det)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reboot", action="store_true")
    ap.add_argument("--no-hid", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    try:
        adb_cmd()
    except NoAdb as e:
        sys.exit(str(e))
    run = Run()
    t0 = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"MK20 acceptance {t0}")
    rc, o = sh("echo adb-ok", timeout=10)
    if not run.check("adb shell", rc == 0 and "adb-ok" in o):
        return 1
    if a.reboot:
        pre = loader_status()
        if pre.get("pending"):
            run.check("reboot: no release pending (a reboot would count as a probation attempt)", False, pre["raw"])
            return 1
        do_reboot(run)
    st = loader_status()
    run.check("loader: good release current, none pending",
              bool(st.get("good")) and st.get("current_v") == st.get("good") and not st.get("pending"), st["raw"])
    v = st.get("good", "")
    h = health(v) if v else {}
    run.check("health: status ok", h.get("status") == "ok", f"v={h.get('version')} status={h.get('status')}")
    run.check("health: composite + kd=1 + watchdog + selftest",
              h.get("usb", {}).get("composite") is True and h.get("kd") == 1 and h.get("watchdog") is True
              and h.get("selftest", {}).get("pass") is True, (h.get("selftest") or {}).get("detail", ""))
    fat, mem = h.get("fat_free_kb", 0), h.get("mem_avail_kb", 0)
    run.check("health: headroom (FAT >= 1024 KiB, mem >= 20 MiB)", fat >= 1024 and mem >= 20480, f"fat={fat} mem={mem}")
    iface = tt.mk20_interface()
    run.check("mac: MK20 interface present", bool(iface), str(iface))
    _, r, _ = run_proc(["route", "-n", "get", DEV_IP], 10)
    m = re.search(r"interface:\s*(\S+)", r)
    rif = m.group(1) if m else None
    run.check("mac: route to device via MK20 interface (not TUN)", bool(iface) and rif == iface, f"{DEV_IP} -> {rif}")
    _, p, _ = run_proc(["ping", "-c", "3", "-i", "0.3", "-t", "5", DEV_IP], 15)
    m = re.search(r"([\d.]+)% packet loss", p)
    loss = float(m.group(1)) if m else 100.0
    m = re.search(r"= [\d.]+/([\d.]+)/", p)
    run.check("ping device (primary)", loss == 0.0, f"loss={loss}% avg={m.group(1) if m else '?'}ms")
    lp, ll = bridge_listening()
    run.check("bridge listening on primary + link-local (device netstat)", lp and ll, f"primary={lp} ll={ll}")
    nc = bridge_clients()
    if nc is None:
        run.check("tcp bridge: client count known (device netstat)", False, "netstat failed: connect tests skipped")
    elif nc:   # connecting would kick the deck daemon off the single-client bridge: an active client proves the path
        run.check("tcp bridge: client already connected (connect tests skipped, not disturbed)", True, f"{nc} client(s)")
    else:
        ok, d = tcp_bound(DEV_IP, iface)
        run.check("tcp bridge primary (bound)", ok, d)
        ok, d = tcp_bound(LL_IP, iface)
        run.check("tcp bridge link-local 169.254.7.2 (bound)", ok, d)
        ok, d = get_info(iface)
        run.check("protocol A getInfo over TCP", ok, d)
    if not a.no_hid:
        hid(run)
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"time": t0, "pass": run.ok, "results": run.results, "health": h}, f, indent=1)
    print("RESULT", "PASS" if run.ok else "FAIL")
    return 0 if run.ok else 1


if __name__ == "__main__":
    sys.exit(main())
