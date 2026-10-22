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

# kbd/UI.md: after a ui.hello the keyboard is PROBING; three consecutive ui.heartbeat samples spanning >= 2 s make it
# ONLINE again (the earliest moment it accepts ui.page / a non-empty ui.hidKeys map).
UI_ONLINE_BEATS = 3


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
        self.ui_epoch = 0  # +1 per accepted ui.hello: the device dropped any ui.hidKeys map then
        self.ui_beats_hello = 0  # heartbeats accepted since the last accepted ui.hello (UI_ONLINE_BEATS: ONLINE)
        # Called (reader / heartbeat thread, must not block) after an accepted ui.hello and when the keyboard should be
        # ONLINE again (UI_ONLINE_BEATS heartbeats later): the daemon wakes its loop to re-send page / map / frame.
        self.on_ui: Callable[[], None] | None = None
        # Device events dispatched so far (reader thread).  Every ack carries the count at its arrival, so the daemon
        # can tell a keyboard K2 tap sent before our ui.page was processed from one sent after it.
        self.rx_events = 0
        self.ui_page_rx = 0  # rx_events when the last accepted ui.page was acknowledged
        self.ui_generation_exhausted = False
        # last ui.hidKeys / ui.page generation we used: one counter for both (each device counter only needs to see
        # strictly increasing values), starting above both of the device's, from the hello
        self.ui_generation = 0
        self.hid_keys_sent = 0
        self.hid_keys_rejected = 0
        self.pages_sent = 0  # accepted ui.page requests
        self.pages_rejected = 0
        self.ui_page_error = ""  # device reason of the last refused ui.page (fixed firmware strings, truncated)
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
                q.put_nowait((P.Frame(0, -1, b""), self.rx_events))
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
                self.rx_events += 1
                if self.on_key:
                    self.on_key(item)
                return  # device events never resolve a waiter (even if their id matched one of ours)
            if not isinstance(item, P.Ack) and "method" in obj and "errorString" not in obj:
                self.rx_events += 1
                self._event(obj)
                return
            if fr.frame_id == 0xFFFFFFFF or (not isinstance(item, P.Ack) and "errorString" in obj):
                self.device_errors.append(str(obj.get("errorString")))
                log.warning("device error: %s", obj.get("errorString"))
        with self._lock:
            waiter = self._pending.pop(fr.frame_id, None)
        if waiter is not None:
            waiter.put((fr, self.rx_events))  # same thread as the events: everything before it is counted

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

    def _send(self, cmd: int, data: bytes, wait: bool, sent_at: list | None = None,
              ack_rx: list | None = None) -> P.Frame | None:
        q: "queue.Queue[tuple[P.Frame, int]]" = queue.Queue(maxsize=1)
        with self._lock:  # ids are allocated from two threads (pushes + heartbeat): a duplicate would misroute acks
            fid = self.ids.next()
            if wait:
                self._pending[fid] = q
        try:
            with self._wlock:
                self.t.write(P.encode_frame(cmd, data, fid))
                if sent_at is not None:
                    sent_at.append(time.monotonic())  # when the frame actually left (after any queued push)
        except Exception as e:  # noqa: BLE001 — serial write error == unplugged
            with self._lock:
                self._pending.pop(fid, None)
            self._lost(f"写入失败（拔线？）: {e}")
            raise ConnectionError(self.lost_reason) from e
        if not wait:
            return None
        try:
            # close() wakes registered waiters; one registered after that wake-up must not sit out ack_timeout
            fr, rx = q.get(timeout=0 if self._stop.is_set() else self.ack_timeout)
        except queue.Empty:
            with self._lock:
                self._pending.pop(fid, None)
            return None
        if ack_rx is not None:
            ack_rx.append(rx)
        return fr

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
        gens = [g for g in (ack.result.get("hid_generation"), ack.result.get("generation")) if type(g) is int]
        with self._lock:
            self.ui_generation = max([self.ui_generation, *gens])
            self.ui_beats_hello = 0  # before the epoch: whoever sees the new epoch sees the reset count
            self.ui_epoch += 1
        self.ui_negotiated = True
        log.info("ui.hello ok: heartbeat_ms=%s generation=%s capabilities=%s, heartbeat every %.2f s",
                 ms, ack.result.get("generation"), ack.result.get("capabilities"), self.ui_beat_s)
        if self.ui_generation >= P.GENERATION_MAX:
            self._exhausted()
        self._ui_changed()
        return True

    def _ui_changed(self) -> None:
        hook = self.on_ui
        if hook is not None:
            try:
                hook()
            except Exception:  # noqa: BLE001 — a waker bug never stops the reader / heartbeat thread
                log.exception("on_ui hook failed")

    def _exhausted(self) -> None:
        if not self.ui_generation_exhausted:
            self.ui_generation_exhausted = True
            log.warning("ui generation exhausted (%d): ui.page / ui.hidKeys disabled on this connection; a re-hello on "
                        "the same connection does not recover it — only mk20-kbd restarting (its counters reset) and "
                        "a new connection do (firmware follow-up)", P.GENERATION_MAX)

    def _next_generation(self) -> int | None:
        """Next ui.page / ui.hidKeys generation, or None once 999999999 is used up (never a busy retry)."""
        with self._lock:
            if self.ui_generation < P.GENERATION_MAX:
                self.ui_generation += 1
                return self.ui_generation
        self._exhausted()
        return None

    def _beat_loop(self) -> None:
        nxt = time.monotonic() + self.ui_beat_s
        next_hello = 0.0
        while not self._stop.is_set() and self.connected:
            if self._stop.wait(max(0.0, nxt - time.monotonic())) or not self.connected:
                return
            sent = time.monotonic()
            wrote: list = []
            try:
                ack = self.call(P.ui_heartbeat(), sent_at=wrote)
            except ConnectionError:
                return
            if self._stop.is_set() or not self.connected:  # closed / lost (_lost woke us): this link is done
                return
            # Next sample one period after this one actually left (write completed): the device-side gap is then
            # >= the period (never in the < 1 s "not counted" zone) and only grows by write queueing, not by the
            # ack round trip (ack-based scheduling left 0.3 s of margin to the 1.5 s "restart count" rule).
            nxt = (wrote[0] if wrote else sent) + self.ui_beat_s
            if ack is None:
                self.ui_beat_timeouts += 1  # busy or gone: SHOW_JPG ack misses decide about the link
                continue
            if ack.success:
                self.ui_beats += 1
                self.ui_beats_hello += 1
                if self.ui_beats == 1:
                    log.info("ui.heartbeat accepted (first)")
                if self.ui_beats_hello == UI_ONLINE_BEATS:
                    self._ui_changed()  # the keyboard should be ONLINE again now
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

    def has_capability(self, capability: str) -> bool:
        """Advertised in the last accepted ui.hello (mk20-kbd); False for stock KD / legacy firmware."""
        caps = self.ui_hello_result.get("capabilities") if self.ui_negotiated else None
        return isinstance(caps, list) and capability in caps

    def supports(self, capability: str) -> bool:
        """Advertised and usable: the generation-checked requests stop once the generation is used up."""
        if capability in ("ui.page", "ui.hidKeys") and self.ui_generation_exhausted:
            return False
        return self.has_capability(capability)

    def ui_hid_keys(self, keys: dict[int, int]) -> str:
        """Option mode: logical fb -> digit map for the live L1 page ({} clears).  Returns "ok", "unsupported"
        (firmware without ui.hidKeys), "rejected" (e.g. page not live yet: retry) or "timeout"."""
        if not self.supports("ui.hidKeys"):
            return "unsupported"
        gen = self._next_generation()
        if gen is None:
            return "unsupported"
        try:
            req = P.ui_hid_keys(keys, gen)
        except ValueError as e:   # generation exhausted / bad map: never let it escape into the frame loop
            self.hid_keys_rejected += 1
            log.warning("ui.hidKeys not sent: %s", e)
            return "rejected"
        try:
            ack = self.call(req)
        except ConnectionError:
            return "timeout"
        if ack is None:
            return "timeout"
        if ack.success and ack.result.get("generation") == gen:
            self.hid_keys_sent += 1
            return "ok"
        self.hid_keys_rejected += 1
        err = str(ack.error or "")
        if "unsupported" in err or "Unknown method" in err:
            return "unsupported"
        log.info("ui.hidKeys %s not accepted (%s)", sorted(keys.items()), err[:80])
        return "rejected"

    def ui_page(self, page: str) -> str:
        """Ask the keyboard to show host page ``page`` ("L1" / "L4").  Returns "ok" (it switched, or re-entered the same
        page: either way it now waits for a fresh full frame and has dropped any ui.hidKeys map), "unsupported" (no
        negotiated ``ui.page``: stock KD / legacy firmware), "rejected" (not ONLINE yet, keyboard on HOME/LS/L5/agent,
        a request made stale by a route change on the keyboard: retry later) or "timeout".  Never logs above debug:
        the daemon logs a failing streak once."""
        if not self.supports("ui.page"):
            return "unsupported"
        gen = self._next_generation()
        if gen is None:
            return "unsupported"
        try:
            req = P.ui_page(page, gen)
        except ValueError as e:   # generation exhausted / bad page: never let it escape into the frame loop
            self.pages_rejected += 1
            self.ui_page_error = str(e)[:80]
            return "rejected"
        rx: list = []
        try:
            ack = self.call(req, ack_rx=rx)
        except ConnectionError:
            return "timeout"
        if ack is None:
            return "timeout"
        if ack.success and ack.result.get("page") == page and ack.result.get("generation") == gen:
            self.pages_sent += 1
            self.ui_page_rx = rx[0] if rx else self.rx_events
            return "ok"
        self.pages_rejected += 1
        err = str(ack.error or "")
        self.ui_page_error = err[:80]
        log.debug("ui.page %s (generation %d) not accepted (%s)", page, gen, self.ui_page_error)
        if "unsupported" in err or "Unknown method" in err:
            return "unsupported"
        return "rejected"

    def call(self, req: dict, sent_at: list | None = None, ack_rx: list | None = None) -> P.Ack | None:
        """``ack_rx``: receives ``rx_events`` at the ack's arrival (device events dispatched before it)."""
        fr = self._send(P.CMD_JSON, P.json_bytes(req), wait=True, sent_at=sent_at, ack_rx=ack_rx)
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
