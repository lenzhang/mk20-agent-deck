"""Main loop: input → controller → renderer → device, with poll/heartbeat/fps budget."""
from __future__ import annotations

import logging
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from . import keytrace as _keytrace
from . import protocol as P
from .adapters.base import BackendUnavailable
from .config import DEFAULT_ARRANGE_PORT, DEFAULT_WEB_URL
from .controller import LONG_PRESS, Controller
from .link import UI_ONLINE_BEATS, DeviceLink, LatencyStats
from .model import MODE_CYCLE
from .render.canvas import Renderer

log = logging.getLogger(__name__)


# One background poll at a time per process: a previous connection's poll thread (Supervisor makes a new
# Daemon per connection, sharing the bg adapter instances) can never poll concurrently with the next one.
_BG_POLL_LOCK = threading.Lock()

OPEN_SETTINGS_EVENT = P.OPEN_SETTINGS_EVENT  # mk20-kbd 网页设置 key, exactly this (no payload)
ARRANGE_OFF = "会话安排网页已关闭（config [web] arrange_port = 0）"
ARRANGE_DOWN = "会话安排网页未启动（端口被占用？见 mk20deck 日志）"
ARRANGE_FAILED = "会话安排：无法生成一次性链接（见 mk20deck 日志）"
ARRANGE_TOAST_S = 5.0  # pressed on the keyboard's own LS page: still readable after 返回 to L1/L4


def _shown(url: str) -> str:
    """For logs: never the one-time token of an arrange link (everything after ``/auth?`` is dropped)."""
    return url.split("/auth?", 1)[0] + "/auth?…" if "/auth?" in url else url


def open_url(url: str) -> bool:
    """Open ``url`` (or a file such as a ``.webloc``) in the Mac's default browser (``open``); elsewhere only
    log. Never raises."""
    if sys.platform != "darwin":
        log.info("web settings %s: not on macOS, not opening", _shown(url))
        return False
    try:
        r = subprocess.run(["open", url], timeout=5, capture_output=True)
    except Exception as e:  # noqa: BLE001 — timeout / missing binary: the deck keeps running
        log.warning("open %s failed: %s", _shown(url), type(e).__name__)
        return False
    if r.returncode != 0:
        log.warning("open %s exited %s", _shown(url), r.returncode)
    return r.returncode == 0


@dataclass
class InputEvent:
    kind: str  # "key" | "knob" | "ui"
    a: int
    b: int | str
    pressed: bool = False
    t: float = 0.0
    code: str = ""  # raw code for the key trace
    hid: int | None = None  # option mode: the keyboard typed this press itself (HID usage)
    ignored: str = ""  # option mode: "ptt" = the keyboard ignored this digit press (Mac PTT held)
    rx: int | None = None  # link.rx_events when the link delivered it (None: injected, not from the keyboard)


class WakeReconnect(ConnectionError):
    """Raised after system/display wake so the caller re-opens the port and re-handshakes."""


@dataclass
class Daemon:
    controller: Controller
    renderer: Renderer
    link: DeviceLink | None
    poll_interval: float = 2.0
    heartbeat: float = 2.0
    knob_map: dict[str, str] = field(default_factory=dict)
    keycode_map: dict[str, str] = field(default_factory=dict)  # "0x00e6" -> "1,4" (doc) or "K1:cw"
    clock: Callable[[], float] = time.monotonic
    sink: Callable[[bytes], None] | None = None  # extra frame consumer (simulator)
    events: "queue.Queue[InputEvent]" = field(default_factory=queue.Queue)
    event_to_render: LatencyStats = field(default_factory=LatencyStats)
    event_to_ack: LatencyStats = field(default_factory=LatencyStats)
    frames_sent: int = 0
    frames_failed: int = 0
    _last_version: int = -1
    _last_frame: float = -1e9
    _next_poll: float = 0.0
    _pending_t: float | None = None
    power: object | None = None  # hw.power.PowerMonitor
    power_interval: float = 1.0
    paused: bool = False  # display asleep: no frames, no heartbeat
    _next_power: float = 0.0
    power_log: list = field(default_factory=list)
    trace: object | None = field(default_factory=lambda: _keytrace.from_env())  # MK20_KEYTRACE=1
    web_url: str = DEFAULT_WEB_URL  # validated config value (config.parse_web_url), never from the device
    open_settings_min_s: float = 3.0
    opener: Callable[[str], object] = open_url
    settings_opened: int = 0
    settings_dropped: int = 0  # rate-limited or malformed ui.openSettings events
    _last_settings: float = -1e9
    # 会话安排 key on the keyboard's LS page (fw >= 1.6.0, ui.openArrange): the in-process ArrangeServer mints a
    # one-time link FILE (0600, in the 0700 run dir) and only its path goes to ``open`` — never the token / URL.
    arrange: object | None = None  # webui.ArrangeServer (mint_file()), None: page off / not started
    arrange_port: int = DEFAULT_ARRANGE_PORT  # config [web] arrange_port: 0 = off (explains a missing server)
    open_arrange_min_s: float = 3.0
    arrange_opened: int = 0
    arrange_dropped: int = 0  # rate-limited ui.openArrange events
    arrange_refused: int = 0  # page off / not started / link not minted: a toast instead
    _last_arrange: float = -1e9
    # Cross-mode awareness: non-current modes are polled on a worker thread every bg_poll_s
    # (0 = off); results are applied on the loop thread in step().  bg_adapters should be
    # separate instances (cli.build_adapters) so a poll never races the foreground refresh.
    bg_poll_s: float = 10.0
    bg_adapters: dict | None = None
    sleep_frame_s: float = 10.0  # re-send "Mac 已休眠" this often while paused (device drops frames off L1/L4)
    _last_sleep_frame: float = float("-inf")
    bg_first_delay_s: float = 1.0
    bg_results: "queue.Queue[tuple]" = field(default_factory=queue.Queue)
    bg_polls: int = 0
    # Option mode (2026-10-22): keep the keyboard's ui.hidKeys map equal to the controller's choice keys.
    hid_refresh_s: float = 10.0  # the keyboard drops a map 30 s after the last request
    hid_retry_s: float = 1.0
    _hid_map: dict = field(default_factory=dict)  # what the keyboard last confirmed ({} = nothing mapped)
    _hid_at: float = 0.0
    _hid_retry_at: float = 0.0
    _hid_epoch: int = -1
    # Page sync (2026-10-22): keep the keyboard's L1/L4 route equal to the controller's page with ui.page.
    page_retry_s: float = 0.5  # first retry of a refused / unanswered ui.page (keyboard on HOME/LS, stale, SUSPECT)
    page_retry_max_s: float = 2.0  # back-off cap: bounded traffic while the keyboard sits on its own pages
    page_online_beats: int = UI_ONLINE_BEATS  # after a hello: accepted heartbeats until the keyboard is ONLINE
    _page_dev: str | None = None  # belief: the keyboard's host page (its L1/L4 route); None = unknown, tell it
    _page_dev_rx: int = 0  # link.rx_events at the ack that set _page_dev (K2 taps up to it are already in it)
    _page_epoch: int = -1
    _page_hello: bool = False  # the first request after a hello is still due (waits for page_online_beats)
    _page_sync: int = 0  # controller.page_sync seen
    _page_tried: str | None = None  # page of the current failing streak
    _page_retry_at: float = 0.0
    _page_backoff: float = 0.0
    _page_failing: bool = False  # a streak was logged (once) and not yet resolved
    _page_off_epoch: int = -1  # the keyboard answered "unsupported" on this epoch: stop asking until a new hello
    bg_sessions: dict = field(default_factory=dict)  # mode -> (sessions | None, error, t0) of the last bg poll
    # Cross-thread hand-off (webui.LoopCalls): the arrange web page queues its changes here; they run on this
    # loop thread inside step() (never from the HTTP thread) and force a frame.
    calls: object | None = None
    bg_join_s: float = 6.0  # run() waits this long for its background poll thread to finish (adapter timeouts)
    # hw/gd32watch.Gd32DfuWatch (process-wide, its own thread; None = off): its confirmed ``stuck`` state becomes the
    # controller's GD32 DFU banner on every step (an attribute read, never the probe itself).
    gd32: object | None = None
    # effects.Effects (None = drop them, e.g. the simulator): runs the controller's queued side effects (approve
    # sound, SNAP screenshot) as child processes it polls every step; results go back via controller.effect_result.
    effects: object | None = None

    # -------------------------------------------------------------- input
    def on_device_key(self, ev: P.KeyEvent) -> None:
        """Called from the link reader thread (firmware coordinates)."""
        now = self.clock()
        code = ""
        if self.trace:
            code = f"kc:0x{ev.keycode:04x}" if (ev.col < 0 and ev.keycode is not None) else f"{ev.col},{ev.row}"
            self.trace.ev("recv", code, ev.pressed)
        if ev.col < 0 and ev.keycode is not None:  # keycode-form event
            target = self.keycode_map.get(f"0x{ev.keycode:04x}")
            if target is None:
                log.info("unmapped keycode event 0x%04x (add it to [keycodes])", ev.keycode)
                return
            if ":" not in target:
                c, r = (int(x) for x in target.split(","))
                self.events.put(InputEvent("key", c, r, ev.pressed, t=now, code=code))
                return
            tag = target
        else:
            tag = self.knob_map.get(f"{ev.col},{ev.row}")
        if tag:  # e.g. "K1:press" → down/up from pressed
            knob = 1 if tag.startswith("K1") else 2
            action = tag.split(":", 1)[1] if ":" in tag else "press"
            if action == "press":
                rx = getattr(self.link, "rx_events", None)  # called on the link's reader thread: this event's count
                self.events.put(InputEvent("knob", knob, "down" if ev.pressed else "up", ev.pressed, t=now, code=code,
                                           rx=rx))
            elif ev.pressed:
                self.events.put(InputEvent("knob", knob, action, True, t=now, code=code))
            return
        lay = self.renderer.layout
        if lay.keys and not any(r.col == ev.col and r.row == ev.row for r in lay.keys):
            # knobs report col/row >= 100; never let an unmapped one reach the key grid
            log.info("unmapped non-key event %d,%d (add it to [knobs])", ev.col, ev.row)
            return
        col, row = lay.fw_to_doc(ev.col, ev.row)
        self.events.put(InputEvent("key", col, row, ev.pressed, t=now, code=code, hid=getattr(ev, "hid", None),
                                   ignored=getattr(ev, "ignored", None) or ""))

    def on_device_event(self, obj: dict) -> None:
        """Non-key device JSON event, called from the link reader thread: only queue, never block or raise.  Only the
        whitelisted fixed-payload events (protocol.DEVICE_UI_EVENTS) are acted on, and only exactly as sent."""
        method = obj.get("method")
        if method not in P.DEVICE_UI_EVENTS:
            log.info("ignored device event %r", str(method)[:64])
            return
        if P.device_ui_event(obj) is None:  # the device never sends a payload: refuse to act on one
            if method == "ui.openArrange":
                self.arrange_dropped += 1
            else:
                self.settings_dropped += 1
            log.warning("%s with extra fields %s dropped", method, sorted(str(k)[:32] for k in obj)[:8])
            return
        self.events.put(InputEvent("ui", 0, method.removeprefix("ui."), t=self.clock()))

    def _open_settings(self) -> None:
        now = self.clock()
        if now - self._last_settings < self.open_settings_min_s:
            self.settings_dropped += 1
            log.info("ui.openSettings rate-limited (%.1f s since the last one)", now - self._last_settings)
            return
        self._last_settings = now
        self.settings_opened += 1
        log.info("ui.openSettings: opening %s", self.web_url)
        # `open` can take a moment (LaunchServices): never stall the render loop for it.
        threading.Thread(target=self.opener, args=(self.web_url,), name="mk20-open-settings", daemon=True).start()

    def _say(self, text: str) -> None:
        say = getattr(self.controller, "_say", None)
        if say is not None:
            say(text, ARRANGE_TOAST_S)

    def _open_arrange(self) -> None:
        """ui.openArrange (keyboard LS fb17 会话安排): a one-time link file from the in-process ArrangeServer, opened
        with ``open <file>`` (token only inside the 0600 file, never in argv / logs).  Page off or not running: a
        toast on the keyboard instead.  At most once per open_arrange_min_s."""
        now = self.clock()
        if now - self._last_arrange < self.open_arrange_min_s:
            self.arrange_dropped += 1
            log.info("ui.openArrange rate-limited (%.1f s since the last one)", now - self._last_arrange)
            return
        self._last_arrange = now
        srv = self.arrange
        if srv is None:
            self.arrange_refused += 1
            self._say(ARRANGE_OFF if not self.arrange_port else ARRANGE_DOWN)
            log.info("ui.openArrange: the 会话安排 page is %s", "off ([web] arrange_port = 0)" if not self.arrange_port
                     else "not running")
            return
        try:
            path = srv.mint_file()  # local 0600 file in the 0700 run dir: quick, safe on the loop thread
        except Exception as e:  # noqa: BLE001 — never let it reach the frame loop; no details that could hold a token
            self.arrange_refused += 1
            self._say(ARRANGE_FAILED)
            log.warning("ui.openArrange: one-time link not created (%s)", type(e).__name__)
            return
        self.arrange_opened += 1
        log.info("ui.openArrange: opening the 会话安排 page (one-time link file)")
        threading.Thread(target=self.opener, args=(path,), name="mk20-open-arrange", daemon=True).start()

    def inject_key(self, col: int, row: int, pressed: bool) -> None:
        """Document coordinates (simulator)."""
        self.events.put(InputEvent("key", col, row, pressed, t=self.clock()))

    def inject_knob(self, knob: int, kind: str) -> None:
        self.events.put(InputEvent("knob", knob, kind, t=self.clock()))

    def _apply(self, ev: InputEvent) -> None:
        if ev.kind == "ui":  # device UI request, not controller input (no render/latency bookkeeping)
            if ev.b == "openSettings":
                self._open_settings()
            elif ev.b == "openArrange":
                self._open_arrange()
            return
        log.info("input %s %s %s%s", ev.kind, ev.a, ev.b, "" if ev.kind != "key" else (" down" if ev.pressed else " up"))
        if hasattr(self.controller, "device_pages"):  # before the input: K1 on L4 and knob timing depend on it
            self.controller.device_pages = self._device_pages()
        tr = self.trace
        if tr:
            c = self.controller
            ptt0, acts0 = c.ptt_held, c.action_count
        if ev.kind == "key" and (ev.hid is not None or ev.ignored):
            self.controller.on_key(ev.a, int(ev.b), ev.pressed, hid=ev.hid, ignored=ev.ignored)
        elif ev.kind == "key":
            self.controller.on_key(ev.a, int(ev.b), ev.pressed)
        else:
            target = self._k2_target(ev)
            if target is not None:
                self.controller.on_knob(ev.a, str(ev.b), target=target, t=ev.t)
            else:
                self.controller.on_knob(ev.a, str(ev.b), t=ev.t)
            if ev.a == 2 and ev.b == "up" and ev.rx is not None:
                self._keyboard_k2_tap(ev.rx)
        if tr:
            code = ev.code or f"inj:{ev.a},{ev.b}"
            tr.ev("routed", code, ev.pressed)
            out = "hid_written" if c.ptt_held != ptt0 else "agent" if c.action_count != acts0 else "local"
            tr.ev(out, code, ev.pressed)
        if self._pending_t is None:
            self._pending_t = ev.t

    # -------------------------------------------------------------- background (other modes)
    def poll_background_once(self) -> int:
        """Poll every non-current mode once (worker thread or tests); results are queued, not applied."""
        with _BG_POLL_LOCK:
            return self._poll_background_locked()

    def _poll_background_locked(self) -> int:
        ads = self.bg_adapters or self.controller.adapters
        current = self.controller.mode
        n = 0
        for m in MODE_CYCLE:
            ad = ads.get(m)
            if m == current or ad is None:
                continue
            t0 = self.controller.wall()
            try:
                res = (m, ad.poll(()), "", t0)
            except BackendUnavailable as e:
                res = (m, None, str(e), t0)
            except Exception as e:  # noqa: BLE001 — a background backend bug never stops the deck
                log.warning("background poll %s failed: %s", m.value, type(e).__name__)
                res = (m, None, f"读取失败：{type(e).__name__}", t0)
            self.bg_results.put(res)
            n += 1
        self.bg_polls += 1
        return n

    def _bg_loop(self, stop: threading.Event, done: threading.Event) -> None:
        wait = self.bg_first_delay_s
        while not (done.wait(wait) or stop.is_set()):  # wakes at once when run() ends
            wait = self.bg_poll_s
            if not self.paused:
                self.poll_background_once()

    def _drain_background(self) -> None:
        while True:
            try:
                m, sessions, err, t0 = self.bg_results.get_nowait()
            except queue.Empty:
                return
            self.bg_sessions[m] = (sessions, err, t0)
            self.controller.apply_background(m, sessions, err, t0)

    # -------------------------------------------------------------- page sync (ui.page)
    # The daemon keeps a belief of the keyboard's host page (``_page_dev``): set by an accepted ui.page, toggled by every
    # K2 tap the keyboard sends after that ack (it sends one exactly when its own host page flips: K2 short, L4 回 L1,
    # HOME/LS -> L1/L4, ONLINE again), unknown after a non-acked request or a controller resync request.  After a hello
    # it is the page the hello reply names (fw >= 1.6.0 "page": the keyboard's route; a route changed while we were
    # away is announced by a K2 tap before that reply, so the reply is where the tap mirror starts), else unknown
    # (older firmware, or "none": the keyboard is on one of its own pages).
    # A request goes out only when the controller's page differs from that belief.  A same-page ui.page (fw < 1.6.0
    # runs a full page boundary for it: redraw AND release of held PTT / Mac HID keys; fw >= 1.6.0 only redraws) can
    # therefore only happen while the belief is unknown:
    #   * the first request after a hello (reconnect / re-hello) on firmware whose hello names no page, sent when the
    #     keyboard is ONLINE again (the link wakes us at the page_online_beats-th heartbeat): the hello itself already
    #     ran a boundary, and a host page owns no key before ONLINE + a fresh frame;
    #   * the request after a refused / stale / unanswered one (we cannot know whether the keyboard moved);
    #   * a controller resync (K1 forwarded while we show L4: the keyboard is on L1, so normally a real change).
    # Never in steady state: a keyboard K2 tap updates the belief and sends nothing.  When the hello named our page,
    # nothing is sent at all: at ONLINE the step only forces the full frame the keyboard needs.
    def _device_pages(self) -> bool:
        """The keyboard firmware has ui.page: it decides knob holds and K1 on its L4 itself."""
        link = self.link
        has = getattr(link, "has_capability", None) if link is not None else None
        return bool(has and getattr(link, "ui_negotiated", False) and has("ui.page"))

    def _pages_sendable(self) -> bool:
        link = self.link
        return (link is not None and getattr(link, "ui_negotiated", False) and link.supports("ui.page")
                and self._page_off_epoch != getattr(link, "ui_epoch", 0))

    def _page_wanted(self) -> str | None:
        page = self.controller.page
        return page if page in P.PAGES else None  # "LS" (legacy settings page) is never claimed

    def _k2_target(self, ev: InputEvent) -> str | None:
        """A K2 tap from the keyboard (ui.page firmware) newer than our known belief: the keyboard now shows the
        other page than the belief, absolutely.  The controller goes there (Kimi K3 1.6.0 MINOR 3: a K2 on the
        keyboard while it was PROBING is tapped at ONLINE; toggling relative to a Mac page that differed would make
        the Mac pull the keyboard back, and the user's K2 would have no effect).  Unknown belief (refused /
        stale request, older firmware) or a tap already contained in the last ack: the relative toggle as before."""
        if (ev.a != 2 or ev.b != "up" or ev.rx is None or self._page_dev not in P.PAGES
                or ev.rx <= self._page_dev_rx or not self._device_pages()):
            return None
        return "L4" if self._page_dev == "L1" else "L1"

    def _keyboard_k2_tap(self, rx: int) -> None:
        """A K2 tap from the keyboard: its host page flipped (and its boundary dropped any ui.hidKeys map).  Taps that
        arrived before the ack of our last accepted ui.page are already contained in that ack."""
        if self._page_dev is not None and rx > self._page_dev_rx:
            self._page_dev = "L4" if self._page_dev == "L1" else "L1"
        if self._device_pages():
            self._hid_map, self._hid_retry_at = {}, 0.0

    def _wake(self) -> None:
        """Link hook (reader / heartbeat thread): a hello or ONLINE again -> run a step now, never after the old wait."""
        self.events.put(InputEvent("ui", 0, "wake", t=self.clock()))

    def _sync_page(self, now: float) -> bool:
        """Send the controller's absolute page when it differs from the belief (see above).  True when the keyboard
        accepted: it then needs a fresh full frame and has dropped its ui.hidKeys map, so both follow right after."""
        c, link = self.controller, self.link
        if link is None:
            return False
        if hasattr(c, "device_pages"):
            c.device_pages = self._device_pages()
        epoch = getattr(link, "ui_epoch", 0)
        if epoch != self._page_epoch and not self.events.empty():
            # Input queued before this hello's reply first (the reader queues the keyboard's events before it hands
            # over the ack): above all the hello-time K2 tap, which the controller must follow before we compare.
            return False
        if epoch != self._page_epoch:  # reconnect / re-hello: the keyboard may show anything (reboot -> auto-L1) ...
            seed = getattr(link, "ui_hello_page", None)  # ... unless the hello said which page (fw >= 1.6.0)
            self._page_epoch, self._page_tried, self._page_retry_at = epoch, None, 0.0
            self._page_dev = seed if seed in P.PAGES else None
            self._page_dev_rx = getattr(link, "ui_hello_rx", 0) if self._page_dev else 0
            self._page_hello = True
        sync = getattr(c, "page_sync", 0)
        if sync != self._page_sync:  # the controller saw the keyboard on another route: say it again
            self._page_sync, self._page_dev, self._page_tried, self._page_retry_at = sync, None, None, 0.0
        want = self._page_wanted()
        if not self._pages_sendable() or want is None:
            return False
        if self._page_hello and self._probing():
            return False  # it refuses everything until ONLINE; the link wakes us at the right heartbeat
        if want == self._page_dev:
            if self._page_hello:  # ONLINE again on the page the hello named: no request, only the fresh full frame
                self._page_hello = False
                return True
            return False
        if want != self._page_tried:  # a new target goes out at once, with a fresh back-off
            self._page_tried, self._page_retry_at, self._page_backoff = want, 0.0, self.page_retry_s
        elif now < self._page_retry_at:
            return False
        self._page_hello = False
        res = link.ui_page(want, epoch=epoch)  # decided under this epoch: dropped if a hello completed since
        if res == "stale":  # a hello completed since we decided: nothing sent, look again under the new epoch
            self._page_hello, self._page_tried = True, None
            return False
        if res == "ok":
            if self._page_failing:
                log.info("ui.page %s accepted", want)
            self._page_dev, self._page_dev_rx = want, getattr(link, "ui_page_rx", 0)
            self._page_tried, self._page_failing = None, False
            self._hid_map, self._hid_retry_at = {}, 0.0  # the keyboard cleared its option-mode map at the boundary
            return True
        self._page_dev = None  # refused / stale / unanswered: the keyboard may have moved meanwhile
        if res == "unsupported":
            self._page_off_epoch = epoch
            log.info("ui.page refused as unsupported: not sent again until the next hello")
            return False
        self._page_retry_at = now + self._page_backoff
        self._page_backoff = min(self.page_retry_max_s, self._page_backoff * 2)
        if not self._page_failing:  # once per streak; the reason is a fixed firmware string, no content
            self._page_failing = True
            log.info("ui.page %s not accepted (%s%s): retrying every <= %.1f s", want, res,
                     f": {link.ui_page_error}" if res == "rejected" and getattr(link, "ui_page_error", "") else "",
                     self.page_retry_max_s)
        return False

    def _probing(self) -> bool:
        return getattr(self.link, "ui_beats_hello", self.page_online_beats) < self.page_online_beats

    def _page_wait(self, now: float) -> float | None:
        want = self._page_wanted()
        if want is None or not self._pages_sendable():
            return None  # legacy firmware: never spin on a request it cannot take
        if (getattr(self.link, "ui_epoch", 0), getattr(self.controller, "page_sync", 0)) != (self._page_epoch,
                                                                                            self._page_sync):
            return 0.0  # a hello / resync request since the last step: look at it now
        if self._page_hello and self._probing():
            return None  # the link wakes us once the keyboard is ONLINE again
        if want == self._page_dev:
            return 0.0 if self._page_hello else None  # ONLINE on the hello's page: the frame now / nothing to say
        return max(0.0, self._page_retry_at - now)

    # -------------------------------------------------------------- option mode (ui.hidKeys)
    def _sync_hid_keys(self, now: float) -> None:
        """Send the choice keys (or the clear) before the frame that shows them; refresh while shown."""
        c, link = self.controller, self.link
        want = c.hid_keys_wanted() if hasattr(c, "hid_keys_wanted") else {}
        if link is None:
            return
        epoch = getattr(link, "ui_epoch", 0)
        if epoch != self._hid_epoch or not getattr(link, "ui_negotiated", False):
            self._hid_epoch, self._hid_map, self._hid_retry_at = epoch, {}, 0.0  # a hello/loss dropped the map
        if not want and not self._hid_map:
            return
        if want and not getattr(link, "ui_negotiated", False):
            c.set_option_hid("pending")   # reconnect: hello not done yet — not "legacy" (Kimi MINOR)
            return
        if want and not link.supports("ui.hidKeys"):
            c.set_option_hid("legacy")
            return
        if want == self._hid_map and now - self._hid_at < self.hid_refresh_s:
            return
        if now < self._hid_retry_at:
            return
        res = link.ui_hid_keys(want, epoch=epoch)
        if res == "stale":  # a hello completed since we decided (it dropped the map): the next step re-sends
            return
        if res == "ok":
            self._hid_map, self._hid_at, self._hid_retry_at = dict(want), now, 0.0
        elif res == "unsupported":
            self._hid_map = {}
        else:
            self._hid_retry_at = now + self.hid_retry_s
        if want:
            c.set_option_hid({"ok": "ok", "unsupported": "legacy"}.get(res, "pending"))

    def _hid_wait(self, now: float) -> float | None:
        want = self.controller.hid_keys_wanted() if hasattr(self.controller, "hid_keys_wanted") else {}
        if self.link is None or (not want and not self._hid_map) or not self.link.supports("ui.hidKeys"):
            return None  # nothing to send (legacy firmware: never spin on a map it cannot take)
        if want != self._hid_map:
            return max(0.0, self._hid_retry_at - now)
        return self._hid_at + self.hid_refresh_s - now

    # -------------------------------------------------------------- loop
    def step(self, force: bool = False) -> bool:
        """One iteration; returns True if a frame was produced."""
        now = self.clock()
        while True:
            try:
                self._apply(self.events.get_nowait())
            except queue.Empty:
                break
        if self.calls is not None and self.calls.drain(self):
            force = True  # an arrange-page change: show it on the keyboard now
        self._drain_background()
        self._sync_gd32()  # a change bumps the version: a frame
        self.controller.tick()
        self._run_effects()
        if self._sync_page(now):  # before the (possibly slow) poll: the keyboard switches at once ...
            force = True          # ... and gets the new page's full frame in this step (it shows a placeholder)
        if now >= self._next_poll:
            self.controller.refresh()
            self._next_poll = now + self.poll_interval
        self._sync_hid_keys(now)  # after ui.page: the boundary dropped the keyboard's map
        view = self.controller.view()
        fps = self.renderer.desired_fps(view, now)
        due = force or self.controller.version != self._last_version
        if fps > 0 and now - self._last_frame >= 1.0 / fps:
            due = True
        if now - self._last_frame >= self.heartbeat:
            due = True
        if not due:
            return False
        self._last_version = self.controller.version
        img = self.renderer.compose(view, now, heartbeat_on=int(now / self.heartbeat) % 2 == 0)
        jpeg = self.renderer.jpeg(img)
        t_render = self.clock()
        if self._pending_t is not None:
            self.event_to_render.add(t_render - self._pending_t)
        ok = True
        if self.link is not None:
            ok = self.link.show_jpeg(jpeg)
            if ok and self._pending_t is not None:
                self.event_to_ack.add(self.clock() - self._pending_t)
        if self.sink:
            self.sink(jpeg)
        self._pending_t = None
        self._last_frame = now
        if ok:
            self.frames_sent += 1
        else:
            self.frames_failed += 1
        return True

    def _sync_gd32(self) -> None:
        """The watch's confirmed GD32 DFU state -> the controller (banner, arrange page); also while paused."""
        if self.gd32 is not None and hasattr(self.controller, "set_gd32_dfu"):
            self.controller.set_gd32_dfu(getattr(self.gd32, "stuck", False))

    def _run_effects(self) -> None:
        queued = getattr(self.controller, "effects", None)
        if queued:
            items = list(queued)
            queued.clear()
            for e in items:
                if self.effects is None:
                    log.info("effect %s dropped (no effects runner)", e[0])
                    continue
                try:
                    self.effects.run(e)
                except Exception:  # noqa: BLE001 — a side effect never takes the device loop down
                    log.exception("effect %s", e[0])
        if self.effects is not None:
            for kind, ok, msg in self.effects.poll():
                self.controller.effect_result(kind, ok, msg)

    def next_wait(self) -> float:
        now = self.clock()
        waits = [self._next_poll - now, self._last_frame + self.heartbeat - now]
        if self.effects is not None and self.effects.busy():
            waits.append(0.1)  # a running screenshot: its result (toast + flash) as soon as it is done
        view_fps = self.renderer.desired_fps(self.controller.view(), now)
        if view_fps > 0:
            waits.append(self._last_frame + 1.0 / view_fps - now)
        c = self.controller
        if c.long_press_pending() if hasattr(c, "long_press_pending") else c.presses:
            waits.append(LONG_PRESS / 8)  # (never for a ui.page firmware knob tap waiting for its up)
        deadline = self.controller.move_deadline()
        if deadline is not None:  # a picked-up slot times out even when nothing animates
            waits.append(deadline - now)
        deadline = getattr(self.controller, "option_deadline", lambda: None)()
        if deadline is not None:  # option mode: 30 s without input / 3 s after a choice
            waits.append(deadline - now)
        deadline = getattr(self.controller, "scroll_deadline", lambda: None)()
        if deadline is not None:  # K1 scroll-back: a send after the Desktop focus settle / the 2 min idle end
            waits.append(deadline - now)
        hw = self._hid_wait(now)
        if hw is not None:  # ui.hidKeys refresh / retry
            waits.append(hw)
        pw = self._page_wait(now)
        if pw is not None:  # ui.page retry
            waits.append(pw)
        if self.power is not None:
            waits.append(self._next_power - now)
        return max(0.005, min(waits))

    def check_power(self) -> None:
        """Display sleep → offline frame + pause; wake → WakeReconnect (PRD §7.5, G24)."""
        if self.power is None:
            return
        now = self.clock()
        if now < self._next_power:
            return
        self._next_power = now + self.power_interval
        for ev in self.power.poll():
            self.power_log.append((time.time(), ev))
            log.info("power event: %s", ev)
            if ev == "display_sleep" and not self.paused:
                self.offline_frame("Mac 已休眠")
                self.paused = True
            elif ev in ("display_wake", "system_wake"):
                was_paused, self.paused = self.paused, False
                self.controller.drop_gestures()  # never replay gestures (presses, pick-up) across sleep
                if ev == "system_wake" or was_paused:
                    raise WakeReconnect(ev)

    def run(self, stop: threading.Event) -> None:
        # A connection made while the display already sleeps (e.g. the keyboard rebooted for an install while
        # the user is away) gets no new display_sleep edge: start paused from the monitor's current state.
        if self.power is not None and getattr(self.power, "display_asleep", False) and not self.paused:
            self.offline_frame("Mac 已休眠")
            self.paused = True
        done = threading.Event()
        if self.bg_poll_s > 0:
            bg = threading.Thread(target=self._bg_loop, args=(stop, done), name="mk20-bg-poll", daemon=True)
            bg.start()
        else:
            bg = None
        if self.calls is not None:  # wake the loop at once when the web page queues a change
            self.calls.attach(lambda: self.events.put(InputEvent("ui", 0, "wake", t=self.clock())))
        if self.link is not None and hasattr(self.link, "on_ui"):  # hello / ONLINE again: page, map, frame at once
            self.link.on_ui = self._wake
        try:
            self._run(stop)
        finally:
            done.set()
            if bg is not None:  # a reconnect never leaves the old poll thread running next to the new one
                bg.join(self.bg_join_s)
            if self.calls is not None:
                self.calls.attach(None)
            if self.link is not None and getattr(self.link, "on_ui", None) == self._wake:
                self.link.on_ui = None

    def _run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.check_power()
            if self.paused:
                # Still watch the link while paused: a device reboot during display sleep must reconnect (and
                # show "Mac 已休眠" again) instead of leaving the keyboard with no host until the next wake.
                if self.link is not None and not self.link.connected:
                    raise ConnectionError(self.link.lost_reason or "device disconnected")
                # Re-send the sleep frame: the keyboard drops frames outside L1/L4 and needs a fresh one after
                # any page change (e.g. it follows the Mac to L1 after boot while we are paused).
                if self.clock() - self._last_sleep_frame >= self.sleep_frame_s:
                    self.offline_frame("Mac 已休眠")
                stop.wait(self.power_interval)
                # Drop input while the Mac sleeps.  A keyboard K2 tap dropped here leaves the page guess stale
                # only until wake: WakeReconnect starts a new epoch, the guess becomes unknown and the absolute
                # page is sent again (Kimi r2 MINOR).
                while not self.events.empty():
                    self.events.get_nowait()
                self._sync_gd32()  # no step() while paused: keep the arrange page's GD32 state current
                if self.calls is not None:
                    self.calls.drain(self)  # arrange-page changes still apply (shown after wake)
                continue
            self.step()
            try:
                ev = self.events.get(timeout=self.next_wait())
                self._apply(ev)
            except queue.Empty:
                pass
            if self.link is not None and not self.link.connected:
                raise ConnectionError(self.link.lost_reason or "device disconnected")

    def offline_frame(self, reason: str) -> None:
        """Best effort on normal exit / sleep (PRD §7.5, AC12)."""
        if reason == "Mac 已休眠":
            self._last_sleep_frame = self.clock()
        when = time.strftime("%H:%M:%S")
        jpeg = self.renderer.jpeg(self.renderer.compose_offline(reason, when))
        if self.link is not None:
            try:
                self.link.show_jpeg(jpeg)
            except Exception:  # noqa: BLE001
                pass
        if self.sink:
            self.sink(jpeg)
