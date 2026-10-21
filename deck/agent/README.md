# deck/agent — on-device agent (mk20-agent), see ../AGENT-BUILD-PLAN.md

Status: A2–A7 host capture, transport, model, safety loop, MCP and web modules are implemented with host fakes. A1 control/rule engine lives in ../kbd (see [CTL-PROTOCOL.md](../kbd/CTL-PROTOCOL.md)). Physical allow/confirm UI and screen rendering remain the separate keyboard track; device acceptance is pending. Release switches for control, scroll and K2 e-stop default off.

- `tests/fakes/fake_vnc.py` — RFB 3.8 server, VNC auth (pure-Python DES in `des.py`), Raw + ZRLE, records input events/ClientCutText.
- `tests/fakes/fake_model.py` — OpenAI-compatible `chat/completions` + `models`, scripted tool calls/errors/raw bodies, request recording.
- `tests/fakes/fake_proxy.py` — HTTP CONNECT proxy, allowlist/refuse/close/delay injection, records non-CONNECT (bypass) attempts.
- `src/oai.c/.h` — bounded offline OpenAI-compatible request/response adapter, separate Authorization header builder, per-call argument errors and coordinate mapping. Uses the pre-existing vendored cJSON 1.7.18; no HTTP transport or key storage.
- `make -C deck/agent test` — ASan/UBSan C tests + CLI build. `make agent-test` (from deck/) runs host fakes and adapter pytest; C/CLI tests skip clearly if cc is absent. Set `MK20_TEST_NO_NETWORK=1` to skip the adapter loopback HTTP test (A0 fake-server tests still require loopback).
- CLI: `build/oai_cli request spec.json`, `parse HTTP_STATUS response.json`, `header key-file`. Request spec: `base_url`, `model`, `system`, `user`, optional `jpeg_file`, `tools` array, `max_tokens` (default 512), `temperature` (default 0), `supports_tool_choice` (default true). Request output is the POST body only; header mode is a fixture tool and prints a credential, so never log its output in production.
- Adapter bounds: body 256 KiB, image 128 KiB, system/user 16 KiB each, tool schemas 32 KiB / 16 tools, response text 32 KiB, 16 calls with 8 KiB argument JSON each, depth 32 / 4096 JSON tokens. Coordinate endpoints are inclusive: norm1000 0..1000 ↔ pixel 0..extent-1. A3 owns HTTP/proxy/TLS/retries; A4 still needs provider acceptance with real keys.

A6 adds `build/mk20-agent --mcp`: JSON-RPC over Streamable HTTP at
`http://172.30.250.2:8765/mcp`, constrained to Linux `usb0` with no wildcard or
interface fallback. Authentication uses a generated 0600 service-owned
`/data/mk20/secrets/mcp.token`. `--mcp-token-file`, `--kbd-sock`, `--policy`,
`--calibration`, `--vnc-host`, `--vnc-port`, `--password-file`, `--provider` and
`--net` select existing runtime inputs. VNC capture is optional at startup;
without `--vnc-host` screenshot reports an error. Direct tools never call a model;
`run_task` submits an autonomous A5 task using the configured model port.

Only keyboard status authorizes input. Actions require a recent MCP screenshot,
matching measured calibration and trusted window/secure-input hints. The production
runtime still lacks a trusted detector, so action calls fail closed as in A5;
read-only screenshot works without permission or calibration. Physical allow and
confirmation rendering remain A7 work. MCP has no enable tool. Estop cancels input
until a fresh keyboard allow cycle. Direct actions share budgets across tool calls.

`make -C deck/agent test` includes the sanitized A6 C suite and builds the separate
`mk20-agent-mcp-test` fixture binary. `python -m pytest deck/agent/tests/test_mcp.py`
runs handwritten protocol, real ctl/HID IPC, fake VNC and real listener tests.
The fixture alone accepts connected HTTP/kbd/VNC fds and trusted synthetic window
hints; production rejects those options. A loopback listener override requires both
`--mcp-listen 127.0.0.1:PORT` and `--mcp-no-bindtodevice`. Real listener failures
remain failures under bind-denying sandboxes; `-k 'not real_socket'` explicitly
selects the supplemental IPC checks. See [NOTES-agent-a6.md](NOTES-agent-a6.md)
for limits, acceptance evidence and registration snippets (not executed).

A7 adds `build/mk20-agent --web` at `http://172.30.250.2:8780/` and `/chat`; it is
the default-on usb0 settings/maintenance web (`FEATURE_WEB=1`, supervised by `mk20d web`,
user guide [../SETTINGS.md](../SETTINGS.md)).

- **Modes.** `--web` alone is settings/maintenance only: no keyboard client, no VNC or
  model runtime inputs (`--vnc-*`, `--provider/--net` are refused), and the task API
  answers `agent_disabled`. `--kbd-sock`/`--mcp` add the agent runtime (mk20d does this
  only with `FEATURE_AGENT=1` and `/run/mk20/kbd.sock`). The web cannot enable the agent;
  run-level permission stays a physical keyboard allow. An idle settings-only process
  blocks in `poll()` (250 ms) instead of the 5 ms pump.
- **Listener.** `--web-listen A.B.C.2:8780` is the usb0 address mk20d reads from
  `/tmp/mk20/gadget.env` (`MK20_ECM_IP`, so a configured `ECM_SUBNET` works). Linux
  `SO_BINDTODEVICE("usb0")`, a unicast `.2` outside 0/8, 127/8, 169.254/16 and >=224/4,
  and port 8780 are required; there is no fallback. Host testing requires the explicit
  pair `--web-listen 127.0.0.1:PORT --web-no-bindtodevice`.
- **DNS rebinding / CSRF.** Every request's `Host` must equal the bound numeric
  `IP:8780`, and a supplied `Origin` must equal `http://IP:8780` (403 otherwise, pages
  included). Every non-GET needs that Origin or `Sec-Fetch-Site: same-origin`; paired
  mutations also need `X-CSRF-Token`. Sessions are HttpOnly/SameSite=Strict cookies plus
  a separate CSRF token, in memory only (two sessions, 30 min idle, 12 h absolute).
  `GET /api/session` returns the CSRF token to a same-origin paired tab.
- **Pairing.** `POST /pair/start` writes a 6-digit code (rejection-sampled from
  /dev/urandom) to `--web-pair-file` (default `/run/mk20/pair-code`, which mk20-kbd
  displays): exactly `code=NNNNNN\nexpires_uptime=<uptime s + 300>\n`, mode 0600,
  `.pair-code.<random>.tmp` + rename, in a directory owned by the service and not
  group/other-writable (`--web-uptime-file`, default `/proc/uptime`, is a test seam).
  The file is removed on pairing, expiry, exhaustion, lockout, startup and exit. A code
  allows 5 wrong attempts; at most 6 new codes per 10 min; 10 failed `/pair` requests in
  10 min, counted whether or not a challenge is live, lock `/pair` and `/pair/start` for
  10 min (`429 rate_limited` with `retry_after_s`). A repeated start keeps the live code
  and its attempt count. If the file cannot be (re)published the start answers `503
  display_unavailable` (with a Chinese `message`), never `started`. The code is never
  returned by HTTP or logged.
- **Sessions.** `WEB_SESSIONS` = 2. With both slots taken `/pair/start` (and a late
  `/pair`) answer `503 sessions_full` ("已有 2 个已配对的浏览器，请等待过期或在已配对页面退出");
  `POST /api/logout` (paired, CSRF) frees the caller's slot, stops a task it owns and
  expires the cookie.

After pairing:

- `POST /secret/{provider|vnc} {"value":"…"}` returns only `{"set":true}` (no length);
  `GET` returns set status only; `DELETE` removes it. No secret read endpoint exists.
- `GET /api/catalogue`: provider/model choices from the generated table
  `src/web_providers.inc` (`../tools/gen-providers.py` from `../share/providers.json`,
  the same source as `kbd/providers.inc`; `make test` runs `--check`), the secret names
  and the proxy hosts of the single proxy-host rule (usb0 peer `.1`, `172.30.250.1`,
  `10.20.0.1`, `169.254.7.1`; board profile also `127.0.0.1`).
- `PUT /api/provider {"provider","model"}` derives URL/route/key path from the catalogue;
  `PUT /config/net` and `/config/provider` accept the `agentcfg.py` device schemas
  (new writes need a supported provider and a catalogue model; an older saved model
  still loads). `GET` returns validated configuration and key paths only. Writes take
  the `.apply-lock` process lock (409 `busy` if kbd holds it), persist
  `.apply-in-progress` before replacing the file and clear it only after the directory
  fsync; while the marker exists readers fail closed (`409 apply_incomplete`).
  `POST /test` performs a device-side provider reachability check over that route.
- `GET /api/status` and `POST /api/maint/{restart-kbd|exit-safe-mode|debug}`: the
  maintenance page, see [../firmware/MAINT-HOOKS.md](../firmware/MAINT-HOOKS.md); a
  snapshot not owned by root/the service or writable by group/other is reported as
  `unavailable` (状态不可用), never parsed
  (`--web-health-file`, `--web-maint-dir`, `--web-debug-file`).
- Agent mode only: `POST /api/tasks {"goal":"…"}` queues while disabled as `waiting_allow`.
  `GET /api/tasks/{id}` returns state/steps/fixed summaries and retained shot numbers.
  `GET /api/tasks/{id}/steps/{n}/shot.jpg` reads the five-entry memory ring.
  `POST /api/tasks/{id}/pause|resume|stop` controls the task; Stop revokes keyboard
  input. Pause holds before the next observation or new action, with budgets and
  safety checks continuing. `GET /api/events` streams bounded SSE progress.

`--web-secrets-dir` and `--web-config-dir` select test storage directories (defaults
`/data/mk20/secrets` and `/data/mk20/agent`). Directory path components cannot be
symlinks; files are atomically installed as 0600 in 0700 directories. Configuration
updates reload into the shared runtime, including subsequent MCP tasks. `--ca-file`
selects the existing TLS client's CA bundle. Capture still uses A6's startup VNC
options; replacing `vnc.pass` does not renegotiate an existing VNC connection.
Production action safety still requires the A5 trusted window detector and measured
calibration. No web operation grants permission or accepts physical confirmation.

`make test` includes the sanitized A7 C suite and builds `mk20-agent-web-test`.
That fixture alone accepts injected HTTP/model fds, synthetic trusted window
hints and a test clock override; its SSE interval is accelerated to test backpressure.
`python -m pytest deck/agent/tests/test_web.py` includes enabled real TCP/Unix gates
and socketpair supplements. See [NOTES-agent-a7.md](NOTES-agent-a7.md) for the
acceptance record and remaining keyboard/device work.
