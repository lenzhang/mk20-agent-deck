# agent A1 cross-review — Kimi K3 (kimi-code/k3), r3

Run 2026-10-19 ~17:19–17:37 +0800, `kimi -p` in a throwaway snapshot of the b46c562 tree (minus the rebuilt binary). Route: kimi CLI default config (no proxy configured), api.kimi.com via TUN rule DomainSuffix kimi.com -> DIRECT; proxy config untouched.

> You are a READ-ONLY code reviewer. Do not edit, create or delete any file, do not run git commands that change state, do not access any device, serial port, adb or network. Only read files (ls, cat, rg, git status, git diff).
> 
> Repo: this directory (snapshot). The UNCOMMITTED changes (git diff + untracked files) are the MK20 agent A1 work after two fix rounds. Previous reviews: deck/reviews/agent-a1-kimi-r1.md, agent-a1-kimi-r2.md (yours: NO-GO then GO), agent-a1-codex-accept-r1.md and -r2.md (both FAIL; r2 found R2-F1 host send could delay e-stop, R2-F2 HID flush before deadline ticks). Fix specs: ../mk20-a1-fix-task.md, ../mk20-a1-fix-r2-task.md. Focus on the r2 fix round (kbd.c, mk20_kbd.c, ctl.c, tests_ctl.inc, e2e_run.py). Original spec: ../mk20-a1-task.md. Design: deck/AGENT-BUILD-PLAN.md 3.4/3.4b/3.5/A1. Ignore deck/agent/vendor/cjson (upstream cJSON 1.7.18).
> 
> Do: (1) for every finding in the codex r2 report (and any open r1 item), state FIXED / NOT FIXED / PARTIAL with file:line evidence; (2) review the fix code for new defects: memory safety on hostile socket/network JSON, e-stop always wins and cannot be cleared from the socket, purge of queued pointer reports on every revoke path, 64-bit ms time base on 32-bit armhf (types, printf formats, comparisons), mk20d boot -> kbd supervisor flag passing (defaults unchanged, scroll descriptor and --hid-scroll cannot disagree, --ctl-allow never passed), K2 --k2-release semantics, oai strict JSON validation; (3) test gaps. Output Markdown: findings with severity (blocker/major/minor/nit), file:line, fix; final line "VERDICT: GO" or "VERDICT: NO-GO".

---

• I'll start by surveying the repo state and reading the review documents and specs.

• Now let me read the fix specs and my previous reviews.

• Now let me read the core files of the r2 fix round: kbd.c, mk20_kbd.c, ctl.c, and the headers.

• Now let me read the test files — tests_ctl.inc and e2e_run.py.

• Now let me read ctl.h, ctlscr.c, and check the constants and screen/key handling.

• ctlscr.c looks consistent. Now let me check the test harness wiring and the oai-side seeded random test (R2-G2 covers both ctl and oai).

• Both seeded tests are wired. Now let me see the git diff for kbd.c and mk20_kbd.c to isolate what changed vs HEAD.

• The wiring is confirmed. Now I need to examine oai.c carefully — particularly the strict JSON scanner and how tool-call arguments are validated, since the seeded test expects escaped control bytes in arguments to fail.

• oai.c verified — the strict scanner runs on the *decoded* arguments string, so escaped control bytes become raw and get rejected. Now the remaining diffs: test_kbd.c, ctl.c vs HEAD, and the plan/protocol docs.

• Now let me check CTL-PROTOCOL.md for r2-related doc updates and verify the release scripts are unchanged from what r2 reviewed.

• CTL-PROTOCOL.md documents the new behavior. Let me verify no production path calls `hid_service`/`hid flush` outside the safety-tick-wrapped `kbd_service`, and check the agent Makefile wiring.

• Two things to chase: the remaining `long now_ms` in gd32io.c, and the release-binary staleness. Let me look at gd32io.c.

• The repo contains the fix-round commits on another branch. Let me compare this snapshot's uncommitted content against commit b46c562 ("agent A1 fix r2") to see exactly what this tree corresponds to, especially the release binary.

• The working tree differs from b46c562. Let me see exactly how — this determines whether the snapshot matches the tested state.

• The binary differs from b46c562's rebuilt one. Let me verify whether the source content here is identical to the r2-fix commit b46c562 (the tested state).

• All source files are byte-identical to the tested r2-fix commit b46c562; only the release binary differs. Let me check the remaining loose ends: the box-test blemish (test_rollback_guard), `--ctl-allow` absence in release scripts, and the gd32io.c `long` clock scope.

• Let me run the read-only syntax verification on the changed C sources (as in prior rounds), then finalize.

• All checks pass. I have everything needed — writing the review report now.

  ---

  # MK20 agent A1 fix-round-2 review (Kimi K3, r3)

  Read-only review of the uncommitted state (HEAD `d172921` + diff + untracked). Scope per prompt: codex r2 findings (R2-F1/F2/G1/G2), any open r1 items, new-defect scan of the r2 fix code, test gaps. The working tree's 25 key source/test/doc files are **byte-identical to commit `b46c562` ("agent A1 fix r2")** present on a sibling branch, so that commit's recorded box results (`make test`: 24 sim + 3829×2 kbd ASan/UBSan+TSan checks + real-binary e2e + 45 agent + 58 tools + 545/546 pytest) apply to exactly this content. From this Mac I ran only read-only checks: `cc -fsyntax-only -std=c11 -Wall -Wextra -Werror` clean for all kbd + agent sources and both test files, `sh -n` clean for mk20d/mk20lib.sh/gadget.sh, `ast.parse` clean for the Python tests.

  ## 1. Disposition of codex r2 findings

  **R2-F1 (major: host send could delay e-stop >100 ms) — FIXED.**
  - Safety ordering in the event path: `kbd_on_gd32` now ticks safety with a fresh clock reading (`kbd.c:122-123`), then K2 (`kbd.c:125-128`), human preemption and the reserved-page key (`kbd.c:129-132`) all run **before** the host forward (`kbd.c:133-135`); a second fresh `kbd_safety_tick` precedes the PTT implicit HID flush (`kbd.c:138-140`).
  - `kbd_host_send` never waits when `k->ctl || k->k2_estop`: EAGAIN → drop remainder, count `host_tx_timeouts`/`host_tx_errors`, set `host_tx_stuck` (`kbd.c:58-59`). Legacy 200 ms bounded wait + fail-fast is retained verbatim when no safety flag is active (`kbd.c:60-66`), so default behavior is unchanged.
  - Loop ordering: socket requests are serviced before host work (`mk20_kbd.c:188` before `:190-205`); with safety active, host bytes are only buffered in the read branch (`:193`) and **one** buffered frame is decoded per poll iteration (`:201-205`) with `to = 0` between frames (`:178`) and a `kbd_service` after each frame (`:205`). GD32 reads are capped at 256 B/iteration under safety (`:183`).
  - Real-binary regression: `stalled_host_safety` (e2e_run.py:69-135) pre-fills the daemon→host pty before launch, verifies `host_tx_timeouts == 0` (no legacy fail-fast shortcut), SIGSTOPs the daemon, queues a getInfo frame + socket estop (or GD32 stop events), SIGCONTs, and asserts all-up within 100 ms (1000+100 ms for `k2_hold`) for all five stop paths: socket, reserved key, human preempt, K2 triple, K2 hold. The old code (forward-then-handle, 200 ms wait) fails every one of these.

  **R2-F2 (major: expired actions could flush before deadline checks) — FIXED.**
  - New `kbd_service` = `kbd_safety_tick` (K2 tick, then `ctl_tick`) **then** `hid_service` (kbd.c:29-40), used at loop entry (mk20_kbd.c:169), on HID POLLOUT (`:189`), and after host-frame work (`:205`); plus `kbd_safety_tick` right after poll (`:181`) and before `ctl_on_poll` (`:188`). Every production HID flush is now preceded by a fresh-timestamp safety tick — verified by grep: no other production caller of `hid_service` remains; the in-`hid.c` flushers (`hid_set_mod`/`hid_key`/`ptr_queue`) are only reached via `kbd_on_gd32` (tick at kbd.c:123/139) or `ctl_handle_line` (tick at ctl.c:285 + mk20_kbd.c:188).
  - Production-order regression: `test_kbd_safety_backpressure` (tests_ctl.inc:480-508) drives the real `kbd_service` — queue key+pointer+scroll under a full pipe at t=999, unblock at t=1001 past the deadline — for both lease expiry (estop-free revoke) and elapsed K2 hold (latched estop, `stop_reason=="k2_hold"`); only the all-up + one neutral pointer report (last *sent* position, buttons/wheel/pan zeroed) come out. The old `test_ctl_backpressure` remains as purge-semantics coverage across all 7 revoke paths × sent-buttons 0/1.

  **R2-G1 (minor: K2 e2e didn't isolate timer-only wakeup) — FIXED.**
  - e2e_run.py:346-360: after the K2-down frame the test sends nothing — a 0.65 s guard asserts zero traffic on both fds, then it collects the `estop` socket event and the all-up report, both asserted within [0.95, 1.10] s. Only a poll timeout can wake the loop. (See nit 2 for a discrimination caveat.)

  **R2-G2 (minor: quote-return + randomized malformed coverage) — FIXED.**
  - ctl.c:290 checks `ctl_json_quote`'s return and fails the status reply with `bad_args`; regression via a corrupted `ui.summary` (tests_ctl.inc:581-583). This also closes the residual half of kimi-r1 #13.
  - Seeded (xorshift, fixed seed `0xa1200202`) malformed-input tests on both parsers under ASan/UBSan: `test_ctl_malformed_seeded` (tests_ctl.inc:586-614 — 512 iterations, corpus mutation or random bytes, guaranteed-malformed `0x01` terminator, randomized reply cap down to 16 B, canary byte, and a no-state-mutation invariant) and `malformed_seeded_test` (test_oai.c:92-126 — same for `oai_parse_response`, plus per-call `invalid_arguments` for escaped-control argument strings). I verified the oai expectation holds: `parse()` strict-scans the **decoded** arguments text (oai.c:237), where escaped `\u0001` has become a raw control byte rejected at oai.c:24.

  ## 2. Open r1 items

  - **kimi #8 (dead `/tmp/mk20-hid-scroll` marker)** — NOT FIXED, explicitly deferred by the fix spec; recorded as A7 follow-up (AGENT-BUILD-PLAN.md:379). Acceptable.
  - **kimi #12 (page.show/layout/create rate limits)** — NOT FIXED, same deferral (plan:379). Acceptable.
  - **kimi #13** — quote-return half FIXED (above); socket-dir half was closed in r1 by doc note + boot test.
  - All codex r1 findings (F1-F7) and the remaining kimi r1 findings were verified FIXED in my r2 review; the relevant files are unchanged since and remain correct.

  ## 3. New-defect scan of the r2 fix code (requested areas)

  - **Hostile socket/network JSON:** no new exposure. The r2 changes add no parser surface; the seeded tests strengthen regression coverage on both sides. ctl line parser still bounds everything (24-key cap, strict escapes/UTF-8, dup keys, NUL, ≤1023 B); oai pre-bounds then strict-scans then cJSON-parses with end-pointer equality.
  - **E-stop always wins / not socket-clearable:** unchanged and intact — no socket op reaches `ctl_set_allowed`; only the dev-only `--ctl-allow` calls it (mk20_kbd.c:154), and `--ctl-allow` appears nowhere under deck/firmware/release (grep-verified).
  - **Purge on every revoke path:** all eleven revoke/teardown paths funnel into `hid_release_all`, which runs `pointer_cancel` before any neutral write (hid.c:218-228); with the r2 ordering, the purge is also guaranteed to precede every subsequent flush.
  - **64-bit ms on armhf:** all A1 code uses `int64_t` ms + `PRId64` (mk20_kbd.c:83, kbd.c:60-61/122/138, hid.c:11, ctl.c:292-293); `%ld` remains only for `long`s provably ≤999999999. `test_ctl_long_uptime` crosses 2³¹ ms for lease/confirm/grant/rate/K2.
  - **boot → kbd supervisor flags:** unchanged since my r2 GO and re-verified: boot resolves once, passes `MK20_AGENT_CTL/HID_SCROLL/K2_ESTOP/K2_RELEASE` into the fresh supervisor shell (mk20d:220-223); `kbd)` consumes with `:-0` defaults and `set --` reset per restart (`:336-340`); `gadget.env` persists the same `MK20_HID_SCROLL`; defaults byte-identical (empty `"$@"`).
  - **K2 `--k2-release`:** unchanged; `k2_estop_init(&k.k2, o->k2_release)` (mk20_kbd.c:114); release-capable cold-start first-hold now covered timer-only (above).
  - **oai strict JSON:** unchanged; seeded test confirms malformed-body → `OAI_BAD_RESPONSE` and bad arguments → per-call `invalid_arguments`, not a crash or acceptance.

  ## 4. Findings

  1. **nit** — gd32io.c:44-53 keeps a 32-bit `long` ms clock (knob-init echo waits). Pre-existing, outside fix-spec item 9's file list, startup-only and bounded (≤18 s worst case); a 24.8-day wrap exactly during knob init is the only exposure. Optional follow-up: same `int64_t` treatment for uniformity.
  2. **nit** — the timer-only K2 e2e window ([0.95, 1.10] s) cannot distinguish the `k2_estop_timeout` bound (mk20_kbd.c:177) from the 100 ms HID base tick — both land in-window, so deleting `:177` would not fail the test. The bound's existence is source-verified; tightening would need the HID closed during the hold (250 ms base tick) or a unit-level poll-timeout assertion.
  3. **nit** — `test_ctl_malformed_seeded` guards reply writes with a single canary byte at `rep[cap]`; a hypothetical overflow landing past `cap` but within the buffer escapes it. ASan/UBSan (which the suite runs under) covers real overflows; acceptable as-is.
  4. **minor (process)** — this snapshot's `deck/firmware/release/bin/mk20-kbd` (529696 B) predates the r2 sources; `b46c562` on the sibling branch carries the rebuilt binary (529728 B). On any box with `arm-linux-gnueabihf-gcc`, `test_release_binaries_match_source` (test_mk20d_router.py:141-152) fails against this snapshot until the binary is refreshed — expected here (fix-round hard rule forbids touching `bin/*`), already resolved on the real branch per its commit message.
  5. **note** — `b46c562`'s recorded full pytest run was 545/546: `test_rollback_guard` hit a 60 s hung-cleanup timeout in the full run and passed 46/46 solo; no shell files changed this round, and that test file does not exist in this snapshot's `deck/tests`. Unrelated to the A1 kbd/agent changes; watch it on the next box run.

  ## 5. Test gaps

  - Linux/device items remain pending under the no-device constraint: on-device e-stop latency with host flood now bounds to one host frame's decode time between safety services — worth one on-device measurement alongside the already-pending descriptor/scroll/K2-capability validation.
  - No real-binary e2e drives HID *gadget* backpressure across a lease expiry (the production-order case is covered at unit level by `test_kbd_safety_backpressure`, and host-stall at e2e level); a fifo-full e2e variant would close that last gap.
  - Randomized coverage is bounded at 512 iterations per parser with a fixed seed — fine as regression, not a fuzz campaign.

  All four codex r2 findings are fixed with genuine regressions (the stalled-host e2e fails against the old ordering by construction), the default path is provably untouched, and no new blocker/major defects were found.

  VERDICT: GO

