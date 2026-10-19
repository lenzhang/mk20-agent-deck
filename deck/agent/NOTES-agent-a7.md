# A7 agent-side host implementation

Scope: `deck/agent/{src,tests,Makefile,README.md,NOTES-agent-a7.md}`, A7 review
records and the A7 plan status line only. No device/adb/serial, external network access,
provider credential, commit, push, dependency download, firmware/vendor/tool or
keyboard/mk20deck source change was used. Test-generated listeners remain enabled
for Linux and fail visibly under this host's listener-denying sandbox.

## A6 Kimi minors (implemented first)

MCP sessions now record monotonic `last_activity`. Access and ticks expire idle
sessions after strictly more than `MCP_SESSION_IDLE_MS` (default ten minutes;
compile-time test override). Initialize uses a free slot or evicts the oldest
session with neither a pending direct call nor a live owned task. Capacity eviction
never chooses a busy owner. Expiration/deletion stops only the expired/deleted
owner's live work, and subsequent access to the removed id returns 404. If no
candidate exists, initialize still returns 503. `confirmation_seen` is removed.
A disconnected pending direct call logs fixed `tools/call` metadata with status
499 through `agent_redact`; tool names are accepted only by registry pointer.

`test_mcp.c::session_lifecycle` covers tick/access expiry, the exact timeout
boundary, LRU refresh/eviction, busy ownership and stale 404. The real ctl/HID IPC
test `test_disconnect_pending_logs_fixed_registry_ipc` starts a risky key call,
disconnects it and checks the fixed `tool=key status=499` log and stopped input.
The Part 0 make record was green before starting the web implementation.

## Listener, pairing and browser security

`--web` defaults to exactly `172.30.250.2:8780` with Linux
`SO_BINDTODEVICE("usb0")`. Explicit loopback test overrides require both
`--web-listen 127.0.0.1:PORT` and `--web-no-bindtodevice`. Wildcards, IPv6,
nonloopback alternatives and alternate device ports are refused. There is no
fallback. `--mcp --web`, in either order, uses the same cooperative `httpd.c`
pump, runtime, keyboard connection, policy and task store. Four total connections
share both listeners; headers remain 8 KiB, bodies 64 KiB, request-read and stalled
response deadlines five seconds. Duplicate Cookie/CSRF/Fetch-Site headers are
rejected along with A6's duplicate framing/security headers. PUT requires CL.
Closing responses use a write half-close and bounded input drain, so rejecting an
oversized header cannot reset the 431 response while the sender finishes writing.
The deterministic split-send regression waits for 431 before sending the remainder.

Only GET `/`, GET `/chat`, POST `/pair/start` and POST `/pair` are public. All
other endpoints return the same unpaired 401 body. Host must match the configured
numeric authority; any supplied Origin must exactly match
`http://172.30.250.2:8780`. All mutations require this Origin or
`Sec-Fetch-Site: same-origin`; authenticated mutations also need `X-CSRF-Token`.
Pairing cannot require an existing session's CSRF token, so the two bootstrap
POSTs use the Origin/Fetch-Site check plus the physical challenge.

Codes use /dev/urandom and rejection sampling on 24 bits: discard values
>=16,000,000, then reduce modulo 1,000,000 and format six digits. The physical
challenge lasts five minutes and is invalidated after five wrong attempts or one
successful pair. A repeated start cannot replace an active challenge/reset its
attempt counter. Codes are never returned by HTTP or logged. The production display
port calls kbd.sock `screen.text` for reserved control page 1 / secondary fb21 and
requires an actual boolean `rendered:true`. Any refusal, missing render confirmation
or `rendered:false` invalidates the code and returns `display_unavailable`.

The currently documented keyboard rule engine refuses writes to the control page
and has no renderer. Consequently **production pairing intentionally remains
unavailable until the separate keyboard track supplies that display capability**.
No screen permission bypass or fake rendered success is implemented here.
Only `MK20_WEB_TEST` can export the challenge through an explicitly inherited fd.

A maximum of two browser sessions uses independent random 128-bit cookie and CSRF
values. Cookies are HttpOnly, SameSite=Strict, Path=/, without Secure on the specified
plain HTTP USB service. Fixed-length volatile comparisons visit all expected bytes.
Sessions expire at thirty idle minutes or twelve absolute hours; time rollback
also invalidates them. Restart loses all sessions. Expiring a task owner stops
that task. A8 correction: an authenticated connected SSE stream keeps the session active;
the twelve-hour absolute limit and rollback checks still apply.
Cookie/CSRF/pair values and task goals never enter the log sink.

The static configuration and chat pages are approximately 10 KB and 15 KB. They
use local HTML/CSS/JS only, exact inline script/style CSP hashes, no external assets,
no-store, nosniff, no-referrer and frame/base/form restrictions. The page clears
secret/code input after an attempt, uses textContent for progress, and keeps the
CSRF value in sessionStorage. It does not access the HttpOnly cookie.

## Persistence and config

Secret names are the seven provider-table names (qwen/glm/kimi/deepseek/openai/
anthropic/gemini) plus vnc. The last maps to `vnc.pass`; providers map to
`<name>.key`. MCP's token and arbitrary filenames cannot be changed through web.
`POST /secret/{name}` accepts exactly `{value:string}`, 1..4096 UTF-8 bytes,
without NUL/CR/LF. The sole success payload is `{set:true,len:N}`. GET uses file
metadata only and returns `{set:bool}`; DELETE removes the fixed named file.
There is no secret-content read endpoint. A6 VNC startup behavior remains: an
already-open VNC session is not renegotiated when its password file is replaced.
VNC authentication itself uses the existing eight-byte password contract.

`web_directory` walks all path components using O_DIRECTORY/O_NOFOLLOW/openat,
refuses dot-dot, non-directory/symlink components and foreign-owned final dirs,
and creates/fixes the final directory as 0700. Test temp paths are canonicalized
to avoid Darwin /tmp and /var symlinks. Writes use a random same-directory
O_CREAT|O_EXCL/O_NOFOLLOW temp, fchmod 0600, full EINTR-aware writes, fsync(file),
renameat, fsync(directory). Existing symlink/nonregular/foreign-owned destinations
are refused. Error cleanup removes temporary names; old open descriptors retain
the previous inode. Deletes also fsync the directory. Raw request bodies are never
logged; the decoded secret string is wiped before freeing its JSON allocation.

`PUT /config/{net,provider}` validates the device schema before identical atomic
installation. GET validates and returns only the config, including key file paths.
Provider fields exactly match agentcfg.py's provider schema; provider/base_url/
domestic/route/key path must match the C registry and configured secrets directory.
Net fields are version/profile/mode/proxy/routes/note; unknown nested fields are
also rejected. All foreign-direct configurations are refused, including usb_wifi
mode direct, following the explicit A7 instruction and §6.2. Mac proxy endpoints
must be the three literal MAC_HOSTS; a board proxy may additionally use
127.0.0.1. Arbitrary DNS/LAN proxy endpoints and URL credentials are refused.
No new resolution code was added.

An AST-only Python drift check reads agentcfg.py without importing it or writing
its pycache and compares the provider table and MAC_HOSTS with `web_schema()`.
The board loopback exception is device policy; agentcfg.py does not define a
separate board proxy host table. No read-only reference was edited.

Config saves are rejected while tasks/direct calls are live. Validated persisted
configs load on web startup and reload after saves; subsequent MCP and web tasks
share them. Invalid/missing pairs clear the runtime config and fail closed.
POST `/test` returns busy while tasks/direct calls occupy the shared slot, so its
synchronous probe cannot delay live cancellation. A8 correction: otherwise a short-lived probe child invokes the existing
`net_request` HTTPS client with the saved provider URL/key path/route, a
1.5-second deadline and bounded response; the HTTP pump keeps serving clients. HTTP replies
(including 401/404) mean reachable, matching agentcfg's connection test semantics.
Proxy failure never becomes direct. It returns only ok/latency_ms/error_class;
it neither returns peer bodies nor changes a proxy or system clock.

## Tasks, pause, stop and streaming

Web submission calls `mcp_task_queue` on A6's single slot. Unlike MCP's unchanged
`run_task` disabled/stopped result, web may retain a goal as `waiting_allow` without
observing, planning or executing. Only K_ALLOW from the keyboard starts it; web,
MCP, resume, pairing and config have no enable/grant/confirm API. Busy tasks/direct
calls block another submission. A queued task records the loop’s 64-bit stop generation: any later keyboard/lease
stop cancels it even if allow follows before the next task tick. A newly submitted
task while already stopped can wait for a new physical allow. This also preserves
A6’s cancellation of accepted-but-not-yet-started MCP work. Goals are erased on terminal/cancel and are never
included in status, SSE or logs. Config/test traffic has no remote-enable side effect.

Pause is a hold before the next observe or a new act; an in-flight port finishes
under its original guard/deadline. Planning/confirmation may finish before the
next held action. Keyboard safety, lease renewal, confirmation deadlines and wall
budgets continue while held. Resume keeps the budget. Stop always calls
`loop_stop`/LEASE_ESTOP, including a waiting/terminal task, revokes input and latches
the loop. Only a fresh keyboard allow cycle can authorize work afterward.

The loop's frame-ready callback copies at most five scaled JPEG observations,
indexed by the next step number. Each is bounded by A4's 128 KiB OAI_IMAGE_MAX;
ring payload is at most 640 KiB. Copies stay in memory, never on disk. The ring is freed at task termination
(including stop/error), before replacement by a new task, and on shutdown. Review
screenshots during a live or paused task. Old task ids/evicted shots return 404.
Steps expose only fixed action-name history, never typed text/window/goal strings.

SSE retains at most two request tags. Each client gets one bounded status payload
per second, with no accumulating application queue. The HTTP pump permits one
bounded unsent output; a five-second stalled write drops the connection and frees
its event slot. Session expiry also closes its stream. The fixture accelerates
only the heartbeat to 10 ms for a deterministic tiny-send-buffer backpressure test.

## What this track does not implement

- Keyboard L5 agent page, allow/confirmation UI, status widget, screen.text pixel
  rendering, upload notice and keyboard screen grants are the concurrent kbd track.
- mk20deck push/settings/status mirroring is the concurrent mk20deck track.
- ASR/voice/UAC, clipboard paste, release supervision/packaging and firmware work
  are outside this requested agent-side phase.
- The A5 production trusted window/secure-input detector and device calibration,
  physical action/confirmation/estop acceptance, usb0 reachability, RSS/latency and
  static armhf execution remain device/Linux acceptance gates. The test binary's
  synthetic Notes hints do not establish production action readiness.

## Verification

Final host results: make test + nonsanitized link PASS (439 web / 267 MCP C
checks, existing suites green); broad IPC 361 passed / 1 existing skip / 16
explicit deselections; standard collection 188 passed / 197 listener EPERM failures
only. After the last busy guard, affected web/MCP rerun: 60 passed / 7 listener
EPERM failures only. No new skip/xfail or warning suppression. armhf is NOT-RUN
because the compiler is absent.

Final command results and the first acceptance verdict are recorded in
[agent-a7-codex-summary.md](../reviews/agent-a7-codex-summary.md). Raw logs use
`deck/reviews/agent-a7-*`. New real-listener tests have no skip/xfail. Supplemental
IPC selections are explicitly labeled; they do not turn EPERM into a passing gate.

```sh
make -C deck/agent test build/mk20-agent-nosan
PY=/Users/dev/.cache/uv/archive-v0/vxkKwzR1zHgPnvko/bin/python
export PYTHONPATH=/Users/dev/.cache/uv/archive-v0/bo26hwk4TgxG1QEs/lib/python3.13/site-packages
$PY -m pytest deck/agent/tests -q --tb=short
$PY -m pytest deck/agent/tests/test_web.py deck/agent/tests/test_mcp.py -q --tb=short
MK20_TEST_SOCKETPAIR=1 MK20_TEST_NO_NETWORK=1 $PY -m pytest \
  deck/agent/tests/test_rfb.py deck/agent/tests/test_oai.py \
  deck/agent/tests/test_fakes.py::test_des_known_vector deck/agent/tests/test_net.py \
  deck/agent/tests/test_llm.py deck/agent/tests/test_loop.py \
  deck/agent/tests/test_mcp.py deck/agent/tests/test_web.py \
  -q -k 'not unreachable_proxy and not fallback_order and not real_socket' --tb=short
```
