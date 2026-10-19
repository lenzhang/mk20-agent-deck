# agent A1 cross-review — Kimi K3 (kimi-code CLI, model kimi-code/k3), r1

Run 2026-10-19 ~15:31–15:58 +0800 on dev-mbp.lan, `kimi -p` (non-interactive) in a throwaway snapshot copy of ~/worktrees/mk20-agent-a1 (snapshot verified unmodified afterwards). Route: CLI default config, no proxy set in ~/.kimi-code/config.toml; mihomo connections showed api.kimi.com via TUN, rule DomainSuffix kimi.com -> DIRECT (proxy config not touched). Reviewed tree = commit 73c4a13 content (uncommitted in that worktree). Prompt: ../../mk20-a1-kimi-prompt.txt (copy below).

> You are a READ-ONLY code reviewer. Do not edit, create or delete any file, do not run git commands that change state, do not access any device, serial port, adb or network. Only read files (you may run read-only commands like ls, cat, rg, git status, git diff).
> 
> Repo: this directory. Review the UNCOMMITTED changes (git status, git diff, plus the untracked files: deck/kbd/ctlscr.c/.h, deck/kbd/k2estop.c/.h, deck/kbd/CTL-PROTOCOL.md, deck/agent/src/*, deck/agent/Makefile, deck/agent/tests/test_oai.*, deck/tests/test_agent_release.py; ignore deck/agent/vendor/cjson which is upstream cJSON 1.7.18). The task spec they implement is ../mk20-a1-task.md; the design is deck/AGENT-BUILD-PLAN.md sections 3.4, 3.4b, 3.5 and A1.
> 
> Context: mk20-kbd is a C daemon on an embedded Linux keyboard (T113, static armhf). It is the only owner of /dev/hidg0 (USB HID to the user's Mac) and exposes a unix control socket so an on-device AI agent can type/click under a lease. Safety is paramount: e-stop must always win, the agent must never be able to re-allow itself, defaults must leave behaviour unchanged, no blocking of the key/HID loop, no memory unsafety from socket input. GD32 serial writes other than the allow-listed knob payload are forbidden.
> 
> Check specifically: (1) memory safety / bounds in ctl.c, ctlscr.c, k2estop.c, oai.c (JSON from socket or network is hostile); (2) e-stop, lease, confirm (OK ignored before 300 ms after shown, cancel, timeout, cancellation on estop/disallow/client gone), ui.agent, screen rules (reserved page/key, grant expiry, per-fb 10/s and global 30/s limits, key events only to the agent-owned shown page), K2 hold/triple state machine (no false trigger from rotation slots 104/105); (3) can any socket op re-enable the agent or bypass e-stop; (4) release wiring in mk20lib.sh/mk20d/gadget.sh: defaults off, launch line unchanged by default, scroll descriptor and --hid-scroll can never disagree, --ctl-allow never passed, any issue with /run/mk20 not existing; (5) oai.c: API key never in body, bounded allocation, tool-call argument parse errors handled; (6) test gaps; (7) CTL-PROTOCOL.md accuracy vs code.
> 
> Output a Markdown report: summary, findings list each with severity (blocker/major/minor/nit), file:line, explanation and suggested fix, then a final line "VERDICT: GO" or "VERDICT: NO-GO".

---

• I'll start by surveying the uncommitted changes and file sizes, then read the spec and design docs.

• Now let me read the task spec and the full diff of tracked files.

• The diff is large; let me read it from the saved output file.

• I have the full tracked diff. Now let me read the new untracked files.

• Now the agent-side files.

• Now let me read the full current `ctl.c`, `kbd.c` context, `gd32.h` for slot definitions, and check mk20d's `/run/mk20` handling.

• Now let me examine `kbd.c`, `gd32.h`, and the `mk20_kbd.c` poll loop in full, plus check mk20d's `/run/mk20` handling and remaining references.

• Let me check the remaining wiring details: gadget.sh scroll marker consumers, mk20lib.sh, and the mk20d kbd section.

• The `set --` accumulation in mk20d needs checking — is it inside the restart loop?

• Let me verify a few remaining details: `mono_ms` implementation, cJSON version, .gitignore files, and whether shellcheck/cc are available for read-only checks.

• `mono_ms` uses 32-bit `long` on armhf — noted. Let me run read-only syntax checks on the C code and check MSG_NOSIGNAL portability.

• Syntax checks pass. Now let me verify the status-reply worst-case size concern with a quick computation.

• Two more wiring checks: where mk20d calls `cfg_load` (so `FEATURE_*` exist in `kbd)`), and the boot) gadget launch environment.

• The `kbd)` case may be missing its own `cfg_load`. Let me check mk20d's main flow.

• Potential blocker: the `kbd)` supervisor may never call `cfg_load`. Let me verify the top of mk20d and how the kbd supervisor is launched.

• Confirmed: `kbd)` never calls `cfg_load`. Let me check whether HEAD's mk20d consumed the old `/tmp/mk20-hid-scroll` marker, to understand what the marker was for.

• The marker was never read — dead code. Now let me check how `test_mk20d_kbd.py` captures supervisor launch argv (to gauge the test gap precisely).

• Let me pin down exact line numbers for the findings.

• # Code Review: MK20 A1 remainder + oai adapter skeleton

  Scope reviewed: full diff of 16 tracked files plus all untracked files listed (kbd: `ctlscr.c/.h`, `k2estop.c/.h`, `CTL-PROTOCOL.md`, full current `ctl.c/.h`, `kbd.c`, `mk20_kbd.c`, `gd32.h/.c`; agent: `oai.c/.h`, `oai_cli.c`, Makefile, `test_oai.c/.py`; release: `mk20lib.sh`, `mk20d`, `gadget.sh`, `conftest.py`, `test_agent_release.py`). Read-only review: I did not build or run the test suites (per instructions); I did run `cc -fsyntax-only -Wall -Wextra -Werror` (clean) and verified invariants by hand-computation. `shellcheck` is not installed on this box, so shellcheck-cleanliness is unverified.

  ## Summary

  The C side is in good shape: the flat-JSON parser is strict (duplicate keys, trailing garbage, NUL, nesting all rejected), every socket input is bounded, all replies are `snprintf`-bounded, e-stop cannot be touched by any socket op, confirm/screen/K2 rules match the spec (300 ms guard, grant expiry, 10/s + 30/s sliding windows, reserved page/fb20/estop key, K2 104/105 inert, no GD32 writes). `oai.c` keeps the API key out of the body by construction and bounds all allocations.

  However there is one release-wiring **blocker**: the `mk20d kbd` supervisor never calls `cfg_load`, so the three new `FEATURE_*` variables are always unset in the process that builds the mk20-kbd argv — the flags are dead, and `FEATURE_HID_SCROLL=1` produces exactly the descriptor/daemon mismatch the task forbids. The new pytest cannot see it because it extracts the shell fragment and injects the variables via env.

  ## Findings

  **1. BLOCKER — mk20d `kbd)` never loads config; new flags are never passed; scroll descriptor and daemon can disagree**
  `deck/firmware/release/sbin/mk20d:302-336`
  The `kbd)` case contains no `cfg_load` call (contrast `router)` at `:288`, `boot)` at `:180`). `cfg_set` assigns plain, unexported shell variables, and the supervisor is started as a fresh `sh "$REL/sbin/mk20d" kbd` process (`:220`), so `FEATURE_AGENT_CTL` / `FEATURE_HID_SCROLL` / `FEATURE_K2_ESTOP` are all unset there (no `set -u`, so `[ "" = 1 ]` just evaluates false). Consequences:
  - `--ctl`, `--hid-scroll`, `--k2-estop` are never passed regardless of config — the features cannot be enabled via release switches at all.
  - Worse: `boot)` *does* run `cfg_load`, so `FEATURE_HID_SCROLL=1` is exported as `MK20_HID_SCROLL=1` and persisted to `gadget.env` → gadget.sh builds the 8-byte scroll descriptor (`gadget.sh:412-414`), while mk20-kbd launches without `--hid-scroll` and sends 7-byte reports. That is precisely the "descriptor and `--hid-scroll` can never disagree" violation, in the one configuration a user enables. (HEAD had the same latent mismatch; this change was supposed to fix it.)
  Fix: add `cfg_load` in the `kbd)` case before the flag block (inside the supervise loop like `router)` would also pick up config changes on restart). Defaults remain off either way, so default behaviour is unchanged by the fix.

  **2. MAJOR — release tests structurally blind to finding 1**
  `deck/tests/test_agent_release.py:33-48,51-65`
  `argv()` cuts the `set --` block out of mk20d and runs it with `FEATURE_AGENT_CTL` etc. injected directly into the environment, and `test_descriptor_and_launch_cannot_disagree` feeds the resolved value the same way. This proves the block is correct *given* the variables, but never that mk20d's `kbd)` path sets them — the exact link that is broken. The existing harness already runs the real `kbd)` case against a fake mk20-kbd that records argv (`deck/tests/test_mk20d_kbd.py:18,526`); extend that (or a new test here) to assert end-to-end: config `FEATURE_AGENT_CTL=1`/`FEATURE_HID_SCROLL=1`/`FEATURE_K2_ESTOP=1` → fake binary receives the flags, and the scroll-descriptor fragment and the recorded argv agree, driven by the *same* real mk20d run.

  **3. MINOR — agent pages leak permanently on client disconnect**
  `deck/kbd/ctlscr.c:15-22,98`
  `ctl_screen_gone` retains pages but sets `client = -1`, and every mutating op requires `p->client == ci` (ci is always ≥ 0), so orphaned pages can never be shown, drawn to, or deleted. Eight agent crash-loops fill the table and `screen.page.create` returns `busy` until reboot — D8 is bricked by a restarting agent. CTL-PROTOCOL.md:94-95 documents this, so it is deliberate, but it is a poor failure mode. Suggest allowing deletion of `client == -1` pages (or reclaim by a new connection), keeping "pages kept" semantics for estop/disallow only.

  **4. MINOR — `hex4` accepts control bytes as hex digits**
  `deck/kbd/ctl.c:19`
  `strchr("0123456789abcdef", p[i] | 32)` folds bytes `0x10..0x19` onto `'0'..'9'`, so `\u` followed by raw control bytes is accepted as a valid escape (raw control bytes are otherwise rejected at `ctl.c:28`). Invalid JSON is accepted and decoded to a different codepoint. No exploit path found (all consumers re-validate the decoded text), but this parser is the hostile-input boundary — fix by checking `p[i]` against `"0123456789abcdefABCDEF"` before folding. Add a regression test.

  **5. MINOR — 32-bit `long` millisecond clock wraps at ~24.8 days on armhf**
  `deck/kbd/mk20_kbd.c:83`
  `mono_ms` computes `tv_sec * 1000L` in a `long`, which is 32-bit on the static armhf target: signed overflow (UB) after ~24.8 days uptime, after which `now >= deadline` comparisons for lease expiry, confirm timeout, rate window, grant and K2 hold misbehave (a lease/confirm could fail to expire). Pre-existing for the A1 lease code; this change extends the same time base to confirm/K2/rate/grant, so it is worth fixing now: use `int64_t`/`uint64_t` for all ms timestamps.

  **6. MINOR — plan still promises a read-only second client; code grants full powers**
  `deck/AGENT-BUILD-PLAN.md:168` vs `deck/kbd/ctl.c:435` and CTL-PROTOCOL.md:10-11
  §3.4 says "agent，和一个只读的 status 客户端" but any accepted peer can acquire the lease, act, draw, bind, and request confirms. CTL-PROTOCOL.md accurately describes the code ("Both clients may query…"), so the plan text is stale. Peers must be root/same-uid anyway, so real exposure is low, but either implement a read-only mode for the second client or correct the plan.

  **7. MINOR — test gaps beyond finding 2**
  - `deck/kbd/tests/tests_ctl.inc:54`: the `H()` helper passes reply cap 4096 while production `read_client` uses `CTL_LINE_MAX` (1024) — tests can pass with replies that production would silently drop. Use `CTL_LINE_MAX` in `H()`. (I verified by computation that the true worst-case status reply is ~803 bytes — 120-char summaries cap the escaped form at 482 bytes — so this is currently latent, but the harness should match production.)
  - No unit tests for `\u` surrogate-pair decoding, the 9/10-digit integer boundary in `ctl_json_int`, or the finding-4 hex control bytes.
  - No pytest covering the `mkdir -p "$R/run/mk20"` line (`mk20d:308`) or the K2 flag through the real mk20d.
  - No fuzz/property test of the line parser against random hostile bytes (the hand-rolled parser would benefit most).
  - `oai`: no randomized malformed-input test; `header` CLI with a trailing-newline key file untested (see nit 3).

  **8. NIT — dead `/tmp/mk20-hid-scroll` marker**
  `deck/firmware/release/share/gadget.sh:414,417`
  Written/removed but read by nothing (HEAD didn't read it either; the old comment's claim was stale). Remove it or document it as operator introspection.

  **9. NIT — undocumented 24-key top-level limit**
  `deck/kbd/ctl.c:72,77` vs CTL-PROTOCOL.md:14
  Requests with more than 24 top-level keys are rejected as `bad_request`; the protocol doc does not mention the cap.

  **10. NIT — negative `id` echoed despite doc saying "nonnegative"**
  `deck/kbd/ctl.c:278` vs CTL-PROTOCOL.md:19
  `{"id":-5,...}` is accepted and echoed as `"id":-5` in success replies (error replies omit it via `err()`'s `id >= 0` test, ctl.c:226). Reject negative ids as `bad_request` or document the echo.

  **11. NIT — `oai_cli header` rejects real-world key files**
  `deck/agent/src/oai_cli.c:71-77`, `deck/agent/src/oai.c:130-136`
  A key file ending in `\n` (the common case) fails the 33..126 character check. Strip one trailing LF in the CLI before calling `oai_auth_header`.

  **12. NIT — `screen.page.show`/`layout`/`page.create` are not rate-limited**
  `deck/kbd/ctlscr.c:80-95`
  Only draw/text consume quota. An agent can flip pages at line rate; harmless today (no rendering) but A7 will do real panel work per flip — consider a modest limit on `show` when A7 lands.

  **13. NIT — ignored `ctl_json_quote` return; socket dir creation**
  `deck/kbd/ctl.c:281` (provably safe today — max quoted summary is 482 B against a 965 B buffer — but worth a defensive check or a comment stating the invariant), and `deck/kbd/ctl.c:377-393` fails closed with a clear log if the socket directory is absent; mk20d creates `/run/mk20` (`mk20d:308`) so the release path is fine, but a manual `mk20-kbd --ctl` run silently loses the socket. Consider `mkdir -p` in `ctl_listen` or an explicit doc note.

  ## Verified clean (no findings)

  - **No socket re-allow / e-stop bypass**: the op dispatch contains no path to `ctl_set_allowed` or to clearing `estopped`; `estop` op, reserved page key and K2 only ever make things safer; tokens are checked against `estopped` in `lease_ok` before every act; duplicate-key rejection prevents op-smuggling; `--ctl-allow` appears nowhere in mk20d.
  - **Bounds/memory**: line cap 1023 + LF, overlong drops the client; `jstr`/`field`/`jval` fully bounded with strict escapes and surrogate handling; all fixed buffers sized exactly (verified the 481/129/20 edge cases arithmetically); `layout()`'s ADD macro keeps `at < cap` invariant; rate table and K2 state are fixed-size; `oai.c` pre-caps bytes (256 KiB), depth (32), nodes (~4k), image (128 KiB), arguments (8 KiB × 16) before any cJSON allocation, and `CJSON_NESTING_LIMIT=32` matches; worst-case `screen.layout` (~805 B) and status (~803 B) fit 1024.
  - **Confirm**: shown-once guard, exactly-300 ms accepted (299 ignored), counted ignores, cancel/timeout/disabled/estop/client_gone all covered with reasons, deadline bounds `ctl_timeout_ms`, results only to the requester.
  - **Screen rules**: reserved control page + fb20 + estop key refuse even with a grant; builtin pages can't be deleted/shown; grant is keyboard-only, capped at 120 s, expires at the exact deadline, cleared on estop/disable/restart; per-fb 10/s and global 30/s sliding windows with correct 1000 ms rollover and no quota consumed on rejection; key events only to the owner of the currently shown agent page, bound keys only, both directions.
  - **K2**: pure state machine, no GD32 writes; hold fires from the poll timer while still held (`k2_estop_timeout` bounds poll); duplicate downs don't restart the timer; a rotation-slot release can't end a hold; press-only mode does rolling 3-in-1500 ms; rotation slots 104/105 provably inert; `--k2-estop` without `--ctl` still all-ups HID on e-stop.
  - **Release defaults**: with no new config the launch line, descriptor bytes and daemon behaviour are byte-identical; env overrides accept only 0/1; digit-in-key regex fix is correct; `gadget.env` + exported `MK20_HID_SCROLL` single-source the descriptor (once finding 1 is fixed); conftest scoping extended to `/tmp/hid.desc` with the unscoped-`/tmp` assertion intact.
  - **oai**: key exists only in the separate header builder (request struct has no key field; test asserts absence from body); header rejects injection characters; endpoint validation blocks userinfo/query/non-HTTP; per-call `invalid_arguments` instead of crashes; typed error mapping 401/403/429/5xx; coordinate helpers are exact (0↔0, 1000↔extent-1) and round-trip-tested.
  - **CTL-PROTOCOL.md** is accurate against the code except nits 9-10.
  - `cc -fsyntax-only -Wall -Wextra -Werror -std=c11` is clean on this host for `ctl.c ctlscr.c k2estop.c kbd.c mk20_kbd.c` and for the agent sources + tests. Linux-only paths (SO_PEERCRED), the full kbd suite, static armhf and shellcheck were not runnable here.

  VERDICT: NO-GO

