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
                 max_ack_misses: int = 3, on_json: Callable[[dict], None] | None = None,
                 on_event: Callable[[dict], None] | None = None) -> None:
        self.t = transport
        self.on_key = on_key
        self.on_event = on_event  # other device-initiated JSON events ({"method": ...}, no ack), e.g. ui.openSettings
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
        self._wlock = threading.Lock()  # one writer at a time: the heartbeat thread shares the tty with pushes
        # mk20-kbd (fw >= 1.3) host liveness, kbd/UI.md: after ui.hello the device counts only ui.heartbeat
        # samples, on every page. Without it the device drops our frames on HOME/settings pages, declares the
        # host lost after 5 s and L1 stays "Reconnect required" (getInfo is only sent at connect).
        self.ui_negotiated = False
        self.ui_beat_s = 1.2
        self.ui_hello_result: dict = {}
        self.ui_beats = 0  # accepted heartbeats
        self.ui_beat_rejects = 0  # heartbeats answered success:false (lost / SUSPECT / not negotiated)
        self.ui_beat_timeouts = 0  # heartbeats without an answer within ack_timeout
        self.ui_rehellos = 0  # hellos after a rejected heartbeat (device had declared us lost)
        self._beat: threading.Thread | None = None

    def start(self) -> None:
        self._reader.start()

    def close(self) -> None:
        self._stop.set()
        self._wake_waiters()  # a heartbeat blocked on its ack returns now instead of after ack_timeout
        self.t.close()
        beat = self._beat
        if beat is not None and beat is not threading.current_thread():
            beat.join(timeout=2)
        if self.ui_negotiated or self.ui_beats or self.ui_rehellos:
            log.info("ui liveness: %d heartbeats accepted, %d rejected, %d timeouts, %d re-hellos",
                     self.ui_beats, self.ui_beat_rejects, self.ui_beat_timeouts, self.ui_rehellos)

    # ------------------------------------------------------------------
    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                chunk = self.t.read(0.05)
            except Exception as e:  # device unplugged
                if self._stop.is_set():  # close() shut the transport under us (EBADF): not a loss
                    return
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
        self._wake_waiters()

    def _wake_waiters(self) -> None:
        with self._lock:
            pending, self._pending = self._pending, {}
        for q in pending.values():  # wake waiters immediately instead of after ack_timeout
            try:
                q.put_nowait(P.Frame(0, -1, b""))
            except queue.Full:
                pass

    def _dispatch(self, fr: P.Frame) -> None:
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
                return  # device events never resolve a waiter (even if their id matched one of ours)
            if not isinstance(item, P.Ack) and "method" in obj and "errorString" not in obj:
                self._event(obj)
                return
            if fr.frame_id == 0xFFFFFFFF or (not isinstance(item, P.Ack) and "errorString" in obj):
                self.device_errors.append(str(obj.get("errorString")))
                log.warning("device error: %s", obj.get("errorString"))
        with self._lock:
            waiter = self._pending.pop(fr.frame_id, None)
        if waiter is not None:
            waiter.put(fr)

    def _event(self, obj: dict) -> None:
        """Device-initiated non-key event: hand to ``on_event``; unknown ones are dropped. Runs on the reader thread,
        so the hook must not block and can never take the reader down."""
        if self.on_event is None:
            log.info("ignored device event %r", str(obj.get("method"))[:64])
            return
        try:
            self.on_event(obj)
        except Exception:  # noqa: BLE001
            log.exception("device event hook failed for %r", str(obj.get("method"))[:64])

    def _send(self, cmd: int, data: bytes, wait: bool) -> P.Frame | None:
        q: "queue.Queue[P.Frame]" = queue.Queue(maxsize=1)
        with self._lock:  # ids are allocated from two threads (pushes + heartbeat): a duplicate would misroute acks
            fid = self.ids.next()
            if wait:
                self._pending[fid] = q
        try:
            with self._wlock:
                self.t.write(P.encode_frame(cmd, data, fid))
        except Exception as e:  # noqa: BLE001 — serial write error == unplugged
            with self._lock:
                self._pending.pop(fid, None)
            self._lost(f"写入失败（拔线？）: {e}")
            raise ConnectionError(self.lost_reason) from e
        if not wait:
            return None
        try:
            # close() wakes registered waiters; one registered after that wake-up must not sit out ack_timeout
            return q.get(timeout=0 if self._stop.is_set() else self.ack_timeout)
        except queue.Empty:
            with self._lock:
                self._pending.pop(fid, None)
            return None

    # ------------------------------------------------------------------
    def handshake(self, fw_base: int | None = None) -> DeviceLayout:
        if self.preamble_flush:
            # The vendor demo writes 1 MiB of '0' before getInfo, presumably to
            # flush/abort any half-received frame on the device side.
            with self._wlock:
                self.t.write(b"0" * self.preamble_flush)
        ack = self.call(P.get_info())
        if ack is None or not ack.success:
            raise ConnectionError(f"getInfo failed: {ack.error if ack else 'timeout'}")
        self.layout = DeviceLayout.from_get_info(ack.result, fw_base=fw_base)
        if self.layout.fw_base is None:  # could not infer: keep the documented 0-based assumption
            self.layout.fw_base = 0
        self.connected = True
        if self.ui_hello() and (self._beat is None or not self._beat.is_alive()):
            self._beat = threading.Thread(target=self._beat_loop, name="mk20-ui-heartbeat", daemon=True)
            self._beat.start()
        return self.layout

    # The device counts heartbeat samples >= 1 s apart and restarts the count after a > 1.5 s gap (kbd/UI.md).
    ui_beat_factor = 1.2  # x heartbeat_ms: aim inside that window
    ui_beat_min_s, ui_beat_max_s = 0.5, 5.0  # never let a device reply make us spin / go silent
    ui_rehello_min_s = 2.0  # rate limit of re-hellos after rejected heartbeats

    def ui_hello(self) -> bool:
        """Negotiate mk20-kbd host liveness. False on stock KD / fw < 1.3 ("Unknown method"): legacy mode,
        where decoded frames on the host page are the liveness signal."""
        try:
            ack = self.call(P.ui_hello())
        except ConnectionError:
            self.ui_negotiated = False
            return False
        if not (ack and ack.success and ack.result.get("version") == 1):
            self.ui_negotiated = False
            log.info("ui.hello not accepted (%s): legacy liveness", ack.error if ack else "timeout")
            return False
        try:
            ms = int(ack.result.get("heartbeat_ms") or 1000)
        except (TypeError, ValueError):
            ms = 1000
        self.ui_beat_s = min(self.ui_beat_max_s, max(self.ui_beat_min_s, ms * self.ui_beat_factor / 1000))
        self.ui_hello_result = dict(ack.result)
        self.ui_negotiated = True
        log.info("ui.hello ok: heartbeat_ms=%s generation=%s capabilities=%s, heartbeat every %.2f s",
                 ms, ack.result.get("generation"), ack.result.get("capabilities"), self.ui_beat_s)
        return True

    def _beat_loop(self) -> None:
        nxt = time.monotonic() + self.ui_beat_s
        next_hello = 0.0
        while not self._stop.is_set() and self.connected:
            if self._stop.wait(max(0.0, nxt - time.monotonic())) or not self.connected:
                return
            sent = time.monotonic()
            try:
                ack = self.call(P.ui_heartbeat())
            except ConnectionError:
                return
            if self._stop.is_set() or not self.connected:  # closed / lost (_lost woke us): this link is done
                return
            # Next sample one period after this one reached the device (its ack is proof): a beat delayed behind a
            # JPEG write or a slow ack never makes the following gap shorter than the period (< 1 s is not counted).
            nxt = (time.monotonic() if ack is not None else sent) + self.ui_beat_s
            if ack is None:
                self.ui_beat_timeouts += 1  # busy or gone: SHOW_JPG ack misses decide about the link
                continue
            if ack.success:
                self.ui_beats += 1
                if self.ui_beats == 1:
                    log.info("ui.heartbeat accepted (first)")
                continue
            self.ui_beat_rejects += 1
            now = time.monotonic()
            if now >= next_hello:
                # Rejected: the device declared the host lost (5 s silence) or SUSPECT (3 s). A new hello restarts
                # negotiation without navigating; rate-limited so a broken device cannot make us spin.
                next_hello = now + self.ui_rehello_min_s
                self.ui_rehellos += 1
                log.info("ui.heartbeat rejected (%s): ui.hello again", ack.error)
                if self.ui_hello():
                    nxt = time.monotonic() + self.ui_beat_s

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
