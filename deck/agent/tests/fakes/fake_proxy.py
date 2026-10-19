"""Fake HTTP CONNECT proxy (stand-in for the Mac's mihomo port / launchd relay) for host tests.
- CONNECT host:port -> 200 and a byte tunnel, unless the target is not in `allow` (403) or `fail` is set
  ("refuse" -> 502, "close" -> drop the connection without a reply, "delay" -> wait `delay_s` first);
- any non-CONNECT request (absolute-URI plain HTTP = an attempt to bypass the tunnel) is answered 405 and recorded
  in `violations`;
- records every CONNECT target in `connects`."""
import select, socket, threading, time

class FakeProxy:
    def __init__(self, allow=None, fail=None, delay_s=0.0, host="127.0.0.1",
                 mode=None, targets=None, socketpair=False, upstream_factory=None, record=None):
        self.allow, self.fail, self.delay_s = allow, fail, delay_s
        self.mode, self.targets, self.upstream_factory = mode, targets or {}, upstream_factory
        self.record = record
        self.client = None
        self.connects, self.violations, self.errors = [], [], []
        self._stop = False
        if socketpair:
            self.client, c = socket.socketpair()
            self.sock = None
            self.port = 7890; self.url = "http://127.0.0.1:7890"
            threading.Thread(target=self._conn, args=(c,), daemon=True).start()
            return
        self.sock = socket.socket(); self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, 0)); self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.url = f"http://{host}:{self.port}"
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    def close(self):
        self._stop = True
        try:
            if self.client: self.client.close()
            if self.sock: self.sock.close()
        except OSError: pass
    def __enter__(self): return self
    def __exit__(self, *a): self.close()

    def _serve(self):
        while not self._stop:
            try: c, _ = self.sock.accept()
            except OSError: return
            threading.Thread(target=self._conn, args=(c,), daemon=True).start()

    def _conn(self, c):
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                d = c.recv(4096)
                if not d: return
                head += d
                if len(head) > 16384: return
            hdr, rest = head.split(b"\r\n\r\n", 1)
            line = hdr.split(b"\r\n")[0].decode("latin-1")
            parts = line.split()
            if len(parts) != 3 or parts[0] != "CONNECT":
                self.violations.append(line)
                c.sendall(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"); return
            target = parts[1]; self.connects.append(target)
            if self.record is not None: self.record.append(self.port)
            if self.mode == "slowloris":
                for b in b"HTTP/1.1 200 Connection established\r\n\r\n":
                    c.sendall(bytes([b])); time.sleep(0.15)
                return
            replies = {"407": b"HTTP/1.1 407 Authentication Required\r\n\r\n",
                       "403": b"HTTP/1.1 403 Forbidden\r\n\r\n",
                       "garbage": b"garbage\r\n\r\n",
                       "extra-bytes": b"HTTP/1.1 200 OK\r\n\r\nX",
                       "oversize": b"HTTP/1.1 200 OK\r\nX: " + b"a" * 8192 + b"\r\n\r\n"}
            if self.mode in replies:
                c.sendall(replies[self.mode]); return
            if self.delay_s: time.sleep(self.delay_s)
            if self.fail == "close": return
            if self.fail == "refuse" or (self.allow is not None and target not in self.allow):
                c.sendall(b"HTTP/1.1 " + (b"403 Forbidden" if self.fail != "refuse" else b"502 Bad Gateway") +
                          b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"); return
            h, p = target.rsplit(":", 1)
            try:
                u = (self.upstream_factory() if self.upstream_factory else
                     socket.create_connection(self.targets.get(target, (h, int(p))), timeout=5))
            except OSError:
                c.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n"); return
            c.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            if rest: u.sendall(rest)
            self._pipe(c, u)
        except OSError as e:
            self.errors.append(repr(e))
        finally:
            c.close()

    @staticmethod
    def _pipe(a, b):
        try:
            while True:
                r, _, _ = select.select([a, b], [], [], 30)
                if not r: return
                for s in r:
                    d = s.recv(65536)
                    if not d: return
                    (b if s is a else a).sendall(d)
        finally:
            b.close()
