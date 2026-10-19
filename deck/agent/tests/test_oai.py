"""C adapter/CLI fixtures; HTTP test is loopback only and can be disabled for offline box work."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import pytest

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT.parents[0]))
from agent.tests.fakes.fake_model import FakeModel  # noqa: E402


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    if not shutil.which(os.environ.get("CC", "cc")):
        pytest.skip("C adapter tests require cc; no C compiler is available")
    build = tmp_path_factory.mktemp("oai-build")
    subprocess.run(["make", "-C", str(AGENT), f"BUILD={build}", "all", str(build / "test_oai")], check=True)
    subprocess.run([str(build / "test_oai")], check=True)
    return build / "oai_cli"


def run(cli, *args, ok=True):
    r = subprocess.run([str(cli), *map(str, args)], capture_output=True, text=True)
    assert (r.returncode == 0) == ok, r.stderr
    return json.loads(r.stdout)


def spec(tmp_path, **fields):
    q = dict(base_url="https://example.test/v1", model="fake-vl", system="Control the desktop", user="输入中文",
             tools=[{"type": "function", "function": {"name": "click", "parameters": {"type": "object"}}}],
             max_tokens=512, temperature=0.2, supports_tool_choice=True)
    q.update(fields)
    path = tmp_path / "request.json"
    path.write_text(json.dumps(q))
    return path


def parse(cli, tmp_path, body, status=200):
    path = tmp_path / "response.json"
    path.write_bytes(body if isinstance(body, bytes) else body.encode())
    return run(cli, "parse", status, path)


def test_request_and_header_separate(cli, tmp_path):
    image = b"\xff\xd8\xff\xd9"
    jpg = tmp_path / "frame.jpg"; jpg.write_bytes(image)
    body = run(cli, "request", spec(tmp_path, jpeg_file=str(jpg), api_key="sk-secret"))
    assert body["model"] == "fake-vl" and body["tool_choice"] == "auto" and body["max_tokens"] == 512
    assert "sk-secret" not in json.dumps(body)
    content = body["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "输入中文"}
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64," + base64.b64encode(image).decode()
    key = tmp_path / "key"; key.write_text("sk-secret")
    assert run(cli, "header", key) == {"authorization": "Authorization: Bearer sk-secret"}
    key.write_text("sk-secret\r\nInjected: x")
    assert run(cli, "header", key, ok=False)["error"] == "bad_args"
    body = run(cli, "request", spec(tmp_path, supports_tool_choice=False))
    assert "tool_choice" not in body and isinstance(body["messages"][1]["content"], str)


@pytest.mark.parametrize("change", [{"max_tokens": 0}, {"temperature": 3}, {"supports_tool_choice": "yes"},
                                    {"tools": {}}, {"user": "x" * 16385}, {"base_url": "file:///tmp/x"}])
def test_bad_request_spec(cli, tmp_path, change):
    assert run(cli, "request", spec(tmp_path, **change), ok=False)["error"] == "bad_args"


def test_scripted_responses_without_transport(cli, tmp_path):
    # The completion formatter itself is pure; no FakeModel server/listener is constructed.
    fake = FakeModel.__new__(FakeModel); fake.model = "fake-vl"
    body = fake.completion({"tool_calls": [{"name": "click", "arguments": {"x": 25}},
                                           {"name": "click", "arguments": "{bad"},
                                           {"name": "click", "arguments": "[]"}]})
    r = parse(cli, tmp_path, json.dumps(body))
    assert r["ok"] and r["finish_reason"] == "tool_calls"
    assert r["tool_calls"][0]["arguments"] == {"x": 25}
    assert [t["error"] for t in r["tool_calls"][1:]] == ["invalid_arguments"] * 2
    r = parse(cli, tmp_path, json.dumps(fake.completion({"content": "完成"})))
    assert r["ok"] and r["content"] == "完成" and r["tool_calls"] == []


@pytest.mark.parametrize("status,kind", [(401, "auth"), (403, "auth"), (429, "rate"), (500, "server"),
                                         (503, "server"), (400, "bad_response"), (200, "bad_response")])
def test_typed_errors(cli, tmp_path, status, kind):
    body = {"error": {"message": "quota exhausted", "type": "quota", "code": "limit"}}
    r = parse(cli, tmp_path, json.dumps(body), status)
    assert r["error"] == dict(kind=kind, http_status=status, **body["error"])
    assert parse(cli, tmp_path, "malformed", status)["error"]["kind"] == kind


@pytest.mark.parametrize("body", [b"{broken", b"{}trailing", b"[" * 40 + b"]" * 40, b"{}\0x"])
def test_malformed_response(cli, tmp_path, body):
    assert parse(cli, tmp_path, body)["error"]["kind"] == "bad_response"


def test_response_bounds(cli, tmp_path):
    fake = FakeModel.__new__(FakeModel); fake.model = "fake-vl"
    calls = [{"name": "click", "arguments": {"x": 0}}] * 17
    assert not parse(cli, tmp_path, json.dumps(fake.completion({"tool_calls": calls})))["ok"]
    assert not parse(cli, tmp_path, json.dumps(fake.completion({"content": "x" * 32769})))["ok"]
    args = "{\"x\":\"" + "a" * 8192 + "\"}"
    result = parse(cli, tmp_path, json.dumps(fake.completion({"tool_calls": [{"name": "click", "arguments": args}]})))
    assert result["ok"] and result["tool_calls"][0]["error"] == "invalid_arguments"
    # NUL escapes cannot silently truncate a cJSON string or a tool argument document.
    assert not parse(cli, tmp_path, json.dumps(fake.completion({"content": "x\0hidden"})))["ok"]
    path = tmp_path / "oversize.json"; path.write_bytes(b"x" * (256 * 1024 + 1))
    assert run(cli, "parse", 200, path, ok=False)["error"] == "bad_args"


@pytest.mark.parametrize("raw,ok", [(b"sk-secret", True), (b"sk-secret\n", True), (b"sk-secret\n\n", False),
                                     (b"sk-secret\r\n", False), (b"sk-\nsecret", False), (b"\n", False),
                                     (b"x" * 512 + b"\n", True), (b"x" * 513 + b"\n", False)])
def test_header_key_file_one_lf(cli, tmp_path, raw, ok):
    key = tmp_path / "key"; key.write_bytes(raw)
    result = run(cli, "header", key, ok=ok)
    if ok:
        assert result == {"authorization": "Authorization: Bearer " + raw.removesuffix(b"\n").decode()}
    else:
        assert result == {"error": "bad_args"}


@pytest.mark.parametrize("args", ['{"x":01}', '{"x":1.}', '{"x":.5}', '{"x":+1}', '{"x":"raw\x01control"}', '{} trailing'])
def test_strict_response_and_tool_arguments(cli, tmp_path, args):
    valid = '{"choices":[{"finish_reason":"stop","message":{"content":"ok"}}],"extra":' + args + '}'
    assert parse(cli, tmp_path, valid)["error"]["kind"] == "bad_response"
    calls = [{"id": "1", "type": "function", "function": {"name": "click", "arguments": args}},
             {"id": "2", "type": "function", "function": {"name": "click", "arguments": '{"x":0.5}'}}]
    result = parse(cli, tmp_path, json.dumps({"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": calls}}]}))
    assert result["ok"] and result["tool_calls"][0]["error"] == "invalid_arguments"
    assert result["tool_calls"][1]["arguments"] == {"x": 0.5}


@pytest.mark.parametrize("message", [{"content": None}, {"content": None, "tool_calls": []}])
def test_missing_tool_calls(cli, tmp_path, message):
    result = parse(cli, tmp_path, json.dumps({"choices": [{"finish_reason": "tool_calls", "message": message}]}))
    assert not result["ok"] and result["error"]["kind"] == "bad_response"


@pytest.mark.skipif(os.environ.get("MK20_TEST_NO_NETWORK") == "1", reason="network disabled by task; loopback HTTP test not run")
def test_fake_model_http(cli, tmp_path):
    script = [{"tool_calls": [{"name": "click", "arguments": {"x": 12}}]}, {"content": "done"},
              {"status": 429, "body": {"error": {"message": "slow", "type": "quota", "code": "rate"}}},
              {"raw": "{broken"}]
    with FakeModel(script) as fake:
        key = tmp_path / "key"; key.write_text(fake.key)
        auth = run(cli, "header", key)["authorization"].split(": ", 1)[1]
        body = run(cli, "request", spec(tmp_path, base_url=fake.base_url))
        results = []
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in script:
            req = urllib.request.Request(fake.base_url + "/chat/completions", json.dumps(body).encode(),
                                         {"Authorization": auth, "Content-Type": "application/json"})
            try:
                with opener.open(req, timeout=5) as response:
                    raw, status = response.read(), response.status
            except urllib.error.HTTPError as response:
                raw, status = response.read(), response.code
            results.append(parse(cli, tmp_path, raw, status))
        assert results[0]["tool_calls"][0]["arguments"] == {"x": 12}
        assert results[1]["content"] == "done"
        assert results[2]["error"]["kind"] == "rate" and results[3]["error"]["kind"] == "bad_response"
        assert all(fake.key.encode() not in r["raw"] and r["headers"]["Authorization"] == auth for r in fake.requests)
