• I'll start by gathering the review context: the r3 spec, both review docs, and the diff scope.

• I have the full picture of the r3 scope. Now let me examine the actual r3 diff and the current state of the changed files.

• The diff is compact. Now I need to read the full current state of the key files to review the fix in context — especially `ctl.c` (dispatch, tick, notify), `mk20_kbd.c` (run loop), `hid.c`, and the new tests.

• Now let me read the main loop, kbd.c, K2 e-stop, gd32io.c, and the new r3 tests.

• Now the new tests and the K2 e-stop module:

• I have the full r3 changes. Now let me verify the cross-references: remaining callers of changed APIs, clock initialization coverage, K2 constants, test helpers, and that release scripts still never pass `--ctl-allow`.

• Let me verify the screen-grant API used by the new test, the test harness wiring, and check for any uninitialized `ctl_t`/`hid_t` usage across tests.

• All test structs go through the init functions that install default clocks. Let me check how the test binary is invoked (Makefile / test runner) and read the final new-test region in full context to double-check compile-level details.

• All key evidence is in hand. Let me pin down exact line numbers in `test_kbd.c` and the plan doc for citations, then double-check the one remaining production caller pattern (estop path gating).

• # MK20 agent A1 fix-round-3 review (r4, read-only)

  Tree = `2ea809a` (`git status` clean), diff under review = `3620415..2ea809a` (13 files, +198/−21; no `deck/agent/`, no release-script, no `gd32.c`/`gd32_tx` changes). Review strictly read-only: files, `git log/show/diff` only — no builds or tests run from here; the recorded box run (`make test` green: 24 + 4018×2 ASan/UBSan+TSan + e2e + 45 + 58 + 546) is taken from the commit message.

  ## 1. Disposition of r3 findings

  **R3-F1 (major: socket batches validated with stale timestamp across blocking HID work) — FIXED.**
  - New `dispatch_time()` (deck/kbd/ctl.c:459-467): samples the injectable clock, runs `safety_tick` (production: `kbd_ctl_safety` → `kbd_safety_tick` = K2 hold tick + `ctl_tick`, kbd.c:36, kbd.c:29-34) plus a direct `ctl_tick`, then re-samples and re-loops if time advanced — so a tick that itself blocked (revoke/estop `hid_release_all` ≤50 ms) re-checks all deadlines before returning. Terminates because all tick wait paths are one-shot (token zeroed, `fired` latched, `pending` cleared).
  - Called before **every** buffered line (`read_client`, ctl.c:480) — covering concatenated requests in one 512-byte read and, via per-client `read_client` from `ctl_on_poll` (ctl.c:489-495), across clients — and again **after** each dispatch (ctl.c:484), covering `act allup`/`release`/`estop` bounded waits, including when the waiting request is the batch's last.
  - Production wiring: `mk20_kbd.c:151` sets `ctl.safety_tick/ctx`; the stale shared `now` and the one-shot pre-tick at old `:188` are gone (`mk20_kbd.c:189`). `ctl_handle_line` retains its own `ctl_tick` (ctl.c:291) as a second barrier.
  - Every r3 failure schedule is closed: post-allup key/ptr act hits `lease_ok` with `token==0`→`no_lease` or `estopped`→`stopped` (ctl.c:250-252); renew of an expired lease is revoked by the pre-dispatch tick before validation; overdue K2 hold estops via the callback before the next request.

  **R3-G1 (minor: no socket-dispatch backpressure regression) — FIXED.** `test_ctl_dispatch_backpressure` (tests_ctl.inc:540-595) drives real `socketpair`→`read_client`/`ctl_on_poll` batches (`allup` + key act + renew + ptr in one write) under a full HID pipe, with one shared fake clock whose HID-side accessor advances 990→1020 across the deadline and drains the pipe on the first EAGAIN wait; 8 combos (lease-expiry/K2-hold × 1/2 clients × allup-last). Asserts rejection of all post-deadline requests, no renew, exactly all-up + one neutral pointer report, queue purge. `test_ctl_dispatch_deadlines` (:597-642) adds the tick-waited-across-a-second-deadline re-loop case (lease-expiry wait crossing K2 hold) and confirm/grant expiry mid-batch. Wired into `--ctl-unit`, new `--ctl-dispatch-unit`, and the default sanitizer run (test_kbd.c:270,273-274,282; Makefile runs the full binary under ASan/UBSan and TSan).

  **R3-D1 (minor: stale A1 status) — FIXED.** deck/AGENT-BUILD-PLAN.md:326-328 records the b46c562 green Linux suite, the r3 change summary, the still-pending Linux re-validation of this round and device/release-artifact prerequisites, and both kimi r3 nits as follow-ups.

  **Kimi r3 nit 1 (gd32io 32-bit ms clock) — FIXED.** `now_ms`/`deadline`/`left` are `int64_t` (gd32io.c:44-56); `poll(&pf,1,(int)left)` is safe (`left ≤ timeout_ms`, an `int`); no printf of these values; `gd32_tx` and the write allow-list untouched (diff is timing-only).

  **Kimi r3 nit 2 (K2 e2e window can't isolate the timeout bound)** — OPEN as a documented follow-up (plan:328); the fix spec explicitly allowed documentation. Acceptable.
  **Kimi r3 nit 3 (single canary in seeded test)** — unchanged; ASan/UBSan coverage makes it acceptable. Nit stands.
  **Kimi r3 nit 4 (release binary older than sources)** — ADDRESSED: `bin/mk20-kbd` rebuilt in 2ea809a (529728 B, content changed); source-match is covered by the box's 546-green pytest run. Not verifiable from a read-only Mac seat.
  **Kimi r3 note 5 (`test_rollback_guard` flake)** — unrelated to A1; watch item.

  ## 2. New-defect scan of the r3 fix (requested areas)

  - **Fresh-clock safety per batch / across clients / after waits** — verified above; no remaining production path validates a socket request against a non-fresh timestamp (only callers of `ctl_handle_line`: `read_client`; grep-confirmed).
  - **Injectable clock correctness** — defaults installed in `ctl_init` (ctl.c:135-139,146) and `hid_init` (hid.c:11,17); both are CLOCK_MONOTONIC in production, so no skew; every test `ctl_t`/`hid_t` goes through init (grep-verified), so no NULL function pointer; `hid_release_all`'s two clock reads per loop (hid.c:227,230) are exactly what the test's side-effecting fake models.
  - **Socket-dispatch backpressure** — replies stay `MSG_DONTWAIT` with drop-on-stall (ctl.c:157-158); per-line work is bounded (2 clock reads + idempotent ticks + parse); tick waits are one-shot safety events a client cannot amplify; mid-batch drop of the current client is handled (ctl.c:481 + loop condition + notify's `cfd<0` guard); a dropped other-client can't be confused by fd reuse within one `ctl_on_poll` pass (accepts happen first, drops later never match stale `revents`).
  - **gd32io int64 on armhf** — types/casts/comparisons sound; 64-bit mul on `tv_sec` is a compiler libcall, fine statically; zero change to GD32 writes.
  - **E-stop always wins / not socket-clearable** — `estop` op is ungated (ctl.c:345-348); `estopped` precedes all lease validation (ctl.c:250,310,329,354); only `ctl_set_allowed(…,1)` clears the latch (ctl.c:218) and its sole caller is dev-only `--ctl-allow` (mk20_kbd.c:155), absent from all release scripts (grep of `deck/firmware/release` text files: no match). Ticks can only move toward stop/revoke, never toward allow.
  - **Purge on every revoke path** — unchanged and intact: all paths funnel into `lease_revoke`→`hid_release_all`→`pointer_cancel` before any neutral write (hid.c:219-222); the new tests assert purge of queued ptr+scroll inside batches (tests_ctl.inc:557,591).
  - **Hostile socket JSON memory safety** — no new parser surface; `rep[CTL_LINE_MAX]` writes guarded by `n>0` with all handler returns `< cap` (ctl.c:479,485); overlong-line drop path intact with fresh-clock `client_gone` (ctl.c:476); seeded malformed-input tests still wired.

  ## 3. Findings

  1. **nit** — Redundant double `ctl_tick` per dispatch in production: `kbd_ctl_safety` already ends in `ctl_tick`, then `dispatch_time` runs it again (ctl.c:463-464), and `ctl_handle_line` ticks a third time (ctl.c:291). All idempotent; pure µs overhead. Fix (optional): skip the direct `ctl_tick` when `safety_tick` is set, or comment that the direct call covers the no-callback unit-test path.
  2. **nit** — `dispatch_time` termination relies on ms-granularity clock equality (ctl.c:465): correct for CLOCK_MONOTONIC-ms and the ms fakes, but a future sub-ms-resolution injected clock would loop until two reads collide. Fix (optional): document the granularity assumption on `clock_ms` in ctl.h:36, or cap iterations.
  3. **minor (process)** — The rebuilt `bin/mk20-kbd` in 2ea809a contradicts the letter of the fix-r3 hard rule ("do not touch deck/firmware/release/bin/*"), same as r2's sanctioned rebuild; box pytest binary-match reportedly green. Not a code defect, but the rule vs. practice discrepancy should be stated explicitly in the task specs.

  ## 4. Test gaps

  - No real-binary e2e drives a socket batch across a deadline under HID backpressure — the spec accepted injectable-clock unit tests, and the e2e socket-estop path does exercise the new `dispatch_time`+`kbd_ctl_safety` wiring with the real clock, but a `stalled_host`-style batch variant would close the last loop-level hole. (Unit coverage is genuinely strong: 8 backpressure combos + double-deadline re-loop + confirm/grant mid-batch.)
  - The drop-current-client-mid-batch branch (ctl.c:481) and holder-served-second client ordering are untested (both need a full client send buffer mid-dispatch / specific slot order).
  - gd32io int64 `wait_echo` has no new timing test (pre-existing `test_knob_init` covers function; the 24.8-day wrap is untestable) — acceptable.
  - This Mac seat ran nothing (read-only mandate); macOS ASan syntax/run verification of the new tests is unperformed here and relies on the recorded Linux box run.

  All three codex r3 findings are fixed with genuine regressions that fail against the r2 code by construction (stale `now` would let id 3/4/5 through and renew the dead lease), the kimi r3 nits are fixed or documented per spec, and no new blocker/major defects were found.

  VERDICT: GO

