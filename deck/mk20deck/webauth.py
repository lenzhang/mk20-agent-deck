"""Who may use the 会话安排 page (Kimi K3 review 2026-10-22, blocker 1).

``GET /`` hands out nothing to an anonymous client: any local process can make HTTP requests to 127.0.0.1
(sandboxed apps included), so possession of the page must be proven through the filesystem instead.

* ``python -m mk20deck arrange`` (and, later, the keyboard link through mk20deck) asks the running daemon
  for a **one-time token** over a unix socket ``$MK20DECK_HOME/run/arrange.sock`` (directory 0700, socket
  0600, peer uid checked where the OS reports it).  Tokens are single use and expire after 60 s.
* The browser opens ``/auth?t=<token>``; the server redeems it and creates a **session**: a random session
  id in a ``SameSite=Strict; HttpOnly`` cookie (30 days) plus a separate per-session CSRF token embedded in
  the page.  The session id rotates on every page load (the previous id stays valid for a short grace so
  in-flight requests and other tabs survive); the CSRF token stays with the session.
* Sessions are kept as SHA-256 hashes of the id in ``$MK20DECK_HOME/run/arrange-sessions.json`` (0600) so
  an open tab survives a daemon restart.  At most ``MAX_SESSIONS`` (oldest dropped).

Thread-safe (HTTP threads, the socket thread); never touches the deck controller.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import socket
import stat
import struct
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable

log = logging.getLogger(__name__)

OTP_TTL_S = 60.0
SESSION_TTL_S = 30 * 86400.0
ROTATE_GRACE_S = 120.0
MAX_OTP = 16
MAX_SESSIONS = 8
SOCKET_TIMEOUT_S = 2.0


def default_run_dir() -> str:
    home = os.path.expanduser(os.environ.get("MK20DECK_HOME", "~/.config/mk20-agent-deck"))
    return os.path.join(home, "run")


def default_socket_path() -> str:
    return os.path.join(default_run_dir(), "arrange.sock")


def default_sessions_path() -> str:
    return os.path.join(default_run_dir(), "arrange-sessions.json")


def _h(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "replace")).hexdigest()


def private_dir(path: str) -> None:
    """Create ``path`` as a 0700 directory owned by us (refuses a symlink / foreign owner)."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or (hasattr(os, "getuid") and st.st_uid != os.getuid()):
        raise PermissionError(f"{path} is not a directory owned by this user")
    if stat.S_IMODE(st.st_mode) != 0o700:
        os.chmod(path, 0o700)


@dataclass
class WebSession:
    h: str          # sha256 of the current session id (the cookie value)
    csrf: str       # per-session CSRF token (stable across id rotation)
    exp: float
    created: float
    prev_h: str = ""        # the id before the last rotation ...
    prev_until: float = 0.0  # ... is accepted until then


class AuthStore:
    def __init__(self, sessions_path: str | None = None, clock: Callable[[], float] = time.time) -> None:
        self.path = sessions_path  # None = in memory only
        self.clock = clock
        self._lock = threading.Lock()
        self._otp: dict[str, float] = {}  # sha256(token) -> expiry
        self._sessions: list[WebSession] = []
        self._load()

    # ------------------------------------------------------------------ one-time tokens
    def mint(self) -> str:
        t = secrets.token_urlsafe(32)
        now = self.clock()
        with self._lock:
            self._otp = {k: v for k, v in self._otp.items() if v > now}
            while len(self._otp) >= MAX_OTP:
                del self._otp[min(self._otp, key=self._otp.get)]
            self._otp[_h(t)] = now + OTP_TTL_S
        return t

    def redeem(self, token: str) -> bool:
        """Single use: a token works once, within OTP_TTL_S of minting."""
        if not isinstance(token, str) or not 20 <= len(token) <= 100:
            return False
        h, now = _h(token), self.clock()
        with self._lock:
            exp = self._otp.pop(h, None)
        return exp is not None and exp > now

    # ------------------------------------------------------------------ sessions
    def create(self) -> tuple[str, WebSession]:
        sid, now = secrets.token_urlsafe(32), self.clock()
        s = WebSession(_h(sid), secrets.token_urlsafe(32), now + SESSION_TTL_S, now)
        with self._lock:
            self._sessions = [x for x in self._sessions if x.exp > now]
            self._sessions.append(s)
            self._sessions = sorted(self._sessions, key=lambda x: x.exp)[-MAX_SESSIONS:]
            self._save_locked()
        return sid, s

    def lookup(self, sid: str | None) -> WebSession | None:
        if not sid or len(sid) > 100:
            return None
        h, now = _h(sid), self.clock()
        with self._lock:
            for s in self._sessions:
                if s.exp > now and (s.h == h or (s.prev_h == h and now < s.prev_until)):
                    return s
        return None

    def rotate(self, s: WebSession) -> str:
        """New session id for ``s`` (page load); the old one stays valid for ROTATE_GRACE_S."""
        sid, now = secrets.token_urlsafe(32), self.clock()
        with self._lock:
            s.prev_h, s.prev_until = s.h, now + ROTATE_GRACE_S
            s.h, s.exp = _h(sid), now + SESSION_TTL_S
            self._save_locked()
        return sid

    def sessions(self) -> int:
        with self._lock:
            return len(self._sessions)

    # ------------------------------------------------------------------ persistence
    def _load(self) -> None:
        if not self.path:
            return
        try:
            with open(self.path, "rb") as f:
                d = json.loads(f.read(256 * 1024).decode("utf-8"))
            now = self.clock()
            out = []
            for x in d.get("sessions", [])[:MAX_SESSIONS] if isinstance(d, dict) else []:
                try:
                    s = WebSession(str(x["h"]), str(x["csrf"]), float(x["exp"]), float(x["created"]),
                                   str(x.get("prev_h", "")), float(x.get("prev_until", 0.0)))
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
                if len(s.h) == 64 and 20 <= len(s.csrf) <= 100 and now < s.exp <= now + SESSION_TTL_S:
                    out.append(s)
            self._sessions = out
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001 — a bad sessions file only means "run mk20deck arrange again"
            log.warning("arrange sessions file ignored (%s)", type(e).__name__)
            self._sessions = []

    def _save_locked(self) -> None:
        if not self.path:
            return
        try:
            folder = os.path.dirname(self.path)
            private_dir(folder)
            fd, tmp = tempfile.mkstemp(prefix=".arrange-sessions.", dir=folder)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"version": 1, "sessions": [asdict(s) for s in self._sessions]}, f)
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as e:  # sessions then only last until the daemon restarts
            log.warning("arrange sessions not saved (%s)", type(e).__name__)


# ---------------------------------------------------------------------- local channel (unix socket)
def _peer_uid(conn: socket.socket) -> int | None:
    """Peer uid where the OS reports it (Linux SO_PEERCRED, macOS LOCAL_PEERCRED), else None."""
    try:
        if hasattr(socket, "SO_PEERCRED"):
            fmt = "3i"
            _pid, uid, _gid = struct.unpack(fmt, conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                                  struct.calcsize(fmt)))
            return uid
        if sys.platform == "darwin":
            fmt = "IIh16I"  # struct xucred: cr_version, cr_uid, cr_ngroups, cr_groups[16]
            raw = conn.getsockopt(0, getattr(socket, "LOCAL_PEERCRED", 1), struct.calcsize(fmt))  # SOL_LOCAL = 0
            return struct.unpack_from("II", raw)[1]
    except (OSError, struct.error):
        return None
    return None


class AuthSocket:
    """``mint\\n`` → ``{"ok": true, "url": "http://127.0.0.1:<port>/auth?t=<one-time>"}``.  Sequential, bounded."""

    def __init__(self, path: str, mint_url: Callable[[], str]) -> None:
        self.path = path
        self.mint_url = mint_url
        self.sock: socket.socket | None = None
        self.thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ino: int | None = None
        self.served = 0
        self.refused = 0

    def start(self) -> bool:
        try:
            private_dir(os.path.dirname(self.path))
            if os.path.lexists(self.path):
                st = os.lstat(self.path)
                if not stat.S_ISSOCK(st.st_mode):
                    raise FileExistsError(f"{self.path} exists and is not a socket")
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                probe.settimeout(0.5)
                try:
                    probe.connect(self.path)
                    probe.close()
                    raise FileExistsError(f"{self.path}: another mk20deck answers there")
                except (ConnectionRefusedError, FileNotFoundError, socket.timeout):
                    probe.close()
                    os.unlink(self.path)  # stale socket of a previous run
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.bind(self.path)  # inside the 0700 directory: no window with looser permissions
            os.chmod(self.path, 0o600)
            s.listen(4)
            s.settimeout(0.2)  # accept poll: stop() returns quickly
        except OSError as e:
            log.warning("会话安排授权通道未启动（%s）：mk20deck arrange 无法打开网页", e)
            return False
        self.sock = s
        self._ino = os.stat(self.path).st_ino
        self.thread = threading.Thread(target=self._serve, name="mk20-arrange-auth", daemon=True)
        self.thread.start()
        return True

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                self._handle(conn)
            except Exception as e:  # noqa: BLE001
                log.debug("arrange auth socket: %s", e)
            finally:
                conn.close()

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(SOCKET_TIMEOUT_S)
        uid = _peer_uid(conn)
        if uid is not None and hasattr(os, "getuid") and uid not in (os.getuid(), 0):
            self.refused += 1
            conn.sendall(b'{"ok": false, "error": "uid"}\n')
            return
        buf = b""
        deadline = time.monotonic() + SOCKET_TIMEOUT_S
        while b"\n" not in buf and len(buf) < 64 and time.monotonic() < deadline:
            chunk = conn.recv(64)
            if not chunk:
                break
            buf += chunk
        if buf.split(b"\n", 1)[0].strip() != b"mint":
            self.refused += 1
            conn.sendall(b'{"ok": false, "error": "request"}\n')
            return
        conn.sendall(json.dumps({"ok": True, "url": self.mint_url()}).encode() + b"\n")
        self.served += 1

    def stop(self) -> None:
        self._stop.set()
        if self.sock is not None:
            self.sock.close()
            self.sock = None
        try:  # only remove our own socket file (a newer daemon may own the path now)
            if self._ino is not None and os.stat(self.path).st_ino == self._ino:
                os.unlink(self.path)
        except OSError:
            pass
        if self.thread is not None:
            self.thread.join(2.0)


def request_url(path: str | None = None, timeout: float = 3.0) -> str:
    """CLI side: one-time page URL from the running daemon.  OSError if it is not reachable."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path or default_socket_path())
        s.sendall(b"mint\n")
        buf = b""
        while len(buf) < 4096:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                break
    finally:
        s.close()
    d = json.loads(buf.decode("utf-8") or "{}")
    if not d.get("ok") or not str(d.get("url", "")).startswith("http://127.0.0.1:"):
        raise ConnectionError(f"daemon refused: {d.get('error', '?')}")
    return d["url"]
