#!/usr/bin/env python3
"""macOS host side of the MK20 ECM link (M4, firmware 1.1.0: 172.30.250.0/24, device .2, Mac .1).

Touches ONLY the network service whose hardware address is the MK20 host MAC (02:11:22:33:44:56, the firmware's
ECM host_addr); no other service is read-modified-written.

  mk20_host_net.py status              service, address, routes, TUN check, device subnet (read-only)
  mk20_host_net.py sync                set the Mac side to match the device's active subnet (read via adb)
  mk20_host_net.py apply [--force]     manual <subnet>.1/24; refuses unless the device is confirmed on <subnet>
  mk20_host_net.py revert              back to the pre-1.1.0 address 10.20.0.1/24
  mk20_host_net.py linklocal           add a second service on the MK20 hardware port, "MK20 link-local" =
                                       manual 169.254.7.1/16 (subnet-independent rescue path to 169.254.7.2)
  mk20_host_net.py linklocal-remove    remove that service
  options: --subnet a.b.c (default 172.30.250 or $MK20_SUBNET)

Safety: refuses on a disabled service, on an existing /24-or-narrower route inside the target /24 on another
interface (use link-local 169.254.7.2 then), or if netstat can't be parsed (fail closed). After the change,
`route get <subnet>.2` must resolve to the MK20 interface (mihomo's TUN excludes 172.16.0.0/12) and the device must
answer ping; otherwise the full previous IPv4 state (DHCP or manual ip/mask/router) is restored. Ctrl-C/SIGTERM
during the change also restores it.
Device subnet: `$MK20_ADB shell cat /tmp/mk20/gadget.env` (MK20_ADB must target the MK20 only, never the watch).
Link-local fallback: the device answers on 169.254.7.2/16 in every release; a reply needs a Mac source address it
can route back to. With the "MK20 link-local" service (169.254.7.1/16 on the same port) a bound connection works
whatever the primary subnets are (e.g. Mac on 172.30.250.1 after a device rollback to 1.0.x); without it, only
when both sides share the ECM subnet. adb over USB is unaffected either way. Afterwards `route get 169.254.1.1` must
still use the primary interface (else the service is removed again).
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import re
import shlex
import signal
import subprocess
import sys
import time

HOST_MAC = os.environ.get("MK20_HOST_MAC", "02:11:22:33:44:56").lower()
LEGACY = "10.20.0"
LL_SERVICE = "MK20 link-local"
LL_HOST, LL_MASK, LL_DEV = "169.254.7.1", "255.255.0.0", "169.254.7.2"


class Refused(Exception):
    pass


def run(cmd: list[str], check: bool = True, timeout: float = 15) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed rc={r.returncode}: {r.stderr.strip()}")
    return r.stdout


def subnet24(s: str) -> ipaddress.IPv4Network:
    if not re.fullmatch(r"(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){2}", s):
        raise argparse.ArgumentTypeError(f"bad subnet {s!r} (want a.b.c)")
    return ipaddress.IPv4Network(s + ".0/24")


def find_device(ports_out: str, mac: str = HOST_MAC) -> str | None:
    dev = None
    for line in ports_out.splitlines():
        if line.startswith("Device:"):
            dev = line.split(":", 1)[1].strip()
        elif line.startswith("Ethernet Address:") and line.split(":", 1)[1].strip().lower() == mac:
            return dev
    return None


def services_on(order_out: str, dev: str) -> list[tuple[str, bool, str]]:
    """[(service name, enabled, hardware port)] on `dev` from `networksetup -listnetworkserviceorder`."""
    name, enabled, out = None, True, []
    for line in order_out.splitlines():
        m = re.match(r"^\((\d+|\*)\) (.+)$", line)
        if m:
            name, enabled = m.group(2), m.group(1) != "*"
            continue
        m = re.match(r"^\(Hardware Port: (.*), Device: ([^\s,)]+)\)\s*$", line)
        if m and name is not None and m.group(2) == dev:
            out.append((name, enabled, m.group(1)))
            name = None
    return out


def find_service(order_out: str, dev: str, exclude: str = LL_SERVICE) -> tuple[str, bool] | None:
    """The one primary service on `dev` (an enabled one preferred; the link-local helper service excluded)."""
    c = [(n, e) for n, e, _ in services_on(order_out, dev) if n != exclude]
    en = [x for x in c if x[1]]
    if len(en) > 1:
        raise Refused(f"several enabled services on {dev}: {[n for n, _ in en]}")
    return (en or c or [None])[0]


def parse_info(info_out: str) -> dict:
    d = {"mode": "none", "ip": None, "mask": None, "router": None}
    for line in info_out.splitlines():
        if line.startswith("Manual Configuration"):
            d["mode"] = "manual"
        elif line.startswith("DHCP Configuration"):
            d["mode"] = "dhcp"
        elif line.startswith("BOOTP Configuration"):
            d["mode"] = "bootp"
        elif ":" in line:
            k, v = (x.strip() for x in line.split(":", 1))
            v = None if v in ("", "none", "(null)") else v
            if k == "IP address":
                d["ip"] = v
            elif k == "Subnet mask":
                d["mask"] = v
            elif k == "Router":
                d["router"] = v
    return d


def norm_dest(dst: str) -> ipaddress.IPv4Network | None:
    """netstat -rn destinations: default, 10.20/24, 172.30.250 (classful-abbreviated), 10.20.0.2, 224.0.0/4."""
    if dst == "default":
        return ipaddress.IPv4Network("0.0.0.0/0")
    ip, _, plen = dst.partition("/")
    parts = ip.split(".")
    if not 1 <= len(parts) <= 4 or not all(p.isdigit() and int(p) <= 255 for p in parts):
        return None
    p = int(plen) if plen.isdigit() else (32 if len(parts) == 4 else 8 * len(parts))
    try:
        return ipaddress.IPv4Network(".".join(parts + ["0"] * (4 - len(parts))) + f"/{p}", strict=False)
    except ValueError:
        return None


def parse_routes(netstat_out: str) -> list[tuple[ipaddress.IPv4Network, str, str]]:
    lines = netstat_out.splitlines()
    for i, line in enumerate(lines):
        hdr = line.split()
        if hdr[:1] == ["Destination"] and "Netif" in hdr:
            ni = hdr.index("Netif")
            out = []
            for row in lines[i + 1:]:
                cols = row.split()
                if not cols or cols[0] in ("Internet:", "Internet6:", "Destination"):
                    continue
                if len(cols) <= ni:
                    raise RuntimeError(f"unparseable netstat row: {row!r}")
                net = norm_dest(cols[0])
                if net is not None:
                    out.append((net, cols[ni], cols[0]))
            return out
    raise RuntimeError("netstat header not found (fail closed)")


def conflicts(routes, target: ipaddress.IPv4Network, dev: str):
    """Routes on other interfaces inside the target /24 that are /24 or narrower (they'd beat or tie our route).
    Broader routes (TUN catch-alls, 172.16/12 ...) lose to the connected /24 and are checked by `route get`."""
    return [raw for net, ifc, raw in routes
            if ifc not in (dev, "lo0") and net.prefixlen >= 24 and net.overlaps(target)]


def route_if(ip: str) -> str | None:
    m = re.search(r"interface:\s*(\S+)", run(["route", "-n", "get", ip], check=False))
    return m.group(1) if m else None


def ping(ip: str, tries: int = 3) -> bool:
    return any(subprocess.run(["ping", "-c", "1", "-t", "2", ip], capture_output=True).returncode == 0
               for _ in range(tries))


def device_ecm_ip() -> str | None:
    adb = os.environ.get("MK20_ADB")
    if not adb:
        return None
    try:
        out = run(shlex.split(adb) + ["shell", "cat /tmp/mk20/gadget.env 2>/dev/null"], check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"^MK20_ECM_IP=([0-9.]+)\s*$", out, re.M)
    return m.group(1) if m else None


class Host:
    def __init__(self):
        ports = run(["networksetup", "-listallhardwareports"])
        devs = [d for d in [find_device(ports)] if d]
        if sum(1 for l in ports.splitlines() if l.startswith("Ethernet Address:") and l.split(":", 1)[1].strip().lower() == HOST_MAC) > 1:
            raise Refused(f"several interfaces carry MAC {HOST_MAC} (two MK20s?) - unplug one")
        if not devs:
            raise Refused(f"MK20 interface (MAC {HOST_MAC}) not present - is the composite up?")
        self.dev = devs[0]
        order = run(["networksetup", "-listnetworkserviceorder"])
        self.all = services_on(order, self.dev)
        svc = find_service(order, self.dev)
        if not svc:
            raise Refused(f"no network service for {self.dev}")
        self.svc, self.enabled = svc
        self.port = next((p for n, _, p in self.all if n == self.svc), None)

    def linklocal(self) -> int:
        have = [x for x in self.all if x[0] == LL_SERVICE]
        if not have:
            names = run(["networksetup", "-listallnetworkservices"]).splitlines()
            if any(n.lstrip("*") == LL_SERVICE for n in names):
                raise Refused(f"a service named {LL_SERVICE!r} exists on another port; remove/rename it first")
            if not self.port:
                raise Refused("hardware port of the MK20 service unknown")
            run(["networksetup", "-createnetworkservice", LL_SERVICE, self.port])
        run(["networksetup", "-setmanual", LL_SERVICE, LL_HOST, LL_MASK])
        for _ in range(10):
            if LL_HOST in run(["ifconfig", self.dev], check=False):
                break
            time.sleep(1)
        prim = route_if("169.254.1.1")
        if prim == self.dev:
            print(f"169.254/16 now prefers {self.dev} over the primary interface -> removing {LL_SERVICE!r}", file=sys.stderr)
            run(["networksetup", "-removenetworkservice", LL_SERVICE])
            return 4
        print(f"OK {LL_SERVICE!r} on {self.port!r} ({self.dev}) = {LL_HOST}/16; 169.254/16 default still via {prim}")
        return 0

    def linklocal_remove(self) -> int:
        if any(x[0] == LL_SERVICE for x in self.all):
            run(["networksetup", "-removenetworkservice", LL_SERVICE])
        print(f"{LL_SERVICE!r} absent")
        return 0

    def info(self) -> dict:
        return parse_info(run(["networksetup", "-getinfo", self.svc]))

    def set_manual(self, ip: str, mask: str = "255.255.255.0", router: str | None = None):
        run(["networksetup", "-setmanual", self.svc, ip, mask] + ([router] if router else []))

    def restore(self, snap: dict):
        if snap["mode"] in ("dhcp", "bootp"):
            run(["networksetup", "-setdhcp" if snap["mode"] == "dhcp" else "-setbootp", self.svc])
        elif snap["mode"] == "manual" and snap["ip"]:
            self.set_manual(snap["ip"], snap["mask"] or "255.255.255.0", snap["router"])

    def status(self, target):
        i = self.info()
        print(f"service={self.svc!r} device={self.dev} enabled={self.enabled} mode={i['mode']} ip={i['ip']} mask={i['mask']}")
        print(f"link-local service {LL_SERVICE!r}: {'present' if any(x[0] == LL_SERVICE for x in self.all) else 'absent'}")
        print(f"route {target[2]} -> {route_if(str(target[2]))}   route 169.254.7.2 -> {route_if('169.254.7.2')}")
        print(f"device MK20_ECM_IP={device_ecm_ip() or 'unknown (set MK20_ADB)'}")
        print(f"ping {target[2]} {'ok' if ping(str(target[2]), 1) else 'FAILED'}")

    def apply(self, target: ipaddress.IPv4Network, force: bool = False) -> int:
        dev_ip, host_ip = str(target[2]), str(target[1])
        if not self.enabled:
            raise Refused(f"service {self.svc!r} is disabled; enable it first")
        bad = conflicts(parse_routes(run(["netstat", "-rn", "-f", "inet"])), target, self.dev)
        if bad:
            raise Refused(f"route(s) {bad} overlap {target} on other interfaces; use link-local 169.254.7.2")
        d = device_ecm_ip()
        confirmed = d is not None and ipaddress.IPv4Address(d) in target
        if not confirmed and not force:
            raise Refused(f"device ECM address is {d or 'unknown'}, not in {target}; run `sync`, or --force")
        snap = self.info()
        if snap["mode"] == "manual" and snap["ip"] == host_ip:
            print(f"already {host_ip}")
        done = False

        def _sig(signum, _frame):
            raise KeyboardInterrupt(f"signal {signum}")
        old = {s: signal.signal(s, _sig) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        try:
            self.set_manual(host_ip)
            for _ in range(10):
                if route_if(dev_ip) == self.dev:
                    break
                time.sleep(1)
            if route_if(dev_ip) != self.dev:
                print(f"route to {dev_ip} does not use {self.dev} (TUN/other route?) -> restoring", file=sys.stderr)
                return 4
            if not ping(dev_ip):
                if confirmed:
                    print(f"device {dev_ip} does not answer -> restoring", file=sys.stderr)
                    return 5
                print(f"WARNING: {host_ip} set (forced) but device {dev_ip} does not answer", file=sys.stderr)
                done = True
                return 6
            done = True
            print(f"OK {self.svc!r} ({self.dev}) = {host_ip}/24, route + ping {dev_ip} verified")
            return 0
        finally:
            if not done:
                self.restore(snap)
            for s, h in old.items():
                signal.signal(s, h)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["status", "apply", "sync", "revert", "linklocal", "linklocal-remove"])
    ap.add_argument("--subnet", type=subnet24, default=subnet24(os.environ.get("MK20_SUBNET", "172.30.250")))
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    try:
        h = Host()
        if a.cmd == "status":
            h.status(a.subnet)
            return 0
        if a.cmd == "linklocal":
            return h.linklocal()
        if a.cmd == "linklocal-remove":
            return h.linklocal_remove()
        if a.cmd == "revert":
            return h.apply(subnet24(LEGACY), a.force)
        if a.cmd == "sync":
            d = device_ecm_ip()
            if not d:
                raise Refused("device ECM address unknown (set MK20_ADB; composite must be up)")
            return h.apply(ipaddress.IPv4Network(d + "/24", strict=False), a.force)
        return h.apply(a.subnet, a.force)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
