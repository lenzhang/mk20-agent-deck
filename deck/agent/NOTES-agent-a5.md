# A5 host loop, safety gate and calibration — 2026-10-19

Scope is `deck/agent/`, the A5 status line in the build plan and A5 review notes. No commit/push, network/provider access, device/ADB/serial work, kbd edits, firmware/kernel/vendor edits or `deck/mk20deck/agentcfg.py` changes. All IPC tests use host fakes, socketpairs, temporary FIFOs and ptys. Temporary kbd builds use a copy of the unchanged source tree and its original Makefile.

## A4 review carry-over (implemented first)

`llm_net_error` whitelists known literal classes. `llm_decode` maps an unknown/NULL network class to `network` with **retryable=false**. The enum retry table explicitly contains rate, server, timeout, network, dns, connect and io. The A4 error-table note now states this exact contract. Invalid/non-array reparsed tools fail config/request. Tests cover all three review gaps: post-fstat provider growth to 16,385 bytes (a reader injection extends valid JSON with whitespace, so a truncated 16-KiB read would incorrectly succeed), malformed CLI tools with zero connects, and HTTP 302 over TLS returning bad_response. Token counts are extracted from bounded, strictly parsed usage metadata; absent/invalid usage is logged as zero.

## Interfaces and safety

* `policy.h` defines one `action_t` for click, double_click, move, drag, scroll, type, paste, key, wait, done and ask_user. Both parsing and the policy boundary validate actions, including canonical modifier/USB-usage agreement, finite/integral coordinates, screenshot bounds, ASCII type, UTF-8 text/targets, 256-character text and known key names. Paste is unsupported. The key parser accepts US layout keys, named navigation keys, F1–F12 and modifier aliases; modifier order cannot bypass the blacklist.
* Local risk and model risk are a union. First input and app/title changes require confirmation by default; dangerous keys, shell-like typing and the specified English/Chinese target words also require it. English comparisons fold ASCII without altering UTF-8 bytes. Default window/target blacklists cover loginwindow, security/privacy, Screen Sharing, mihomo and proxy settings. Password/secure-input hints and unlock targets are rejected before confirmation. A lock hint permits only HID move/wait/completion/user-query operations, with every type/paste and every VNC action rejected; an identified login/lock window is rejected outright.
* `loop.h` has exactly disabled, idle, observing, planning, acting, awaiting_confirm, done, stopped, error. Boot starts disabled. There is no enable function or command. Only the trusted kbd poll port can report allow; `loop_submit` cannot change permission. A local stop sends estop to kbd; all stop events clear permission, cancel pending work and suppress subsequent act/release traffic. A new task after an ordinary task error/confirmation denial retains the existing keyboard authorization; after estop/lease loss, fresh keyboard authorization is mandatory.
* Ports are observe, model, kbd event/lease/action/confirmation/UI, monotonic clock, cancel and log. Pending asynchronous operations return 1; synchronous implementations service the cooperative guard and check it before writes. Safety events take priority over queued confirmation results. Only a matching confirm.result with ok=true is accepted; stale IDs, denial and deadline expiry cannot authorize an action. No-progress is represented by stopped + ask_user=true (there is no extra paused state).
* Limits default to 30 steps (cap 100), 600,000 ms wall, 40 model calls, 2,000 ms per input action. Retries require both the adapter retryable flag and its explicit retryable enum; 500/1000-ms backoff permits two retries, counted as calls. Three consecutive equal screenshot hashes stop and ask the user before another model call. Long waits/typing are still subject to the 2-second action deadline and can time out after partial input; they are never replayed.
* `runtime.c` connects current-only RFB capture/JPEG, `llm_chat`, text-only bounded action history, kbd and the redacted log sink. It requires a trusted window/lock/secure-input hint port and calibration matching the current processed frame geometry; missing hints/calibration fail closed. It does not treat the VNC server name as the foreground window title. The runtime caller supplies the task, existing authenticated RFB connection, provider/net config and a log sink. Service startup, HTTP/MCP entrypoints, log rotation and release switches remain A6–A8 work.
* Network waits/DNS/TLS and established RFB capture now support cooperative safety servicing, with poll slices up to 25 ms when enabled. Net also accepts a cancellation flag. Existing A2/A3 paths retain their deadlines when no service is installed. Fatal control-stream errors shut down the connection so kbd can release partial input. An expired action deadline never prevents safety release/status traffic. A real-ctl test verifies all-up after partial typing times out. A C IPC test cancels a live blocked CONNECT read in under 500 ms, and verifies pre-cancelled requests make zero socket calls. CPU work such as JPEG encoding checks the guard before/after; device latency still requires measurement.

## Policy configuration

`policy_load(NULL,...)` reads `/data/mk20/agent/policy.json`; pass a path to override it. `loop_init` with a NULL policy loads this file. Missing means defaults. A malformed, unreadable, nonregular, oversized, duplicate-key or incorrectly typed file replaces the **whole** configuration with defaults and emits a fixed warning through the redacted sink. Thus a broken file containing confirm_first=false cannot disable confirmation. Unknown fields are ignored. Numeric values are clamped; wall/model/action limits cannot exceed their default caps. Additional dangerous keys and window blacklists extend the built-in protections.

Example (all fields optional):

```json
{
  "max_steps": 30,
  "wall_ms": 600000,
  "model_calls": 40,
  "exec_ms": 2000,
  "confirm_ms": 30000,
  "confirm_first": true,
  "confirm_app_switch": true,
  "dangerous_keys": ["cmd+a"],
  "window_blacklist": ["Bank account"]
}
```

No new onboard-ui/agentcfg provider fields are required. A7 may optionally write this separate agent-side policy file atomically; agentcfg/settings remain owned by the other branch. GLM coding-plan URLs use the existing A4 provider schema and need no special adapter. Vision + tool compatibility with an actual GLM coding model remains an A4 [U] benchmark; it is not assumed here.

## kbd client and arbitration limits

`kbdc.c` is an AF_UNIX stream client with bounded nonblocking request/reply handling, unique IDs, strictly increasing action seq, token/TTL checks, renewal before expiry, interleaved event handling and the 1024-byte line cap. Split replies, malformed replies, wrong IDs and disconnects fail closed. It uses only the documented v1 status/lease/act/ui.agent/confirm.request/estop operations on current kbd. Allow evidence originates from kbd status; the development script's fake allow is ignored whenever a real kbd transport is selected. No socket command can grant permission or forge a real confirmation.

The pure arbitration machine freezes after two consecutive HID failures, performs all-up, acquires the selected channel lease, and switches back after three healthy probes at an observe/idle boundary. Failed actions are recorded/consumed, never replayed on the other channel. Tests cover HID → VNC → HID and reset of consecutive counters.

**Upstream dependency, deliberately fail-closed:** current `deck/kbd/CTL-PROTOCOL.md` has neither input=vnc lease enforcement nor hid.health events (its final paragraph explicitly says so). Its accepted HID acts do not guarantee delivery, and hid_open alone is not a write-health probe. A5 parses future hid.health notifications, but does not pretend current status is proof of delivered input. Real VNC acquisition is refused unless kbd explicitly advertises and echoes an input-specific VNC lease; current v1 never does. No HID command is silently sent as VNC. The RFB input writer also returns unsupported. The protocol owner must supply/enforce input leases and trustworthy health before real fallback can run. After lease loss, current kbd status has no allow generation/event; explicit off→on at the keyboard supplies fresh evidence. No kbd change was made in this branch.

## Calibration

`calib_fit` fits four commanded HID corners plus center to measured frame pixels by least squares with normalized HID inputs and pivoted elimination. It rejects missing/duplicate samples, singular fits, nonfinite/out-of-frame data and max Euclidean residual above 2 logical pixels. `calib_fit_logical` additionally takes logical display dimensions so a scaled/Retina screenshot's residual is evaluated at the correct logical scale; the 1:1 wrapper is suitable only for logical-pixel frames. Tests include a fit accepted at 1:1 but rejected at 2:1 logical scaling.

The inverse maps frame pixels to 0..32767, with finite/range checks and nearest rounding. Persistence defaults to `/data/mk20/agent/calib.json` and accepts a caller path override: mkstemp (O_EXCL, 0600), write-all, fsync file, close, rename, fsync directory. The file contains version, frame/logical geometry and the five measured pairs. Loading requires a regular 0600 file owned by the current service uid and **re-fits the measurements**, rather than trusting serialized inverse coefficients. Any failure zeros validity. Runtime rejects a resized frame pending recalibration. Finding the cursor and identifying logical display geometry are measurement ports/device work [D]; no cursor detection is claimed.

## Logs and host CLI

Each consumed step sends one bounded line to an injectable sink: step/action/coordinates/model/latency/input-output token counts plus text length and FNV-1a hash. No task text, typed text, target, window title, frame or raw provider error is logged. Model IDs are JSON-quoted. Every line passes `agent_redact`; Authorization/Bearer/sk- markers or a supplied known secret replace the whole line with `[redacted]`. The runtime caller can register known VNC/API secret values with the sink redactor; those values are never printed. Tests assert both grep-style absence and correct length/hash.

```sh
# JSON script path is required; stdout is exactly one JSON result line.
deck/agent/build/mk20-agent --loop-test /tmp/task.json
```

A minimal offline script:

```json
{"allow":true,"model":[
  {"action":"click","x":300,"y":200,"risk":"low","target":"Notes"},
  {"action":"type","text":"hello","risk":"low","target":"Notes"},
  {"action":"done","risk":"low","target":"Notes"}
]}
```

Script ports support observations, errors/delays, confirmation decisions, events, fake time and HID failures. `kbd_socket` runs the same task against the real client; `kbd_fd` is its explicit socketpair test equivalent. Both ignore fake allow/confirmation events and respect actual keyboard permission. Fake calibration is used only for this fake-observer harness; the runtime requires loaded/measured calibration. The harness never opens real HID/VNC/model endpoints by itself.

## Verification record

### Fix r1

* Verified the existing `loop_submit` STOPPED/ERROR path end to end: ordinary finishes retain keyboard permission and accept another task; emergency finishes remain cancelled/disallowed until fresh keyboard allow. Added ERROR to `K_ALLOW` handling. The kbdc `notified_allowed`/`require_off` rules are unchanged.
* `loop_service` now keeps returning nonzero in DONE/STOPPED/ERROR, including repeated guard calls after a non-emergency finish. Idle/disabled calls outside a task still return zero. Audited runtime observe/model/execute, kbdc guards, net and RFB callbacks; their existing nonzero-abort handling needs no change.
* Trivial minor #3: ignore `lease_revoked/released` while releasing or when no token remains, including notification-after-reply ordering. Other revocations still stop the agent.
* `--loop-test` accepts an optional `resubmit` array (`state`, optional `reason`/`tick`); attempts report permission, cancellation and acceptance. `probe_service:true` records two post-task service results. Tests cover second tasks after confirm_denied/no_progress/model error/ask_user, ERROR allow, estop refusal and fresh-allow recovery, real ctl permission retention, late released events, empty acts on wall/action timeouts and confirmation denial/timeout, and repeated terminal versus idle/disabled guards.
* The real socket acting estop case now uses `confirm_first:false`, so it reaches acting and stops during the existing 300-ms act delay before any pointer report. The no-0x02-report assertion is retained; the other phases keep default confirmation.
* No device access, commit, new dependencies, warning suppressions, or changes to kbd/agentcfg/settings/vendor. New counters use bounded `int`/`unsigned`; time remains `int64_t`, with no 64-bit `long` assumption. No armhf compiler is installed here.
* `make -C deck/agent test`: **PASS**, including 262 A5 checks and all existing C/CLI suites with ASan/UBSan. Loop pytest without listener cases: **84 passed, 6 deselected**, 11.25 s. System `python3 -m pytest deck/agent/tests -q` cannot start (`No module named pytest`); the cached Python/pytest plus Pillow PYTHONPATH shown below ran the full suite: **128 passed, 190 failed**, 91.59 s, no skips/xfails.
* Broader socketpair supplement (same command below): **301 passed, 1 skipped, 9 deselected**, 84.76 s. The skip is the existing OAI HTTP offline switch; deselections are the two A3 TCP-only tests, A4 unreachable-proxy and six real Unix-daemon cases. No new skip/xfail was added.
* **Sandbox failures, separate from passing checks:** all 190 are listener EPERM: 184 TCP cases (`test_fakes.py`: 7, `test_llm.py`: 49, `test_net.py`: 41, `test_oai.py`: 1, `test_rfb.py`: 86), plus six Unix-daemon cases in `test_loop.py`: `test_real_socket_daemon_complete`, `test_real_socket_daemon_confirm_timeout`, and `test_real_socket_daemon_estop[observing|planning|acting|awaiting_confirm]`. The acting-case test fix still needs the box listener run; it is not claimed as passing here.

### Earlier A5 verification

Host: Darwin arm64, C11 -Wall -Wextra -Werror, ASan/UBSan with nonrecovering undefined-behavior errors. No warning suppression added. Cached Python/pytest/Pillow are used without installing/downloading packages:

```sh
PY=/Users/dev/.cache/uv/archive-v0/vxkKwzR1zHgPnvko/bin/python
export PYTHONPATH=/Users/dev/.cache/uv/archive-v0/bo26hwk4TgxG1QEs/lib/python3.13/site-packages
make -C deck/agent test build/mk20-agent-nosan
$PY -m pytest deck/agent/tests -q --tb=short
MK20_TEST_SOCKETPAIR=1 MK20_TEST_NO_NETWORK=1 $PY -m pytest \
  deck/agent/tests/test_rfb.py deck/agent/tests/test_oai.py \
  deck/agent/tests/test_fakes.py::test_des_known_vector deck/agent/tests/test_net.py \
  deck/agent/tests/test_llm.py deck/agent/tests/test_loop.py -q \
  -k 'not unreachable_proxy and not fallback_order and not real_socket' --tb=short
MK20_TEST_SOCKETPAIR=1 $PY -m pytest deck/agent/tests/test_llm.py -q
```

Final verification:

* `make test` and the nosan target: **PASS**. 254 A5 policy/calibration/arbitration/state-machine checks; 256 LLM checks; 3145 OAI checks; 349 net/clock checks; existing RFB/scale/JPEG and CLI checks. ASan/UBSan reported no errors. The live cancel test verifies zero connects when pre-cancelled and interruption of a blocked read; invalid-config socket count remains zero.
* Final combined socketpair supplement: **289 passed, 1 skipped, 9 deselected**, 88.17 s. The skip is the existing OAI HTTP offline switch. Deselections are exactly the two A3 TCP-only tests, A4 unreachable-proxy (covered separately below), and six real Unix-daemon tests. No new skip/xfail. A5 contributes **72 passing tests**; A2/A3 contribute 169, A4 contributes 48 in this combined command.
* Final standalone A4 socketpair suite: **49 passed**, 16.82 s, no skip/xfail/deselection. This includes the injected unreachable-proxy proof that the combined command explicitly deselects.
* Final A5 suite after the test-driver startup/cleanup repair: **72 passed, 6 explicitly deselected**, 10.69 s. The driver waits for startup before any task deadline begins (bounded 15 seconds), reports process failures and cleans up on startup failure; it does not relax an agent/confirmation/action deadline. A preceding normal run had one driver-readiness failure under simultaneous builds; that is not included as a protocol pass.
* Final standard suite: **116 passed, 190 failed**, 94.56 s, **no skips/xfails**. All failures are 184 TCP listener binds returning EPERM plus six real-daemon Unix binds returning EPERM. Individual failed IDs are in [agent-a5-socket-blocked.txt](../reviews/agent-a5-socket-blocked.txt). This is a **FAIL for full host socket acceptance**, not a green normal pytest run.
* The six real-daemon cases were separately rerun with short `/tmp` paths (avoiding macOS sockaddr_un/HID path limits): **6 failed**, each with the daemon's explicit `control socket ... failed: Operation not permitted (agent input disabled)` diagnostic. These are visible environment failures. The full standard suite remains mandatory on Linux.
* `git diff --check`: PASS. Outside agent/reviews, only the A5 plan status line changed. The preexisting untracked `deck/reviews/prompt-agent-a5.txt` was left untouched.

Timing evidence: one preceding combined run had **286 passed / 1 failed / 1 skipped / 9 deselected** because `test_compressed_frame_decode_timeout[True]` took 1.054 s against its unchanged <1-s wall assertion, while returning the correct timeout. A fresh-build isolated rerun also exceeded that wall bound (1.046/1.730 s). Reusing those exact binaries for four live capture requests each measured 0.118–0.120 s sanitized and 0.104–0.106 s nosan. Newly linked invalid-argument-only processes measured 0.412/0.648 s on first launch versus 0.017/0.003 s subsequently, consistent with cold executable startup overhead. **No A2 test threshold, timeout behavior or fixture warm-up was changed.** The final full supplement above passed both timeout cases. Cold-start timing remains a host sensitivity, not a claimed device measurement.

The six real Unix daemon tests remain enabled: harmless completion, confirmation timeout, and estop in observing/planning/acting/awaiting_confirm. They build the real mk20-kbd binary via its Makefile into a temporary copied tree and use FIFO hidg0 + two ptys. Sandbox denial is a visible failure, never a skip or xfail. Socketpair tests execute unchanged real ctl.c/ctlscr.c/hid.c under sanitizers with keyboard-only callbacks, covering successful tasks, ≥300-ms early-confirm rejection, cancel, timeout, estop, disabled permission and renewal through a 3.2-second planning request. This supplements, and does not replace, real-daemon acceptance on Linux.

## Remaining gates

* [D] Real cursor localization; logical/Retina/multimonitor geometry; measured calibration/click error ≤2 logical px; device estop and physical input preemption latency. Current runtime window/secure-input hints need a trusted real detector; missing hints fail closed.
* [D] Static armhf/device build, combined capture/model/loop RSS ≤16 MiB, sustained CPU/latency and typing/layout behavior. No device memory or runtime claim is made from host unit results.
* [U] User-present harmless task and high-risk confirmation UX, with actual keyboard allow/deny/estop; real provider screenshot/tool benchmark and GLM coding-model compatibility.
* Linux host: rerun all TCP and real Unix-daemon tests without sandbox listener denial. Current v1 has no rendered confirmation UI; the shim tests the real rule engine, while actual display/confirm UX remains A7/[D].
* kbd protocol branch: input-specific VNC leases and real health evidence; RFB pointer/key writer before device fallback. Paste remains unsupported pending the already-planned Extended Clipboard decision. No Mac helper or clipboard readback was added.

## Acceptance summary (2026-10-19 22:35 +08)

- Fix r1 (codex gpt-6.1-sol, deck/reviews/prompt-agent-a5-fix-r1.txt): Kimi r1 MAJOR #1 (S_ERROR / non-emergency S_STOPPED dead-end; new task accepted while kbd allows, emergency stops still need a kbd allow cycle), MAJOR #2 (loop_service aborts in-flight ports after any finish; tests assert no fake act after wall_budget / action_timeout / confirm deny), minor #3 (kbdc ignores `released` with no token), box failure test_real_socket_daemon_estop[acting] = test bug (default confirm_first parked the agent in awaiting_confirm; acting case now uses confirm_first=False).
- Reviews: codex acceptance r1 PASS (no findings), deck/reviews/agent-a5-codex-accept-r1.md. Kimi K3 r2 diff recheck: watchdog-killed at 600 s before a formal verdict; its log verified all four items, no BLOCKER/MAJOR (deck/reviews/agent-a5-kimi-r2.md).
- Box Linux: make -C deck/agent test green (ASan/UBSan); agent pytest 318 passed (incl. all real-socket daemon tests); full make -C deck test green (24 + kbd 4018x2 + 318 + 58 + 546).
- Route checks before every codex/Kimi run: AI=US-Exit-Hy2 exit 203.0.113.10.
- Remaining for device: real kbd daemon allow/estop/confirm on hardware, HID lease + act path, VNC arbitration, calibrate on real screen, live model loop via Mac proxy.
