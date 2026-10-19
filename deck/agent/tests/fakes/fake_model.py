"""Fake OpenAI-compatible chat/completions server for host tests of mk20-agent.
Script = list of steps consumed in order, one per POST .../chat/completions:
  {"tool_calls": [{"name": "click", "arguments": {...}}]}   -> assistant message with tool calls
  {"content": "done"}                                        -> plain assistant message
  {"status": 429, "body": {...}}                             -> error status
  {"raw": b"..."} / {"raw": "..."}                           -> raw body (malformed JSON tests)
  {"delay": 0.5, ...}                                        -> sleep before answering
Records every request (path, headers, parsed JSON or raw body) so tests can assert e.g. that the API key appears
only in the Authorization header and never in the body. GET .../models -> 401 without a bearer, 200 with one."""
import json, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class FakeModel:
    def __init__(self, script=None, key="sk-test-0000", host="127.0.0.1", model="fake-vl"):
        self.script, self.key, self.model = list(script or []), key, model
        self.requests, self.lock = [], threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *a): pass
            def _send(self, code, body, ctype="application/json"):
                if isinstance(body, (dict, list)): body = json.dumps(body).encode()
                elif isinstance(body, str): body = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)
            def _authed(self):
                return self.headers.get("Authorization") == f"Bearer {outer.key}"
            def do_GET(self):
                if self.path.rstrip("/").endswith("/models"):
                    if not self._authed(): return self._send(401, {"error": {"message": "invalid api key", "type": "auth"}})
                    return self._send(200, {"object": "list", "data": [{"id": outer.model, "object": "model"}]})
                self._send(404, {"error": {"message": "not found"}})
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n)
                try: body = json.loads(raw)
                except ValueError: body = None
                with outer.lock:
                    outer.requests.append({"path": self.path, "headers": dict(self.headers.items()), "raw": raw, "json": body})
                    step = outer.script.pop(0) if outer.script else {"content": "script exhausted"}
                if not self.path.rstrip("/").endswith("/chat/completions"):
                    return self._send(404, {"error": {"message": "not found"}})
                if not self._authed(): return self._send(401, {"error": {"message": "invalid api key", "type": "auth"}})
                if step.get("delay"): time.sleep(step["delay"])
                if "raw" in step: return self._send(step.get("status", 200), step["raw"])
                if "status" in step: return self._send(step["status"], step.get("body", {"error": {"message": "err"}}))
                self._send(200, outer.completion(step))

        self.httpd = ThreadingHTTPServer((host, 0), H)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.base_url = f"http://{host}:{self.port}/v1"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True); self.thread.start()

    _n = 0
    def completion(self, step):
        FakeModel._n += 1
        msg = {"role": "assistant", "content": step.get("content")}
        finish = "stop"
        if step.get("tool_calls"):
            msg["tool_calls"] = [{"id": f"call_{FakeModel._n}_{i}", "type": "function",
                                  "function": {"name": t["name"],
                                               "arguments": t["arguments"] if isinstance(t["arguments"], str) else json.dumps(t["arguments"])}}
                                 for i, t in enumerate(step["tool_calls"])]
            finish = "tool_calls"
        return {"id": f"chatcmpl-{FakeModel._n}", "object": "chat.completion", "model": self.model,
                "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}}

    def close(self):
        self.httpd.shutdown(); self.httpd.server_close()
    def __enter__(self): return self
    def __exit__(self, *a): self.close()


class ScriptedTLSModel:
    """Same completion scripts over A3's throwaway-CA TLS transport (TCP or IPC).
    Extra steps: close_delimited, close_notify=False, slowloris=True.
    No external connections; record only synthetic test credentials.
    """
    def __init__(self, cert, key_file, script, *, socketpair=False, key="a4-synthetic-secret", **kw):
        from .fake_tls import FakeTLS
        self.script, self.key, self.model, self.requests = list(script), key, "fake-vl", []
        step = self.script[0] if self.script else {}
        self.tls = FakeTLS(cert, key_file, socketpair=socketpair, response_factory=self._response,
                           close_notify=step.get("close_notify", True),
                           byte_delay=0.05 if step.get("slowloris") else 0, **kw)
        self.port = self.tls.port

    def _response(self, head, raw):
        lines = head.decode("ascii").split("\r\n")
        headers = dict(line.split(": ", 1) for line in lines[1:])
        try: body = json.loads(raw)
        except ValueError: body = None
        self.requests.append({"path": lines[0].split()[1], "headers": headers, "raw": raw, "json": body})
        step = self.script.pop(0) if self.script else {"content": "script exhausted"}
        if step.get("delay"): time.sleep(step["delay"])
        status = step.get("status", 200)
        if headers.get("Authorization") != "Bearer " + self.key:
            status, body = 401, {"error": {"message": "invalid key"}}
        elif "raw" in step: body = step["raw"]
        elif "status" in step: body = step.get("body", {"error": {"message": "err"}})
        else: body = FakeModel.completion(self, step)
        if isinstance(body, (dict, list)): body = json.dumps(body).encode()
        elif isinstance(body, str): body = body.encode()
        framing = b"" if step.get("close_delimited") else f"Content-Length: {len(body)}\r\n".encode()
        if step.get("chunked"):
            framing = b"Transfer-Encoding: chunked\r\n"
            body = f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"
        return f"HTTP/1.1 {status} Test\r\n".encode() + framing + b"Content-Type: application/json\r\nConnection: close\r\n\r\n" + body

    @property
    def connections(self): return self.tls.connections
    def open_stream(self): return self.tls.open_stream()
    def close(self): self.tls.close()
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
