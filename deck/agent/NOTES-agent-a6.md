# A6 MCP host implementation and first acceptance

Scope: host build only, 2026-10-19. No network/provider access, device/adb/serial
operation, registration, commit or push was performed. The only permanent changes
outside `deck/agent/` are A6 review artifacts and the A6 status line in the plan.
`deck/kbd`, `deck/mk20deck`, vendor, firmware and kernel sources are unchanged.

## Design

`httpd.c/.h` implements a bounded single-threaded nonblocking HTTP/1.1 server.
`mcp.c/.h` owns JSON-RPC dispatch, one 13-tool registry (including input schemas),
four in-memory sessions and a single-slot in-process task store. `mcp_cli.c` wires
these to A5 runtime/kbdc/RFB. The default listener is exactly
`172.30.250.2:8765` with Linux `SO_BINDTODEVICE("usb0")`. Every bind/device error
aborts startup; there is no fallback listener. Other nonloopback addresses,
wildcards and IPv6 are refused. Loopback testing requires explicit
`--mcp-listen 127.0.0.1:PORT --mcp-no-bindtodevice`.

Every parsed request authenticates before dispatch, including GET/DELETE. Missing
and incorrect credentials return identical empty 401 responses with
`WWW-Authenticate: Bearer`. The comparison always visits the 71 expected header
bytes using a volatile difference accumulator, with no secret-dependent early exit.
The public supplied length is checked separately. No token or header is logged.
The token is exactly 64 lowercase hex characters (32 random bytes), without LF.
Existing tokens must be regular, nonsymlink files owned by the effective service
uid with mode exactly 0600. `/dev/urandom` supplies entropy. First creation uses an
exclusive random temporary file, fchmod 0600, complete writes, fsync, atomic rename
and fsync of the containing directory. An exclusive directory-local creation lock
prevents two service instances from replacing each other's new token; a stale lock
fails closed and requires local maintenance. Parent directories must already exist.

Host must equal the configured numeric listen authority. Origin, if present, must
be exactly `http://172.30.250.2:8765`, including in loopback tests. A supplied
`MCP-Protocol-Version` must equal `2025-06-18`. POST accepts one JSON-RPC message;
batches return -32600. JSON syntax, method and parameter failures return -32700,
-32601 and -32602 respectively. Notifications and client responses return empty
202. Initialize returns the protocol version, tools capability with
`listChanged:false`, `serverInfo.name=mk20-agent`, and a 128-bit random hex
`Mcp-Session-Id`. Later messages require this session (missing 400, unknown 404).
Clients send `notifications/initialized` before tools/call. DELETE ends the session;
GET returns 405 and `Allow: POST`. Responses use application/json; no SSE is needed.

Limits: 8 KiB total request headers, 64 KiB body, four simultaneous connections,
four sessions, a five-second absolute request-read deadline (not extended by
trickle writes), five-second output/idle bounds, and at most 32 requests per
keep-alive connection. POST requires Content-Length; transfer encoding is refused
(411, or 400 for ambiguous CL+TE), oversize bodies return 413 and headers 431.
Duplicate security/framing headers, folding, malformed request lines and Expect
are refused. Pipelined messages retain their exact boundaries. Waiting for a
keyboard confirmation is execution time, separate from request-read time.

## Shared safety and tasks

`loop_direct` enters the existing A5 policy/confirmation/acting states. The same
`validate_action` helper serves model replies and MCP actions. `policy_check`,
`kbdc_lease`, `kbdc_confirm`, `kbdc_execute`, `loop_service`, execution deadlines,
renewal, risk union, app-switch checks and never-do rules are shared. Direct mode
has no observe/model call. Direct calls retain their step/wall budget and previous
app context across requests, sessions, screenshots and reconnects. A keyboard allow
cycle starts a fresh epoch; autonomous task submission starts an ordinary A5 task.
No tool or HTTP operation can set allowed. All K_ALLOW events originate in kbdc's
keyboard-owned status. The separate test driver uses keyboard-only ctl callbacks.

The HTTP I/O pump runs cooperatively from A5 safety guards during execution, so
another connection can issue estop/status/cancel while an action or confirmation
is in progress. It is guarded against recursive pumping. Estop always invokes the
kbd estop operation, even with no permission or input lease. It latches the loop
stopped; kbdc's existing allow/off/estop rules control recovery. A disconnected
pending direct caller also triggers the stop path. SIGTERM closes connections and
stops live work; core dumps are disabled before reading secrets (Linux also sets
PR_SET_DUMPABLE=0).

Actions use the latest MCP screenshot, with screenshot-pixel coordinates and a
matching valid calibration. A newer screenshot from another session invalidates
older coordinate authorization. Trusted window hints are refreshed before policy
validation. Without such a detector the production runtime marks a screenshot
secure and refuses actions, preserving A5's fail-closed behavior. The dedicated
`MK20_MCP_TEST` binary supplies synthetic Notes hints and fd injection; production
accepts neither. Unicode type_text goes through A5's unsupported paste policy;
there is no clipboard or VNC input writer. Tool failures contain short classes:
disabled, stopped, needs_confirmation: denied/timeout, rejected, budget, busy,
unsupported or error. Confirmation can wait up to the configured keyboard timeout;
its final denial/timeout returns isError:true.

Screenshot uses the runtime's authenticated existing RFB connection, the A2
snapshot/scale/JPEG path, and image/jpeg base64 content plus frame/source size and
scale factors. It is read-only and may run while disabled or uncalibrated. Nothing
is saved to disk. A5 has no implemented upload-notice renderer or occlusion
configuration; those remain A7/device gates. No masking or rendered notice is
claimed here. Stopped sessions return stopped until keyboard reauthorization.

`agent_task_t`, `mcp_task_submit` and `mcp_task_cancel` are the shared in-process
single-slot design for A7 `/api/tasks`. Submission while disabled/stopped returns
that class and creates no task. A queued accepted task starts via `loop_submit`;
it uses the autonomous A5 observe/model loop, whereas direct MCP tools never call
its model port. No provider configured means that autonomous work fails closed.
A busy slot blocks new tasks/direct actions. `task_status{id}` returns id/state,
step count and fixed action-name history only, never frames, goal or typed text.
Cancel uses `loop_stop`; terminal goals are wiped. Session deletion stops its live
work. The queue is not persistent and only retains the current/most recent task.

Only fixed method/tool/status/latency metadata is logged, through `agent_redact`.
Unknown client method/tool strings are never echoed into logs. Runtime action/body
logging is not wired to the MCP sink. Tests inspect all MCP logs for the token,
Authorization marker, VNC password and task text.

## Verification

Final results:

- `make -C deck/agent test`: PASS, 236 A6 / 262 A5 / 256 LLM / 3145 OAI /
  349 net/clock checks, plus RFB/scale/JPEG and CLI checks; no sanitizer errors.
  `build/mk20-agent-nosan` also builds.
- Final A6 pytest: **30 passed, 4 failed**, 27.99 s, no skip/xfail. Every failure
  is listener EPERM: `test_real_socket_protocol` (TCP bind errno=1),
  `test_real_socket_daemon_mcp[False]`, `test_real_socket_daemon_mcp[True]`,
  `test_real_socket_estop_mid_drag` (real kbd Unix control socket refused).
- Standard full pytest: **157 passed, 194 failed**, 123.41 s, no skip/xfail.
  This collection preceded the final added disabled-read-only screenshot case;
  the final A6 and supplement runs include it. The failures are exactly listener
  EPERM: 7 fakes + 49 LLM + 6 A5 daemon + 4 A6 + 41 net + 1 OAI + 86 RFB.
  All 194 failed IDs are listed in [agent-a6-socket-blocked.txt](../reviews/agent-a6-socket-blocked.txt).
- Final A2–A6 socketpair supplement: **331 passed, 1 existing skip, 13 explicitly
  deselected**, 112.15 s. The skip is A0/OAI's existing offline HTTP switch; the
  deselections are the existing three TCP-only proxy cases, six A5 real daemon
  cases and four A6 real listener cases. No new skip/xfail was added. Both existing
  compressed-frame timeout cases passed in this final run.
- Earlier broad IPC selection: 320 passed / 8 failed / 1 existing skip /
  12 deselected. It accidentally included the seven A0 listener-only tests
  (EPERM), plus an unchanged A2 <1-second wall assertion taking 1.360 s under
  cold host startup. The corrected final selection above passed that assertion;
  no deadline or test threshold was changed.
- Linux netns, foreign-owner chown branch, armhf, device and Mac registration:
  NOT-RUN as detailed below. `git diff --check`: PASS.

The [acceptance review](../reviews/agent-a6-codex-summary.md) maps every requirement
to implementation and test evidence. Raw logs are under `deck/reviews/agent-a6-*`.
Commands use the existing cached Python/pytest/Pillow without installing or
downloading anything:

```sh
PY=/Users/dev/.cache/uv/archive-v0/vxkKwzR1zHgPnvko/bin/python
export PYTHONPATH=/Users/dev/.cache/uv/archive-v0/bo26hwk4TgxG1QEs/lib/python3.13/site-packages
make -C deck/agent test build/mk20-agent-nosan
$PY -m pytest deck/agent/tests -q --tb=short
$PY -m pytest deck/agent/tests/test_mcp.py -q --tb=short
MK20_TEST_SOCKETPAIR=1 MK20_TEST_NO_NETWORK=1 $PY -m pytest \
  deck/agent/tests/test_rfb.py deck/agent/tests/test_oai.py \
  deck/agent/tests/test_fakes.py::test_des_known_vector deck/agent/tests/test_net.py \
  deck/agent/tests/test_llm.py deck/agent/tests/test_loop.py deck/agent/tests/test_mcp.py \
  -q -k 'not unreachable_proxy and not fallback_order and not real_socket' --tb=short
```

The C suite is built with C11 -Wall -Wextra -Werror and ASan/UBSan; no new warning
suppression or dependency was added. Time/tag/hash types are explicitly 64 bit;
there is no assumption that long is 64 bit. The armhf toolchain is absent here,
so static cross-build/32-bit execution is NOT-RUN. Ownership refusal is implemented;
the unit's foreign-owner chown branch requires uid 0 and is NOT-RUN as this user.

Real TCP/Unix tests remain enabled and fail visibly on EPERM, without skip/xfail.
The real daemon harness is reused from test_loop.py (copied unchanged kbd build,
FIFO hidg0, two ptys). New real-daemon cases cover disabled and initially allowed
click, dangerous-key timeout and estop mid-drag. The real ctl/HID socketpair
supplement adds keyboard allow/off/on callbacks, deny/timeout/early/accept,
no-input-before-confirm, post-stop silence, leases, action deadlines, budget and
read-only capture. The existing kbd v1 has no remote enable or confirm-result API.

The netns test runs `unshare -Urn` and creates usb0 when unshare/ip exist; a tooling,
namespace or bind failure is a test failure. Without those tools, actual missing-
usb0 bind refusal plus wildcard/override validation are tested. This Darwin host
uses that refusal branch; Linux network isolation itself remains NOT-RUN here.

## [M] Registration snippets — documentation only, not executed

Codex config keys were checked against the installed CLI's local
`codex mcp add --help` and local executable strings, following the OpenAI Docs
skill without network access. No MCP registration/configuration was changed.
The following entry goes in `~/.codex/config.toml` on the Mac:

```toml
[mcp_servers.mk20]
url = "http://172.30.250.2:8765/mcp"
bearer_token_env_var = "MK20_MCP_TOKEN"
tool_timeout_sec = 125
```

Load the token from a protected file into the launching process environment (the
file is not a TOML command substitution):

```sh
export MK20_MCP_TOKEN="$(cat "$HOME/.config/mk20/mcp.token")"
# Launch Codex from this environment; it builds the Bearer header itself.
```

Claude registration (requested header form; local Claude-version compatibility
and actual connection remain NOT-RUN):

```sh
claude mcp add --transport http mk20 http://172.30.250.2:8765/mcp \
  --header "Authorization: Bearer $(cat "$HOME/.config/mk20/mcp.token")"
```

## Remaining [D]/[M]/[U] acceptance

- [D] Build static armhf, start on the device with usb0, verify interface binding
  and rejection/reachability from every other interface; measure RSS and deadlines.
- [D][M] Retrieve the token through the authorized device channel into a protected
  local file, without terminal output or shell tracing. Example for the later
  authorized device session only (not run in A6):

  ```sh
  umask 077
  mkdir -p "$HOME/.config/mk20"
  adb exec-out 'cat /data/mk20/secrets/mcp.token' > "$HOME/.config/mk20/mcp.token"
  chmod 600 "$HOME/.config/mk20/mcp.token"
  ```

- [M] Register clients using the examples; initialize, list tools, call only status
  and screenshot from the Mac. Confirm JPEG/scale metadata and no secret logging.
- [D][U] Supply measured calibration and a trusted window/secure-input detector;
  finish A7 physical allow/confirmation UI. User-present harmless action, dangerous
  action deny/timeout, real keyboard estop, off→on recovery and no subsequent HID
  reports are required. No remote enable path is to be added to make a test pass.
- Linux host: run all real-socket and netns gates without the listener sandbox.
  Production/client registration, device and Mac acceptance are not inferred from
  socketpair or fixture-only results.

## Acceptance summary (2026-10-19 23:08 +08)

- codex gpt-6.1-sol (deck/reviews/prompt-agent-a6.txt) implementation + first acceptance (deck/reviews/agent-a6-codex-summary.md).
- Box Linux fixes: httpd.c `address()` snprintf bounded to `%.15s` (GCC -Werror=format-truncation false positive on Linux);
  tests/netns_mcp.py falls back to a `bridge` usb0 when the kernel lacks the `dummy` module.
- Kimi K3 r1: **GO**, no BLOCKER/MAJOR; 3 MINOR deferred (dead `confirmation_seen`, no log line for a pending call ended by
  disconnect, sessions never expire → authenticated 4-slot exhaustion 503). deck/reviews/agent-a6-kimi-r1.md.
- Box Linux (real sockets, netns via `unshare -Urn`): test_mcp.py 34/34 incl. real-socket protocol client, real mk20-kbd daemon
  MCP e2e (disabled/allowed/risky confirm timeout/mid-drag estop) and usb0 namespace isolation; agent pytest 352 passed;
  full `make -C deck test` green.
- Route checks before codex/Kimi: AI=US-Exit-Hy2 exit 203.0.113.10.
- Open: [M] register MCP in Codex/Claude (read-only status/screenshot), [D] usb0 bind + token retrieval on device, [U] action calls
  with user present, armhf build.
