#!/usr/bin/env python3
"""MK20 hardware acceptance (M7). Runs on the Mac; needs MK20_ADB (MK20-only adb wrapper).

  mk20_accept.py [--reboot] [--json OUT] [--no-hid]

Checks (each PASS/FAIL, exit 1 if any FAIL):
  adb shell; loader good==current, nothing pending; mk20d health status ok / composite / keyboard owner (stock: kd=1; mk20-kbd release: kd=0 + mk20-kbd normal) /
  watchdog / selftest
  (the device-side HID check is mk20d's self-test "PASS hid"); ping 172.30.250.2 and link-local 169.254.7.2, both
  bound to the MK20 interface; device netstat shows the bridge listening on primary + link-local; HID: the Mac
  enumerates the 1d6b:0104 keyboard (hidutil list; enumeration only, nothing is written to /dev/hidg0; --no-hid
  skips it); Mac route to the device not via a TUN; FAT/mem headroom. --reboot first does one watcher hardreset (WDT)
  and checks hardreset.last fat_ro=y.
Read-only by default (writes nothing to the device). Opt-in: --reboot (one WDT reboot); --tcp (maintenance only:
active bridge connects + Protocol A getInfo, both bound to the MK20 interface; skipped while a client is connected,
but can still evict one that attaches during the probe).
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


# The device's debug adbd reads each host packet with one unaligned bulk read: a request ("shell:<cmd>" + NUL) over
# 512 bytes can fail with EOVERFLOW, adbd drops the transport and reopens functionfs, and the WHOLE USB gadget
# re-enumerates (HID keyboard + ECM link drop; seen 2026-10-23 00:48 with a 661-byte request). Never send one.
ADB_SHELL_MAX = 480


def adb_payload_len(cmd: str) -> int:
    return len(("shell:" + cmd).encode()) + 1


def sh(cmd: str, timeout: float = 20) -> tuple[int, str]:
    n = adb_payload_len(cmd)
    if n > ADB_SHELL_MAX:
        raise ValueError(f"adb shell request of {n} bytes > {ADB_SHELL_MAX} (device adbd EOVERFLOW re-enumerates USB)")
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


def mac_bridge_sockets() -> bool:
    """Any socket on this Mac to the bridge (established or connecting)? Unknown -> True (fail closed)."""
    rc, o, _ = run_proc(["lsof", "-nP", f"-iTCP@{DEV_IP}:{PORT}", f"-iTCP@{LL_IP}:{PORT}"], 10)
    return rc not in (0, 1) or bool(o.strip())


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
    _, bid0 = sh("cat /proc/sys/kernel/random/boot_id")
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
    _, bid1 = sh("cat /proc/sys/kernel/random/boot_id")
    run.check("reboot: fresh boot (boot_id changed)", bool(bid0.strip()) and bool(bid1.strip()) and bid0.strip() != bid1.strip(),
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
    # Passive only: no writes to /dev/hidg0 (a blocked writer could linger if the host stops polling). The device side
    # is covered by mk20d's self-test ("PASS hid") in the health check.
    run.check("hid: Mac enumerates 1d6b:0104 keyboard", hid_enumerated())


def keyboard_check(h: dict) -> tuple[str, bool]:
    """Composite + keyboard owner + watchdog + selftest. Stock-era releases need KD (kd=1); a release with mk20-kbd
    enabled (health "kbd".enabled, K4 >= 1.3) must have no KD and mk20-kbd running in normal mode."""
    base = (h.get("usb", {}).get("composite") is True and h.get("watchdog") is True
            and (h.get("selftest") or {}).get("pass") is True)
    k = h.get("kbd") or {}
    if k.get("enabled") is True:
        return ("health: composite + kd=0 + mk20-kbd running (normal) + watchdog + selftest",
                base and h.get("kd") == 0 and k.get("running") is True and k.get("mode") == "normal")
    return "health: composite + kd=1 + watchdog + selftest", base and h.get("kd") == 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reboot", action="store_true")
    ap.add_argument("--no-hid", action="store_true", help="skip the HID enumeration check")
    ap.add_argument("--tcp", action="store_true",
                    help="MAINTENANCE ONLY: bound TCP connects + getInfo. The bridge is single-client, so this evicts any"
                         " connected client (e.g. the deck daemon); refused if one is connected, but a client attaching"
                         " during the probe is still evicted (accepted risk, operator's explicit choice)")
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
    name, ok = keyboard_check(h)
    run.check(name, ok, (h.get("selftest") or {}).get("detail", ""))
    fat, mem = h.get("fat_free_kb", 0), h.get("mem_avail_kb", 0)
    run.check("health: headroom (FAT >= 1024 KiB, mem >= 20 MiB)", fat >= 1024 and mem >= 20480, f"fat={fat} mem={mem}")
    iface = tt.mk20_interface()
    run.check("mac: MK20 interface present", bool(iface), str(iface))
    _, r, _ = run_proc(["route", "-n", "get", DEV_IP], 10)
    m = re.search(r"interface:\s*(\S+)", r)
    rif = m.group(1) if m else None
    run.check("mac: route to device via MK20 interface (not TUN)", bool(iface) and rif == iface, f"{DEV_IP} -> {rif}")
    p = ""
    if iface:   # bound to the MK20 interface: never via TUN
        _, p, _ = run_proc(["ping", "-b", iface, "-c", "3", "-i", "0.3", "-t", "5", DEV_IP], 15)
    m = re.search(r"([\d.]+)% packet loss", p)
    loss = float(m.group(1)) if m else 100.0
    m = re.search(r"= [\d.]+/([\d.]+)/", p)
    run.check("ping device (primary)", loss == 0.0, f"loss={loss}% avg={m.group(1) if m else '?'}ms")
    lp, ll = bridge_listening()
    run.check("bridge listening on primary + link-local (device netstat)", lp and ll, f"primary={lp} ll={ll}")
    if iface:
        _, pl, _ = run_proc(["ping", "-b", iface, "-c", "2", "-i", "0.3", "-t", "5", LL_IP], 15)
        m2 = re.search(r"([\d.]+)% packet loss", pl)
        run.check("ping link-local 169.254.7.2 (bound)", bool(m2) and float(m2.group(1)) == 0.0, m2.group(0) if m2 else "?")
    nc = bridge_clients() if a.tcp else None
    if not a.tcp:
        run.check("tcp bridge: active connects skipped (use --tcp when no other client uses the bridge)", True, "passive")
    elif nc is None:
        run.check("tcp bridge: client count known (device netstat)", False, "netstat failed: connect tests skipped")
    elif nc:   # connecting would kick the deck daemon off the single-client bridge: an active client proves the path
        run.check("tcp bridge: client already connected (connect tests skipped, not disturbed)", True, f"{nc} client(s)")
    else:
        # re-check right before each connect (device netstat + no local socket to the bridge); narrows, not closes, the race
        for name, fn in (("tcp bridge link-local 169.254.7.2 (bound)", lambda: tcp_bound(LL_IP, iface)),
                         ("protocol A getInfo over TCP (bound, primary)", lambda: get_info(iface))):
            if bridge_clients() != 0 or mac_bridge_sockets():
                run.check(name, False, "a client appeared: skipped (not evicted)")
                break
            ok, d = fn()
            run.check(name, ok, d)
    if not a.no_hid:
        hid(run)
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"time": t0, "pass": run.ok, "results": run.results, "health": h}, f, indent=1)
    print("RESULT", "PASS" if run.ok else "FAIL")
    return 0 if run.ok else 1


if __name__ == "__main__":
    sys.exit(main())
