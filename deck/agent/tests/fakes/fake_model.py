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
