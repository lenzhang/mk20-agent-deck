# agent A1 codex acceptance r2 (gpt-6.1-sol xhigh, read-only, 2026-10-19 16:31 +0800; route AI=US-Exit-Hy2 exit=203.0.113.10; tree = 10a1374; tests = box log rc=0)

**Round 2 acceptance: FAIL.** The r1 implementation fixes are present, including both original blockers. Two production-loop defects remain: host output can delay e-stop beyond 100 ms, and HID queues can flush before expired safety deadlines are processed.

This review changed no files and accessed no devices, serial ports, adb, or network. Paths below are relative to this worktree.

Verification evidence:

- Authoritative Linux run: **rc=0**, **2,265 keyboard checks** under ASan/UBSan and again under TSan; real-binary e2e passed, measuring ordinary socket e-stop → all-up at **1.1 ms**. [Linux log:7](/Users/dev/worktrees/mk20-a1-boxtest2.log:7), [log:29](/Users/dev/worktrees/mk20-a1-boxtest2.log:29), [log:49](/Users/dev/worktrees/mk20-a1-boxtest2.log:49), [log:51](/Users/dev/worktrees/mk20-a1-boxtest2.log:51).
- Linux: **24 simulator, 45 agent, 58 tools, and 546 main pytest tests passed**; shellcheck passed. The supplied log reports **546**, rather than the prompt’s 541. [Log:4](/Users/dev/worktrees/mk20-a1-boxtest2.log:4), [log:56](/Users/dev/worktrees/mk20-a1-boxtest2.log:56), [log:57](/Users/dev/worktrees/mk20-a1-boxtest2.log:57), [log:62](/Users/dev/worktrees/mk20-a1-boxtest2.log:62), [log:72](/Users/dev/worktrees/mk20-a1-boxtest2.log:72), [log:77](/Users/dev/worktrees/mk20-a1-boxtest2.log:77).
- This pass: existing host binaries passed **571 control checks** (`test_kbd --ctl-unit`) and **1,097 adapter checks** (`make -C deck/agent test`). These were prebuilt binaries, not fresh linked builds. Make reported sandbox-blocked compiler-cache writes.
- Current changed keyboard and adapter sources passed C11 `-Wall -Wextra -Werror` syntax checks using direct Clang with the installed SDK. Changed shell scripts passed syntax checks; `git diff --check` passed.
- Full Linux keyboard tests, pytest HTTP fixtures, and device validation were not rerun here.

The eight specification items assess as follows:

| Item | Result | Evidence |
|---|---|---|
| **1. Release wiring** | **PASS** | All four switches default to zero and validate boolean configuration/overrides: [mk20lib.sh:46](/Users/dev/worktrees/mk20-agent-a1/deck/firmware/release/share/mk20lib.sh:46), [mk20lib.sh:73](/Users/dev/worktrees/mk20-agent-a1/deck/firmware/release/share/mk20lib.sh:73). Boot explicitly carries resolved values into the fresh supervisor shell: [mk20d:220](/Users/dev/worktrees/mk20-agent-a1/deck/firmware/release/sbin/mk20d:220). The supervisor constructs flags without reloading changed configuration: [mk20d:335](/Users/dev/worktrees/mk20-agent-a1/deck/firmware/release/sbin/mk20d:335). `test_boot_supervisor_flags_and_descriptor` exercises the real process boundary, overrides, post-setup configuration changes, exact default argv and descriptor agreement: [test_agent_release.py:42](/Users/dev/worktrees/mk20-agent-a1/deck/tests/test_agent_release.py:42). Linux main pytest passed, log:72; shellcheck, log:57. |
| **2. `confirm.request`** | **PASS** | Allowed/e-stop gating, singleton pending request, validation and immediate reply: [ctl.c:320](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/ctl.c:320). Shown-once guard, ignored early OK, cancellation and exact deadline handling: [ctl.c:167](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/ctl.c:167). Poll timeout includes confirmation: [ctl.c:223](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/ctl.c:223). `test_ctl_confirm` covers before-shown, 299/300 ms, timeout, disable, e-stop, disconnect and validation: [tests_ctl.inc:201](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/tests/tests_ctl.inc:201). Linux log:29/49. |
| **3. `ui.agent`** | **PASS** | Required fields, supported states, input, summary and progress are validated; changed values set the consumed dirty bit: [ctl.c:301](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/ctl.c:301). Status exposes the cache at ctl.c:288. `test_ctl_ui`: [tests_ctl.inc:250](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/tests/tests_ctl.inc:250); Linux log:29/49. |
| **4. D8 screen protocol/rules** | **PASS** | Layout, ownership, reservations, grants, geometry and sliding limits: [ctlscr.c:53](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/ctlscr.c:53), [ctlscr.c:72](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/ctlscr.c:72). Draw/text return `rendered:false`; disconnect now frees connection-owned pages at ctlscr.c:15. `test_ctl_screen`, `test_ctl_edges`, `test_ctl_screen_disconnect` and `test_ctl_long_uptime` cover the rules and reclamation: tests_ctl.inc:290,392,478,528; Linux log:29/49. Build source lists include both new modules: [kbd/Makefile:2](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/Makefile:2), [mk20-build-bins.sh:15](/Users/dev/worktrees/mk20-agent-a1/deck/tools/mk20-build-bins.sh:15). |
| **5. Flagged K2 e-stop** | **PARTIAL** | Hold/triple state machines, rotation exclusion, release learning and explicit cold-start release capability are implemented: [k2estop.c:3](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/k2estop.c:3), [mk20_kbd.c:114](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/mk20_kbd.c:114). `test_k2_estop` covers boundaries; real-binary cold-start hold coverage exists at [e2e_run.py:255](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/tests/e2e_run.py:255), included in log:52. Production safety processing remains subject to **R2-F1/R2-F2** below. Firmware capability measurement remains pending. |
| **6. Protocol/docs/status** | **PASS** | The protocol documents implemented framing, limits, operations, errors, events, keyboard-only APIs, timing, flags and rendering deferral: [CTL-PROTOCOL.md:3](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/CTL-PROTOCOL.md:3). README and A1 status were updated: [README.md:3](/Users/dev/worktrees/mk20-agent-a1/deck/agent/README.md:3), [plan:322](/Users/dev/worktrees/mk20-agent-a1/deck/AGENT-BUILD-PLAN.md:322). Documentation does not resolve the production timing defects below. |
| **7. OpenAI-compatible adapter skeleton** | **PASS** | Bounded request/image/tools construction, endpoint/header separation, strict response/argument parsing, typed errors and coordinate conversion: [oai.c:134](/Users/dev/worktrees/mk20-agent-a1/deck/agent/src/oai.c:134), [oai.c:185](/Users/dev/worktrees/mk20-agent-a1/deck/agent/src/oai.c:185), [oai.c:211](/Users/dev/worktrees/mk20-agent-a1/deck/agent/src/oai.c:211). Sanitizer C tests and CLI pytest are wired through [agent/Makefile:1](/Users/dev/worktrees/mk20-agent-a1/deck/agent/Makefile:1) and [deck/Makefile:17](/Users/dev/worktrees/mk20-agent-a1/deck/Makefile:17). `test_fake_model_http` covers tool calls, content, 429, malformed bodies and credential separation: [test_oai.py:145](/Users/dev/worktrees/mk20-agent-a1/deck/agent/tests/test_oai.py:145). Linux agent suite: log:56. |
| **8. Chinese clipboard, plan only** | **PASS** | [Plan:335](/Users/dev/worktrees/mk20-agent-a1/deck/AGENT-BUILD-PLAN.md:335) records classic Latin-1 versus Extended Clipboard UTF-8, `0xC0A1E5CE`, unknown macOS support requiring measurement, leased HID paste, constrained optional restoration, no clipboard readback and no Mac helper. No clipboard implementation was added. |

The plan’s A1 criteria and applicable A4 skeleton criteria assess as follows:

| Criterion | Result | Evidence |
|---|---|---|
| Control socket, lease TTL/revocation and `act` mapping | **PARTIAL** | Authentication, connection-bound tokens, sequence checks and action validation are implemented at ctl.c:240,343,360,428. `test_ctl_lease`, `test_ctl_scroll_act`, `test_ctl_socket` pass, Linux log:29/49. Production expiry ordering remains defective (**R2-F2**). |
| Wheel/AC Pan descriptor and matching reports | **PASS** | Release descriptor/argv agreement is checked by `test_boot_supervisor_flags_and_descriptor`, test_agent_release.py:54. `test_hid_scroll` verifies six-byte default and eight-byte scroll reports, tests_ctl.inc:24. HID endpoint `report_length` remains 9. Linux log:29/49/72. |
| Lease expiry produces all-up safely | **FAIL** | Direct expiry and cancellation tests pass, tests_ctl.inc:100,433; ordinary real-binary expiry passes, e2e_run.py:244. The real loop can flush expired queued actions first (**R2-F2**). |
| E-stop → all-up ≤100 ms | **FAIL** | Quiet-path measurement is 1.1 ms, log:51. Its binary runs with `--no-host`, e2e_run.py:195. The enabled host path can wait 200 ms before servicing e-stop (**R2-F1**). |
| Subsequent `act` rejected after e-stop | **PASS** | Latch check precedes lease use at ctl.c:244. `test_ctl_lease` rejects act/acquire and socket `allow`, tests_ctl.inc:120. Linux log:29/49. |
| PTT press preempts agent | **PARTIAL** | Revocation precedes the PTT HID edge at kbd.c:117–123; ordinary preemption/PTT coverage passes. Host forwarding precedes both and can delay preemption, kbd.c:110 (**R2-F1**). |
| Confirmation below 300 ms ineffective | **PASS** | Explicit before-shown and 299/300 ms assertions, tests_ctl.inc:212–215; Linux log:29/49. |
| `ui.agent` cache | **PASS** | ctl.c:301; `test_ctl_ui`, tests_ctl.inc:250; Linux log:29/49. |
| HID rebind queues/fails without spinning | **PASS** | Backoff and poll gating at hid.c:105,145. `test_hid`, `test_hid_replace` and real-binary rebind coverage, e2e_run.py:138; Linux log:29/49/52. Safety deadline ordering is assessed separately above. |
| D8 rule-engine acceptance | **PASS** | Reserved targets, grants, per-fb/global rollover, disabled access and owner-only active-page events are covered by `test_ctl_screen`/`test_ctl_edges`, tests_ctl.inc:290,392; Linux log:29/49. Rendering remains A7. |
| Box tests all pass | **PASS** | Complete green authoritative run, log:77. Passing tests omit the two production-loop scenarios below. |
| Original keyboard acceptance does not regress | **PARTIAL** | Existing Linux keyboard/e2e coverage passes, log:29/49/52, and default contracts are preserved. Device behavior has not been verified. |
| Device descriptor inspection, real scrolling, probation/rollback | **PARTIAL** | Required by plan:319–320; no device evidence supplied or collected. |
| A4 single configurable adapter, tools and base64 image input | **PASS** | oai.h:15; oai.c:134,185. `request_test` and `test_request_and_header_separate`, test_oai.c:9 and test_oai.py:50; Linux log:56. |
| A4 malformed/oversized response and missing tool-call cases | **PASS** | Strict bounded scanner, oai.c:14–97; missing-call check, oai.c:230. `strict_json_test`, `test_response_bounds`, `test_strict_response_and_tool_arguments`, `test_missing_tool_calls`; Linux log:56. |
| A4 coordinate conversion and out-of-range rejection | **PASS** | oai.c:252; endpoint, invalid-range and round-trip assertions in `coordinate_test`, test_oai.c:81; Linux log:56. |
| A4 typed HTTP/provider errors | **PASS** | oai.c:211; `response_test` and `test_typed_errors`, test_oai.c:33/test_oai.py:87; Linux log:56. |
| A4 credential exclusion from request body | **PASS** | Request type has no key field, oai.h:15; authorization builder is separate, oai.c:195. Explicit body-exclusion assertions at test_oai.py:55,167; Linux log:56. |

Real-provider 8/10 localization benchmarks, production secret-file permissions/scrubbing, proxy/TLS transport and request cancellation remain outside this offline skeleton’s delivered scope.

The explicitly requested invariants assess as follows:

| Invariant | Result |
|---|---|
| **No new flags preserve defaults** | **PASS.** New options initialize off at mk20_kbd.c:230; release switches initialize off at mk20lib.sh:73. Read-only comparison against HEAD confirmed the **119-byte default descriptor is identical**, and the binary launch command is identical after expansion of the empty `"$@"`. Default report length remains six bytes. Golden descriptor/default argv regressions: test_agent_release.py:71,91. |
| **Descriptor and daemon use the same resolved scroll value** | **PASS for the release boot/supervisor path.** mk20lib.sh:107 exports it; mk20d:199 persists it and mk20d:220 passes it to the supervisor. The real-path regression also changes configuration after gadget setup, test_agent_release.py:54. |
| **Socket cannot bypass or clear the e-stop latch** | **PASS.** No socket allow/grant/confirmation-result injection operation exists. Only keyboard-side `ctl_set_allowed(...,1)` clears the latch, ctl.c:209. Reconnect does not clear it. Act/acquire/ui/screen/confirm paths remain gated. **Unconditional response timing fails separately under R2-F1/R2-F2.** |
| **Revocation purges queued pointer/scroll work** | **PASS once revocation executes.** `pointer_cancel` discards queued actions and preserves a neutral report when queued/desired/sent state requires one, hid.c:198. `test_ctl_backpressure` covers seven cancellation paths and both sent-button states, tests_ctl.inc:433. Production deadline processing can execute too late (**R2-F2**). |
| **Confirmation honors ≥300 ms** | **PASS.** ctl.c:180; exact before-shown/299/300 ms tests, tests_ctl.inc:212. |
| **API credential never enters the request body** | **PASS for the implemented credential path.** Separate request/header APIs and explicit FakeModel body assertions, oai.h:15/test_oai.py:167. |
| **No new GD32 write beyond the allow-listed knob payload** | **PASS.** GD32 writer/allow-list files and tracked release binaries have no diff. The gate still permits only `05 00 r r 00 00`, slots 100–105: [gd32.c:115](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/gd32.c:115), [gd32io.c:24](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/gd32io.c:24). New K2 code only consumes inbound events. `test_gd32_allowlist` is included in Linux log:29/49. |

Each Codex r1 finding:

| Finding | Status | Evidence |
|---|---|---|
| **F1: release flags lost across process boundary** | **FIXED** | Explicit boot environment at mk20d:220–223; supervisor consumption at :335–346. Real boot→supervisor regression, test_agent_release.py:54, including configuration mutation and descriptor agreement. Linux log:72. |
| **F2: queued pointer actions survive revocation** | **FIXED at revocation** | `pointer_cancel` and last-sent tracking, hid.c:55,198,218; shared `lease_revoke` uses `hid_release_all`, ctl.c:156. Nonblocking-pipe regression covers every requested revoke path, tests_ctl.inc:433. **R2-F2 is a remaining production ordering defect before revocation executes.** |
| **F3: disconnected pages exhaust pool** | **FIXED** | Connection pages are zeroed at ctlscr.c:15. Eight creates→disconnect→reconnect→create regression, tests_ctl.inc:478. Protocol updated at CTL-PROTOCOL.md:99. |
| **F4: first K2 hold cannot stop production** | **FIXED** | `--k2-release` initializes release capability at mk20_kbd.c:114,250; release switch is wired. Real-binary first-hold test, e2e_run.py:255; Linux log:52. Loaded-loop timing remains a separate issue. |
| **F5: permissive malformed JSON accepted** | **FIXED** | Strict grammar validation before cJSON, oai.c:14–97. Numeric/control/trailing-garbage regressions in test_oai.c:56 and test_oai.py:127; Linux log:56. |
| **F6: missing/empty tool calls accepted** | **FIXED** | oai.c:230; absent/empty/null coverage at test_oai.c:73 and test_oai.py:138; Linux log:56. |
| **F7: invalid documented invocation** | **FIXED** | CTL-PROTOCOL.md:3 now includes `--run`. |

Each Kimi r1 finding:

| Finding | Status | Evidence |
|---|---|---|
| **1: supervisor configuration/scroll mismatch** | **FIXED** | Same evidence as Codex F1. |
| **2: release tests miss process boundary** | **FIXED** | `test_boot_supervisor_flags_and_descriptor`, test_agent_release.py:54, drives real boot and supervisor shells; flags are not injected as `FEATURE_*` into an extracted argv fragment. |
| **3: disconnected-page leak** | **FIXED** | ctlscr.c:15; `test_ctl_screen_disconnect`, tests_ctl.inc:478. |
| **4: control bytes accepted as hex digits** | **FIXED** | Explicit ASCII ranges at ctl.c:17; every escape position tested with bytes `0x10..0x19`, tests_ctl.inc:502. |
| **5: 32-bit millisecond overflow** | **FIXED** | Monotonic clocks, deadlines and stored timestamps use `int64_t`: mk20_kbd.c:83, ctl.h:37, ctlscr.h:24, k2estop.h:9, hid.h:45. `test_ctl_long_uptime` crosses `2^31` for lease, confirm, grants, rate windows and K2, tests_ctl.inc:528; Linux log:29/49. |
| **6: stale read-only second-client promise** | **FIXED** | Plan:168 explicitly states equal permissions after UID authentication and connection-bound leases/pages. |
| **7: test-cap/boundary/coverage gaps** | **PARTIAL** | `H()` now uses `CTL_LINE_MAX`, tests_ctl.inc:57. Surrogate, integer, hex-control and key-file regressions exist at tests_ctl.inc:493/test_oai.py:115. Real boot coverage checks directory creation and flags, test_agent_release.py:65–85. Randomized malformed-input coverage is still absent; production-loop gaps remain below. |
| **8: unused scroll marker** | **NOT FIXED — explicitly deferred** | Marker remains; A7 follow-up recorded at plan:379, as instructed by the fix task. |
| **9: undocumented 24-key cap** | **FIXED** | CTL-PROTOCOL.md:15; 24/25-key regression, tests_ctl.inc:518. |
| **10: negative request IDs** | **FIXED** | ctl.c:286 rejects supplied negative/invalid IDs; tests_ctl.inc:512 and CTL-PROTOCOL.md:20. |
| **11: trailing-LF key files rejected** | **FIXED** | Exactly one LF stripped at oai_cli.c:74; empty/double-LF/CRLF/injected/maximum-length cases, test_oai.py:115. |
| **12: page show/layout/create rate limits** | **NOT FIXED — explicitly deferred** | Current quotas cover draw/text; A7 evaluation recorded at plan:379, as instructed. |
| **13: quote return/socket-directory handling** | **PARTIAL** | Directory prerequisite is documented at CTL-PROTOCOL.md:5; real boot regression checks creation, test_agent_release.py:79. `ctl_json_quote` return remains unchecked at ctl.c:289; current validated summary bounds make its buffer sufficient. |

Concrete remaining defects and test gaps:

1. **R2-F1 — major: host output can delay e-stop beyond 100 ms.**

   `kbd_host_send` waits on `POLLOUT` for up to **200 ms** on a newly stalled host connection: [kbd.c:45](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/kbd.c:45), [kbd.h:58](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/kbd.h:58). Physical events are forwarded through that function **before** K2, human preemption and reserved-page e-stop handling: [kbd.c:110](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/kbd.c:110). The main loop also processes host frames before socket requests: [mk20_kbd.c:187](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/mk20_kbd.c:187).

   Consequently, a ready socket e-stop or physical stop event can wait behind a 200 ms host send, already exceeding plan:318. This conclusion follows from the control flow; it was not measured in this sandbox.

   The 1.1 ms acceptance test runs with `--no-host` at e2e_run.py:195. Existing host-stall coverage permits **1.5 seconds** for PTT release at e2e_run.py:168–171.

   **Fix:** when control/K2 safety is active, process physical safety before host notification and replace blocking host transmission with bounded nonblocking work. Service control requests/timers between bounded host batches. Preserve the default path if required. Add real-binary tests with an initially stalled host, writable HID and held agent input; assert socket and physical e-stop all-up within 100 ms.

2. **R2-F2 — major: expired actions can flush before lease/K2 deadlines are checked.**

   At loop entry, `hid_service` runs before `ctl_tick` and `k2_estop_tick`: [mk20_kbd.c:167](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/mk20_kbd.c:167). After poll, the HID-writable branch flushes again without first checking elapsed safety deadlines: [mk20_kbd.c:186](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/mk20_kbd.c:186).

   If HID becomes writable after a lease expires, queued click/scroll reports can therefore be transmitted before expiry revocation purges them. The same ordering permits queued input before an already-due K2 hold stop is latched.

   `test_ctl_backpressure` invokes `ctl_tick` **before** draining and servicing HID—tests_ctl.inc:449,458–459—so it verifies cancellation in the safer order rather than production order.

   **Fix:** evaluate fresh lease/K2 deadlines before every HID queue flush, including immediately after poll and after work that can consume time. Add a production-loop regression that holds HID backpressure across lease expiry, then unblocks it; only neutral/release reports may follow. Cover an elapsed K2 hold deadline similarly.

3. **R2-G1 — minor: the K2 e2e test does not isolate timer-only wakeup.**

   The cold-start test repeatedly sends `status` requests while waiting: [e2e_run.py:269](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/tests/e2e_run.py:269). Those requests wake the loop, so this test alone would not detect a missing K2 poll-timeout bound. Current source does contain the bound at mk20_kbd.c:177.

   **Fix:** add a hold test that sends no requests or further key events after K2 down and observes the asynchronous e-stop event/all-up report within the expected interval.

4. **R2-G2 — minor: optional hostile-input/defensive coverage from Kimi r1 remains incomplete.**

   Deterministic parser regressions are substantially improved, but no randomized control/adapter malformed-input harness was added. The status path also still ignores `ctl_json_quote` failure at ctl.c:289, although current bounds make failure unreachable.

   **Fix:** add bounded seeded malformed-byte/property coverage under sanitizers, respecting the protocol’s documented restrictions; check the quote result or document/assert the buffer-size invariant.

Device scrolling, descriptor inspection, K2 firmware capability measurement and probation remain pending under the requested no-device constraint. The acceptance failure here is independently justified by **R2-F1 and R2-F2**.

ACCEPTANCE: FAIL