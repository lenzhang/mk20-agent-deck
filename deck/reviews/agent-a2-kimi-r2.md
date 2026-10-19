• Cross-review of REVIEW-r2.diff against `deck/agent/src` (files re-read: `agent_cli.c`, `rfb.c`, `des.c`, `mem.c`, plus test fake/harness). Verified: deadline call sites actually cover every ZRLE/Hextile tile (`tile()` at rfb.c:214 is shared by zrle.c:328 and hextile rfb.c:258), every Raw row (rfb.c:224), pre/post `scale_finish` (rfb.c:414/417), and pre/post CLI JPEG before `fopen` (agent_cli.c:86-91); `c->deadline` persists after `rfb_snapshot` so the post-checks use the last refresh's deadline; DES wipe covers all key-derived locals (`k, cd, c, d, sub, b, l, rr, x, f, t, pre, o`) with no early return, caller `key`/`response`/`pw`/`b` already wiped (rfb.c:150, agent_cli.c:33/83/102); `mem_wipe` is volatile-store (mem.c:16); test canned frame compresses to 93 B < 128 (zlib 1.2.12) and the exec-wrapper correctly reproduces inherited-ru_maxrss semantics on Linux only. Rejected r1 #1 not re-raised.

  **Findings**

  1. MINOR — `agent_cli.c:42-43`: if `/proc/self/status` opens but has no `VmHWM` line (kernels <2.6.32), `rss_kb` returns 0 instead of falling back to `getrusage`, and the new `0 < peak_rss_kb` assert then fails loudly for the wrong reason. Fix: `fclose(f); if (peak) return peak;` and let control fall through.
  2. MINOR — `agent_cli.c:88-89`: deadline is checked before `img_jpeg`'s `rc`, so a genuine `image_limit`/`jpeg` failure past the deadline is misreported as `timeout`. Fix: evaluate `rc` first (or check deadline only when `rc == 0`).
  3. MINOR — `des.c:33`: S-box temps `v`, `row`, `col` (derived from `x ^ sub[r]`) are the only key-derived locals not wiped. Fix: declare them at function scope and `mem_wipe` them with the rest, or document that register-resident derivatives are out of scope.

  Non-findings checked: per-tile/row `clock_gettime` overhead is negligible (≤16 K calls); `>=` boundary matches `ready()`'s `left <= 0`; refresh re-arms the deadline per NOTES; no leak on the new `bad` paths (`jpeg` freed at agent_cli.c:104); socketpair fds survive `pass_fds`→`execv`; `solid` avoids the 192 MiB fake gradient and `_px` stays consistent; ZRLE solid wire format (`\x01`+CPIXEL BGR) matches the decoder (rfb.c:303).

  Verdict: GO


---
Triage (Grok Bot, final acceptor): codex r2 PASS, Kimi r2 GO. Minors #1 (VmHWM fallback when line absent), #2 (jpeg rc vs timeout precedence), #3 (S-box temps wipe) deferred to A3. Box Linux: full make test on d63c206 green (24 + 132 + 58 + 546, plus C/e2e checks); DES fix re-verified on box: make -C agent test + 132 agent pytest passed.
