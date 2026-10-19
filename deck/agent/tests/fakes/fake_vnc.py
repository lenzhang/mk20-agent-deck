"""Fake RFB 3.8 server (macOS Screen Sharing stand-in) for host tests of mk20-agent.
- security type 2 (VNC auth, DES) by default, optionally type 1 (None) or a refusal;
- framebuffer = synthetic RGB bytes, served as Raw, Hextile or ZRLE (all tile modes,
  one zlib stream per connection as the spec requires);
- honours SetPixelFormat only for 32bpp true colour (anything else is recorded and the client is dropped);
- records KeyEvent / PointerEvent / ClientCutText for assertions. Single-threaded per connection."""
import os, socket, struct, threading, time, zlib
from .des import vnc_response

def gradient(w, h):
    """Deterministic synthetic desktop: R = x, G = y, B = (x ^ y), all & 0xFF."""
    return bytes(b for y in range(h) for x in range(w) for b in (x & 0xFF, y & 0xFF, (x ^ y) & 0xFF))

class FakeVNC:
    def __init__(self, width=320, height=200, password=b"secret12", sec_type=2, image=None, host="127.0.0.1",
                 encoding=None, version=8, resize=None, copyrect=False, cursor=False, extended=False,
                 cut_length=0, cut_wire_length=None, delay=0, truncate=False, malformed=None,
                 zrle_mode=0, hextile_mode="raw", split=False, name_length=None, security_reason=None,
                 socketpair=False, messages=False, resize_at=0, solid=None, changing=False):
        self.w, self.h, self.password, self.sec_type = width, height, password, sec_type
        self.changing = changing
        self.solid = solid
        self.rgb = None if solid is not None else image if image is not None else gradient(width, height)
        assert len(solid) == 3 if solid is not None else len(self.rgb) == width * height * 3
        self.encoding, self.version, self.resize = encoding, version, resize
        self.copyrect, self.cursor, self.extended = copyrect, cursor, extended
        self.cut_length, self.cut_wire_length, self.delay = cut_length, cut_wire_length, delay
        self.truncate, self.malformed, self.zrle_mode = truncate, malformed, zrle_mode
        self.hextile_mode, self.split, self.name_length = hextile_mode, split, name_length
        self.security_reason = security_reason
        self.messages, self.resize_at = messages, resize_at
        self.events, self.cut_text, self.errors = [], [], []
        self.requests = []
        self.auth_ok = self.auth_fail = self.updates = 0
        self.encodings_seen = []
        self._stop = False
        self._clients = set()
        self.client = self.sock = None
        if socketpair:
            self.client, server = socket.socketpair()
            self._clients.add(server); self.port = 1
            self.thread = threading.Thread(target=self._conn, args=(server,), daemon=True); self.thread.start()
            return
        self.sock = socket.socket(); self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, 0)); self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True); self.thread.start()

    def close(self):
        self._stop = True
        for c in list(self._clients):
            try: c.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        if self.client: self.client.close()
        try:
            if self.sock: self.sock.close()
        except OSError: pass

    def __enter__(self): return self
    def __exit__(self, *a): self.close()

    def _serve(self):
        while not self._stop:
            try: c, _ = self.sock.accept()
            except OSError: return
            self._clients.add(c)
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
        finally:
            self._clients.discard(c)
            c.close()

    def _session(self, c):
        r = self._recv
        banner = f"RFB 003.{self.version:03d}\n".encode()
        c.sendall(banner)
        if r(c, 12) != banner: self.errors.append("version"); return
        if self.sec_type == 0:   # refuse with reason
            msg = self.security_reason or b"no access"
            c.sendall((b"\0" * 4 if self.version == 3 else b"\0") + struct.pack(">I", len(msg)) + msg); return
        if self.version == 3: c.sendall(struct.pack(">I", self.sec_type))
        else:
            c.sendall(bytes([1, self.sec_type]))
            if r(c, 1)[0] != self.sec_type: self.errors.append("sectype"); return
        if self.sec_type == 2:
            ch = os.urandom(16); c.sendall(ch)
            if r(c, 16) != vnc_response(self.password, ch):
                self.auth_fail += 1
                msg = self.security_reason or b"Authentication failed"
                c.sendall(struct.pack(">I", 1) + (struct.pack(">I", len(msg)) + msg if self.version == 8 else b"")); return
        self.auth_ok += 1
        if self.sec_type == 2 or self.version == 8: c.sendall(struct.pack(">I", 0))
        r(c, 1)   # ClientInit (shared flag)
        name = b"Fake Mac"
        pf = struct.pack(">BBBBHHHBBBxxx", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
        c.sendall(struct.pack(">HH", self.w, self.h) + pf + struct.pack(">I", self.name_length if self.name_length is not None else len(name)) + name)
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
                self.requests.append((inc, x, y, w, h))
                if self.changing and self.rgb is not None:
                    self.rgb = bytes([(self.updates+1) & 255]) + self.rgb[1:]
                first = self.updates == 0
                if self.delay: time.sleep(self.delay)
                if first and self.cut_wire_length is not None:
                    c.sendall(struct.pack(">BxxxI", 3, self.cut_wire_length)); return
                if first and self.cut_length:
                    c.sendall(struct.pack(">BxxxI", 3, self.cut_length))
                    remaining = self.cut_length
                    while remaining:
                        block = min(4096, remaining); c.sendall(b"x" * block); remaining -= block
                if first and self.extended == "caps":
                    c.sendall(struct.pack(">BxxxiII", 3, -8, 0x01000001, 1024 * 1024))
                if self.messages:  # Bell and an ignored SetColourMapEntries before the frame.
                    c.sendall(b"\2" + struct.pack(">BBHHHHH", 1, 0, 0, 1, 255, 0, 0))
                rects = []
                if first and self.extended is True: rects.append((0, 0, 0, 0, 0xC0A1E5CE, b""))
                if first and self.cursor: rects.append((1, 1, 2, 2, -239, b"\0" * 18))
                resized = self.resize and len(self.requests) - 1 == self.resize_at
                if resized:
                    self.w, self.h = self.resize; self.rgb = gradient(self.w, self.h)
                    rects.append((0, 0, self.w, self.h, -223, b""))
                if first and self.copyrect:
                    rects.append((0, 0, 1, 1, 1, struct.pack(">HH", 1, 1)))
                if rects:
                    self._update(c, rects)
                    self.updates += 1
                    if resized or self.copyrect: continue
                w, h = min(w, self.w - x), min(h, self.h - y)
                use_zrle = 16 in encs and (encs.index(16) < encs.index(0) if 0 in encs else True)
                encoding = self.encoding if self.encoding is not None else (16 if use_zrle else 0)
                if encoding == 16 and zs is None: zs = zlib.compressobj()
                areas = [(x, y, w, h)]
                if self.split:
                    # Right half first, then left: target accumulators must not assume scan order.
                    areas = [(x + w // 2, y, w - w // 2, h), (x, y, w // 2, h)]
                rects = []
                for ax, ay, aw, ah in areas:
                    body = (self._zrle(zs, fmt, ax, ay, aw, ah) if encoding == 16 else
                            self._hextile(fmt, ax, ay, aw, ah) if encoding == 5 else self._raw(fmt, ax, ay, aw, ah))
                    if self.malformed == "bounds": aw += self.w
                    if self.malformed == "zrle_length": body = struct.pack(">I", 0xffffffff)
                    if self.malformed == "zrle_bad": body = struct.pack(">I", 4) + b"nope"
                    if self.malformed == "hextile_bounds": body = b"\x1a\0\0\0\0\x01\0\xff\xff\0\xff\xff"
                    rects.append((ax, ay, aw, ah, encoding, body))
                if self.truncate:
                    body = self._packet(rects); c.sendall(body[:len(body) // 2]); return
                self._update(c, rects)
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
        rr, gg, bb = self.solid if self.solid is not None else (self.rgb[i], self.rgb[i + 1], self.rgb[i + 2])
        v = (rr << fmt["rs"]) | (gg << fmt["gs"]) | (bb << fmt["bs"])
        return v

    @staticmethod
    def _packet(rects):
        out = bytearray(struct.pack(">BxH", 0, len(rects)))
        for x, y, w, h, enc, body in rects:
            out += struct.pack(">HHHHI", x, y, w, h, enc & 0xffffffff) + body
        return bytes(out)

    def _update(self, c, rects): c.sendall(self._packet(rects))

    def _hextile(self, fmt, x0, y0, w, h):
        out = bytearray()
        for ty in range(y0, y0 + h, 16):
            for tx in range(x0, x0 + w, 16):
                tw, th = min(16, x0 + w - tx), min(16, y0 + h - ty)
                if self.hextile_mode == "raw": out += b"\1" + self._raw(fmt, tx, ty, tw, th)
                elif self.hextile_mode == "solid":
                    out += b"\2" + self._raw(fmt, tx, ty, 1, 1)
                else:
                    colored = self.hextile_mode == "colored"
                    out.append(26 if colored else 14)  # background + subrects + colour or foreground
                    out += self._raw(fmt, tx, ty, 1, 1)
                    if not colored: out += self._raw(fmt, tx, ty, 1, 1)
                    out.append(tw * th - 1)
                    for yy in range(th):
                        for xx in range(tw):
                            if xx == yy == 0: continue
                            if colored: out += self._raw(fmt, tx + xx, ty + yy, 1, 1)
                            out += bytes([(xx << 4) | yy, 0])
        return bytes(out)

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
                mode = self.zrle_mode
                raw.append(mode)
                pixels = []
                for y in range(ty, min(ty + 64, y0 + h)):
                    for x in range(tx, min(tx + 64, x0 + w)):
                        v = self._px(fmt, (y * self.w + x) * 3)
                        b4 = struct.pack(">I" if fmt["big"] else "<I", v)
                        pixels.append(b4[1:] if fmt["big"] else b4[:3])
                if mode == 0: raw += b"".join(pixels)
                elif mode == 1: raw += pixels[0]
                elif mode == 128:
                    for p, length in self._runs(pixels): raw += p + self._run_length(length)
                else:
                    np = mode & 127
                    palette = list(dict.fromkeys(pixels))
                    assert len(palette) <= np
                    palette += [palette[0]] * (np - len(palette))
                    raw += b"".join(palette)
                    indices = [palette.index(p) for p in pixels]
                    if mode > 128:
                        for idx, length in self._runs(indices): raw += bytes([idx | 128]) + self._run_length(length)
                    else:
                        bits = 1 if np <= 2 else 2 if np <= 4 else 4
                        tw = min(64, x0 + w - tx)
                        for row in range(0, len(indices), tw):
                            byte, used = 0, 0
                            for idx in indices[row:row + tw]:
                                byte = (byte << bits) | idx; used += bits
                                if used == 8: raw.append(byte); byte = used = 0
                            if used: raw.append(byte << (8 - used))
        if self.malformed == "zrle_palette": raw = b"\x03" + b"\0" * 9 + b"\xff" * 4096
        if self.malformed == "zrle_run": raw = b"\x80\0\0\0\xff\xff\xff"
        if self.malformed == "zrle_mode": raw = b"\x81"
        if self.malformed == "zrle_extra": raw += b"\xff" * 100
        data = zs.compress(bytes(raw)) + zs.flush(zlib.Z_SYNC_FLUSH)
        return struct.pack(">I", len(data)) + data

    @staticmethod
    def _runs(items):
        prev, length = items[0], 0
        for item in items:
            if item != prev:
                yield prev, length
                prev, length = item, 0
            length += 1
        yield prev, length

    @staticmethod
    def _run_length(n):
        n -= 1
        return b"\xff" * (n // 255) + bytes([n % 255])
