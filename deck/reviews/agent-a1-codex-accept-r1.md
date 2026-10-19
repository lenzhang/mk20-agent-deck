Two blocker defects remain: enabled release flags do not reach the keyboard process, and queued pointer actions can execute after e-stop.

This was a read-only review. No files were edited; no devices, serial ports, adb, or network were accessed.

Paths below are relative to `deck/`. “Linux log” means `../mk20-a1-boxtest.log`.

The available verification evidence is:

- **Linux:** 1,991 checks passed under ASan/UBSan and again under TSan; real-binary e2e passed, measuring e-stop → all-up at **1.1 ms** ([log:7–24](/Users/dev/worktrees/mk20-a1-boxtest.log:7)).
- **Linux:** 29 agent tests and 58 tools tests passed. Main pytest initially reported **540 passed, 1 failed**; the log header records the corrected binary/source test passing separately ([log:26](/Users/dev/worktrees/mk20-a1-boxtest.log:26), [log:159](/Users/dev/worktrees/mk20-a1-boxtest.log:159), [log:1](/Users/dev/worktrees/mk20-a1-boxtest.log:1)). The current source list includes both added modules at [tests/test_mk20d_router.py:145](/Users/dev/worktrees/mk20-agent-a1/deck/tests/test_mk20d_router.py:145).
- **This pass:** existing host binaries passed **284 control-rule checks** and **1,065 adapter checks**. These were prebuilt artifacts, not fresh builds.
- **Current sources:** changed keyboard C files and adapter sources/tests passed C11 syntax checks with `-Wall -Wextra -Werror`; shell syntax and `git diff --check` passed.
- The compiler wrapper initially encountered a sandbox-blocked cache write; direct Clang syntax checks succeeded. No fresh linked build, Linux suite, pytest suite, or local shellcheck run was performed. Linux shellcheck evidence is at [log:29](/Users/dev/worktrees/mk20-a1-boxtest.log:29).

The eight task items assess as follows:

| Item | Result | Evidence |
|---|---|---|
| **1. Release wiring** | **FAIL** | Defaults, validation and overrides exist in [mk20lib.sh:46](/Users/dev/worktrees/mk20-agent-a1/deck/firmware/release/share/mk20lib.sh:46). However, boot starts a fresh shell at [mk20d:220](/Users/dev/worktrees/mk20-agent-a1/deck/firmware/release/sbin/mk20d:220); the `kbd` branch never loads configuration before reading `FEATURE_*` at [mk20d:333](/Users/dev/worktrees/mk20-agent-a1/deck/firmware/release/sbin/mk20d:333). **F1** below. `test_default_launch_unchanged` and `test_descriptor_and_launch_cannot_disagree` test extracted fragments with supplied variables, missing this process boundary ([test_agent_release.py:33](/Users/dev/worktrees/mk20-agent-a1/deck/tests/test_agent_release.py:33)). |
| **2. Confirmation** | **PASS** | Allowed/e-stop checks, singleton pending request, timeout validation and immediate reply: [ctl.c:312](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/ctl.c:312). Shown guard, early-OK counting and cancellation: [ctl.c:159](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/ctl.c:159). Deadline bounds poll at [ctl.c:215](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/ctl.c:215). `test_ctl_confirm` covers before-shown, 299/300 ms, timeout, disable, e-stop and disconnect ([tests_ctl.inc:196](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/tests/tests_ctl.inc:196)); included in Linux log:15/21. |
| **3. Agent UI cache** | **PASS** | State/input/summary/progress validation, caching and dirty-on-change at [ctl.c:293](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/ctl.c:293); status exposes cached UI at [ctl.c:280](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/ctl.c:280). `test_ctl_ui` checks supported states, invalid fields, gating and dirty consumption ([tests_ctl.inc:245](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/tests/tests_ctl.inc:245)); Linux log:15/21. |
| **4. Screen protocol/rules** | **PARTIAL** | Layout, ownership, reservations, geometry, grants and sliding rate limits are implemented in [ctlscr.c:42](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/ctlscr.c:42) and [ctlscr.c:73](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/ctlscr.c:73). Draw/text return `rendered:false`. `test_ctl_screen` covers the requested principal rules ([tests_ctl.inc:285](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/tests/tests_ctl.inc:285)); Linux log:15/21. Disconnect leaves permanently inaccessible pages occupying the eight-page limit: **F3**. |
| **5. K2 e-stop** | **PARTIAL** | Pure hold/triple modes and rotation exclusion are tested by `test_k2_estop` ([tests_ctl.inc:358](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/tests/tests_ctl.inc:358)). Poll timing is wired at [mk20_kbd.c:169](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/mk20_kbd.c:169). Production always starts in press-only mode, so its first held press cannot trigger: **F4**. Release enablement also fails through **F1**. |
| **6. Protocol/docs/status** | **PARTIAL** | Comprehensive implemented-protocol documentation exists in [CTL-PROTOCOL.md:6](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/CTL-PROTOCOL.md:6), including framing limits, keyboard-only APIs and rendering deferral. README and plan were updated. The release consistency assertion is contradicted by **F1**, and the opening invocation omits `--run`: **F7**. |
| **7. OpenAI-compatible skeleton** | **PARTIAL** | Request/image/tools construction, separate authorization header, bounded parsing, typed errors and coordinates exist in [oai.c:69](/Users/dev/worktrees/mk20-agent-a1/deck/agent/src/oai.c:69), [oai.c:130](/Users/dev/worktrees/mk20-agent-a1/deck/agent/src/oai.c:130), and [oai.c:146](/Users/dev/worktrees/mk20-agent-a1/deck/agent/src/oai.c:146). Sanitizer build and pytest integration exist. `test_fake_model_http` covers calls/content/429/malformed responses ([test_oai.py:115](/Users/dev/worktrees/mk20-agent-a1/deck/agent/tests/test_oai.py:115)); Linux log:28. Strict invalid-JSON handling and missing-call consistency remain deficient: **F5–F6**. |
| **8. Chinese clipboard plan only** | **PASS** | [AGENT-BUILD-PLAN.md:334](/Users/dev/worktrees/mk20-agent-a1/deck/AGENT-BUILD-PLAN.md:334) records Latin-1 versus Extended Clipboard UTF-8, `0xC0A1E5CE`, unknown macOS support requiring measurement, leased HID paste, constrained optional restoration, no clipboard readback and no Mac helper. |

The plan’s A1 acceptance criteria and applicable A4 skeleton checks assess as follows:

| Criterion | Result | Evidence |
|---|---|---|
| Control socket, lease TTL/revocation, act mapping, confirmation and UI cache | **PARTIAL** | Individual rule implementations pass their unit tests; release activation fails through **F1**, and revocation does not cancel queued pointer work (**F2**). |
| Wheel/AC Pan descriptor and matching pointer report format | **PARTIAL** | Descriptor contains both usages; `test_hid_scroll` checks eight-byte scroll reports and six-byte defaults ([tests_ctl.inc:24](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/tests/tests_ctl.inc:24)). Release can select the scroll descriptor while launching the default report format (**F1**). |
| Lease expiry causes all-up | **PARTIAL** | `test_ctl_lease` and real-binary e2e cover ordinary delivery ([tests_ctl.inc:95](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/tests/tests_ctl.inc:95), [e2e_run.py:244](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/tests/e2e_run.py:244)); queued-pointer cancellation is missing (**F2**). |
| E-stop ≤100 ms | **PARTIAL** | Real-binary test measured **1.1 ms**, Linux log:23. It does not exercise queued pointer actions under HID backpressure (**F2**). |
| Act rejected after e-stop | **PASS** | Latch check at [ctl.c:236](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/ctl.c:236); `test_ctl_lease` rejects subsequent act/acquire and socket `allow` ([tests_ctl.inc:119](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/tests/tests_ctl.inc:119)). This does not cancel previously queued actions. |
| PTT press preempts agent | **PASS** | All physical matrix presses revoke before the PTT modifier edge ([kbd.c:117](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/kbd.c:117)). Human preemption is covered by `test_ctl_lease` and real-binary e2e; existing PTT tests cover its HID edge behavior. |
| Confirmation below 300 ms ineffective | **PASS** | `test_ctl_confirm` explicitly rejects 299 ms and accepts 300 ms; before-shown OK is ignored and counted ([tests_ctl.inc:207](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/tests/tests_ctl.inc:207)). |
| HID rebind queues/fails without spinning | **PASS** | Backoff and poll gating at [hid.c:104](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/hid.c:104); `test_hid`, `test_hid_replace` and e2e cover transport recovery. Linux log:15/21/24. |
| Box tests all pass | **PASS**, with recorded rerun | Provided log shows successful component suites and 540 main pytest passes; its header records the corrected remaining test passing. There is no subsequent clean full-run transcript. |
| Original keyboard acceptance does not regress | **PASS for available box coverage** | Existing full keyboard suites and e2e passed, Linux log:15/21/24. Hardware behavior remains unverified. |
| Device descriptor inspection, actual scrolling and probation/rollback | **PARTIAL** | Required by [plan:319](/Users/dev/worktrees/mk20-agent-a1/deck/AGENT-BUILD-PLAN.md:319); no device evidence supplied or collected. |
| A4 single configurable adapter, tools/image input, coordinates and typed errors | **PASS for skeleton mechanics** | `request_test`, `response_test`, `coordinate_test`, typed-error pytest and FakeModel tests cover these paths. Linux log:28; existing host C binary passed 1,065 checks here. |
| A4 malformed/oversized responses and missing tool-call cases | **PARTIAL** | Oversize/depth/content/call-count bounds are tested ([test_oai.py:96](/Users/dev/worktrees/mk20-agent-a1/deck/agent/tests/test_oai.py:96)); **F5–F6** remain. |
| A4 request-body credential exclusion | **PASS for skeleton** | Request type contains no key field ([oai.h:15](/Users/dev/worktrees/mk20-agent-a1/deck/agent/src/oai.h:15)); header construction is separate. `test_request_and_header_separate` and `test_fake_model_http` assert body exclusion. |

Real-provider 8/10 localization benchmarks, production secret-file handling, HTTPS/proxy transport and request cancellation remain outside the adapter skeleton’s delivered scope.

The explicitly requested invariants assess as follows:

| Invariant | Result |
|---|---|
| **No flags preserve defaults** | **PASS.** All new defaults are off. The default descriptor is byte-identical to HEAD (**119 bytes**); default pointer reports remain six bytes. The default launch expands to the previous arguments. See [mk20_kbd.c:230](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/mk20_kbd.c:230), [test_agent_release.py:41](/Users/dev/worktrees/mk20-agent-a1/deck/tests/test_agent_release.py:41). |
| **Descriptor and daemon cannot disagree when enabled** | **FAIL — F1.** |
| **Socket cannot clear e-stop** | **PASS.** Only keyboard-side `ctl_set_allowed(...,1)` clears the latch ([ctl.c:201](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/ctl.c:201)); no socket allow/grant/confirmation-injection operation exists. |
| **E-stop prevents subsequent agent input** | **FAIL — F2.** New acts are rejected, but already queued pointer actions survive. |
| **Confirmation honors ≥300 ms** | **PASS**, including exactly 299/300 ms. |
| **API key never enters request body** | **PASS for the implemented credential path.** Separate API/header builder and explicit body-exclusion tests. |
| **No additional GD32 writes** | **PASS.** GD32 writer/allow-list code is unchanged. New K2 handling consumes inbound events only. The existing gate restricts output to the knob payload ([gd32.c:115](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/gd32.c:115), [gd32io.c:24](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/gd32io.c:24)); `test_gd32_allowlist` is included in the successful Linux suites. No tracked release binaries changed. |

Concrete defects and test gaps:

1. **F1 — blocker: release flags disappear at the supervisor process boundary.**  
   `cfg_load` assigns unexported `FEATURE_AGENT_CTL`, `FEATURE_HID_SCROLL` and `FEATURE_K2_ESTOP`. Boot execs a new shell, whose `kbd` branch never calls `cfg_load`. It therefore omits all three options. Meanwhile, exported `MK20_HID_SCROLL=1` reaches gadget setup, producing a scroll descriptor with a default-format daemon. A read-only reproduction using the actual flag fragment printed:
   ```
   child scroll_env=1 flags=[]
   ```
   **Fix:** load configuration in the `kbd` branch before constructing arguments, preserve the boot-resolved scroll override, and verify it against the active descriptor selection. Add a boot→supervisor regression using the real shell entry points and scoped fixtures; do not inject `FEATURE_*` directly as the current fragment test does.

2. **F2 — blocker: e-stop/revocation leaves queued pointer actions executable.**  
   [ctl.c:152](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/ctl.c:152) releases keyboard state and calls `hid_pointer_release`, but neither clears `ptrq`. That release also returns immediately when the latest desired button state is already zero ([hid.c:197](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/hid.c:197)). Under backpressure, queued down/up or scroll reports remain and later flush after the latch is set ([hid.c:53](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/hid.c:53)). This is present in the current implementation, including unchanged HID code.  
   **Fix:** purge pending pointer actions before issuing neutral reports on e-stop, expiry, disable, disconnect and human preemption. Add a nonblocking-pipe regression that queues click/scroll actions, stops, unblocks output, and verifies that only neutral/release reports follow.

3. **F3 — major: disconnect can permanently exhaust the page pool.**  
   [ctlscr.c:15](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/ctlscr.c:15) retains pages with `used=1` and sets their client to `-1`. Ownership checks then prevent every subsequent client from showing, deleting or modifying them ([ctlscr.c:94](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/ctlscr.c:94)). Eight abandoned pages make creation return `busy` until keyboard-process restart.  
   **Fix:** delete connection-owned pages on disconnect, while preserving pages on e-stop/disallow as required. Test eight creates → disconnect → reconnect → successful create.

4. **F4 — major acceptance gap: the first K2 hold cannot stop the production process.**  
   Production initializes `reports_release=0` ([mk20_kbd.c:114](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/mk20_kbd.c:114)). A first down, 1,000 ms tick and later release produce no stop; release capability is learned only afterward ([k2estop.c:4](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/k2estop.c:4)). The documentation acknowledges this limitation, but it does not satisfy an unconditional first-hold guarantee.  
   **Fix:** provide an explicit measured release-capability setting for production initialization, retain triple-press mode for press-only firmware, and add a cold-start test through the real poll loop. Device capability measurement remains pending.

5. **F5 — major: malformed argument JSON can be accepted as valid.**  
   The adapter’s precheck bounds complexity but does not validate JSON token grammar ([oai.c:15](/Users/dev/worktrees/mk20-agent-a1/deck/agent/src/oai.c:15)). Vendored cJSON parses numbers through permissive `strtod` ([cJSON.c:355](/Users/dev/worktrees/mk20-agent-a1/deck/agent/vendor/cjson/cJSON.c:355)), allowing invalid JSON forms such as `{"x":01}` and `{"x":1.}` to become argument objects instead of per-call errors.  
   **Fix:** validate strict JSON syntax within the existing bounds before cJSON parsing. Add these numeric forms and unescaped control characters to response and per-call argument tests; malformed arguments must produce `invalid_arguments`.

6. **F6 — minor: missing tool calls are neither rejected nor tested.**  
   `finish_reason:"tool_calls"` with absent or empty `tool_calls` currently returns success because calls are optional at [oai.c:160](/Users/dev/worktrees/mk20-agent-a1/deck/agent/src/oai.c:160). The A4 missing-call scenario is absent from tests.  
   **Fix:** require a nonempty call array when that finish reason is reported, return `bad_response` otherwise, and test both absent and empty arrays.

7. **F7 — minor: documented opt-in command is invalid.**  
   [CTL-PROTOCOL.md:3](/Users/dev/worktrees/mk20-agent-a1/deck/kbd/CTL-PROTOCOL.md:3) shows `mk20-kbd --ctl ...`, while option parsing requires `--run`.  
   **Fix:** change it to `mk20-kbd --run --ctl /run/mk20/kbd.sock`.

ACCEPTANCE: FAIL