# A5 Codex self-acceptance — 2026-10-19

**Verdict: FAIL for complete host acceptance.** The implementation and sanitizer/socketpair checks pass; the required real TCP/Unix-daemon acceptance cannot pass in this sandbox. This is not a passing real-socket or device acceptance claim.

Implemented the A4 Kimi minor fixes first, then the action/policy gate, state machine and injectable ports, bounded real kbd client, cooperative cancellation, risk/never-do rules, budgets/retries/no-progress, HID/VNC arbitration, redacted per-step logs, affine calibration with logical-pixel residuals and atomic persistence, and `--loop-test`. A5 source and tests are under `deck/agent/`; kbd is read-only and built from an unchanged temporary copy using its Makefile. Only the A5 status line changed in the plan. No commit/push, external network/provider, device/ADB/serial, vendor, firmware, kernel or onboard-ui changes.

| Evidence | Result |
| --- | --- |
| C11 -Wall -Wextra -Werror; `make -C deck/agent test` | PASS: A5 254, LLM 256, OAI 3145, net/clock 349 checks; RFB/scale/JPEG and CLI regressions; ASan/UBSan clean |
| `build/mk20-agent-nosan` | PASS, same warning policy; no new warning suppression |
| Combined current-source socketpair supplement | PASS: 289 passed, 1 preexisting offline HTTP skip, 9 explicit deselections, 88.17 s |
| A5 coverage within that supplement | 72 passed: harmless task, stop/cancel, real ctl confirmation/early press/reject/timeout, budgets, no progress, arbitration, secure/lock/never-do, retries, log redaction, malformed control replies, disabled permission, renewal and partial-type timeout all-up |
| Standalone A4 socketpair suite | PASS: 49 passed, 16.82 s, no skip/xfail/deselection |
| A5 standalone after startup/cleanup repair | PASS: 72 passed, 6 explicitly deselected, 10.69 s |
| Final standard pytest suite | FAIL: 116 passed, 190 failed, 94.56 s; all failures are 184 TCP bind EPERM + 6 Unix bind EPERM; no skips/xfails |
| Real Unix daemon/FIFO/pty cases after short-path fixture correction | FAIL: 6 explicit kbd socket bind EPERM failures, no skip/xfail |
| `git diff --check` and path-scope audit | PASS |

Failed socket IDs are retained in [agent-a5-socket-blocked.txt](agent-a5-socket-blocked.txt).

The unchanged A2 <1-s compressed-frame wall check was sensitive to cold process startup in preceding runs (correct 100-ms timeout response, 1.054-s wall failure). An isolated cold-build rerun also failed that wall check; repeated requests using the same binaries ran in 0.104–0.120 s. The final combined supplement passed both variants without changing thresholds or adding warm-ups. This sensitivity is disclosed in the [A5 notes](../agent/NOTES-agent-a5.md).

Current kbd v1 does not implement input=vnc leases or hid.health; A5 refuses a real VNC switch without enforced channel ownership. RFB input sending is explicitly unsupported. Trusted window/secure-input detection, cursor localization and actual logical display geometry are required ports; missing hints/calibration fail closed. These dependencies, [D] accuracy/estop/RSS/armhf gates and [U] real-provider/user-present tasks remain listed in the notes. GLM coding uses the existing A4 provider config; no new agentcfg field is required, and no live model compatibility is claimed.

To close host acceptance: rerun the unmodified standard suite on a Linux host allowing loopback and Unix listeners, then inspect the real FIFO HID reports. Device/user gates remain separate. The socketpair control driver calls unchanged keyboard-side allow/render/confirm callbacks; it does not invent a socket enable or confirmation API.
