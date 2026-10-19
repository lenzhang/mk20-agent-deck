"""Minimal plain /time fake, with TCP and explicit test-only socketpair transports."""
import socket
import threading


class FakeTime:
    def __init__(self, body, socketpair=False):
        self.requests = []
        self.sock = self.client = None
        self.body = body
        if socketpair:
            self.client, c = socket.socketpair()
            self.port = 80
            threading.Thread(target=self._conn, args=(c,), daemon=True).start()
        else:
            self.sock = socket.socket()
            self.sock.bind(("127.0.0.1", 0)); self.sock.listen(1)
            self.port = self.sock.getsockname()[1]
            threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        try: c, _ = self.sock.accept()
        except OSError: return
        self._conn(c)

    def _conn(self, c):
        try:
            data = b""
            while b"\r\n\r\n" not in data:
                d = c.recv(4096)
                if not d: return
                data += d
            self.requests.append(data)
            c.sendall(f"HTTP/1.1 200 OK\r\nContent-Length: {len(self.body)}\r\n\r\n".encode() + self.body)
        except OSError: pass
        finally: c.close()

    def __enter__(self): return self
    def __exit__(self, *args):
        if self.sock: self.sock.close()
        if self.client: self.client.close()
