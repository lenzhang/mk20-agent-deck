"""A4 host-only acceptance; all sockets are local fakes, no provider traffic.
MK20_TEST_SOCKETPAIR=1 executes the TLS/CONNECT protocol without TCP binds.
"""
import base64
import json
import os
from pathlib import Path
import socket
import subprocess

import pytest
from .fakes.fake_model import ScriptedTLSModel
from .fakes.fake_proxy import FakeProxy
from .test_net import certs, config  # shared throwaway CA fixture; no downloads

AGENT = Path(__file__).resolve().parents[1]
IPC = os.environ.get("MK20_TEST_SOCKETPAIR") == "1"
TOOLS = [{"type": "function", "function": {"name": "click", "parameters": {
    "type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}, "required": ["x", "y"]}}}]


@pytest.fixture(scope="module")
def llm_bins(tmp_path_factory):
    build = tmp_path_factory.mktemp("llm-build")
    subprocess.run(["make", "-j8", "-C", str(AGENT), f"BUILD={build}",
                    "CFLAGS=-std=c11 -Wall -Wextra -Werror -O1 -g -DMK20_NET_TEST",
                    str(build / "mk20-agent"), str(build / "mk20-agent-nosan"), str(build / "test_llm")],
                   check=True, stdout=subprocess.DEVNULL)
    return build


def model(certs, step, **kw):
    return ScriptedTLSModel(certs / "valid.pem", certs / "valid.key", [step], socketpair=IPC, **kw)


def proxy_for(server, **kw):
    return FakeProxy(socketpair=IPC, targets={"localhost:443": ("127.0.0.1", server.port)},
                     upstream_factory=server.open_stream if IPC else None, **kw)


def provider(tmp_path, **kw):
    key = tmp_path / "provider.key"; key.write_text("a4-synthetic-secret\n"); key.chmod(0o600)
    cfg = dict(version=1, provider="arbitrary", model="test-vl", base_url="https://localhost/api/coding/paas/v4/",
               domestic=True, route="domestic", api_key_file=str(key))
    cfg.update(kw)
    path = tmp_path / "provider.json"; path.write_text(json.dumps(cfg)); return path


def call(llm_bins, certs, tmp_path, proxy, *, options=None, tools=None, nosan=False, cfg=None, nc=None):
    cfg = cfg or provider(tmp_path)
    nc = nc or config(tmp_path, proxy=proxy.url)
    command = [str(llm_bins / ("mk20-agent-nosan" if nosan else "mk20-agent")), "--llm-test",
               "--provider", str(cfg), "--net", str(nc), "--ca", str(certs / "ca.pem"),
               "--trust-clock", "--system", "Control the screen", "--user", "Go", "--image-w", "201", "--image-h", "101"]
    if tools is not None:
        path = tmp_path / "tools.json"; path.write_text(json.dumps(tools)); command += ["--tools", str(path)]
    command += list(map(str, options or []))
    env, fds = dict(os.environ), ()
    if proxy.client:
        fds = (proxy.client.fileno(),); env["MK20_NET_TEST_FD"] = str(fds[0])
    result = subprocess.run(command, env=env, pass_fds=fds, capture_output=True, text=True, timeout=15)
    assert not result.stderr, result.stderr
    assert len(result.stdout.splitlines()) == 1
    assert "a4-synthetic-secret" not in result.stdout + result.stderr
    assert "Authorization" not in result.stdout + result.stderr
    r = json.loads(result.stdout)
    assert (result.returncode == 0) == (r["class"] == "ok"), r
    assert r["elapsed_ms"] >= 0 and r["peak_rss_kb"] > 0
    return r


def test_ok_text(llm_bins, certs, tmp_path):
    with model(certs, {"content": "done"}) as server, proxy_for(server) as proxy:
        r = call(llm_bins, certs, tmp_path, proxy)
        assert r["class"] == "ok" and r["content"] == "done" and r["calls"] == []
        assert r["finish_reason"] == "stop" and not r["retryable"] and r["http_status"] == 200
        assert proxy.connects == ["localhost:443"] and server.connections == 1
        req = server.requests[0]
        assert req["path"] == "/api/coding/paas/v4/chat/completions"
        assert req["headers"]["Authorization"] == "Bearer " + server.key
        assert req["headers"]["Content-Type"] == "application/json"
        assert server.key.encode() not in req["raw"]


@pytest.mark.parametrize("mode,args,expected", [
    ("norm1000", {"x": 500, "y": 1000, "x2": 1000, "y2": 500}, {"x": 100, "y": 100, "x2": 200, "y2": 50}),
    ("norm1000", {"x": -20, "y": 1020}, {"x": 0, "y": 100}),
    ("pixel", {"x": 200, "y": 100}, {"x": 200, "y": 100}),
    ("pixel", {"x": -4, "y": 102}, {"x": 0, "y": 100}),
])
def test_ok_tool(llm_bins, certs, tmp_path, mode, args, expected):
    with model(certs, {"tool_calls": [{"name": "click", "arguments": args}]}) as server, proxy_for(server) as proxy:
        r = call(llm_bins, certs, tmp_path, proxy, tools=TOOLS, cfg=provider(tmp_path, coord_mode=mode))
        assert r["class"] == "ok" and r["calls"] == [{"name": "click", "args": expected}]
        body = server.requests[0]["json"]
        assert body["tools"] == TOOLS and body["tool_choice"] == "required"


def test_image_request(llm_bins, certs, tmp_path):
    jpeg = b"\xff\xd8\xff\xd9"; path = tmp_path / "frame.jpg"; path.write_bytes(jpeg)
    with model(certs, {"content": "image seen"}) as server, proxy_for(server) as proxy:
        r = call(llm_bins, certs, tmp_path, proxy, options=["--image", path])
        assert r["class"] == "ok"
        body = server.requests[0]["json"]
        assert body == {"model": "test-vl", "max_tokens": 512, "temperature": 0,
                        "messages": [{"role": "system", "content": "Control the screen"},
                                     {"role": "user", "content": [{"type": "text", "text": "Go"},
                                      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}]}]}


@pytest.mark.parametrize("step,tools,kind,retry,error", [
    ({"raw": "{broken"}, None, "bad_response", False, "response"),
    ({"raw": {}}, None, "bad_response", False, "response"),
    ({"raw": "x" * (1024 * 1024 + 1)}, None, "too_large", False, "transport"),
    ({"content": "no call"}, TOOLS, "bad_response", False, "missing_tool_call"),
    ({"tool_calls": [{"name": "click", "arguments": "{bad"}]}, TOOLS, "bad_response", False, "arguments"),
    ({"tool_calls": [{"name": "unknown", "arguments": {}}]}, TOOLS, "bad_response", False, "unknown_tool"),
    ({"tool_calls": [{"name": "click", "arguments": {"x": 0, "y": 0}}, {"name": "click", "arguments": {"x": 3000, "y": 0}}]}, TOOLS, "coord_range", False, "coord_range"),
    ({"tool_calls": [{"name": "click", "arguments": {"x": 1021, "y": 0}}]}, TOOLS, "coord_range", False, "coord_range"),
    ({"tool_calls": [{"name": "click", "arguments": {"x": -21, "y": 0}}]}, TOOLS, "coord_range", False, "coord_range"),
    ({"tool_calls": [{"name": "click", "arguments": {"x": 0.5, "y": 0}}]}, TOOLS, "coord_type", False, "coord_type"),
    ({"tool_calls": [{"name": "click", "arguments": {"y": 0}}]}, TOOLS, "coord_type", False, "coord_type"),
    ({"tool_calls": [{"name": "click", "arguments": {"x": 0, "y": 0}}] * 17}, TOOLS, "bad_response", False, "response"),
    ({"content": "x" * 32769}, None, "bad_response", False, "response"),
    ({"tool_calls": [{"name": "x" * 65, "arguments": {}}]}, TOOLS, "bad_response", False, "arguments"),
    ({"status": 401}, None, "auth", False, "http_status"),
    ({"status": 403}, None, "auth", False, "http_status"),
    ({"status": 429}, None, "rate", True, "http_status"),
    ({"status": 500}, None, "server", True, "http_status"),
    ({"status": 400}, None, "bad_request", False, "http_status"),
    ({"status": 404}, None, "bad_request", False, "http_status"),
    ({"status": 422}, None, "bad_request", False, "http_status"),
])
def test_rejections(llm_bins, certs, tmp_path, step, tools, kind, retry, error):
    with model(certs, step) as server, proxy_for(server) as proxy:
        r = call(llm_bins, certs, tmp_path, proxy, tools=tools)
        assert (r["class"], r["retryable"], r["error"]) == (kind, retry, error)
        assert r["calls"] == [] and not r["content"]
        assert len(server.requests) == 1  # adapter performs no retries


@pytest.mark.parametrize("framing,notify,kind", [("close", True, "ok"), ("close", False, "bad_response"),
                                                 ("fixed", False, "ok"), ("chunked", False, "ok")])
def test_close_notify(llm_bins, certs, tmp_path, framing, notify, kind):
    step = {"content": "complete JSON", "close_delimited": framing == "close", "chunked": framing == "chunked", "close_notify": notify}
    with model(certs, step) as server, proxy_for(server) as proxy:
        r = call(llm_bins, certs, tmp_path, proxy)
        assert r["class"] == kind
        if kind != "ok": assert r["error"] == "truncated" and r["calls"] == []


def test_slowloris(llm_bins, certs, tmp_path):
    with model(certs, {"content": "slow", "slowloris": True}) as server, proxy_for(server) as proxy:
        r = call(llm_bins, certs, tmp_path, proxy, options=["--timeout", "350"])
        assert r["class"] == "timeout" and r["retryable"] and r["elapsed_ms"] < 2000


def test_error_echo_redacted(llm_bins, certs, tmp_path):
    step = {"status": 401, "body": {"error": {"message": "Authorization: Bearer a4-synthetic-secret"}}}
    with model(certs, step) as server, proxy_for(server) as proxy:
        assert call(llm_bins, certs, tmp_path, proxy)["class"] == "auth"
        assert server.requests[0]["headers"]["Authorization"] == "Bearer " + server.key


def test_proxy_date_does_not_set_clock(llm_bins, certs, tmp_path):
    with model(certs, {"content": "ok"}) as server, proxy_for(server, date="Sat, 01 Jan 2050 00:00:00 GMT") as proxy:
        assert call(llm_bins, certs, tmp_path, proxy)["class"] == "ok"


def test_tool_choice_unsupported(llm_bins, certs, tmp_path):
    with model(certs, {"content": "no tools"}) as server, proxy_for(server) as proxy:
        r = call(llm_bins, certs, tmp_path, proxy, tools=TOOLS, cfg=provider(tmp_path, supports_tool_choice=False))
        assert r["class"] == "ok" and "tool_choice" not in server.requests[0]["json"]


def test_unreachable_proxy_never_direct(llm_bins, certs, tmp_path):
    with model(certs, {"content": "must not be reached"}) as server:
        if IPC:
            # Inject only the failing socket factory: real llm_chat -> net_request
            # routing still executes. This supplements, and cannot prove TCP dialing.
            cfg = provider(tmp_path)
            nc = config(tmp_path, proxy="http://127.0.0.1:7890")
            p = subprocess.run([str(llm_bins / "test_llm"), str(cfg), str(nc), str(certs / "ca.pem")],
                               capture_output=True, text=True, timeout=5, cwd=AGENT)
            assert p.returncode == 0 and not p.stderr and json.loads(p.stdout) == {"class": "connect", "attempts": 1}
        else:
            with socket.socket() as refused:
                refused.bind(("127.0.0.1", 0))
                class Proxy:
                    client = None
                    url = f"http://127.0.0.1:{refused.getsockname()[1]}"
                cfg = provider(tmp_path, base_url=f"https://localhost:{server.port}/v1")
                assert call(llm_bins, certs, tmp_path, Proxy(), cfg=cfg)["class"] == "connect"
        assert server.connections == 0


@pytest.mark.parametrize("bundle", ["throwaway", "mozilla"])
def test_nosan_memory(llm_bins, certs, tmp_path, bundle):
    # A real ~100 KiB JPEG, generated offline by cached Pillow.
    from PIL import Image
    from random import Random
    image = Image.frombytes("RGB", (400, 400), Random(20).randbytes(400 * 400 * 3))
    path = tmp_path / "frame.jpg"; image.save(path, quality=80)
    assert 95 * 1024 < path.stat().st_size < 115 * 1024
    samples = []
    options = ["--image", path]
    if bundle == "mozilla":
        combined = tmp_path / "combined-ca.pem"
        combined.write_bytes((AGENT / "vendor/ca/cacert.pem").read_bytes() + (certs / "ca.pem").read_bytes())
        options += ["--ca", combined]
    for _ in range(5):
        with model(certs, {"tool_calls": [{"name": "click", "arguments": {"x": 500, "y": 500}}]}) as server, proxy_for(server) as proxy:
            r = call(llm_bins, certs, tmp_path, proxy, tools=TOOLS, nosan=True, options=options)
            assert r["class"] == "ok" and r["peak_rss_kb"] < 16384
            samples.append(r["peak_rss_kb"])
    print(json.dumps({"ca_bundle": bundle, "jpeg_bytes": path.stat().st_size, "peak_rss_kb": samples}))


def test_content_limit_utf8(llm_bins, certs, tmp_path):
    with model(certs, {"content": "屏" * 2001}) as server, proxy_for(server) as proxy:
        r = call(llm_bins, certs, tmp_path, proxy)
        assert r["class"] == "ok" and r["content"] == "屏" * 2000


def test_response_above_legacy_cap(llm_bins, certs, tmp_path):
    # Providers can include large reasoning/metadata fields. The model response
    # budget is 1 MiB even though requests/offline oai_cli remain capped at 256 KiB.
    body = {"choices": [{"finish_reason": "stop", "message": {"content": "done"}}], "metadata": "x" * (512 * 1024)}
    with model(certs, {"raw": body}) as server, proxy_for(server) as proxy:
        r = call(llm_bins, certs, tmp_path, proxy)
        assert r["class"] == "ok" and r["content"] == "done"


@pytest.mark.parametrize("change", [{"vision": False}, {"version": 2}, {"route": "direct"},
                                    {"base_url": "http://localhost/v1"}, {"api_key_file": ""}])
def test_bad_config_never_connects(llm_bins, certs, tmp_path, change):
    path = tmp_path / "frame.jpg"; path.write_bytes(b"\xff\xd8\xff\xd9")
    with model(certs, {"content": "must not be reached"}) as server, proxy_for(server) as proxy:
        r = call(llm_bins, certs, tmp_path, proxy, cfg=provider(tmp_path, **change), options=["--image", path])
        assert r["class"] == "config" and not proxy.connects and server.connections == 0


def test_blocked_route(llm_bins, certs, tmp_path):
    with model(certs, {"content": "must not be reached"}) as server, proxy_for(server) as proxy:
        cfg = provider(tmp_path, domestic=False, route="foreign")
        nc = config(tmp_path, profile="board_wifi")
        r = call(llm_bins, certs, tmp_path, proxy, cfg=cfg, nc=nc)
        assert r["class"] == "blocked" and not proxy.connects and server.connections == 0


def test_image_cap_never_connects(llm_bins, certs, tmp_path):
    path = tmp_path / "too-large.jpg"; path.write_bytes(b"x" * (128 * 1024 + 1))
    with model(certs, {"content": "must not be reached"}) as server, proxy_for(server) as proxy:
        r = call(llm_bins, certs, tmp_path, proxy, options=["--image", path])
        assert r["class"] == "too_large" and not proxy.connects and server.connections == 0
