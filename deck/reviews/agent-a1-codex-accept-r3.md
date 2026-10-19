# agent A1 codex acceptance r3 (gpt-6.1-sol xhigh, read-only, 2026-10-19 17:32 +0800; route AI=US-Exit-Hy2 exit=203.0.113.10; tree = b46c562; tests = box run rc=0 fully green)

**Round 3 acceptance: FAIL.** The stalled-host fixes and new regressions address R2-F1, R2-G1, and R2-G2. **R2-F2 remains partial:** socket dispatch can execute several requests using a stale timestamp, allowing HID output after a lease or K2 deadline.

Review was read-only. No files were changed and no devices, serial ports, adb, or network were accessed. Paths below are relative to the repository. `L<number>` refers to [../mk20-a1-boxtest3.log](/Users/dev/worktrees/mk20-a1-boxtest3.log).

**Verification evidence**

- Authoritative Linux run: **rc=0**; **3,829 keyboard checks** under ASan/UBSan and again under TSan; real-binary e2e passed. L26, L46, L54, L79.
- Measured all-up latency: ordinary socket stop **1.1 ms**; initially stalled host: socket **0.2 ms**, reserved key **0.0 ms**, human preemption **0.2 ms**, K2 triple **0.0 ms**, K2 hold **1000.9 ms from down**, including its required 1000 ms hold. L48–53.
- Linux: **24 simulator, 45 agent, 58 tools, and 546 main pytest tests passed**. The supplied log reports 546, not the prompt’s 541. Shellcheck passed. L4, L58–64, L74.
- This pass: current keyboard and adapter sources passed C11 `-Wall -Wextra -Werror` syntax checks; changed shell scripts passed `sh -n`; changed Python tests passed `ast.parse`; `git diff --check` passed.
- Linux execution was not repeated on macOS. Sandbox restrictions blocked compiler-helper cache writes; direct Clang syntax checks succeeded. No test builds or fixtures requiring writes/network were run here.

**Disposition of r2 findings**

| Finding | Disposition | Evidence |
|---|---|---|
| **R2-F1: host forwarding delays safety** | **FIXED** | Safety-enabled host sends return immediately on backpressure and count dropped output: `deck/kbd/kbd.c:50–71`. Physical safety precedes forwarding at `:123–135`; control precedes host work at `deck/kbd/mk20_kbd.c:181–205`. `test_kbd_safety_host`, `tests_ctl.inc:516`; real-binary `stalled_host_safety`, `e2e_run.py:69`. L49–53. |
| **R2-F2: expired actions flush before deadline processing** | **PARTIAL** | `kbd_service` checks safety before `hid_service`, `kbd.c:29–39`; loop entry, post-poll and post-host paths use it, `mk20_kbd.c:169,189,205`. `test_kbd_safety_backpressure`, `tests_ctl.inc:480`, covers overdue lease/K2 with writable HID. **Socket request batches still bypass this protection; see R3-F1 below.** |
| **R2-G1: K2 test relies on status traffic** | **FIXED** | Cold-start hold sends no requests or further GD32 events after down, then observes both asynchronous stop and all-up within 1.1 s: `e2e_run.py:335–361`. Included in successful e2e, L54. |
| **R2-G2: unchecked quote result and missing randomized malformed-input tests** | **FIXED** | Quote failure produces an error at `ctl.c:290`; regression at `tests_ctl.inc:581`. Seeded 512-iteration tests: `test_ctl_malformed_seeded`, `tests_ctl.inc:591`, and `malformed_seeded_test`, `deck/agent/tests/test_oai.c:96`. Invoked by sanitizer suites; L26, L46, L58. |
| **Kimi r2 nit 1: unchecked status quoting** | **FIXED** | Same quote check and defensive regression above. |
| **Kimi r2 nit 2: release binary predates sources** | **PARTIAL** | The supplied Linux suite passes, including its binary-match test (`deck/tests/test_mk20d_router.py:141`; L74). This worktree’s tracked binaries remain unchanged, as required; its `mk20-kbd` still contains the older usage without K2 options. Release artifacts must be rebuilt and checked separately before packaging. |

Kimi’s additional randomized-input gap is closed. Integrated real-binary HID-backpressure coverage remains absent. Device K2 capability measurement remains explicitly deferred; shellcheck uncertainty is resolved by L59–60. The intentionally deferred marker cleanup and additional screen-operation rate limits remain recorded at `deck/AGENT-BUILD-PLAN.md:379`.

**Original specification items 1–8**

| Item | Result | Evidence |
|---|---|---|
| **1. Release wiring** | **PASS** | Boolean validation, off-defaults and environment overrides: `mk20lib.sh:46–107`. Boot freezes resolved flags across the supervisor boundary, `mk20d:199–223`; supervisor constructs arguments without reloading configuration, `:335–346`. `test_release_switches` and `test_boot_supervisor_flags_and_descriptor`, `test_agent_release.py:22,54`, cover flags, exact defaults, changed configuration and descriptor agreement. L59, L74. |
| **2. `confirm.request`** | **PASS** | Validation, allowed/e-stop gating, singleton pending request and immediate reply: `ctl.c:321–337`. Shown-once guard, early-OK counting, cancellation and timeout: `:167–186,216–229,404–412`. `test_ctl_confirm`, `tests_ctl.inc:201`, covers before-shown, 299/300 ms, timeout, disable, stop, requester loss and argument bounds. L26, L46. Physical rendering remains A7. |
| **3. `ui.agent`** | **PASS** | State/input/summary/progress validation and cached dirty state: `ctl.c:175,302–319`; status at `:291–294`. `test_ctl_ui`, `tests_ctl.inc:250`, covers gating, valid states, bad arguments and dirty-bit consumption. L26, L46. |
| **4. D8 screen protocol/rules** | **PASS** | Layout, ownership, grants, reservations, geometry and sliding limits: `ctlscr.c:22–130`. Draw/text return `rendered:false`; disconnect reclaims pages at `:15–20`. `test_ctl_screen`, `test_ctl_edges`, `test_ctl_screen_disconnect`, and `test_ctl_long_uptime`, `tests_ctl.inc:290,392,533,616`. Both build source lists include the modules: `kbd/Makefile:2`, `tools/mk20-build-bins.sh:15`. L26, L46, L74. |
| **5. Flagged K2 e-stop** | **PARTIAL** | Hold/triple logic, release learning, rotation exclusion and cold-start release capability are implemented: `k2estop.c:3–30`; flag initialization at `mk20_kbd.c:114`. Boundary tests at `tests_ctl.inc:363`; timer-only and stalled-host e2e at `e2e_run.py:335,69`. L26, L46, L52–54. **Deadline enforcement during socket batches remains incomplete under R3-F1.** |
| **6. Protocol/docs/status** | **PASS** | `deck/kbd/CTL-PROTOCOL.md` documents framing, authentication, operations, errors, events, keyboard-only APIs, limits and deferred rendering. README and plan status updated at `deck/agent/README.md:3` and `AGENT-BUILD-PLAN.md:322`. The plan’s Linux-validation status at `:326` should now be refreshed. |
| **7. OpenAI-compatible adapter skeleton** | **PASS** | Bounded request/image/tools construction: `oai.c:118–183`; endpoint/header separation: `:185–200`; strict bounded response/argument parsing and typed errors: `:14–98,211–249`; coordinates: `:252–260`. C sanitizer tests and CLI/FakeModel pytest are wired through `agent/Makefile:12–19` and `test_oai.py:18–25,145–167`. L58. |
| **8. Chinese clipboard plan only** | **PASS** | `AGENT-BUILD-PLAN.md:335–340` records Latin-1 versus Extended Clipboard UTF-8, `0xC0A1E5CE`, unknown macOS support requiring measurement, leased HID paste, constrained optional restoration, no readback and no Mac helper. No clipboard implementation was added. |

**Plan A1 acceptance and applicable A4 skeleton criteria**

| Criterion | Result | Evidence |
|---|---|---|
| UID-authenticated, 0600 socket; maximum two clients | **PASS** | `ctl.c:386–447`; `test_ctl_socket`, `tests_ctl.inc:157`. L26, L46. |
| Connection-bound leases, renewal, sequence checks and HID action mapping | **PARTIAL** | `ctl.c:240–277,344–379`; `test_ctl_lease` and `test_ctl_scroll_act`, `tests_ctl.inc:66,140`. Socket-batch timestamps can outlive their authorization deadline: R3-F1. |
| Lease expiry safely produces all-up | **FAIL** | Direct expiry and shared-loop regression pass, `tests_ctl.inc:100–108,480–507`; ordinary binary expiry passes, `e2e_run.py:324–327`. Later requests in an already-running socket batch can still send expired input. |
| E-stop → all-up within 100 ms | **PARTIAL** | Quiet and initially stalled-host cases pass, L48–53. K2 timer-only coverage passes. Enforcement before every socket-triggered HID flush remains incomplete. |
| Act rejected after e-stop | **PASS** | Latched-stop check at `ctl.c:244`; act/acquire/remote-allow rejection at `tests_ctl.inc:120–133`. L26, L46. |
| PTT/physical press preempts agent | **PASS** | Human revocation precedes forwarding and the PTT HID edge, `kbd.c:129–140`. `test_ctl_lease`, `tests_ctl.inc:109`; PTT tests, `tests_k3.inc:463`; stalled-host human e2e, `e2e_run.py:71–131`. L26, L46, L51. |
| Confirmation below 300 ms is ineffective | **PASS** | Exact before-shown, 299 ms ignored and 300 ms accepted assertions, `tests_ctl.inc:212–215`. L26, L46. |
| Agent UI cache | **PASS** | `ctl.c:302–319`; `test_ctl_ui`, `tests_ctl.inc:250`. L26, L46. |
| Wheel/AC Pan descriptor and matching report format | **PASS** | Descriptor at `gadget.sh:408–419`; six/eight-byte reports tested at `tests_ctl.inc:24`. Real boot/argv agreement tested at `test_agent_release.py:54–86`. Endpoint report length remains 9. L26, L46, L74. |
| HID rebind queues/fails without spinning | **PASS** | Backoff/poll gating at `hid.c:105–145`; `test_hid`, `test_hid_replace`, and real-binary rebind at `e2e_run.py:205–236`. L26, L46, L54. |
| D8 reservations, grant expiry, limits and owner-only key events | **PASS** | `ctlscr.c:31–130`; `test_ctl_screen` and `test_ctl_long_uptime`. L26, L46. Rendering remains A7. |
| Box tests all pass | **PASS** | Authoritative full run, L79. Existing tests do not cover R3-F1. |
| Original keyboard acceptance does not regress | **PARTIAL** | Existing Linux keyboard/e2e coverage passes, L26, L46, L54; default contracts match. Device behavior was not checked. |
| Device descriptor inspection, scrolling and probation/rollback | **PARTIAL** | Required at `AGENT-BUILD-PLAN.md:319–320`; no device evidence supplied or collected. |
| A4 configurable OpenAI-compatible body, tools and image input | **PASS** | `oai.h:15–19`, `oai.c:134–193`; `request_test`, `test_oai.c:9`, and `test_request_and_header_separate`, `test_oai.py:50`. L58. |
| A4 malformed/oversized responses and missing tool calls | **PASS** | Strict scanner and missing-call check, `oai.c:14–98,230`; `strict_json_test`, `test_response_bounds`, `test_missing_tool_calls`. L58. |
| A4 typed HTTP/provider errors | **PASS** | `oai.c:211–244`; `response_test`, `test_oai.c:34`, and `test_typed_errors`, `test_oai.py:87`. L58. |
| A4 coordinate conversion/out-of-range refusal | **PASS** | `oai.c:252–260`; `coordinate_test`, `test_oai.c:82`. L58. |
| A4 credential exclusion from request body | **PASS** | Request structure has no key field, `oai.h:15–19`; separate header builder, `oai.c:195`. Explicit body exclusion assertions, `test_oai.py:55,167`. L58. |

Real-provider 8/10 localization benchmarks, production secret-file handling, transport/TLS/proxy/retries and cancellation remain outside this offline skeleton’s scope.

**Required invariants**

| Invariant | Result |
|---|---|
| **Defaults unchanged** | **PASS.** Options initialize off at `mk20_kbd.c:241–243`; release switches default off at `mk20lib.sh:73`. Read-only comparison against HEAD confirmed the **119-byte default descriptor is identical** and the default launch command is identical after expanding empty `"$@"`. Golden descriptor/default argv tests: `test_agent_release.py:71–98`; L74. |
| **Descriptor/daemon agreement** | **PASS for release boot/supervisor flow.** Export, persisted gadget environment and explicit supervisor environment share the resolved scroll value: `mk20lib.sh:107`, `mk20d:199–223`. Post-setup configuration mutation is tested. |
| **Socket cannot bypass or clear a latched e-stop** | **PASS.** Only keyboard-side `ctl_set_allowed(...,1)` clears it, `ctl.c:209–213`; no socket allow/grant/confirm-result injection exists. Reconnection does not clear it. **Deadline processing before the latch is raised remains defective under R3-F1.** |
| **Confirmation honors ≥300 ms** | **PASS.** `ctl.c:180–186`; exact boundary regression at `tests_ctl.inc:212–215`. |
| **API key never enters the request body** | **PASS for the implemented credential path.** Separate request/header interfaces and FakeModel assertions above. |
| **No additional GD32 write** | **PASS.** GD32 writer/allow-list files have no diff. The existing gate permits only `05 00 r r 00 00`, slots 100–105: `gd32.c:108–124`, `gd32io.c:24–28`. K2 code consumes events only. Allow-list tests are included in L26/L46. |

**Concrete remaining defects and gaps**

1. **R3-F1 — major: socket batches retain stale authorization/deadline times across blocking HID work.**

   [mk20_kbd.c:188](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/mk20_kbd.c:188) checks safety once before `ctl_on_poll`. [ctl.c:450](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/ctl.c:450) then processes every complete line in its 512-byte read using the same `now`; `ctl_on_poll` also shares that timestamp across clients.

   `act allup` can wait up to 50 ms at `ctl.c:277` and `hid.c:226–232`. Subsequent requests still validate the lease against the earlier time at `ctl.c:285,246`, then call HID functions that flush immediately at `hid.c:151,161,174`. K2 is not checked between these requests.

   A concrete failing schedule follows from this control flow:

   - A lease expires at time **T**; an agent key is held and HID becomes blocked.
   - At **T−10 ms**, one socket read contains `act allup` followed by another valid-sequence key/pointer action.
   - HID becomes writable at **T+20 ms**. All-up completes, then the next action uses **T−10 ms** for validation and sends input after expiry.
   - The outer loop’s next safety check happens after that output. An overdue K2 hold has the equivalent gap; stale renewal can also renew an already-expired lease.

   This is a source-derived finding, not a measured reproduction in this sandbox.

   **Fix:** refresh monotonic time and run both lease and K2 safety checks between socket requests, including across clients and after any bounded wait. Ensure socket-triggered HID mutations cannot flush before that check. Dispatching one buffered request per safety-loop iteration, or using a shared pre-dispatch safety callback, would address this; refreshing `ctl_tick` alone would leave K2 uncovered.

2. **R3-G1 — minor: the new backpressure regression does not exercise socket dispatch.**

   [test_kbd_safety_backpressure](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/tests/tests_ctl.inc:480) correctly tests the production `kbd_service` boundary, but it never executes `read_client`/`ctl_on_poll` or crosses a deadline inside a multi-request batch. Current e2e uses writable HID.

   **Fix:** add a Linux regression with concatenated socket requests, held agent input and HID backpressure released after lease expiry; assert that no subsequent non-neutral report appears. Repeat for K2 hold expiry and cover dispatch across two clients.

3. **R3-D1 — minor: A1 validation status is stale.**

   `deck/AGENT-BUILD-PLAN.md:326` still says the full Linux suite is pending despite the supplied successful run.

   **Fix:** record the current Linux run and results, while retaining the separate device-validation and release-artifact prerequisites.

The acceptance failure is independently justified by **R3-F1 / incomplete R2-F2**, despite the green supplied test run.

ACCEPTANCE: FAIL