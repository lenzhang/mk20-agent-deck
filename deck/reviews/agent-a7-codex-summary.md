# A7 agent-side first acceptance — Codex

Verdict: **host implementation passes C/supplemental IPC checks; real-listener
acceptance fails visibly with sandbox EPERM. This is not a Linux/device GO.**
Production pairing fails closed until the separate keyboard track can actually
render the challenge. Production input retains A5's trusted-window/calibration
requirements. No external network/provider/device/adb/serial/commit/push operation occurred.

## Requirement → implementation → test evidence

| Requirement | Implementation / evidence | Verdict |
|---|---|---|
| A6 idle expiry, LRU, busy-owner protection, removed id 404 | `mcp_session_t.last_activity`, `sessions_expire`, `session_remove`, initialize candidate selection; `test_mcp.c::session_lifecycle` | PASS |
| Pending direct disconnect log via redaction and fixed registry only | `mcp_disconnect`, `log_status`; C log check and real ctl/HID `test_disconnect_pending_logs_fixed_registry_ipc` | PASS |
| Remove dead confirmation_seen | mcp.c/.h diff and source search | PASS |
| Exact default 172.30.250.2:8780 + usb0; no fallback/wildcard/IPv6/other address | CLI and reused `httpd_address/listen`; override refusal matrix and missing-usb0 probe | PASS refusal paths; actual Linux isolation pending |
| Real TCP/Unix/netns gates stay enabled | `test_real_socket_full_flow`, `test_real_socket_daemon_web`, `test_real_socket_web_listener`, `test_web_usb0_namespace_isolation` | Three real-listener tests fail EPERM here; netns-tool-absence refusal path passes |
| One process/pump/task store with MCP | `httpd_run_services`, per-connection handler, shared mcp/loop/runtime; `test_shared_mcp_task_slot_ipc` | PASS |
| 4 total connections, headers 8 KiB/body 64 KiB/5s, strict framing | Existing parser limits retained; shared slots, PUT CL, duplicate Cookie/CSRF/Fetch-Site refusal; C parser and web/MCP HTTP/deadline and deterministic oversized-header half-close cases | PASS IPC; real accept saturation pending Linux |
| Exact Host/Origin, mutation Origin or same-origin Fetch-Site + paired CSRF | `web_handle`, strict header parser; `test_web.c::auth`, `test_auth_csrf_origin_and_secrets_ipc` | PASS |
| Only four specified bootstrap/page endpoints public; identical unpaired 401 | Auth-first routing; C path matrix, web auth IPC, no extra public script endpoint | PASS |
| Uniform six-digit CSPRNG code; 5min/5 attempts/one-time | 24-bit rejection sampling, expiry/attempt counter; `test_web.c::pairing` | PASS source + state tests; no statistical randomness claim |
| Display port must confirm actual render | `kbdc_pair_display` uses control page 1/fb21 `screen.text`, requires rendered:true; unavailable C/real ctl IPC cases | PASS fail-closed; actual rendering is another track |
| Pair code exposed only by test fd, never HTTP/log | `MK20_WEB_TEST` pairing port; production rejects fixture seams; per-flow sensitive-log assertions | PASS |
| Cookie 128 bits, HttpOnly/Strict/Path, no Secure; separate CSRF; max2; 12h/30min/restart | `web_session_t`, fixed-length comparisons, `expire`; pairing/auth/absolute/idle/restart unit tests | PASS |
| Static local pages ~10KB/~15KB, CSP | `web_pages.c` 10,336/14,711 bytes; hash verification IPC and offline JavaScript syntax check | PASS |
| Provider allowlist plus vnc.pass; 1..4096/no NUL/CR/LF; set/len only; no content GET | `web_secret_name`, `secret`, strict JSON parser; secret boundary/unknown/traversal/status tests, actual TLS key use | PASS |
| Same-dir O_EXCL 0600 full write/fsync/rename/fsync dir; safe parent | `web_directory`, `web_atomic`, `web_remove`; inode replacement/modes/symlink/nonregular-directory refusal cases | PASS host; foreign-uid branch source audited |
| Device schemas, unknown fields/foreign-direct/bad proxy/base/key path refused | `web_config_validate` plus existing netcfg/llm parsers; `test_web.c::configs` | PASS |
| Read-only Python table/proxy drift check | AST extraction of PROVIDERS/MAC_HOSTS compared with `web_schema` exported only by test binary | PASS; references unedited |
| Config GET contains paths only; reload affects both web and MCP | Safe validated reads, reload on startup/save; full flow and `test_config_update_applies_to_mcp_tasks_ipc` | PASS |
| POST /test uses configured existing HTTPS route, only ok/latency/error_class | `connection_test`, `net_request`; unit proxy-route callback and real fake CONNECT/TLS flow with pinned provider certificate | PASS IPC, no direct fallback |
| Web disabled submission is waiting_allow; only keyboard allow starts | `mcp_task_queue`, queued gating in `mcp_tick`; C task counters/queued stop-generation regression and full ctl/HID/VNC/model IPC flow | PASS; MCP disabled behavior retained |
| Task status fixed action summaries/no typed text/goal | Existing fixed loop history, web JSON/SSE filtering; unit/flow/privacy checks | PASS |
| Last5 scaled JPEG memory ring, no disk, freed on termination/replacement | loop frame callback and bounded owned copies; live-paused ring eviction/JPEG checks and terminal/replacement cleanup | PASS |
| Pause at safe boundary, resume same budgets; Stop same keyboard revoke path | `loop.paused`, terminal clear, existing `loop_service`/`loop_stop`/LEASE_ESTOP; paused budget C regression and `test_web_stop_mid_drag_ipc` | PASS |
| SSE ≤2, bounded buffers, slow drop, expiry/disconnect cleanup | `web.events[2]`, bounded output, write deadline; C events plus limit/disconnect/tiny-buffer slow-reader IPC | PASS |
| No secret/code/goal/cookie/auth-header/VNC-password logs | No web request/body logger, A6 fixed metadata/redaction only; fixture output scans and actual pending-disconnect log check | PASS |
| Secrets excluded from backup | Read-only `test_backup_excludes_secrets` checks existing tools/backup-exclude.txt | PASS; tools untouched |
| C11/-Wall/-Wextra/-Werror/ASan/UBSan, no new dependencies/suppressions | Existing Makefile flags, sanitized C suites and nonsanitized host link | PASS |
| 32-bit/static friendly, no new name-resolution code | int64_t/uint64_t times/tags, bounded sizes/counters, existing net_request only | Source PASS; armhf compiler absent, build NOT-RUN |
| Preserve A2–A6 | Full standard pytest plus explicit broad IPC supplement, C suites | C/IPC PASS; standard listener failures classified separately |
| Agent-only paths; docs/first acceptance/A7 status | Scope assertion, git diff --check, README/NOTES/review/A7 status | PASS; final checks recorded below |

## Test record

- Final `make -C deck/agent test build/mk20-agent-nosan armhf`: PASS for host
  C/ASan/UBSan and nonsanitized link. **439 A7 web**, **267 A6 MCP**, **262 A5**,
  **256 LLM**, **3145 OAI**, **349 net/clock**, plus RFB/scale/JPEG and CLI checks.
  No sanitizer diagnostics. The armhf target explicitly reports compiler absent;
  static cross-build/32-bit execution is NOT-RUN.
- Broad IPC supplement: **361 passed, 1 existing skip, 16 explicitly deselected**,
  140.36 seconds. The skip is A0/OAI's existing offline HTTP switch. Deselections
  are three existing TCP-only proxy cases, six real loop daemons, four real MCP
  cases and three real web cases. No new skip/xfail was introduced.
- Full standard collection: **188 passed, 197 failed**, 158.22 seconds, no
  skip/xfail. Every failure explicitly reports listener EPERM: 7 fakes, 49 LLM,
  6 loop daemon, 4 MCP, 41 net, 1 OAI, 86 RFB, 3 web. The complete IDs are in
  [agent-a7-socket-blocked.txt](agent-a7-socket-blocked.txt).
- The final small connection-test busy guard was then covered by the C suite and
  the affected web/MCP collection: **60 passed, 7 failed**, all seven real-listener
  EPERM (four MCP, three web), no skip/xfail. The probe returns busy while
  queued/running tasks or direct calls need the pump for cancellation.
- Offline inline JavaScript syntax, both CSP hashes, dead-field removal, path
  scope, plan-only status-line diff and `git diff --check`: PASS. See
  `agent-a7-final-checks.txt`.

An intermediate parallel
standard run also exposed an oversized-header BrokenPipe race: the previous hard
close could race a sender still writing after the 8 KiB limit. The response path
now half-closes writes and drains unread input under a bounded deadline. The
new deterministic half-close case plus all nine existing MCP/web HTTP-limit cases
passed (**10 passed / 57 deselected**, 5.66 s). No existing deadline, assertion
threshold or test was relaxed. The full and broad collections above include this regression.

Raw records: [C/link checks](agent-a7-make.txt),
[full pytest](agent-a7-pytest.txt), [broad IPC](agent-a7-ipc-pytest.txt),
[web/MCP](agent-a7-web-mcp-pytest.txt),
[half-close regression](agent-a7-halfclose-pytest.txt).

## Acceptance limits and changes outside this track

- kbd L5/allow/confirm/status widget/screen rendering/upload notice remain another
  track. Until its render-confirmed pairing display exists, production returns
  display_unavailable and accepts no pairing code.
- mk20deck settings push/status mirror remains another track.
- Linux TCP/Unix daemon/netns, static armhf, real usb0 isolation, measured calibration,
  trusted window hints, physical stop/confirmation, RSS/latency and device acceptance
  remain pending; IPC does not establish those results.
- No remote enable/grant/confirmation was added. Missing runtime configuration or
  trusted capture hints fails closed. No ASR/UAC/clipboard/release work was attempted.
- The pre-existing untracked prompt-agent-a7.txt is untouched. No git commit/push.
