import socket
import threading

from mk20deck import transport as T


def _server():
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    port = srv.getsockname()[1]

    def run():
        c, _ = srv.accept()
        c.sendall(c.recv(100)[::-1])
        c.close(); srv.close()
    threading.Thread(target=run, daemon=True).start()
    return port


def test_find_ports_prefers_tcp_when_reachable(monkeypatch):
    port = _server()
    monkeypatch.setenv("MK20_TCP", f"127.0.0.1:{port}")
    assert T.find_ports()[0] == f"tcp://127.0.0.1:{port}"


def test_find_ports_tcp_off(monkeypatch):
    monkeypatch.setenv("MK20_TCP", "off")
    assert not any(p.startswith("tcp://") for p in T.find_ports())


def test_tcp_transport_roundtrip_and_close(monkeypatch):
    port = _server()
    tr = T.open_transport(f"tcp://127.0.0.1:{port}")
    assert isinstance(tr, T.TcpTransport)
    tr.write(b"abc")
    got = b""
    while len(got) < 3:
        got += tr.read(1.0)
    assert got == b"cba"
    try:
        for _ in range(20):
            tr.read(0.2)
        raise AssertionError("expected OSError on peer close")
    except OSError:
        pass
    tr.close()
