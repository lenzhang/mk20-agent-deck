"""A0: the fake environment itself works (fake VNC, fake OpenAI-compatible model, fake CONNECT proxy)."""
import json, os, socket, struct, sys, zlib, http.client
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.tests.fakes.des import des_encrypt_block, vnc_response   # noqa: E402
from agent.tests.fakes.fake_vnc import FakeVNC, gradient            # noqa: E402
from agent.tests.fakes.fake_model import FakeModel                  # noqa: E402
from agent.tests.fakes.fake_proxy import FakeProxy                  # noqa: E402

def test_des_known_vector():
    assert des_encrypt_block(bytes.fromhex("133457799BBCDFF1"), bytes.fromhex("0123456789ABCDEF")).hex() == "85e813540f0ab405"
    # RFB VNC auth with an all-zero challenge is deterministic and password-dependent
    assert vnc_response(b"a", b"\0" * 16) != vnc_response(b"b", b"\0" * 16)

def _recv(s, n):
    b = b""
    while len(b) < n:
        d = s.recv(n - len(b))
        assert d, "eof"
        b += d
    return b

def rfb_connect(port, password, encodings=(0,)):
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    assert _recv(s, 12) == b"RFB 003.008\n"; s.sendall(b"RFB 003.008\n")
    n = _recv(s, 1)[0]
    if n == 0:
        return s, None
    types = _recv(s, n); assert 2 in types or 1 in types
    t = 2 if 2 in types else 1
    s.sendall(bytes([t]))
    if t == 2:
        s.sendall(vnc_response(password, _recv(s, 16)))
    if struct.unpack(">I", _recv(s, 4))[0] != 0:
        return s, None
    s.sendall(b"\1")
    w, h = struct.unpack(">HH", _recv(s, 4)); _recv(s, 16); _recv(s, struct.unpack(">I", _recv(s, 4))[0])
    # 32bpp little-endian, r<<16 g<<8 b
    s.sendall(b"\0\0\0\0" + struct.pack(">BBBBHHHBBBxxx", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0))
    s.sendall(struct.pack(">BxH", 2, len(encodings)) + struct.pack(f">{len(encodings)}i", *encodings))
    return s, (w, h)

def rfb_grab(s, w, h, zd=None):
    s.sendall(struct.pack(">BBHHHH", 3, 0, 0, 0, w, h))
    t, nr = struct.unpack(">BxH", _recv(s, 4)); assert t == 0 and nr == 1
    x, y, rw, rh, enc = struct.unpack(">HHHHi", _recv(s, 12))
    rgb = bytearray(w * h * 3)
    if enc == 0:
        data = _recv(s, rw * rh * 4)
        for i in range(rw * rh):
            v = struct.unpack("<I", data[i * 4:i * 4 + 4])[0]
            rgb[i * 3:i * 3 + 3] = bytes([(v >> 16) & 255, (v >> 8) & 255, v & 255])
    else:
        assert enc == 16
        raw = zd.decompress(_recv(s, struct.unpack(">I", _recv(s, 4))[0])); o = 0
        for ty in range(0, rh, 64):
            for tx in range(0, rw, 64):
                assert raw[o] == 0; o += 1
                for yy in range(ty, min(ty + 64, rh)):
                    for xx in range(tx, min(tx + 64, rw)):
                        b, g, r = raw[o], raw[o + 1], raw[o + 2]; o += 3
                        rgb[(yy * w + xx) * 3:(yy * w + xx) * 3 + 3] = bytes([r, g, b])
    return bytes(rgb), enc

@pytest.mark.parametrize("encs,want", [((0,), 0), ((16, 0), 16)])
def test_fake_vnc_auth_and_frame(encs, want):
    with FakeVNC(width=130, height=70, password=b"pw123456") as v:
        s, size = rfb_connect(v.port, b"pw123456", encs)
        assert size == (130, 70)
        zd = zlib.decompressobj()
        img, enc = rfb_grab(s, *size, zd)
        assert enc == want and img == gradient(130, 70)
        img2, _ = rfb_grab(s, *size, zd)   # second update reuses the per-connection zlib stream
        assert img2 == img
        s.sendall(struct.pack(">BBHH", 5, 1, 10, 20) + struct.pack(">BBxxI", 4, 1, 0x61))
        s.sendall(struct.pack(">BxxxI", 6, 5) + b"hello")
        s.close()
        import time; time.sleep(0.2)
        assert ("ptr", 1, 10, 20) in v.events and ("key", 0x61, 1) in v.events and v.cut_text == [b"hello"]
        assert v.auth_ok == 1

def test_fake_vnc_wrong_password():
    with FakeVNC(password=b"right") as v:
        s, size = rfb_connect(v.port, b"wrong")
        assert size is None and v.auth_fail == 1
        s.close()

def _post(host, port, path, body, key, via_proxy=None):
    if via_proxy:
        c = http.client.HTTPConnection("127.0.0.1", via_proxy.port, timeout=5); c.set_tunnel(host, port)
    else:
        c = http.client.HTTPConnection(host, port, timeout=5)
    c.request("POST", path, json.dumps(body), {"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    r = c.getresponse(); return r.status, r.read()

def test_fake_model_script_and_recording():
    script = [{"tool_calls": [{"name": "click", "arguments": {"x": 10, "y": 20}}]}, {"status": 429}, {"raw": "{not json"}, {"content": "done"}]
    with FakeModel(script, key="sk-abc") as m:
        req = {"model": "fake-vl", "messages": [{"role": "user", "content": "hi"}]}
        st, b = _post("127.0.0.1", m.port, "/v1/chat/completions", req, "sk-abc")
        j = json.loads(b); tc = j["choices"][0]["message"]["tool_calls"][0]
        assert st == 200 and j["choices"][0]["finish_reason"] == "tool_calls"
        assert tc["function"]["name"] == "click" and json.loads(tc["function"]["arguments"]) == {"x": 10, "y": 20}
        assert _post("127.0.0.1", m.port, "/v1/chat/completions", req, "sk-abc")[0] == 429
        st, b = _post("127.0.0.1", m.port, "/v1/chat/completions", req, "sk-abc"); assert st == 200 and b == b"{not json"
        assert json.loads(_post("127.0.0.1", m.port, "/v1/chat/completions", req, "sk-abc")[1])["choices"][0]["message"]["content"] == "done"
        assert _post("127.0.0.1", m.port, "/v1/chat/completions", req, "sk-bad")[0] == 401
        assert len(m.requests) == 5 and m.requests[0]["json"] == req
        assert all(b"sk-abc" not in r["raw"] for r in m.requests)   # the key travels only in the header

def test_fake_proxy_connect_tunnel_and_violations():
    with FakeModel([{"content": "via proxy"}], key="k") as m, FakeProxy() as p:
        st, b = _post("127.0.0.1", m.port, "/v1/chat/completions", {"messages": []}, "k", via_proxy=p)
        assert st == 200 and json.loads(b)["choices"][0]["message"]["content"] == "via proxy"
        assert p.connects == [f"127.0.0.1:{m.port}"]
        c = http.client.HTTPConnection("127.0.0.1", p.port, timeout=5)
        c.request("GET", f"http://127.0.0.1:{m.port}/v1/models"); assert c.getresponse().status == 405
        assert len(p.violations) == 1

@pytest.mark.parametrize("mode,status", [("refuse", 502), (None, 403)])
def test_fake_proxy_failures(mode, status):
    with FakeProxy(fail=mode, allow=[] if mode is None else None) as p:
        c = http.client.HTTPConnection("127.0.0.1", p.port, timeout=5); c.set_tunnel("example.invalid", 443)
        with pytest.raises(OSError, match=str(status)):
            c.request("GET", "/")
