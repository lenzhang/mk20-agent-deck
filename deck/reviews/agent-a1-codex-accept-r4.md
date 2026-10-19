# Agent A1 acceptance — round 4

**The reviewed box implementation passes. No blocker or major defect remains identified.** Device acceptance and production provider validation remain pending.

Reviewed commit `2ea809a39d7069cc021bd715f9ebee5d4f7f59e1`, A1 diff `d172921..2ea809a`, and r3 fix diff `3620415..2ea809a`. Review was read-only; no devices, serial ports, adb, or network were accessed. The worktree remained clean. Paths below are repository-relative.

**Verification evidence**

- The authoritative Linux result recorded in commit `2ea809a` is fully green: **24 simulator tests, 4,018 keyboard checks twice, real-binary e2e, 45 agent tests, 58 tools tests, and 546 main pytest tests**. This supplied result was not reproduced on macOS.
- Locally, all keyboard sources and `test_kbd.c`, adapter sources and `test_oai.c`, and vendored cJSON passed C11 `-Wall -Wextra -Werror` syntax checks. Vendored cJSON used the Makefile’s macOS deprecation-warning exception.
- Each changed shell script passed `sh -n`; all five changed Python files passed `ast.parse`.
- Independent read-only assertions confirmed descriptor byte equality and unchanged default daemon arguments against `d172921`. The pure `test_des_known_vector` assertions also passed directly.
- Local pytest attempts could not start because pytest is absent from both available Python environments. No dependencies were installed. Linux-only keyboard tests, linked builds, fixture tests, and local shellcheck were not run.
- Compiler-wrapper cache writes and a shell here-document temporary file were sandbox-blocked. Direct Clang with the installed SDK subsequently completed the syntax checks without generated files.
- `git diff --check 3620415..2ea809a` passed. Implementation paths also passed across the full A1 diff; the broader historical diff reports whitespace in vendored cJSON and archived review reports.

**Disposition of r3 findings**

| Finding | Disposition | Evidence |
|---|---|---|
| **R3-F1 — stale socket-dispatch timestamps** | **FIXED** | [`ctl.c:459`](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/ctl.c:459) samples the injectable monotonic clock, invokes keyboard safety and `ctl_tick`, and repeats if safety work advanced time. `read_client` calls it before every request and after dispatch, including the last request (`ctl.c:480,484`). Every client traverses this path (`:489`). Production installs the K2/ctl callback at `mk20_kbd.c:151`; callback implementation is `kbd.c:29`. |
| **R3-G1 — regression misses actual socket dispatch** | **FIXED** | [`test_ctl_dispatch_backpressure`, `tests_ctl.inc:540`](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/tests/tests_ctl.inc:540) exercises `ctl_on_poll`/`read_client`, concatenated requests, one/two clients, lease/K2 deadlines, held input, and actual pipe backpressure. Its fake clock advances from 990 to 1020 ms inside all-up’s retry path. It asserts rejection of subsequent key/pointer/renew requests and neutral-only HID output. `test_ctl_dispatch_deadlines` at `:597` additionally covers a safety check itself waiting across K2 expiry, plus confirmation/grant expiry. Both run in the full suite (`test_kbd.c:282`). |
| **R3-D1 — stale historical Linux status** | **FIXED** | [`AGENT-BUILD-PLAN.md:326`](/Users/dev/worktrees/mk20-agent-a1/deck/AGENT-BUILD-PLAN.md:326) now records the requested successful `b46c562` run, while retaining device/release prerequisites and Kimi follow-ups. The subsequent `2ea809a` result still needs incorporation; see the minor documentation finding below. |

The new regression reproduces the reported failure schedule: all-up starts before expiry, encounters backpressure, completes after expiry, and is followed by another request in the same read. Fresh post-dispatch safety now revokes/latches before that later request can emit input. Cross-client acquisition also uses the advanced time.

No r3 regression was found in callback initialization, client-drop handling, response bounds, default behavior, or GD32 output.

**Other previously open items**

| Item | Disposition | Evidence / remaining scope |
|---|---|---|
| R2-F2 production deadline ordering | **FIXED** | `kbd_service` checks safety before HID service (`kbd.c:38`); r3 closes the remaining socket-batch path above. |
| R2-F1, R2-G1, R2-G2; Kimi quote-return concern | **FIXED** | Nonblocking safety-enabled host forwarding (`kbd.c:61`), physical-before-host ordering (`:125`), stalled-host e2e (`e2e_run.py:69`), timer-only K2 e2e (`:335`), checked status quoting (`ctl.c:296`), and seeded parser tests (`tests_ctl.inc:725`, `test_oai.c:96`). |
| Kimi r3: `gd32io.c` 32-bit clock | **FIXED** | Clock, deadline, and remaining interval are now `int64_t`, `gd32io.c:44,53,55`. |
| Previously stale release binary | **FIXED for this revision** | The r3 diff changes the tracked `mk20-kbd` binary; the commit records its rebuild. Source matching remains enforced by `test_release_binaries_match_source`, `test_mk20d_router.py:141`. Final packaging/MANIFEST work remains separate. |
| Earlier incomplete full pytest run / rollback-test failure | **FIXED for the supplied run** | Current authoritative record reports all 546 main tests passing. This does not establish that an intermittent failure can never recur. |
| Real-binary HID-backpressure integration coverage | **PARTIAL** | Actual socket-dispatch/backpressure coverage now exists in C; it still does not launch the daemon process under HID backpressure. |
| K2 timeout-bound discrimination; malformed-test canary | **NOT FIXED** | Minor gaps detailed below. |
| Unused scroll marker; page show/layout/create rate limits | **NOT FIXED — explicitly deferred** | Recorded A7 follow-ups at `AGENT-BUILD-PLAN.md:381`. |
| Device scrolling, descriptor inspection, K2 capability and probation/rollback | **NOT FIXED — pending device acceptance** | Required by plan `:319`; outside this authorized read-only pass. |

Previously closed r1 fixes remain present, including frozen release flags, pointer-queue cancellation, disconnected-page reclamation, first-hold capability selection, strict adapter JSON parsing, missing-tool-call rejection, and corrected protocol invocation.

**Original specification items**

| Item | Result | Evidence and tests |
|---|---|---|
| **1. Release switches and descriptor agreement** | **PASS** | Validation/defaults/overrides in `mk20lib.sh:46,73,102`; resolved scroll exported at `:107`. Boot freezes all four switches across the supervisor boundary (`mk20d:220`); supervisor resets and constructs optional arguments at `:336`. `test_release_switches`, `test_boot_supervisor_flags_and_descriptor`, and `test_default_descriptor_bytes` (`test_agent_release.py:23,54,91`) cover defaults, flags, overrides, configuration changes after setup, and descriptor agreement. Gadget fragments use `scope_gadget_fn` (`:38`). |
| **2. `confirm.request`** | **PASS** | Validation, gating, singleton pending state and immediate reply at `ctl.c:327`; shown-once guard and keyboard decisions at `:182`; timeout/cancellation at `:173,195,215,222,410`; poll deadline at `:229`. `test_ctl_confirm`, `tests_ctl.inc:201`, includes before-shown OK, 299/300 ms, cancel, timeout, disable, stop, requester loss, and field bounds. |
| **3. `ui.agent`** | **PASS** | Validated state/input/summary/progress cache at `ctl.c:308`; status at `:297`; dirty consumption at `:181`. `test_ctl_ui`, `tests_ctl.inc:250`, covers allowed-state gating, all states, invalid values, and dirty-bit behavior. |
| **4. D8 screen protocol/rules** | **PASS** | Layout, connection ownership, grants, reservations, geometry, and sliding quotas in `ctlscr.c:5,22,31,41,72`. Draw/text return `rendered:false` at `:123`. `test_ctl_screen`, `test_ctl_edges`, `test_ctl_screen_disconnect`, and `test_ctl_long_uptime` cover requested rules. Both build source lists include the modules (`kbd/Makefile:2`, `mk20-build-bins.sh:15`). Rendering remains A7. |
| **5. Flagged K2 e-stop** | **PASS** | Hold/triple modes, release learning and rotation exclusion in `k2estop.c:3`; explicit release capability initialized at `mk20_kbd.c:114`. `test_k2_estop` (`tests_ctl.inc:363`), socket deadline regressions, timer-only first-hold e2e, and stalled-host e2e cover the implementation. Physical firmware capability remains unmeasured. |
| **6. Protocol, README and plan status** | **PARTIAL** | `CTL-PROTOCOL.md` covers implemented framing, permissions, operations, errors, events, timing, keyboard-only APIs and flags; r3 timing behavior is documented at `:68`. README status is at `agent/README.md:3`. Plan status now contains the historical run but still describes current Linux/rebuild verification as pending. Minor documentation update required. |
| **7. OpenAI-compatible C adapter skeleton** | **PASS** | Bounded body/image/tools generation (`oai.c:118,134`), endpoint and separate header builders (`:185,195`), strict response/argument parsing and typed errors (`:14,211`), coordinates (`:252`). Sanitized C/CLI builds are wired through `agent/Makefile:12`; pytest builds/runs C tests at `test_oai.py:18`; `make agent-test` invokes the suite (`deck/Makefile:17`). Missing-compiler skip is explicit. |
| **8. Chinese clipboard plan only** | **PASS** | `AGENT-BUILD-PLAN.md:337` documents classic Latin-1 versus Extended Clipboard UTF-8, `0xC0A1E5CE`, unknown macOS support requiring measurement, leased HID paste, constrained optional restoration, no clipboard readback, and no Mac helper. No clipboard implementation was added. |

**Plan A1 acceptance and applicable A4 skeleton criteria**

| Criterion | Result | Evidence |
|---|---|---|
| UID-authenticated 0600 socket; at most two clients | **PASS** | `ctl.c:392,435`; `test_ctl_socket`, `tests_ctl.inc:157`, checks mode, client limit, framing and disconnect behavior. UID rejection is source-verified at `ctl.c:449`. |
| Connection-bound lease, renewal, sequence checks and HID action mapping | **PASS** | `ctl.c:246,256,350,367`; `test_ctl_lease`, `test_ctl_scroll_act`, and new dispatch regressions. |
| Lease expiry produces all-up without flushing expired actions first | **PASS** | `ctl.c:222`; `test_kbd_safety_backpressure`, `tests_ctl.inc:480`; `test_ctl_dispatch_backpressure`, `:540`; ordinary expiry e2e, `e2e_run.py:324`. |
| E-stop → all-up within 100 ms, including stalled host | **PASS** | Ordinary e2e times keyboard and pointer release (`e2e_run.py:300`). `stalled_host_safety` at `:69` asserts the bound with writable HID for socket, reserved key, human preemption and K2 triple; K2 hold allows its required 1000 ms plus 100 ms. Included in supplied green e2e. |
| Act rejected after e-stop | **PASS** | `ctl.c:250`; `test_ctl_lease`, `tests_ctl.inc:124`; dispatch regression also rejects actions after overdue K2 latching. |
| Physical/PTT press preempts agent | **PASS** | Revocation precedes forwarding and PTT HID mutation (`kbd.c:131,140`); `test_ctl_lease`, existing PTT coverage (`tests_k3.inc:460`), and stalled-host human e2e. |
| Confirmation below 300 ms ineffective | **PASS** | `ctl.c:191`; exact before-shown, 299 ms and 300 ms assertions, `tests_ctl.inc:212`. |
| Agent UI state cache | **PASS** | `ctl.c:308`; `test_ctl_ui`. |
| Wheel/AC Pan descriptor and matching report format | **PASS** | `gadget.sh:408`; six/eight-byte format tests in `test_hid_scroll`, `tests_ctl.inc:24`; real boot/argv agreement in `test_agent_release.py:54`. Endpoint report length remains 9. |
| HID rebind queues/fails without spinning | **PASS** | Backoff and poll gating in `hid.c:106,146`; `test_hid`, `test_hid_replace`, and existing real-binary rebind e2e. |
| D8 reservations, grants, quotas and owner-only key events | **PASS** | `ctlscr.c:22,31,41,96`; `test_ctl_screen`, `test_ctl_edges`, and long-uptime tests. |
| Box tests all pass | **PASS** | Authoritative `2ea809a` full-run record. |
| Original keyboard acceptance does not regress | **PARTIAL** | Existing Linux keyboard/e2e suites pass and default contracts match; physical keyboard validation remains pending. |
| Real scrolling, descriptor inspection, probation/rollback; screen latency budget | **PARTIAL** | Device-dependent criteria remain unmeasured. A1 currently validates screen metadata without rendering. |
| §3.4 `hid.health` push event | **PARTIAL** | Not implemented; explicitly acknowledged at `CTL-PROTOCOL.md:180`. Status polling exists. See minor follow-up below. |
| A4 configurable chat/completions, one image, system/user prompts and tools | **PASS** | `oai.h:15`, `oai.c:134,185`; `request_test`, `test_oai.c:10`; `test_request_and_header_separate`, `test_oai.py:50`. |
| A4 malformed/oversized response and missing tool calls | **PASS** | `oai.c:88,230`; `strict_json_test`, `test_response_bounds`, `test_missing_tool_calls`, and seeded malformed-input tests. Invalid argument JSON yields a per-call error. |
| A4 typed HTTP/provider errors | **PASS** | `oai.c:211`; `response_test` and `test_typed_errors`, `test_oai.py:87`. |
| A4 coordinate conversion and out-of-range refusal | **PASS** | `oai.c:252`; `coordinate_test`, `test_oai.c:82`. |
| A4 credential exclusion from request body | **PASS** | Request structure deliberately has no key field (`oai.h:19`); header builder is separate (`oai.c:195`). Body-exclusion assertions at `test_oai.py:55,167`. |

Real-provider 8/10 localization benchmarks, production secret-file ownership/mode enforcement, transport/TLS/proxy, retries and cancellation remain outside this offline skeleton.

**Required invariants**

| Invariant | Result |
|---|---|
| **Defaults unchanged** | **PASS.** New switches/options default off (`mk20lib.sh:73`, `mk20_kbd.c:242`). Independent comparison against `d172921` confirmed identical **119-byte default descriptor**, identical scroll descriptor, and identical default `mk20d` keyboard command after expanding empty `"$@"`. Legacy host-send waiting remains on the no-safety-flags path (`kbd.c:61`). |
| **Descriptor and daemon agree** | **PASS in the release-managed path.** One resolved value feeds gadget setup, saved gadget environment and supervisor arguments (`mk20lib.sh:107`, `mk20d:199,220,338`). Post-setup configuration mutation is tested. |
| **Socket cannot bypass or clear e-stop** | **PASS.** Socket operations cannot reach keyboard-only `ctl_set_allowed(...,1)` (`ctl.c:215`). Act/acquire/UI/confirm/screen paths remain gated. Reconnection preserves the latch; release scripts never pass `--ctl-allow`. |
| **Pending actions are cancelled on revocation** | **PASS.** `hid_release_all` purges pointer/scroll work before neutral writes (`hid.c:199,219`), including queued and last-sent button state. `test_ctl_backpressure` covers seven cancellation paths; new dispatch tests cover deadline ordering. |
| **Confirmation honors ≥300 ms** | **PASS.** Before-shown and 299 ms OK are ignored; exactly 300 ms succeeds. |
| **API key never enters request body** | **PASS for the implemented credential path.** Body/header interfaces are separate and explicitly tested. CLI header mode is a documented fixture tool, not production logging. |
| **No new GD32 write beyond allow-list** | **PASS.** `gd32.c` and its allow-list are unchanged. The only `gd32io.c` change widens timing types. Existing output remains `05 00 r r 00 00`, slots 100–105 (`gd32.c:108,115`; `gd32io.c:24,88`). K2 consumes events only. |

**Remaining defects and test gaps — all minor**

| Finding | Precise fix |
|---|---|
| **R4-D1 — current validation status is stale.** `AGENT-BUILD-PLAN.md:327` still says this round needs Linux verification and binary rebuilding despite the supplied `2ea809a` record. | Record the current **24 + 4018×2 + e2e + 45 + 58 + 546** result and rebuilt binary; retain genuinely pending device and packaging prerequisites. |
| **R4-G1 — K2 e2e does not isolate production timeout selection.** Its 0.95–1.10 s window (`e2e_run.py:359`) can pass using the ordinary 100 ms HID tick. | Factor production poll-timeout selection into a shared helper and test that an active K2 deadline reduces a 250 ms base timeout to 1 ms at hold age 999 ms. Use that helper in `run()`. |
| **R4-G2 — HID-backpressure coverage stops at C integration.** New tests exercise real socket dispatch, but not a launched daemon. | Add a real-binary FIFO-backpressure case spanning lease/K2 expiry; unblock HID and assert neutral-only output and rejection of subsequent batched actions. |
| **R4-G3 — malformed-control test checks one canary byte.** `tests_ctl.inc:744` cannot detect an overwrite that skips that byte but stays inside the allocation. | Check the complete untouched tail beginning at `rep[cap]`, alongside sanitizer coverage. |
| **R4-D2 — `hid.health` plan/protocol mismatch.** Plan §3.4 lists the event; protocol documentation says it is absent. | Explicitly schedule it in the plan, or implement and test transition notifications for HID availability/rebind state. |
| **Deferred A7 marker and operation-rate follow-ups.** `gadget.sh:414` retains an unused marker; only draw/text consume screen quotas. | Remove or document the observational marker; define and test appropriate show/layout/create limits when rendering is integrated, as recorded in plan `:381`. |
| **Device acceptance remains open.** | Complete planned descriptor, scrolling, K2 capability, latency and probation/rollback validation before release acceptance. |

These minor findings and explicitly deferred stages do not fail this box acceptance round.

ACCEPTANCE: PASS