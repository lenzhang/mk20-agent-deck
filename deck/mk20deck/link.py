"""DeviceLink: request/ack bookkeeping and stop-and-wait canvas pushes over a Transport."""
from __future__ import annotations

import logging
import queue
import statistics
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from . import protocol as P
from .layout import DeviceLayout
from .transport import Transport

log = logging.getLogger(__name__)


@dataclass
class LatencyStats:
    samples: list[float] = field(default_factory=list)
    max_samples: int = 500

    def add(self, v: float) -> None:
        self.samples.append(v)
        if len(self.samples) > self.max_samples:
            del self.samples[0]

    def pct(self, p: float) -> float | None:
        if not self.samples:
            return None
        s = sorted(self.samples)
        k = min(len(s) - 1, max(0, int(round(p / 100 * (len(s) - 1)))))
        return s[k]

    def summary(self) -> dict:
        if not self.samples:
            return {"n": 0}
        return {"n": len(self.samples), "p50_ms": round(self.pct(50) * 1000, 1),
                "p95_ms": round(self.pct(95) * 1000, 1), "mean_ms": round(statistics.mean(self.samples) * 1000, 1)}


class DeviceLink:
    """Owns the reader thread.  Key events go to ``on_key``; acks resolve waiters.

    Display flow control follows the vendor demo: one ``SHOW_JPG`` in flight; the
    device answers with ``SHOW_JPG`` + same id once drawn ("frame N update finish").
    That answer is our *display ACK* (PRD §6.3/§7.4) and its round trip is
    recorded in ``frame_ack`` stats.
    """

    def __init__(self, transport: Transport, on_key: Callable[[P.KeyEvent], None] | None = None,
                 ack_timeout: float = 1.0, preamble_flush: int = 1024 * 1024,
                 max_ack_misses: int = 3, on_json: Callable[[dict], None] | None = None) -> None:
        self.t = transport
        self.on_key = on_key
        self.ack_timeout = ack_timeout
        self.preamble_flush = preamble_flush
        self.parser = P.FrameParser()
        self.ids = P.FrameIdCounter()
        self.layout: DeviceLayout = DeviceLayout.default()
        self.frame_ack = LatencyStats()
        self.device_errors: list[str] = []
        self._pending: dict[int, "queue.Queue[P.Frame]"] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reader = threading.Thread(target=self._read_loop, name="mk20-reader", daemon=True)
        self.connected = False
        self.lost_reason: str | None = None
        self.max_ack_misses = max_ack_misses
        self.on_json = on_json  # raw tap of every device JSON frame (devtest event recorder)
        self.ack_misses = 0  # consecutive SHOW_JPG ack timeouts

    def start(self) -> None:
        self._reader.start()

    def close(self) -> None:
        self._stop.set()
        self.t.close()

    # ------------------------------------------------------------------
    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                chunk = self.t.read(0.05)
            except Exception as e:  # device unplugged
                log.warning("transport read failed: %s", e)
                self._lost(f"读取失败（拔线？）: {e}")
                return
            if not chunk:
                continue
            for fr in self.parser.feed(chunk):
                self._dispatch(fr)

    def _lost(self, reason: str) -> None:
        self.connected = False
        self.lost_reason = reason
        with self._lock:
            pending, self._pending = self._pending, {}
        for q in pending.values():  # wake waiters immediately instead of after ack_timeout
            try:
                q.put_nowait(P.Frame(0, -1, b""))
            except queue.Full:
                pass

    def _dispatch(self, fr: P.Frame) -> None:
        with self._lock:
            waiter = self._pending.pop(fr.frame_id, None)
        if fr.cmd == P.CMD_JSON:
            obj = fr.json() or {}
            if self.on_json is not None:
                try:
                    self.on_json({"id": fr.frame_id, **obj})
                except Exception:  # noqa: BLE001
                    log.exception("on_json hook failed")
            item = P.classify_json(obj)
            if isinstance(item, P.KeyEvent):
                if self.on_key:
                    self.on_key(item)
                return
            if fr.frame_id == 0xFFFFFFFF or (not isinstance(item, P.Ack) and "errorString" in obj):
                self.device_errors.append(str(obj.get("errorString")))
                log.warning("device error: %s", obj.get("errorString"))
        if waiter is not None:
            waiter.put(fr)

    def _send(self, cmd: int, data: bytes, wait: bool) -> P.Frame | None:
        fid = self.ids.next()
        q: "queue.Queue[P.Frame]" = queue.Queue(maxsize=1)
        if wait:
            with self._lock:
                self._pending[fid] = q
        try:
            self.t.write(P.encode_frame(cmd, data, fid))
        except Exception as e:  # noqa: BLE001 — serial write error == unplugged
            with self._lock:
                self._pending.pop(fid, None)
            self._lost(f"写入失败（拔线？）: {e}")
            raise ConnectionError(self.lost_reason) from e
        if not wait:
            return None
        try:
            return q.get(timeout=self.ack_timeout)
        except queue.Empty:
            with self._lock:
                self._pending.pop(fid, None)
            return None

    # ------------------------------------------------------------------
    def handshake(self, fw_base: int | None = None) -> DeviceLayout:
        if self.preamble_flush:
            # The vendor demo writes 1 MiB of '0' before getInfo, presumably to
            # flush/abort any half-received frame on the device side.
            self.t.write(b"0" * self.preamble_flush)
        ack = self.call(P.get_info())
        if ack is None or not ack.success:
            raise ConnectionError(f"getInfo failed: {ack.error if ack else 'timeout'}")
        self.layout = DeviceLayout.from_get_info(ack.result, fw_base=fw_base)
        if self.layout.fw_base is None:  # could not infer: keep the documented 0-based assumption
            self.layout.fw_base = 0
        self.connected = True
        return self.layout

    def call(self, req: dict) -> P.Ack | None:
        fr = self._send(P.CMD_JSON, P.json_bytes(req), wait=True)
        if fr is None:
            return None
        item = P.classify_json(fr.json() or {})
        return item if isinstance(item, P.Ack) else None

    def show_jpeg(self, jpeg: bytes) -> bool:
        """Push one canvas and block until the device ACKs it (or timeout)."""
        t0 = time.monotonic()
        fr = self._send(P.CMD_SHOW_JPG, jpeg, wait=True)
        if fr is None or fr.cmd != P.CMD_SHOW_JPG:
            if fr is None and self.connected:
                self.ack_misses += 1
                if self.ack_misses >= self.max_ack_misses:
                    # Port still open but the T113 stopped answering (hung / rebooting / slept).
                    self._lost(f"连续 {self.ack_misses} 帧无 ACK")
            return False
        self.ack_misses = 0
        self.frame_ack.add(time.monotonic() - t0)
        return True
