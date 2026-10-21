"""Who may use the 会话安排 page (Kimi K3 review 2026-10-22, blocker 1).

``GET /`` hands out nothing to an anonymous client: any local process can make HTTP requests to 127.0.0.1
(sandboxed apps included), so possession of the page must be proven through the filesystem instead.

* ``python -m mk20deck arrange`` (and, later, the keyboard link through mk20deck) asks the running daemon
  for a **one-time token** over a unix socket ``$MK20DECK_HOME/run/arrange.sock`` (directory 0700, socket
  0600, peer uid checked where the OS reports it).  Tokens are single use and expire after 60 s.
* The token never appears in a process argument list (``ps`` shows other users' argv on macOS): the daemon
  writes the link into a 0600 **link file** in the 0700 run directory (``open-*.webloc`` on macOS — handed
  to the default browser by LaunchServices — or ``open-*.html`` with a meta refresh elsewhere) and only that
  file *path* is passed to ``open``.  The file is deleted when its token is redeemed or expires, when the
  server stops, and stale ones at start-up.
* The browser opens ``/auth?t=<token>``; the server redeems it and creates a **session**: a random session
  id in a ``SameSite=Strict; HttpOnly`` cookie (30 days) plus a separate per-session CSRF token embedded in
  the page.  The session id rotates on every page load (the previous id stays valid for a short grace so
  in-flight requests and other tabs survive); the CSRF token stays with the session.
* Sessions are kept as SHA-256 hashes of the id in ``$MK20DECK_HOME/run/arrange-sessions.json`` (0600) so
  an open tab survives a daemon restart.  At most ``MAX_SESSIONS`` (oldest dropped).

Thread-safe (HTTP threads, the socket thread); never touches the deck controller.
"""
from __future__ import annotations

import glob
import hashlib
import html
import json
import logging
import os
import plistlib
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


LINK_GLOB = ("open-*.webloc", "open-*.html")


def link_file_bytes(url: str, kind: str) -> bytes:
    if kind == "webloc":  # macOS "Web internet location": `open` hands the URL to the default browser
        return plistlib.dumps({"URL": url})
    u = html.escape(url, quote=True)
    return (f'<!doctype html><html><head><meta charset="utf-8"><meta name="referrer" content="no-referrer">'
            f'<meta http-equiv="refresh" content="0;url={u}"><title>MK20 会话安排</title></head>'
            f'<body><a href="{u}">打开会话安排</a></body></html>').encode("utf-8")


class AuthStore:
    def __init__(self, sessions_path: str | None = None, clock: Callable[[], float] = time.time,
                 link_dir: str | None = None) -> None:
        self.path = sessions_path  # None = in memory only
        self.clock = clock
        self.link_dir = link_dir  # 0700 directory for link files (None = no link files)
        self._lock = threading.Lock()
        self._otp: dict[str, float] = {}  # sha256(token) -> expiry
        self._files: dict[str, str] = {}  # sha256(token) -> link file path
        self._sessions: list[WebSession] = []
        self._load()

    # ------------------------------------------------------------------ one-time tokens
    def mint(self) -> str:
        t = secrets.token_urlsafe(32)
        now = self.clock()
        with self._lock:
            gone = [k for k, v in self._otp.items() if v <= now]
            while len(self._otp) - len(gone) >= MAX_OTP:
                live = {k: v for k, v in self._otp.items() if k not in gone}
                gone.append(min(live, key=live.get))
            for k in gone:
                self._drop_locked(k)
            self._otp[_h(t)] = now + OTP_TTL_S
        return t

    def redeem(self, token: str) -> bool:
        """Single use: a token works once, within OTP_TTL_S of minting.  Its link file goes away too."""
        if not isinstance(token, str) or not 20 <= len(token) <= 100:
            return False
        h, now = _h(token), self.clock()
        with self._lock:
            exp = self._otp.get(h)
            self._drop_locked(h)
        return exp is not None and exp > now

    def _drop_locked(self, h: str) -> None:
        self._otp.pop(h, None)
        path = self._files.pop(h, None)
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass

    def prune(self) -> int:
        """Forget expired tokens and delete their link files (called ~5x per second by the socket thread)."""
        now = self.clock()
        with self._lock:
            gone = [k for k, v in self._otp.items() if v <= now]
            for k in gone:
                self._drop_locked(k)
        return len(gone)

    def mint_file(self, url_for: Callable[[str], str], kind: str | None = None) -> str:
        """A one-time link written to a 0600 file in the 0700 link directory; returns the file *path* (safe
        to pass to ``open``: no token in any argv)."""
        if not self.link_dir:
            raise OSError("no link directory")
        kind = kind or ("webloc" if sys.platform == "darwin" else "html")
        private_dir(self.link_dir)
        t = self.mint()
        fd, path = tempfile.mkstemp(prefix="open-", suffix=f".{kind}", dir=self.link_dir)  # 0600
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(link_file_bytes(url_for(t), kind))
            os.chmod(path, 0o600)
        except BaseException:
            try:
                os.unlink(path)
            except OSError:
                pass
            raise
        with self._lock:
            if _h(t) in self._otp:
                self._files[_h(t)] = path
            else:  # dropped meanwhile (MAX_OTP churn): never leave a file behind
                os.unlink(path)
                raise OSError("token dropped")
        return path

    def clear_links(self) -> int:
        """Delete every link file of ours (outstanding ones and stale ones from an earlier run)."""
        with self._lock:
            for h in list(self._files):
                self._drop_locked(h)
        n = 0
        if self.link_dir and os.path.isdir(self.link_dir):
            for pat in LINK_GLOB:
                for p in glob.glob(os.path.join(self.link_dir, pat)):
                    try:
                        os.unlink(p)
                        n += 1
                    except OSError:
                        pass
        return n

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
    """One request line per connection, one JSON reply line, sequential and bounded:

    * ``open``  → ``{"ok": true, "file": "<0600 link file>", "page": "http://127.0.0.1:<port>/"}``
    * ``mint``  → ``{"ok": true, "url": "http://127.0.0.1:<port>/auth?t=<one-time>"}`` (``arrange --print``)

    A refusal (foreign uid, unknown request) is sent before anything is read; the server then half-closes and
    drains, so the client always gets the reply (never a reset)."""

    def __init__(self, path: str, mint_url: Callable[[], str], mint_file: Callable[[], str] | None = None,
                 page_url: str = "", on_tick: Callable[[], object] | None = None) -> None:
        self.path = path
        self.mint_url = mint_url
        self.mint_file = mint_file
        self.page_url = page_url
        self.on_tick = on_tick
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
            if self.on_tick is not None:
                try:
                    self.on_tick()  # e.g. delete link files of expired tokens
                except Exception as e:  # noqa: BLE001
                    log.debug("arrange auth tick: %s", e)
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

    @staticmethod
    def _reply(conn: socket.socket, obj: dict) -> None:
        """Reply line, then half-close and drain what the client still sends: it reads our line, never a reset."""
        try:
            conn.sendall(json.dumps(obj).encode() + b"\n")
            conn.shutdown(socket.SHUT_WR)
            conn.settimeout(0.3)
            left = 4096
            while left > 0:
                chunk = conn.recv(min(left, 1024))
                if not chunk:
                    break
                left -= len(chunk)
        except OSError:
            pass

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(SOCKET_TIMEOUT_S)
        uid = _peer_uid(conn)
        if uid is not None and hasattr(os, "getuid") and uid not in (os.getuid(), 0):
            self.refused += 1
            self._reply(conn, {"ok": False, "error": "uid"})  # before reading anything
            return
        buf = b""
        deadline = time.monotonic() + SOCKET_TIMEOUT_S
        while b"\n" not in buf and len(buf) < 64 and time.monotonic() < deadline:
            chunk = conn.recv(64)
            if not chunk:
                break
            buf += chunk
        req = buf.split(b"\n", 1)[0].strip()
        try:
            if req == b"mint":
                out = {"ok": True, "url": self.mint_url()}
            elif req == b"open" and self.mint_file is not None:
                out = {"ok": True, "file": self.mint_file(), "page": self.page_url}
            else:
                self.refused += 1
                self._reply(conn, {"ok": False, "error": "request"})
                return
        except OSError as e:
            self._reply(conn, {"ok": False, "error": f"link: {type(e).__name__}"})
            return
        self._reply(conn, out)
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


def _ask(path: str | None, request: bytes, timeout: float) -> dict:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    buf = b""
    try:
        s.connect(path or default_socket_path())
        try:
            s.sendall(request)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the daemon refused before reading: its reply is already here
        while len(buf) < 8192 and b"\n" not in buf:
            try:
                chunk = s.recv(4096)
            except ConnectionResetError:
                break
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    if not buf:
        raise ConnectionError("daemon refused: closed without a reply")
    d = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
    if not isinstance(d, dict) or not d.get("ok"):
        raise ConnectionError(f"daemon refused: {d.get('error', '?') if isinstance(d, dict) else '?'}")
    return d


def request_url(path: str | None = None, timeout: float = 3.0) -> str:
    """CLI ``--print``: one-time page URL from the running daemon.  OSError if it is not reachable."""
    d = _ask(path, b"mint\n", timeout)
    if not str(d.get("url", "")).startswith("http://127.0.0.1:"):
        raise ConnectionError("daemon refused: bad url")
    return d["url"]


def request_link_file(path: str | None = None, timeout: float = 3.0) -> tuple[str, str]:
    """CLI: ``(link file path, page url without token)`` — the path is what goes to ``open``."""
    d = _ask(path, b"open\n", timeout)
    f = str(d.get("file", ""))
    run_dir = os.path.dirname(os.path.abspath(path or default_socket_path()))
    if not f or os.path.dirname(os.path.abspath(f)) != run_dir or not os.path.basename(f).startswith("open-"):
        raise ConnectionError("daemon refused: bad link file")
    return f, str(d.get("page", ""))
