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
    def __init__(self, sound: str = "Glass", snap_dir: str = "", platform: str = sys.platform,
                 popen: Callable[..., Any] = subprocess.Popen, access: Callable[[bool], bool | None] | None = None,
                 clock: Callable[[], float] = time.monotonic, exists: Callable[[str], bool] = os.path.exists):
        self.sound, self.snap_dir, self.platform = sound, snap_dir, platform
        self.popen, self.clock, self.exists = popen, clock, exists
        self.access = access or screen_capture_access
        self._asked = False  # CGRequestScreenCaptureAccess at most once per process
        self._sounds: list[Any] = []
        self._snap: tuple[Any, float, str, str] | None = None  # (proc, started, stage, png path)
        self._done: list[tuple[str, bool, str]] = []

    @classmethod
    def from_config(cls, cfg: Any) -> "Effects":
        return cls(sound=getattr(cfg, "approve_sound", "Glass"), snap_dir=getattr(cfg, "snap_dir", ""))

    def busy(self) -> bool:
        return self._snap is not None or bool(self._done)

    def run(self, effect: tuple) -> None:
        if not effect:
            return
        if effect[0] == "sound":
            self.play()
        elif effect[0] == "snap":
            self.snap()

    def _spawn(self, argv: list[str]) -> Any:
        return self.popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    # ------------------------------------------------------------------ sound
    def play(self) -> None:
        if not self.sound or self.platform != "darwin":
            return
        path = os.path.join(SOUND_DIR, self.sound + ".aiff")
        if not self.exists(path):
            log.warning("approve sound %s not found", path)
            return
        try:
            self._sounds.append(self._spawn([AFPLAY, path]))
        except OSError as e:
            log.warning("afplay: %s", e)

    # ------------------------------------------------------------------ SNAP
    def snap(self) -> None:
        if self._snap is not None:
            self._done.append(("snap", False, SNAP_RUNNING))
            return
        if self.platform != "darwin":
            self._done.append(("snap", False, SNAP_NOT_MAC))
            return
        ok = self.access(not self._asked)
        if ok is False:
            self._asked = True
            log.warning("snap: Screen Recording permission missing for %s", sys.executable)
            self._done.append(("snap", False, SNAP_NEED_TCC))
            return
        png = ""
        if self.snap_dir:
            try:
                os.makedirs(self.snap_dir, exist_ok=True)
            except OSError as e:
                log.warning("snap dir %s: %s", self.snap_dir, e)
                self._done.append(("snap", False, f"{SNAP_FAILED}：保存目录不可用"))
                return
            png = os.path.join(self.snap_dir, time.strftime("mk20-snap-%Y%m%d-%H%M%S.png"))
            argv = [SCREENCAPTURE, "-x", png]
        else:
            argv = [SCREENCAPTURE, "-x", "-c"]  # -x: no shutter sound; -c: straight to the clipboard
        try:
            self._snap = (self._spawn(argv), self.clock(), "capture", png)
        except OSError as e:
            log.warning("screencapture: %s", e)
            self._done.append(("snap", False, SNAP_FAILED))

    def _clipboard_argv(self, png: str) -> list[str]:
        # the PNG file onto the clipboard as an image (path passed as an argument, never spliced into the script)
        return [OSASCRIPT, "-e", "on run argv", "-e",
                "set the clipboard to (read (POSIX file (item 1 of argv)) as «class PNGf»)", "-e", "end run", png]

    def poll(self) -> list[tuple[str, bool, str]]:
        """Finished effects as (kind, ok, message); reaps sound players."""
        self._sounds = [p for p in self._sounds if p.poll() is None]
        if self._snap is not None:
            proc, t0, stage, png = self._snap
            rc = proc.poll()
            if rc is None:
                if self.clock() - t0 > SNAP_TIMEOUT_S:
                    proc.kill()
                    self._snap = None
                    self._done.append(("snap", False, f"{SNAP_FAILED}：超时"))
            elif rc != 0:
                self._snap = None
                err = (proc.stderr.read() if proc.stderr else b"") or b""
                log.warning("snap %s failed rc=%s %s", stage, rc, err[:200])
                self._done.append(("snap", False, SNAP_FAILED if stage == "capture" else
                                   f"已存 {os.path.basename(png)}，复制到剪贴板失败"))
            elif stage == "capture" and png:
                try:
                    self._snap = (self._spawn(self._clipboard_argv(png)), self.clock(), "clipboard", png)
                except OSError:
                    self._snap = None
                    self._done.append(("snap", False, f"已存 {os.path.basename(png)}，复制到剪贴板失败"))
            else:
                self._snap = None
                log.info("snap ok%s", f" ({png})" if png else "")
                self._done.append(("snap", True, SNAP_OK))
        out, self._done = self._done, []
        return out
