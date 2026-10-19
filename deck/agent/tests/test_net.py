"""A3 offline acceptance. Normal mode uses TCP; optional IPC supplements denied binds.
No test downloads, provider credentials, real proxy, or device access.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import time

import pytest

from .fakes.fake_proxy import FakeProxy
from .fakes.fake_tls import FakeTLS

AGENT = Path(__file__).resolve().parents[1]
IPC = os.environ.get("MK20_TEST_SOCKETPAIR") == "1"


@pytest.fixture(scope="module")
def net_bins(tmp_path_factory):
    build = tmp_path_factory.mktemp("net-build")
    subprocess.run(["make", "-j8", "-C", str(AGENT), f"BUILD={build}",
                    "CFLAGS=-std=c11 -Wall -Wextra -Werror -O1 -g -DMK20_NET_TEST",
                    str(build / "mk20-agent"), str(build / "mk20-agent-nosan"), str(build / "test_net")], check=True,
                   stdout=subprocess.DEVNULL)
    return build


def openssl(path, *args):
    subprocess.run(["openssl", *map(str, args)], cwd=path, check=True, capture_output=True)


@pytest.fixture(scope="module")
def certs(tmp_path_factory):
    path = tmp_path_factory.mktemp("net-certs")
    (path / "index").write_text(""); (path / "serial").write_text("1000\n")
    (path / "ca.cnf").write_text("""[ca]
default_ca = local
[local]
database = index
serial = serial
new_certs_dir = .
certificate = ca.pem
private_key = ca.key
default_md = sha256
policy = policy
unique_subject = no
[policy]
commonName = supplied
[server]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:localhost
[wrong]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature
extendedKeyUsage = serverAuth
subjectAltName = DNS:wrong.invalid
[client_only]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = clientAuth
subjectAltName = DNS:localhost
[bad_ku]
basicConstraints = critical,CA:FALSE
keyUsage = critical,keyCertSign
extendedKeyUsage = serverAuth
subjectAltName = DNS:localhost
""")
    openssl(path, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.pem",
            "-days", "3650", "-subj", "/CN=A3 test CA", "-addext", "basicConstraints=critical,CA:TRUE",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    now = datetime.now(timezone.utc)
    dates = {"valid": (-1, 30), "expired": (-30, -1), "future": (1, 30), "wrong": (-1, 30), "ecdsa": (-1, 30),
             "client_only": (-1, 30), "bad_ku": (-1, 30)}
    for name, (start, end) in dates.items():
        key_args = ["ec", "-pkeyopt", "ec_paramgen_curve:P-256"] if name == "ecdsa" else ["rsa:2048"]
        openssl(path, "req", "-new", "-newkey", *key_args, "-nodes", "-keyout", f"{name}.key", "-out", f"{name}.csr", "-subj", "/CN=localhost")
        extension = name if name in ("wrong", "client_only", "bad_ku") else "server"
        openssl(path, "ca", "-batch", "-config", "ca.cnf", "-extensions", extension,
                "-startdate", (now + timedelta(days=start)).strftime("%Y%m%d%H%M%SZ"),
                "-enddate", (now + timedelta(days=end)).strftime("%Y%m%d%H%M%SZ"),
                "-in", f"{name}.csr", "-out", f"{name}.pem", "-notext")
    openssl(path, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "unknown.key", "-out", "unknown.pem",
            "-days", "30", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost")
    return path


def config(tmp_path, profile="mac_proxy", proxy=None, fallbacks=None):
    value = {"version": 1, "profile": profile}
    if profile == "usb_wifi": value["mode"] = "proxy" if proxy else "direct"
    else: value["routes"] = {"domestic": "proxy" if profile == "mac_proxy" else "direct",
                             "foreign": "proxy" if proxy else "blocked"}
    value["proxy"] = {"url": proxy, "fallbacks": fallbacks or []}
    path = tmp_path / "net.json"; path.write_text(json.dumps(value)); return path


def call(net_bins, certs, cfg, *args, client=None, trust=True, nosan=False, url="https://localhost/models", ok=True):
    command = [str(net_bins / ("mk20-agent-nosan" if nosan else "mk20-agent")), "--net-test", url,
               "--net", str(cfg), "--ca", str(certs / "ca.pem")]
    if trust: command += ["--trust-clock"]
    command += list(map(str, args))
    env, fds = dict(os.environ), ()
    if client: fds = (client.fileno(),); env["MK20_NET_TEST_FD"] = str(fds[0])
    p = subprocess.run(command, capture_output=True, text=True, timeout=15, env=env, pass_fds=fds)
    assert p.stderr == "", p.stderr
    assert len(p.stdout.splitlines()) == 1, p.stdout
    result = json.loads(p.stdout)
    assert (p.returncode == 0) == ok, result
    assert result["ok"] == ok
    return result, p.stdout + p.stderr


def tls_server(certs, name="valid", **kw):
    return FakeTLS(certs / f"{name}.pem", certs / f"{name}.key", socketpair=IPC, **kw)


def proxy_server(tls=None, **kw):
    return FakeProxy(socketpair=IPC, targets={"localhost:443": ("127.0.0.1", tls.port)} if tls else {},
                     upstream_factory=tls.open_stream if IPC and tls else None, **kw)


@pytest.mark.parametrize("cert,detail", [("valid", ""), ("ecdsa", ""), ("expired", "expired"),
                                        ("future", "not-yet-valid"), ("wrong", "name mismatch"), ("unknown", "unknown CA"),
                                        ("client_only", "extended key usage"), ("bad_ku", "key usage")])
def test_tls_verification(net_bins, certs, tmp_path, cert, detail):
    with tls_server(certs, cert) as server, proxy_server(server) as proxy:
        cfg = config(tmp_path, proxy=proxy.url)
        r, _ = call(net_bins, certs, cfg, client=proxy.client, ok=not detail)
        assert proxy.connects == ["localhost:443"] and not proxy.violations
        assert server.sni == ["localhost"]
        if detail: assert r["class"] == "tls_verify" and detail in r["detail"]
        else: assert r["http_status"] == 200 and r["bytes"] == 2 and r["via"].startswith("proxy ")


@pytest.mark.parametrize("version", [ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3])
def test_tls_versions(net_bins, certs, tmp_path, version):
    with tls_server(certs, version=version) as server, proxy_server(server) as proxy:
        call(net_bins, certs, config(tmp_path, proxy=proxy.url), client=proxy.client)
        assert server.versions == ["TLSv1.2" if version == ssl.TLSVersion.TLSv1_2 else "TLSv1.3"]


@pytest.mark.parametrize("profile,route", [("usb_wifi", "foreign"), ("usb_wifi", "domestic"), ("board_wifi", "domestic")])
def test_explicit_direct(net_bins, certs, tmp_path, profile, route):
    with tls_server(certs) as server:
        client = server.open_stream() if IPC else None
        try:
            r, _ = call(net_bins, certs, config(tmp_path, profile), "--route", route, client=client,
                        url=f"https://localhost:{server.port}/models")
            assert r["via"] == "direct" and server.connections == 1
        finally:
            if client: client.close()


@pytest.mark.parametrize("mode,status", [("407", 407), ("403", 403), ("garbage", 0), ("extra-bytes", 200), ("oversize", 0)])
def test_proxy_error_no_fallback(net_bins, certs, tmp_path, mode, status):
    with proxy_server(mode=mode) as proxy, proxy_server(mode="403") as fallback:
        r, _ = call(net_bins, certs, config(tmp_path, proxy=proxy.url, fallbacks=[fallback.url]), client=proxy.client, ok=False)
        assert r["class"] == "proxy" and r["http_status"] == status and not fallback.connects


@pytest.mark.parametrize("where", ["proxy", "tls", "http"])
def test_deadline(net_bins, certs, tmp_path, where):
    with tls_server(certs, delay=3 if where == "http" else 0, handshake_delay=3 if where == "tls" else 0) as server, proxy_server(server, mode="slowloris" if where == "proxy" else None) as proxy:
        start = time.monotonic()
        r, _ = call(net_bins, certs, config(tmp_path, proxy=proxy.url), "--timeout", 1, client=proxy.client, ok=False)
        assert r["class"] == "timeout" and time.monotonic() - start < 2.5


@pytest.mark.parametrize("response", [b"HTTP/1.1 200 OK\r\nContent-Length: 9\r\n\r\n123456789",
                                      b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n9\r\n123456789\r\n0\r\n\r\n",
                                      b"HTTP/1.1 200 OK\r\n\r\n123456789"])
def test_too_large(net_bins, certs, tmp_path, response):
    with tls_server(certs, response=response) as server, proxy_server(server) as proxy:
        r, _ = call(net_bins, certs, config(tmp_path, proxy=proxy.url), "--body-cap", 8, client=proxy.client, ok=False)
        assert r["class"] == "too_large"


@pytest.mark.parametrize("response", [b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 3\r\n\r\nabc",
                                      b"HTTP/1.1 401 Unauthorized\r\nTransfer-Encoding: chunked\r\n\r\n1;foo=bar\r\na\r\n2\r\nbc\r\n0\r\nX-Trailer: ok\r\n\r\n",
                                      b"HTTP/1.1 401 Unauthorized\r\n\r\nabc"])
def test_response_framing(net_bins, certs, tmp_path, response):
    with tls_server(certs, response=response, fragment=True) as server, proxy_server(server) as proxy:
        r, _ = call(net_bins, certs, config(tmp_path, proxy=proxy.url), client=proxy.client)
        assert r["http_status"] == 401 and r["bytes"] == 3


def test_authorization_redaction(net_bins, certs, tmp_path):
    secret = "test-key-do-not-log-9cbe31"
    key = tmp_path / "provider.key"; key.write_text(secret + " \n"); key.chmod(0o600)
    # Even a server that echoes it in the body/headers must not leak it to CLI output.
    response = f"HTTP/1.1 200 OK\r\nX-Echo: {secret}\r\nContent-Length: {len(secret)}\r\n\r\n{secret}".encode()
    with tls_server(certs, response=response) as server, proxy_server(server) as proxy:
        r, output = call(net_bins, certs, config(tmp_path, proxy=proxy.url), "--key-file", key, client=proxy.client)
        assert r["ok"] and secret not in output and "Authorization" not in output
        assert b"Authorization: Bearer " + secret.encode() in server.requests[0][0]


def test_clock_untrusted_no_tls(net_bins, certs, tmp_path):
    with tls_server(certs) as server, proxy_server(server) as proxy:
        r, _ = call(net_bins, certs, config(tmp_path, proxy=proxy.url), "--clock-marker", tmp_path / "missing",
                    client=proxy.client, trust=False, ok=False)
        assert r["class"] == "clock" and server.connections == 0 and not proxy.connects


def test_blocked_no_socket(net_bins, certs, tmp_path):
    r, _ = call(net_bins, certs, config(tmp_path, "board_wifi"), ok=False)
    assert r["class"] == "blocked"


def test_config_failure_no_socket(net_bins, certs, tmp_path):
    cfg = tmp_path / "missing.json"
    r, _ = call(net_bins, certs, cfg, ok=False)
    assert r["class"] == "config"


@pytest.mark.parametrize("kind", ["fifo", "directory", "oversize"])
def test_ca_invalid_file(net_bins, tmp_path, kind):
    ca = tmp_path / "ca.pem"
    if kind == "fifo": os.mkfifo(ca)
    elif kind == "directory": ca.mkdir()
    else:
        with ca.open("wb") as f: f.truncate(4 * 1024 * 1024 + 1)
    cfg = config(tmp_path, "usb_wifi")
    start = time.monotonic()
    p = subprocess.run([str(net_bins / "mk20-agent"), "--net-test", "https://localhost/models",
                        "--net", str(cfg), "--ca", str(ca), "--trust-clock"],
                       capture_output=True, text=True, timeout=2)
    r = json.loads(p.stdout)
    assert p.returncode == 1 and not p.stderr and not r["ok"] and r["class"] == "config"
    assert time.monotonic() - start < 2


def test_rss(net_bins, certs, tmp_path):
    with tls_server(certs) as server, proxy_server(server) as proxy:
        r, _ = call(net_bins, certs, config(tmp_path, proxy=proxy.url), client=proxy.client, nosan=True)
        assert 0 < r["peak_rss_kb"] <= 16384
        (tmp_path / "rss.json").write_text(json.dumps(r))
        print(f"A3 nosan peak RSS: {r['peak_rss_kb']} KiB")


def test_unreachable_proxy_never_direct(net_bins, certs, tmp_path):
    # Keep a bound but non-listening TCP port to get deterministic ECONNREFUSED.
    with socket.socket() as refused, tls_server(certs) as server:
        refused.bind(("127.0.0.1", 0))
        cfg = config(tmp_path, proxy=f"http://127.0.0.1:{refused.getsockname()[1]}")
        r, _ = call(net_bins, certs, cfg, ok=False, url=f"https://localhost:{server.port}/models")
        assert r["class"] == "connect" and server.connections == 0


def test_fallback_order(net_bins, certs, tmp_path):
    with socket.socket() as first, socket.socket() as second, tls_server(certs) as server, proxy_server(server) as proxy:
        first.bind(("127.0.0.1", 0)); second.bind(("127.0.0.1", 0))
        urls = [f"http://127.0.0.1:{s.getsockname()[1]}" for s in (first, second)]
        cfg = config(tmp_path, proxy=urls[0], fallbacks=[urls[1], proxy.url])
        p = subprocess.run([str(net_bins / "test_net"), str(cfg), "https://localhost/models", "-", str(certs / "ca.pem")],
                           capture_output=True, text=True, timeout=10)
        r = json.loads(p.stdout)
        assert p.returncode == 0 and p.stderr == "" and r["ok"]
        assert r["attempts"] == [first.getsockname()[1], second.getsockname()[1], proxy.port]
        assert proxy.connects == ["localhost:443"]


@pytest.mark.parametrize("kind", ["valid", "skew", "float", "future_type", "duplicate", "garbage"])
def test_time_sync(net_bins, tmp_path, kind):
    from .fakes.fake_time import FakeTime
    epoch = int(time.time())
    body = {"valid": f'{{"epoch":{epoch}}}', "skew": f'{{"epoch":{epoch + 1000}}}',
            "float": f'{{"epoch":{epoch}.0}}', "future_type": '{"epoch":"123"}',
            "duplicate": f'{{"epoch":{epoch},"epoch":{epoch}}}', "garbage": '{broken'}[kind].encode()
    marker = tmp_path / "clock.synced"
    with FakeTime(body, socketpair=IPC) as server:
        fds = (server.client.fileno(),) if server.client else ()
        env = dict(os.environ)
        if fds: env["MK20_TIME_TEST_FD"] = str(fds[0])
        p = subprocess.run([str(net_bins / "mk20-agent"), "--time-sync", "--mac-host", "127.0.0.1",
                            "--port", str(server.port), "--clock-marker", str(marker)], env=env,
                           pass_fds=fds, capture_output=True, text=True, timeout=15)
        r = json.loads(p.stdout)
        assert p.stderr == "" and r["ok"] == (kind == "valid") and (p.returncode == 0) == (kind == "valid")
        assert marker.exists() == (kind == "valid")
        assert server.requests[0].startswith(b"GET /time HTTP/1.1\r\n")
        if kind != "valid": assert r["class"] == "clock"


def test_time_source_restricted(net_bins, tmp_path):
    p = subprocess.run([str(net_bins / "mk20-agent"), "--time-sync", "--mac-host", "8.8.8.8"],
                       capture_output=True, text=True, timeout=2)
    assert p.returncode == 1 and json.loads(p.stdout)["class"] == "config" and not p.stderr


def test_post_and_extra_headers(net_bins, certs, tmp_path):
    with tls_server(certs) as server, proxy_server(server) as proxy:
        cfg = config(tmp_path, proxy=proxy.url)
        env, fds = dict(os.environ), ()
        if proxy.client:
            fds = (proxy.client.fileno(),); env["MK20_NET_TEST_FD"] = str(fds[0])
        p = subprocess.run([str(net_bins / "test_net"), str(cfg), "https://localhost/models", "-", str(certs / "ca.pem")],
                           env=env, pass_fds=fds, capture_output=True, text=True, timeout=10)
        assert p.returncode == 0 and p.stderr == "" and json.loads(p.stdout)["bytes"] == 2
        head, body = server.requests[0]
        assert head.startswith(b"POST /models HTTP/1.1") and body == b"payload"
        assert b"Content-Type: application/json" in head and b"Content-Length: 7" in head
        assert b"Connection: close" in head


@pytest.mark.parametrize("route", ["domestic", "foreign"])
def test_mac_proxy_both_routes(net_bins, certs, tmp_path, route):
    with tls_server(certs) as server, proxy_server(server) as proxy:
        r, _ = call(net_bins, certs, config(tmp_path, proxy=proxy.url), "--route", route, client=proxy.client)
        assert r["via"].startswith("proxy ") and proxy.connects == ["localhost:443"]
        assert server.connections == 1
