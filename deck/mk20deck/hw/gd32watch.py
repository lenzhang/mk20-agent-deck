"""Watch for the MK20's GD32 key MCU stuck in its Maple DFU bootloader (read-only, macOS).

Incident 2026-10-25 14:08: after an external power loss the GD32 did not start its keyboard application.  The Mac
then lists **"Maple 003"** (LeafLabs, ``1eaf:0003``, serial "LLM 003") where "syk_keyboards" (``4250:426f``)
normally is, next to the T113 ("Serial", ``1d6b:0104``) under the MK20's internal hub.  The T113 side (screens,
host link) works, so the keyboard looks normal but ignores every key and knob, the K2 e-stop included.  Only a
physical replug of the MK20 USB cable fixes it (the T113 has no path to the GD32 reset / BOOT pins).

This module only *reads* the macOS USB registry (``ioreg -p IOUSB -l -w0 -a``, plist, bounded by a timeout).  It
never opens, claims or sends anything to the GD32 or its bootloader (no DFU requests, no control transfers).

A Maple device counts only when it sits on the same hub as an MK20 T113: now, or within the last ``HUB_MEMORY_S`` on
the same hub (same locationID *and* hub VID/PID: the T113 drops off the bus for a few seconds during an ECM re-arm;
another hub later plugged into that port is not the MK20's), and only after two consecutive sightings: the bootloader
is also visible for about a second at every normal power-on, never for a whole check interval.
"""
from __future__ import annotations

import logging
import os
import plistlib
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

MAPLE_DFU = (0x1EAF, 0x0003)  # stm32duino / Maple DFU bootloader (hw/gd32dfu.DFU_ID)
T113_IDS = frozenset({(0x1D6B, 0x0104), (0x1234, 0x5678)})  # T113 gadget (hw/usbenum.KNOWN)
IOREG = "/usr/sbin/ioreg"
IOREG_ARGS = ("-p", "IOUSB", "-l", "-w0", "-a")
INTERVAL_S = 30.0   # at most one ioreg run per interval
FIRST_S = 5.0       # first check this long after the daemon starts
TIMEOUT_S = 5.0     # ioreg normally takes ~30 ms
CONFIRM = 2         # consecutive sightings before the notice (a normal power-on shows the bootloader ~1 s)
HUB_MEMORY_S = 600.0  # a hub counts as the MK20's this long (wall time: Mac sleep counts) after its T113 was seen

HubKey = tuple  # (locationID, idVendor, idProduct) of a hub: the port and the hub model


@dataclass(frozen=True)
class UsbNode:
    name: str
    vid: int | None
    pid: int | None
    location: int | None  # locationID
    parent: int  # index of the parent node in the parse result, -1 = top level
    session: int | None = None  # sessionID: new for every enumeration
    serial: str = ""
    vendor: str = ""


@dataclass(frozen=True)
class Sighting:
    """A Maple DFU device on the hub of an MK20 T113."""
    name: str
    location: int | None
    hub: int | None  # locationID of the shared hub
    t113: int | None  # locationID of the T113 next to it (None: only seen there earlier)
    session: int | None
    serial: str
    vendor: str

    def describe(self) -> str:
        t113 = f"the MK20 T113 at {_loc(self.t113)}" if self.t113 is not None else "where the MK20 T113 was"
        return (f"{self.vendor or '?'} {MAPLE_DFU[0]:04x}:{MAPLE_DFU[1]:04x} \"{self.name}\" serial "
                f"\"{self.serial}\" at {_loc(self.location)}, hub {_loc(self.hub)}, next to {t113}")


def _loc(v: int | None) -> str:
    return "?" if v is None else f"0x{v:08x}"


def _int(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _str(v) -> str:
    return v if isinstance(v, str) else ""


def parse_ioreg_plist(data: bytes) -> list[UsbNode]:
    """Flatten ``ioreg -p IOUSB -l -a`` output (a plist tree under ``IORegistryEntryChildren``).  Raises on
    malformed input (``plistlib.InvalidFileException`` / ``ValueError`` ...)."""
    root = plistlib.loads(data)
    out: list[UsbNode] = []
    stack: list[tuple[object, int]] = [(c, -1) for c in reversed(_children(root))]
    while stack:
        node, parent = stack.pop()
        if not isinstance(node, dict):
            continue
        out.append(UsbNode(
            name=_str(node.get("USB Product Name")) or _str(node.get("IORegistryEntryName")),
            vid=_int(node.get("idVendor")), pid=_int(node.get("idProduct")), location=_int(node.get("locationID")),
            parent=parent, session=_int(node.get("sessionID")),
            serial=_str(node.get("kUSBSerialNumberString")) or _str(node.get("USB Serial Number")),
            vendor=_str(node.get("USB Vendor Name")) or _str(node.get("kUSBVendorString"))))
        me = len(out) - 1
        stack.extend((c, me) for c in reversed(_children(node)))
    return out


def _children(node) -> list:
    kids = node.get("IORegistryEntryChildren") if isinstance(node, dict) else None
    return kids if isinstance(kids, list) else []


def hub_key(n: UsbNode) -> HubKey:
    return (n.location, n.vid, n.pid)


def find_stuck(nodes: list[UsbNode], known_hubs: set | frozenset = frozenset()) -> tuple[
        list[Sighting], list[UsbNode], set]:
    """``(sightings, unrelated Maple devices, keys of the hubs with a T113 now)``.  A Maple DFU device is a sighting
    when its parent hub has a T113 child now, or is a hub in ``known_hubs`` (``hub_key``: locationID + VID/PID)."""
    t113_by_parent: dict[int, UsbNode] = {}
    for n in nodes:
        if (n.vid, n.pid) in T113_IDS and n.parent >= 0:  # a T113 always hangs off a hub / root port, never top level
            t113_by_parent.setdefault(n.parent, n)
    hubs_now = {hub_key(nodes[p]) for p in t113_by_parent if nodes[p].location is not None}
    hits: list[Sighting] = []
    other: list[UsbNode] = []
    for n in nodes:
        if (n.vid, n.pid) != MAPLE_DFU:
            continue
        hub = nodes[n.parent].location if n.parent >= 0 else None
        t113 = t113_by_parent.get(n.parent) if n.parent >= 0 else None
        if t113 is not None or (hub is not None and hub_key(nodes[n.parent]) in known_hubs):
            hits.append(Sighting(n.name, n.location, hub, t113.location if t113 else None, n.session, n.serial,
                                 n.vendor))
        else:
            other.append(n)
    return hits, other, hubs_now


def ioreg_probe(timeout: float = TIMEOUT_S) -> bytes:
    """The IOUSB plane as a plist (bytes).  Raises on a missing binary, timeout or non-zero exit."""
    exe = IOREG if os.path.exists(IOREG) else (shutil.which("ioreg") or "ioreg")
    p = subprocess.run([exe, *IOREG_ARGS], capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise OSError(f"ioreg exited {p.returncode}")
    return p.stdout


class Gd32DfuWatch:
    """Rate-limited detector.  ``poll()`` runs at most one probe per ``interval_s``; ``run`` / ``start`` loop it on a
    daemon thread (only while the deck daemon runs).  ``stuck`` (thread-safe read) is the confirmed state the loop
    thread shows on the keyboard; every confirmed change logs exactly one line (WARNING when it starts)."""

    def __init__(self, probe: Callable[[], bytes] = ioreg_probe, interval_s: float = INTERVAL_S,
                 first_s: float = FIRST_S, confirm: int = CONFIRM, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time) -> None:
        self.probe = probe
        self.interval_s = interval_s
        self.first_s = first_s
        self.confirm = max(1, confirm)
        self.clock = clock
        self.wall = wall
        self._lock = threading.Lock()
        self._next = clock() + first_s
        self.sighting: Sighting | None = None  # confirmed (what the notice is about); None = not stuck
        self.since: float | None = None  # wall time it was confirmed
        self.seen = 0  # consecutive checks that saw it
        self.known_hubs: dict[HubKey, float] = {}  # hub -> wall time a T113 was last seen on it (this process)
        self.hub_memory_s = HUB_MEMORY_S
        self.checks = 0
        self.failures = 0
        self._failing = False  # a failure was logged and no probe succeeded since
        self._other: frozenset = frozenset()  # unrelated Maple devices last logged (locations)

    @property
    def stuck(self) -> bool:
        return self.sighting is not None

    def status(self) -> dict:
        s = self.sighting
        return {"gd32_dfu": s is not None, "location": _loc(s.location) if s else "",
                "since": self.since, "checks": self.checks, "failures": self.failures}

    def poll(self) -> bool | None:
        """One probe if the interval has passed: the confirmed state after it, None when not due / it failed."""
        with self._lock:
            now = self.clock()
            if now < self._next:
                return None
            self._next = now + self.interval_s
            return self._check()

    def _check(self) -> bool | None:
        self.checks += 1
        try:
            nodes = parse_ioreg_plist(self.probe())
            now = self.wall()
            fresh = {k for k, t in self.known_hubs.items() if 0.0 <= now - t <= self.hub_memory_s}
            self.known_hubs = {k: self.known_hubs[k] for k in fresh}  # expired (or clock went back): forgotten
            hits, other, hubs = find_stuck(nodes, fresh)
        except Exception as e:  # noqa: BLE001 — never reaches the deck; no information this round
            self.failures += 1
            if not self._failing:
                self._failing = True
                log.debug("GD32 DFU watch: USB registry not readable (%s: %s); retrying every %.0f s",
                          type(e).__name__, str(e)[:120], self.interval_s)
            return None
        self._failing = False
        self.known_hubs.update((k, now) for k in hubs)
        locs = frozenset(n.location for n in other)
        if locs != self._other:
            self._other = locs
            if other:
                log.info("Maple DFU device(s) %s not on an MK20 hub: ignored",
                         ", ".join(f"\"{n.name}\" at {_loc(n.location)}" for n in other))
        if not hits:
            self.seen = 0
            if self.sighting is not None:
                mins = (now - self.since) / 60 if self.since is not None else 0.0
                if self.sighting.location in locs:  # still there, but its hub is no longer known as the MK20's
                    log.info("Maple DFU device at %s no longer attributed to the MK20 (no T113 on its hub for "
                             "%.0f min): GD32 notice cleared after %.0f min", _loc(self.sighting.location),
                             self.hub_memory_s / 60, mins)
                else:
                    log.info("GD32 key MCU left its DFU bootloader (Maple at %s gone after %.0f min): keys and knobs "
                             "should work again", _loc(self.sighting.location), mins)
                self.sighting, self.since = None, None
            return False
        self.seen += 1
        if self.sighting is None and self.seen >= self.confirm:
            self.sighting, self.since = hits[0], now
            log.warning("GD32 key MCU stuck in its Maple DFU bootloader (%s, seen in %d checks %.0f s apart): the "
                        "MK20 keys and knobs (incl. the K2 e-stop) do not work. Replug the MK20 USB cable. "
                        "mk20deck never talks to the bootloader.", hits[0].describe(), self.seen, self.interval_s)
        elif self.sighting is not None:
            self.sighting = hits[0]  # e.g. a new enumeration (sessionID); no new log line
        return self.sighting is not None

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.poll()
            if stop.wait(max(0.05, self._next - self.clock())):  # poll() itself enforces the interval
                return

    def start(self, stop: threading.Event) -> threading.Thread:
        th = threading.Thread(target=self.run, args=(stop,), name="mk20-gd32-watch", daemon=True)
        th.start()
        return th


def default_watch() -> Gd32DfuWatch | None:
    """The watch for ``mk20deck run``: macOS only (elsewhere there is no ioreg)."""
    return Gd32DfuWatch() if sys.platform == "darwin" else None
