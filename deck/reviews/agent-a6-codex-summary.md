# A6 first acceptance — Codex

Verdict: **host implementation and IPC safety checks pass; full socket acceptance
is FAIL in this sandbox, not a Linux/device GO**. Production input still requires
the trusted detector and measured calibration already required by A5. No device,
provider, registration, commit or push was performed.

## Requirement → implementation/test → result

| Requirement | Evidence | Result |
|---|---|---|
| C11, -Wall -Wextra -Werror, ASan/UBSan; no new deps/suppressions | agent Makefile, final make test | PASS |
| Only allowed paths; kbd/vendor/settings/firmware untouched | final git diff/status and scope check | PASS |
| armhf/32-bit safe types | explicit uint64_t/int64_t, bounded unsigned counters; source review | PASS source audit; armhf build NOT-RUN (compiler absent) |
| POST /mcp, single JSON-RPC 2.0, JSON responses, no batches | httpd_parse, mcp_handle; C parser/dispatch + test_protocol_socketpair | PASS |
| initialize/version/capabilities/serverInfo, initialized 202, ping | mcp_handle; protocol client checks | PASS |
| Parse -32700, invalid request -32600, method -32601, params -32602 | test_mcp.c and protocol checks | PASS |
| Notifications/client responses 202, empty body | protocol client | PASS |
| Random 128-bit session, required later, 400/404, DELETE | mcp session store; C and protocol checks | PASS |
| Optional MCP-Protocol-Version header validation | protocol client | PASS |
| GET 405, Allow: POST | C and protocol checks | PASS |
| Bearer required on every dispatched request; identical 401/challenge | httpd_authorized + mcp auth-first dispatch; protocol client | PASS |
| Constant-time secret comparison path | volatile fixed 71-byte loop; unit flips each token byte, missing/short/right inputs | PASS source/path tests (no timing claim) |
| Exact Origin and configured numeric Host | parser/mcp validation; C and protocol tests | PASS |
| Random token creation, O_EXCL 0600 temp/fsync/rename/fsync dir | httpd_token; test_mcp.c creation/reload/mode/symlink/FIFO/directory checks | PASS |
| Present token regular, 0600, service uid, no symlink | load_token source; unit permission/type tests | PASS except foreign-owner chown execution NOT-RUN as nonroot |
| Headers 8 KiB, bodies 64 KiB, CL required, no chunked | exact-boundary C tests, malformed/framing cases, HTTP IPC limit cases | PASS |
| Pipelining, slowloris absolute 5 s, bounded keep-alive | parser plus test_pipelining_ipc, test_slowloris_ipc, test_keepalive_is_bounded_ipc | PASS |
| At most four connections | fixed connection array; four-connection IPC/signal test | PASS; real accept() saturation NOT-RUN due EPERM |
| Default exact usb0 address + SO_BINDTODEVICE; no fallback/wildcards | httpd_address/listen, C wildcard test, bind probe/refusal test | PASS refusal paths; actual Linux usb0 bind NOT-RUN |
| Loopback override requires both explicit flags; test-only fd seams | CLI parser + production rejection test | PASS |
| SIGTERM clean exit; core dump prevention before secrets | mcp_cli; four-connection SIGTERM IPC test | PASS on host; Linux prctl source audited |
| One registry and JSON Schema for all 13 named tools | registry/schema/mcp_tools; list and argument checks | PASS |
| D6: no enable/allow/grant operation | registry/dispatch review; unknown enable -32602/-32601; real keyboard status only | PASS |
| Shared policy/risk/never-do/app context/confirmation | common loop validate_action; four never-do targets, risky key low report still confirmed | PASS |
| Same input lease, renewal, per-action deadlines, budgets | loop_direct → loop_tick → kbdc_execute; C and real ctl IPC; action deadline/wall/step budget tests | PASS |
| Direct tools do not call a model | loop_direct enters shared policy/acting states; C counter remains zero before autonomous task | PASS |
| Screenshot first and matching valid calibration, latest frame across sessions | runtime_snapshot + preflight, calibration callback; bounds/latest/calibration IPC tests | PASS |
| Trustworthy window/secure input detector must not be fabricated in production | missing hints mark secure; synthetic hint only in MK20_MCP_TEST | PASS fail-closed source audit; real detector NOT-RUN/pending A5 device gate |
| Short isError classes, unsupported paste/VNC | outcome mapping; disabled/stopped/confirm/reject/budget/scroll/Unicode tests | PASS; VNC input remains unsupported by existing kbdc |
| Estop always sends kbd operation, even disabled | loop_stop/LEASE_ESTOP; unit stop counter, IPC estop | PASS |
| Mid-action stop, no subsequent HID, off→on recovery | test_estop_mid_action_and_keyboard_reallow_ipc with real ctl/HID and fake VNC | PASS |
| JPEG/base64 image and frame/scale metadata from authenticated RFB | screenshot path; decoded JPEG magic/dimensions; disabled read-only capture | PASS |
| Existing occlusion/upload notice behavior | A5 has neither renderer nor masking config; explicitly documented in A6 notes | PASS documentation; rendering/masking NOT-RUN/pending A7 |
| Single-slot shared in-process task queue/id; disabled not queued | agent_task_t and mcp_task_submit; C disabled/busy/success/model counts, IPC disabled | PASS |
| Task status state/steps/history without screenshots/typed text; cancel stop path | C task completion/cancel and IPC task summary/cancel | PASS |
| No bodies/goals/typed text/token/headers/VNC password in logs | fixed metadata through agent_redact; every MCP fixture checks application output; task checks | PASS |
| Self-written conformance client over actual sockets | test_real_socket_protocol | FAIL environment: bind errno=1 (EPERM); same protocol over socketpair PASS |
| Real mk20-kbd daemon + fake VNC disabled/allowed/risky timeout/mid-action estop | test_real_socket_daemon_mcp[False/True], test_real_socket_estop_mid_drag | FAIL environment: Unix control listener EPERM; unchanged ctl/HID IPC supplement PASS |
| Linux network namespace isolation, visible failure if tooling fails | netns_mcp.py; test_usb0_namespace_isolation | NOT-RUN Linux isolation (unshare/ip unavailable); missing-usb0/wildcard/override refusal branch PASS |
| Preserve A2–A5 | make test C checks; full standard suite and IPC supplement | PASS C; standard listener gates FAIL EPERM; supplement counts below |
| New C tests included in make test; pytest auto discovery | Makefile/test_mcp.c/test_mcp.py | PASS |
| Notes, [M] registration snippets, [D]/[U] gates, plan line | NOTES-agent-a6.md, README, A6 status line | PASS; registration NOT-RUN |

## Test record

- Final `make -C deck/agent test`: PASS. **236 A6**, **262 A5**, **256 LLM**,
  **3145 OAI**, **349 net/clock** checks, plus RFB/scale/JPEG and CLI tests.
  ASan/UBSan produced no diagnostics. The nonsanitized host binary also builds.
- Standard pytest run: **157 passed, 194 failed**, no skip/xfail, 123.41 s.
  All 194 failures are listener EPERM: 7 fakes, 49 LLM, 6 A5 daemon, 4 A6,
  41 net, 1 OAI, 86 RFB. That run collected before the last disabled-read-only
  screenshot supplement was added; the final standalone A6 run below includes it.
- Final standalone A6 pytest: **30 passed, 4 failed**, no skip/xfail, 27.99 s.
  The four failures are exactly test_real_socket_protocol,
  test_real_socket_daemon_mcp[False], test_real_socket_daemon_mcp[True], and
  test_real_socket_estop_mid_drag. No other A6 failures remain.
- Final selected A2–A6 IPC supplement: **331 passed, 1 existing skip, 13 explicitly
  deselected**, 112.15 s. The skip is the pre-existing OAI offline HTTP switch.
  Deselections are three existing TCP-only proxy cases, six A5 real-daemon cases
  and four A6 real-listener cases. No A6 skip/xfail was added. Both RFB compressed
  timeout assertions passed unchanged in this run.
- An earlier overly broad IPC command also collected the seven A0 TCP-only fake
  tests: **320 passed, 8 failed, 1 existing skip, 12 deselected**. Seven failures
  were EPERM; the other was the existing compressed-frame <1-second wall assertion
  (1.360 s). As in A5 notes, cold host executable startup is a timing sensitivity;
  no assertion or deadline was relaxed. The corrected selection explicitly names
  the DES-only fake test and retains the RFB timeout cases.
- armhf: NOT-RUN, make reports toolchain unavailable. No cross/device memory,
  latency or physical confirmation result is inferred from host results.

Raw records: [standard pytest](agent-a6-pytest.txt),
[A6 pytest](agent-a6-mcp-pytest.txt), [IPC supplement](agent-a6-ipc-pytest.txt),
[all 194 failed test IDs](agent-a6-socket-blocked.txt).

## Acceptance limits

The implementation is reviewable and the host-only safety evidence is available.
Linux must rerun the real TCP/Unix daemon and netns cases before socket acceptance
can be green. Device usb0 binding, static armhf, measured calibration, trustworthy
window hints, physical A7 allow/confirmation UI and user-present action acceptance
remain open. Mac client registration and read-only status/screenshot were documented
but not performed. The pre-existing untracked prompt-agent-a6.txt was left untouched.

Final acceptance reread: compared the complete tracked diff and new HTTP/MCP/test
files with the A6 prompt, D6, plan §§3.4/3.6/3.7 and A6/§10. Re-ran make test
after that review: PASS with the counts above; nonsanitized host link PASS.
`git diff --check` and the path-scope assertion both PASS. No additional host
implementation finding remains from this first pass; the listed socket/device
acceptance failures and unrun gates remain open.
