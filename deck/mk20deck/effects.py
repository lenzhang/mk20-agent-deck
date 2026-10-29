"""Side effects the controller asks for (2026-10-29): the approve sound and SNAP (screenshot to the clipboard).

The controller stays I/O-free: it queues ``("sound", "approve")`` / ``("snap",)`` in ``Controller.effects``; the
daemon hands them to :class:`Effects` and polls it every step (child processes never block the device loop) and
reports finished screenshots back with ``Controller.effect_result``.

SNAP uses macOS ``/usr/sbin/screencapture``.  Capturing other apps' windows needs the Screen Recording permission
(TCC) for the process that runs mk20deck (the LaunchAgent's python3).  Without it screencapture still "succeeds" but
only shows the desktop, so the permission is checked first (CoreGraphics ``CGPreflightScreenCaptureAccess``); when
it is missing the deck asks the system once (``CGRequestScreenCaptureAccess``: adds python3 to the list) and says
what the user has to switch on.  Nothing here works around the permission.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Any, Callable

log = logging.getLogger(__name__)

SCREENCAPTURE = "/usr/sbin/screencapture"
AFPLAY = "/usr/bin/afplay"
OSASCRIPT = "/usr/bin/osascript"
SOUND_DIR = "/System/Library/Sounds"
CG = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
SNAP_TIMEOUT_S = 10.0

SNAP_OK = "已截图 · 已复制到剪贴板"
SNAP_NEED_TCC = "截图需要屏幕录制权限：系统设置→隐私与安全性→屏幕录制，打开 python3 后重启 mk20deck"
SNAP_NOT_MAC = "截图仅支持 macOS"
SNAP_FAILED = "截图失败"
SNAP_RUNNING = "上一张截图还没完成"


def screen_capture_access(request: bool = False) -> bool | None:
    """True/False = Screen Recording allowed for this process; None = unknown (no CoreGraphics / old macOS).
    ``request``: when not allowed, ask the system once (it lists the app in the settings; no blocking dialog)."""
    try:
        cg = ctypes.cdll.LoadLibrary(CG)
        pre = cg.CGPreflightScreenCaptureAccess
    except (OSError, AttributeError):
        return None
    pre.restype = ctypes.c_bool
    ok = bool(pre())
    if not ok and request:
        try:
            req = cg.CGRequestScreenCaptureAccess
            req.restype = ctypes.c_bool
            req()
        except AttributeError:
            pass
    return ok


class Effects:
    """Runs on the daemon thread only as far as queueing: SNAP (permission probe, directory, screencapture, clipboard)
    runs in a worker thread; the approve sound is one non-blocking Popen.  ``poll()`` hands back finished results."""

    def __init__(self, sound: str = "Glass", snap_dir: str = "", platform: str = sys.platform,
                 popen: Callable[..., Any] = subprocess.Popen, access: Callable[[bool], bool | None] | None = None,
                 clock: Callable[[], float] = time.monotonic, exists: Callable[[str], bool] = os.path.exists,
                 threaded: bool = True):
        self.sound, self.snap_dir, self.platform = sound, snap_dir, platform
        self.popen, self.clock, self.exists, self.threaded = popen, clock, exists, threaded
        self.access = access or screen_capture_access
        self._asked = False  # CGRequestScreenCaptureAccess at most once per process
        self._sounds: list[Any] = []
        self._lock = threading.Lock()
        self._running = False  # a SNAP job is in flight
        self._done: list[tuple[str, bool, str]] = []

    @classmethod
    def from_config(cls, cfg: Any) -> "Effects":
        return cls(sound=getattr(cfg, "approve_sound", "Glass"), snap_dir=getattr(cfg, "snap_dir", ""))

    def busy(self) -> bool:
        with self._lock:
            return self._running or bool(self._done)

    def run(self, effect: tuple) -> None:
        if not effect:
            return
        if effect[0] == "sound":
            self.play()
        elif effect[0] == "snap":
            self.snap()

    def _spawn(self, argv: list[str]) -> Any:
        return self.popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _result(self, ok: bool, msg: str) -> None:
        with self._lock:
            self._done.append(("snap", ok, msg))

    # ------------------------------------------------------------------ sound
    def play(self) -> None:
        if not self.sound or self.platform != "darwin":
            return
        path = os.path.join(SOUND_DIR, self.sound + ".aiff")  # name validated by config.parse_sound
        try:
            self._sounds.append(self._spawn([AFPLAY, path]))
        except OSError as e:
            log.warning("afplay: %s", e)

    # ------------------------------------------------------------------ SNAP
    def snap(self) -> None:
        with self._lock:
            if self._running:
                self._done.append(("snap", False, SNAP_RUNNING))
                return
            self._running = True
        if self.threaded:
            threading.Thread(target=self._snap_job, name="mk20-snap", daemon=True).start()
        else:
            self._snap_job()

    def _snap_job(self) -> None:
        try:
            ok, msg = self._snap_work()
        except Exception as e:  # noqa: BLE001 — the worker never dies silently
            log.exception("snap")
            ok, msg = False, f"{SNAP_FAILED}：{type(e).__name__}"
        with self._lock:
            self._running = False
            self._done.append(("snap", ok, msg))

    def _wait(self, proc: Any, what: str) -> int | None:
        t0 = self.clock()
        while True:
            rc = proc.poll()
            if rc is not None:
                return rc
            if self.clock() - t0 > SNAP_TIMEOUT_S:
                proc.kill()
                log.warning("snap %s timed out", what)
                return None
            time.sleep(0.05) if self.threaded else None

    def _snap_work(self) -> tuple[bool, str]:
        if self.platform != "darwin":
            return False, SNAP_NOT_MAC
        ok = self.access(not self._asked)
        if ok is False:
            self._asked = True
            log.warning("snap: Screen Recording permission missing for %s", sys.executable)
            return False, SNAP_NEED_TCC
        png = ""
        if self.snap_dir:
            try:
                os.makedirs(self.snap_dir, exist_ok=True)
            except OSError as e:
                log.warning("snap dir %s: %s", self.snap_dir, e)
                return False, f"{SNAP_FAILED}：保存目录不可用"
            png = os.path.join(self.snap_dir, time.strftime("mk20-snap-%Y%m%d-%H%M%S.png"))
            argv = [SCREENCAPTURE, "-x", png]
        else:
            argv = [SCREENCAPTURE, "-x", "-c"]  # -x: no shutter sound; -c: straight to the clipboard
        rc = self._wait(self._spawn(argv), "capture")
        if rc is None:
            return False, f"{SNAP_FAILED}：超时"
        if rc != 0:
            log.warning("screencapture rc=%s", rc)
            return False, SNAP_FAILED
        if png:
            rc = self._wait(self._spawn(self._clipboard_argv(png)), "clipboard")
            if rc != 0:
                return False, f"已存 {os.path.basename(png)}，复制到剪贴板失败"
        log.info("snap ok%s", f" ({png})" if png else "")
        return True, SNAP_OK

    def _clipboard_argv(self, png: str) -> list[str]:
        # the PNG file onto the clipboard as an image (path passed as an argument, never spliced into the script)
        return [OSASCRIPT, "-e", "on run argv", "-e",
                "set the clipboard to (read (POSIX file (item 1 of argv)) as «class PNGf»)", "-e", "end run", png]

    def poll(self) -> list[tuple[str, bool, str]]:
        """Finished effects as (kind, ok, message); reaps sound players.  Never blocks."""
        self._sounds = [p for p in self._sounds if p.poll() is None]
        with self._lock:
            out, self._done = self._done, []
        return out
