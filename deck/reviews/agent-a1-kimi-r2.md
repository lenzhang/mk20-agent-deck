# agent A1 cross-review — Kimi K3 (kimi-code/k3), r2

Run 2026-10-19 ~16:17–16:40 +0800, `kimi -p` in a throwaway snapshot of the 10a1374 tree (verified unmodified afterwards). Route: kimi CLI default config (no proxy configured), api.kimi.com via TUN rule DomainSuffix kimi.com -> DIRECT; proxy config untouched.

> You are a READ-ONLY code reviewer. Do not edit, create or delete any file, do not run git commands that change state, do not access any device, serial port, adb or network. Only read files (ls, cat, rg, git status, git diff).
> 
> Repo: this directory (snapshot). The UNCOMMITTED changes (git diff + untracked files) are the MK20 agent A1 work after a fix round. Previous reviews: deck/reviews/agent-a1-kimi-r1.md (yours, NO-GO) and deck/reviews/agent-a1-codex-accept-r1.md (FAIL). Fix spec: ../mk20-a1-fix-task.md. Original spec: ../mk20-a1-task.md. Design: deck/AGENT-BUILD-PLAN.md 3.4/3.4b/3.5/A1. Ignore deck/agent/vendor/cjson (upstream cJSON 1.7.18).
> 
> Do: (1) for every finding in both r1 reports, state FIXED / NOT FIXED / PARTIAL with file:line evidence; (2) review the fix code for new defects: memory safety on hostile socket/network JSON, e-stop always wins and cannot be cleared from the socket, purge of queued pointer reports on every revoke path, 64-bit ms time base on 32-bit armhf (types, printf formats, comparisons), mk20d boot -> kbd supervisor flag passing (defaults unchanged, scroll descriptor and --hid-scroll cannot disagree, --ctl-allow never passed), K2 --k2-release semantics, oai strict JSON validation; (3) test gaps. Output Markdown: findings with severity (blocker/major/minor/nit), file:line, fix; final line "VERDICT: GO" or "VERDICT: NO-GO".

---

• I'll start by reading the previous review reports and the specs, then examine the uncommitted changes.

• Now let me get the git status and the original spec.

• mk20d looks right. Now let me check how gadget.sh is invoked outside boot (flap rollback etc.) and who consumes gadget.env — to confirm descriptor/daemon can't diverge on re-setup.

• gadget.sh consumes `MK20_HID_SCROLL` from gadget.env on re-setup (:42) — consistent with boot-resolved env. Now the release tests and the F2 HID purge.

• Now let me read the full hid.c to verify wr() semantics and the complete F2 fix, then ctl.c.

• hid.c purge logic looks sound. Now the core: ctl.c in full.

• ctl.c read. Now ctl.h, ctlscr.c, k2estop.c/.h.

• Now let me check who calls `ctl_set_allowed` / `ctl_screen_grant` (keyboard-only paths), and read the oai.c fix areas.

• Now the agent side: oai.c and oai_cli.c.

• oai looks solid so far. Now oai.h, the agent Makefile, and the test files.

• Now the test side: tests_ctl.inc changes, test_kbd.c, e2e_run.py.

• Let me check test_mk20d.py's dev/conf/run_boot fixtures to validate the new test's conf-rewrite path.

• `dev.run` merges env — the override parametrization is real. Let me check for other launchers of `mk20d kbd` and then read the docs (CTL-PROTOCOL.md, plan diff, README diff).

• CTL-PROTOCOL.md covers F7, the 24-key cap, negative id, /run/mk20, purge semantics, F3, F4. Now the plan/README/Makefile diffs.

• Now the oai test files.

• Tests look strong. Let me see the middle of the tests_ctl.inc diff I skipped (test_k2_estop, screen rate limits).

• Let me check for leftover 32-bit `long` time uses across the kbd sources and verify GD32_IN_KEY_EVENT, plus look at the top of tests_ctl.inc.

• Let me view the e2e context around the new K2 block and check helper functions in tests_ctl.inc.

• All revoke paths funnel through `hid_release_all`. Now read-only verification: syntax checks (no files created) and shell syntax.

• Syntax checks pass. Let me verify the fake_model API used by test_oai.py and glance at agent/.gitignore.

• Let me view the remaining unseen diff chunk of tests_ctl.inc (lines ~100-160 of the diff) to make sure existing tests weren't semantically altered.

• Final checks: whitespace, untouched binaries, and a look at remaining repo state.

• Let me diff ctl.c against HEAD to catch every change precisely.

• The full picture is clear. A few last consistency checks: test includes, and whether the kbd unit tests reference anything missing.

• One last hostile-input corner: how the vendored cJSON handles lone surrogates (the oai strict scanner lets them through to cJSON).

• Review complete. All evidence gathered; here is the report.

  # MK20 agent A1 fix-round review (r2)

  Read-only review of the uncommitted state (diff vs `d172921` + untracked files). Verified by full-file reads of `ctl.c`, `ctlscr.c`, `k2estop.c/.h`, `hid.c/.h`, `kbd.c`, `mk20_kbd.c`, `oai.c`, `oai_cli.c`, `mk20d`, `mk20lib.sh`, `gadget.sh`, the test files and docs; cross-checked callers and the test harness. Ran only read-only checks: `cc -fsyntax-only -std=c11 -Wall -Wextra -Werror` clean for all kbd and agent sources (incl. `-DCJSON_NESTING_LIMIT=32`), `sh -n` clean for the three shell scripts, `ast.parse` clean for the new/changed Python tests, `git diff --check` clean. Linux-only suites (SO_PEERCRED, pty e2e, busybox boot harness) cannot run on this Mac; `shellcheck` and `busybox` are not installed here.

  ## Summary

  Both r1 blockers (release flags lost at the supervisor boundary; queued pointer reports surviving e-stop) and every other r1 finding are fixed, each with a regression test that exercises the real path. The fix code itself holds up under the requested hostile-input, e-stop, purge, 32-bit-armhf, wiring, K2 and strict-JSON checks. Remaining items are nits or explicitly deferred by the fix spec.

  ## Disposition of r1 findings

  ### deck/reviews/agent-a1-kimi-r1.md

  1. **BLOCKER — `mk20d kbd` never loaded config → FIXED.** boot resolves once via `cfg_load` (mk20d:180) and passes all four values as env into the fresh supervisor shell (mk20d:220-223); the `kbd)` branch consumes them with off-defaults and never reloads the conf (mk20d:335-340), so flags are frozen with the descriptor decision. `cfg_load` exports `MK20_HID_SCROLL` (mk20lib.sh:107) and boot persists it to `gadget.env` (mk20d:199-201), which gadget.sh reads back on later runs (gadget.sh:42) — descriptor and `--hid-scroll` single-source the same boot-resolved value even if mk20.conf changes after boot. `set --` is reset each supervise-loop iteration (mk20d:336), so no argv accumulation across restarts. `--ctl-allow` appears nowhere in the release.
  2. **MAJOR — tests blind to the boundary → FIXED.** `test_boot_supervisor_flags_and_descriptor` (deck/tests/test_agent_release.py:42-88) drives real `mk20d boot` → detached `mk20d kbd` → fake mk20-kbd argv, parametrized over defaults/each flag/all flags/env overrides/bad env, and rewrites mk20.conf *after* gadget setup to prove the supervisor uses boot-frozen values; asserts descriptor ↔ `--hid-scroll` agreement and `--ctl-allow` absence. Fixtures verified compatible (`dev.run` merges env, conftest.py:46-50; `kb` fake records `gadget.boot.env`, test_mk20d_kbd.py:52).
  3. **MINOR — orphaned pages brick D8 → FIXED.** `ctl_screen_gone` frees the departed client's pages (ctlscr.c:15-21); estop/disallow keep pages via `ctl_screen_reset` (ctlscr.c:10-14). Test: 8 creates → disconnect → reconnect → create succeeds (tests_ctl.inc `test_ctl_screen_disconnect`); doc updated (CTL-PROTOCOL.md:99-101).
  4. **MINOR — `hex4` accepted control bytes → FIXED.** Explicit `[0-9a-fA-F]` ranges (ctl.c:21-25); regression loops bytes 0x10..0x19 in all four digits (`test_ctl_json_edges`).
  5. **MINOR — 32-bit `long` ms clock → FIXED.** `mono_ms` returns `int64_t` in mk20_kbd.c:83, hid.c:11, kbd.c:47/113/118; all deadlines/windows are `int64_t` (ctl.h:37-40, ctlscr.h:24-25, k2estop.h:9); printf uses `PRId64` (ctl.c:291-292); `%ld` remains only for `long` values bounded ≤ 999999999 by `ctl_json_int`. `test_ctl_long_uptime` runs lease expiry, confirm timeout, rate window, grant and K2 hold at 2³¹−100 ms.
  6. **MINOR — stale "read-only status client" plan text → FIXED.** Plan §3.4 now documents equal-permission clients (AGENT-BUILD-PLAN.md:168); CTL-PROTOCOL.md:11-12 agrees.
  7. **MINOR — test gaps → FIXED (except fuzz, see Test gaps).** `H()` uses `CTL_LINE_MAX` (tests_ctl.inc:56); surrogate pair/lone-surrogate/hex-control/9-10-digit-int/negative-id/24-key-cap tests in `test_ctl_json_edges`; `/run/mk20` creation asserted in the boot test (test_agent_release.py:65,79).
  8. **NIT — dead `/tmp/mk20-hid-scroll` marker → DEFERRED per fix spec**, recorded as A7 follow-up (AGENT-BUILD-PLAN.md:379).
  9. **NIT — 24-key cap undocumented → FIXED** (CTL-PROTOCOL.md:15, plus boundary test at 24 ok / 25 rejected).
  10. **NIT — negative `id` echoed → FIXED.** `id < 0` → `bad_request` (ctl.c:286); documented (CTL-PROTOCOL.md:20-22); tested (`bad_ids` table).
  11. **NIT — key file trailing LF → FIXED.** One trailing LF stripped before validation (oai_cli.c:74); `test_header_key_file_one_lf` covers LF/CRLF/double-LF/embedded-LF/513-byte boundary.
  12. **NIT — `screen.page.show` unrate-limited → DEFERRED per fix spec**, recorded as A7 follow-up (AGENT-BUILD-PLAN.md:379).
  13. **NIT — quote return / socket dir → doc note added** (CTL-PROTOCOL.md:5). The unchecked `ctl_json_quote` return remains (ctl.c:289) but is still provably safe: summaries are pre-validated ≤120 chars, worst quoted form 482 B against a 965 B buffer. Carried as a residual nit.

  ### deck/reviews/agent-a1-codex-accept-r1.md

  - **F1 BLOCKER → FIXED** (see kimi 1/2 above).
  - **F2 BLOCKER → FIXED.** `pointer_cancel` (hid.c:199-210) purges the queue and re-queues one neutral report when anything was queued **or** desired **or** last-sent buttons were down (`need = ptr_n || ptr_btn || ptr_sent[1]`); it runs inside `hid_release_all` (hid.c:220) and `hid_pointer_release` (hid.c:213), so every revoke path — estop, expiry, disable, client-gone, human preempt, release, `act allup` (ctl.c:160,277 via `lease_revoke`) — purges before the neutral writes. `ptr_sent` is updated only after a fully accepted write (hid.c:53-57; `wr` treats short writes as errors, hid.c:38-39). `test_ctl_backpressure` fills a nonblocking pipe, queues click+scroll, and asserts only kbd-up + one neutral pointer report follow for all 7 revoke modes × sent-buttons 0/1, plus the "sent buttons down, queue empty" case.
  - **F3 MAJOR → FIXED** (see kimi 3).
  - **F4 MAJOR → FIXED.** `--k2-release` / `FEATURE_K2_RELEASE` (default 0) initialize `reports_release=1` so the first 1 s hold fires from the poll timer (k2estop.h:8-10, mk20_kbd.c:114/250, mk20d:222/340, mk20lib.sh:50/61/76/106). Without it, press-only triple mode + learn-on-first-release is unchanged (k2estop.c:12-24). Cold-start e2e through the real binary's poll loop (e2e_run.py:255-276); rotation slots 104/105 inert (k2estop.c:11, tested). The flag alone does not enable K2 (gated by `k->k2_estop`, kbd.c:112; documented CTL-PROTOCOL.md:163).
  - **F5 MAJOR → FIXED.** A strict grammar scanner (oai.c:16-93) runs before any cJSON allocation for response bodies, tool schemas and per-call arguments (oai.c:94-98,165,237): rejects `01`, `1.`, `.5`, `+1`, `-01`, bare control chars, bad escapes, trailing garbage, >32 depth, >4096 nodes. Argument failures → per-call `invalid_arguments` (oai.c:238). Tests: `strict_json_test` (test_oai.c:56-80) and `test_strict_response_and_tool_arguments` (test_oai.py:127-135). Vendored cJSON additionally rejects lone surrogates (cJSON.c:680-709), so the double-parse cannot disagree in the accept direction.
  - **F6 MINOR → FIXED.** `finish_reason:"tool_calls"` with absent/empty/`null` `tool_calls` → `bad_response` (oai.c:230); tested in C (test_oai.c:73-77) and pytest (test_oai.py:138-141).
  - **F7 MINOR → FIXED** (CTL-PROTOCOL.md:3 shows `--run --ctl`).

  ## Fix-code defect review (requested areas)

  - **Hostile socket/network JSON:** all ctl-side parsing bounded (1023-byte lines, 24-key cap, dup-key rejection, NUL rejection, strict escapes/UTF-8); all oai-side input pre-bounded then strict-scanned then cJSON-parsed with end-pointer check. No new memory-safety defects found.
  - **E-stop always wins / cannot be cleared from socket:** no socket op reaches `ctl_set_allowed` (only caller is the dev-only `--ctl-allow`, mk20_kbd.c:154, never passed by the release; asserted by test). `estop` op, page key, K2 only ever latch. Confirmed no re-allow path.
  - **Purge on every revoke path:** all paths funnel through `lease_revoke`/`hid_release_all`; `pointer_cancel` semantics match the spec exactly, including the sent-state case.
  - **64-bit ms on armhf:** types, `PRId64` formats and comparisons consistent everywhere; 2³¹-boundary test present.
  - **boot → kbd flag passing:** defaults byte-identical (empty `"$@"` with no flags; existing exact-argv test test_mk20d_kbd.py:99-101 still holds); descriptor and `--hid-scroll` cannot disagree (single boot-resolved source, verified end-to-end); `--ctl-allow` never passed.
  - **K2 `--k2-release`:** semantics as specified; no GD32 writes added (k2estop.c is pure; gd32io/gd32.c unchanged).
  - **oai strict JSON:** as analyzed above; the only non-strict parse left is the oai_cli *request-spec* file (oai_cli.c:23) — local fixture input, not network; noted for completeness.

  ## New findings

  None at blocker/major level. Residual nits:

  1. **nit** — ctl.c:289: unchecked `ctl_json_quote` return (provably safe today; a one-line comment stating the 482 B ≤ 965 B invariant would future-proof it).
  2. **nit** — `test_release_binaries_match_source` (test_mk20d_router.py:142-162) will fail on the next Linux box run until `bin/mk20-kbd` is rebuilt via mk20-build-bins.sh (sources gained ctlscr.c/k2estop.c; the committed binary predates them). This is the intended workflow (hard rule forbids touching bin/* here), but the rebuild must happen before that test is expected to pass.

  ## Test gaps

  - Linux-only suites (full kbd ASan/UBSan/TSan suite, e2e incl. the new cold-K2-hold test, busybox boot harness) are unverifiable from this Mac; syntax/type-cleanliness verified instead.
  - The queued-pointer purge is covered at unit level only; no real-binary e2e drives HID backpressure.
  - Still no fuzz/property test of the ctl line parser (r1 suggestion, not mandated by the fix spec).
  - Device-side K2 press/release capability remains unmeasured by design; `--k2-release` is the measured-firmware switch.
  - shellcheck could not be run here (not installed); `sh -n` is clean.

  VERDICT: GO

