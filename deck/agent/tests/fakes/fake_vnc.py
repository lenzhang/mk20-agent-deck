"""Fake RFB 3.8 server (macOS Screen Sharing stand-in) for host tests of mk20-agent.
- security type 2 (VNC auth, DES) by default, optionally type 1 (None) or a refusal;
- framebuffer = synthetic RGB image (callable or bytes), served as Raw (0) or ZRLE (16, raw CPIXEL tiles,
  one zlib stream per connection as the spec requires);
- honours SetPixelFormat only for 32bpp true colour (anything else is recorded and the client is dropped);
- records KeyEvent / PointerEvent / ClientCutText for assertions. Single-threaded per connection."""
import os, socket, struct, threading, zlib
from .des import vnc_response

def gradient(w, h):
    """Deterministic synthetic desktop: R = x, G = y, B = (x ^ y), all & 0xFF."""
    return bytes(b for y in range(h) for x in range(w) for b in (x & 0xFF, y & 0xFF, (x ^ y) & 0xFF))

class FakeVNC:
    def __init__(self, width=320, height=200, password=b"secret12", sec_type=2, image=None, host="127.0.0.1"):
        self.w, self.h, self.password, self.sec_type = width, height, password, sec_type
        self.rgb = image if image is not None else gradient(width, height)
        assert len(self.rgb) == width * height * 3
        self.events, self.cut_text, self.errors = [], [], []
        self.auth_ok = self.auth_fail = self.updates = 0
        self.encodings_seen = []
        self.sock = socket.socket(); self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, 0)); self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self._stop = False
        self.thread = threading.Thread(target=self._serve, daemon=True); self.thread.start()

    def close(self):
        self._stop = True
        try: self.sock.close()
        except OSError: pass

    def __enter__(self): return self
    def __exit__(self, *a): self.close()

    def _serve(self):
        while not self._stop:
            try: c, _ = self.sock.accept()
            except OSError: return
            threading.Thread(target=self._conn, args=(c,), daemon=True).start()

    @staticmethod
    def _recv(c, n):
        b = b""
        while len(b) < n:
            d = c.recv(n - len(b))
            if not d: raise EOFError
            b += d
        return b

    def _conn(self, c):
        try: self._session(c)
        except (EOFError, OSError, struct.error) as e: self.errors.append(repr(e))
        finally: c.close()

    def _session(self, c):
        r = self._recv
        c.sendall(b"RFB 003.008\n")
        if r(c, 12) != b"RFB 003.008\n": self.errors.append("version"); return
        if self.sec_type == 0:   # refuse with reason
            msg = b"no access"; c.sendall(b"\0" + struct.pack(">I", len(msg)) + msg); return
        c.sendall(bytes([1, self.sec_type]))
        if r(c, 1)[0] != self.sec_type: self.errors.append("sectype"); return
        if self.sec_type == 2:
            ch = os.urandom(16); c.sendall(ch)
            if r(c, 16) != vnc_response(self.password, ch):
                self.auth_fail += 1
                msg = b"Authentication failed"; c.sendall(struct.pack(">I", 1) + struct.pack(">I", len(msg)) + msg); return
        self.auth_ok += 1
        c.sendall(struct.pack(">I", 0))
        r(c, 1)   # ClientInit (shared flag)
        name = b"Fake Mac"
        pf = struct.pack(">BBBBHHHBBBxxx", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
        c.sendall(struct.pack(">HH", self.w, self.h) + pf + struct.pack(">I", len(name)) + name)
        fmt = dict(bpp=32, big=0, rs=16, gs=8, bs=0)
        encs, zs = [0], None
        while True:
            t = r(c, 1)[0]
            if t == 0:   # SetPixelFormat
                p = r(c, 19)[3:]
                bpp, depth, big, tc, rm, gm, bm, rs, gs, bs = struct.unpack(">BBBBHHHBBB", p[:13])
                if bpp != 32 or not tc or (rm, gm, bm) != (255, 255, 255):
                    self.errors.append(f"pixfmt bpp={bpp} tc={tc}"); return
                fmt = dict(bpp=32, big=big, rs=rs, gs=gs, bs=bs)
            elif t == 2:  # SetEncodings
                _, n = struct.unpack(">BH", r(c, 3))
                encs = list(struct.unpack(f">{n}i", r(c, 4 * n))) if n else []
                self.encodings_seen.append(encs)
            elif t == 3:  # FramebufferUpdateRequest
                inc, x, y, w, h = struct.unpack(">BHHHH", r(c, 9))
                w, h = min(w, self.w - x), min(h, self.h - y)
                use_zrle = 16 in encs and (encs.index(16) < encs.index(0) if 0 in encs else True)
                if use_zrle and zs is None: zs = zlib.compressobj()
                body = self._zrle(zs, fmt, x, y, w, h) if use_zrle else self._raw(fmt, x, y, w, h)
                c.sendall(struct.pack(">BxH", 0, 1) + struct.pack(">HHHHi", x, y, w, h, 16 if use_zrle else 0) + body)
                self.updates += 1
            elif t == 4:  # KeyEvent
                b = r(c, 7); down = b[0]; key = struct.unpack(">I", b[3:7])[0]
                self.events.append(("key", key, down))
            elif t == 5:  # PointerEvent
                m, x, y = struct.unpack(">BHH", r(c, 5))
                self.events.append(("ptr", m, x, y))
            elif t == 6:  # ClientCutText
                n = struct.unpack(">xxxI", r(c, 7))[0]
                if n > 1 << 20: self.errors.append("cut too long"); return
                self.cut_text.append(r(c, n))
            else:
                self.errors.append(f"unknown msg {t}"); return

    def _px(self, fmt, i):
        rr, gg, bb = self.rgb[i], self.rgb[i + 1], self.rgb[i + 2]
        v = (rr << fmt["rs"]) | (gg << fmt["gs"]) | (bb << fmt["bs"])
        return v

    def _raw(self, fmt, x0, y0, w, h):
        out = bytearray(); end = ">I" if fmt["big"] else "<I"
        for y in range(y0, y0 + h):
            for x in range(x0, x0 + w):
                out += struct.pack(end, self._px(fmt, (y * self.w + x) * 3))
        return bytes(out)

    def _zrle(self, zs, fmt, x0, y0, w, h):
        # CPIXEL = 3 bytes (32bpp, depth 24, all colour bits fit in the low/high 3 bytes)
        raw = bytearray()
        for ty in range(y0, y0 + h, 64):
            for tx in range(x0, x0 + w, 64):
                raw.append(0)   # subencoding 0: raw CPIXELs
                for y in range(ty, min(ty + 64, y0 + h)):
                    for x in range(tx, min(tx + 64, x0 + w)):
                        v = self._px(fmt, (y * self.w + x) * 3)
                        b4 = struct.pack(">I" if fmt["big"] else "<I", v)
                        raw += b4[1:] if fmt["big"] else b4[:3]
        data = zs.compress(bytes(raw)) + zs.flush(zlib.Z_SYNC_FLUSH)
        return struct.pack(">I", len(data)) + data
