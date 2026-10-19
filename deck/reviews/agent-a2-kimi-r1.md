  **Verdict: NO-GO** — one blocking protocol bug (finding 1: spec requires accepting 3.7 servers, and the test suite actively masks the defect). Fix is one line plus a fake fix and a test; findings 2–5 are non-blocking. Memory safety, bounds checking, memory bounds, timeouts, and secret handling otherwise hold up under a hostile-server reading.

  ## Findings

  1. **Medium — protocol desync: SecurityResult not read for RFB 3.7 + None auth**
     - `deck/agent/src/rfb.c:151`: `if (sec == 2 || version == 8)` skips the 4-byte SecurityResult for version 7 with security type 1. Per RFC 6143 §7.1.3 (and libvncclient behavior), 3.7+ servers send SecurityResult for *all* security types; only 3.3 omits it for None. Against a real 3.7-only server offering None, the client then reads the 0x00000000 result as the first bytes of ServerInit → w=h=0 → `framebuffer_size` (fails closed, but never connects).
     - Masked by `deck/agent/tests/fakes/fake_vnc.py:102`: `if self.sec_type == 2 or self.version == 8` — the fake mirrors the same wrong condition, so `test_auth_versions` (version=7, sec_type=1) passes against itself.
     - Fix: rfb.c → `if (sec == 2 || version >= 7)`; fake_vnc.py:102 → `self.version >= 7`; keep the 3.3 + type-1 no-result case in both. The existing parametrized test then actually verifies the real behavior.

  2. **Low — Hextile bg/fg state reset per rectangle**
     - `rfb.c:228`: `bg/fg/have_bg/have_fg` are re-zeroed on every `hextile()` call. The rfbproto Hextile spec carries background/foreground across tiles including rectangle boundaries within one update; a server that specifies bg once and sends subsequent rects without BackgroundSpecified gets a spurious `hextile` error (fails closed; macOS uses ZRLE/Raw so impact is low, and I couldn't confirm a mainstream server that relies on cross-rect carry-over).
     - Fix: move the four state variables into `struct rfb` (or pass across calls per update), or document the deviation in NOTES and add a two-rect carry-over test to pin the chosen behavior.

  3. **Low — DES key material left on stack**
     - `des.c:25-36`: derived subkeys (`sub[16]`, `k`) are not cleared before return, while rfb.c carefully wipes `key`/`response` (`rfb.c:149`) and the CLI wipes `pw`/its file buffer. Inconsistent hygiene on the one secret in the process.
     - Fix: volatile-zero `sub` and `k` at the end of `des_encrypt_block` (or call `mem_wipe`).

  4. **Low / documented — snapshot coverage not verified per-pixel**
     - `scale.c:46` + `scale.c:70`: only the total delivered pixel count is checked, so a hostile server can deliver one region twice and omit an equal-area region and still pass `scale_finish` (identity path silently keeps stale/zeroed pixels). Integrity-only impact — the server already controls all pixels — and it is documented in `NOTES-agent-a2.md` line 22, so accepted; a full coverage bitmap would cost 8 MiB at 8192², over budget. Optional cheap hardening: per-source-row coverage bitset (~1 KiB).
     - Fix: none required; if desired, row-bitset.

  5. **Test gaps** (`tests/test_rfb.py`)
     - No ASan/UBSan fuzz pass feeding garbage/random server bytes over the socketpair transport asserting clean errors (only structured malformations are covered).
     - `rfb_request(c, 1)` (incremental, advertised in `rfb.h:21`) is never exercised; all tests assert `inc == 0`.
     - No connect-stage timeout test (only mid-session delay at line 190).
     - `refresh_limit` (>3 DesktopSize/CopyRect refreshes, rfb.c:409) and the >16 MiB `SKIP_MAX` cut-text boundary (`rfb.c:73`) are untested.
     - Finding 1's 3.7+None case is masked by the fake (fix with finding 1).

  ## Verified clean (no findings)

  - **Memory safety vs hostile server**: every rect bounds-checked (`inside()`, rfb.c:207-209) before payload reads; Hextile subrect bounds (`rfb.c:250`), ZRLE palette indices (`rfb.c:309,318`), run lengths (`rfb.c:280`), and mode validation all enforced; tile/row/input buffers are fixed-size and all payloads fit (max Raw row 32 KiB = buffer, 64²×3 tile exact, 16²×4 Hextile ≤ 1 KiB); in-place 4→3 byte conversions advance read ahead of write safely; zlib decompression bombs capped by `expand_max` (`rfb.c:286`) with no-progress detection (`rfb.c:273`); trailing-data and mid-stream `Z_STREAM_END` rejected.
  - **32-bit overflow (armhf)**: no path found. All wire-derived products are ≤ 8192² and promoted to `size_t`/`uint64_t` before multiply (`rfb.c:213,286,375,396`; `scale.c:11-16,46,59`); `mem.c:7` limit arithmetic can't underflow; scaler accumulator width selected from a 64-bit `max` with guarded truncation (`scale.c:22-24`).
  - **Unbounded memory**: none. Wire lengths are never allocated — reason strings, names, cut text, cursor payloads, and ZRLE compressed data are streamed through fixed buffers with caps (`TEXT_MAX` 1 MiB, `SKIP_MAX` 16 MiB) and clean `length_limit` errors; heap is hard-capped at 14 MiB (`MEM_LIMIT`) and all failures return cleanly.
  - **Timeouts**: absolute deadlines per connect/handshake/snapshot/refresh (`rfb.c:37`), so slow-drip servers can't extend the window; every read/write/drain goes through the deadline; refresh count capped at 3, messages at 1024.
  - **Secret leakage**: password accepted only via file/env, never argv; error JSON uses fixed identifiers; server reason strings are drained, never echoed (`rfb.c:153`, verified by `test_auth_failure_no_password_in_output`); key/response/password buffers wiped; clipboard contents are discarded, never stored; the fd/env transport override is compiled out of production binaries (`agent_cli.c:67`).

  Note: NOTES reports 92 loopback-bind sandbox failures with socketpair as supplement, and the armhf/Linux builds are unverified — acceptance of those remains open per the NOTES itself, independent of these findings.


---
Triage (Grok Bot, final acceptor): #1 rejected — rfbproto/RFC 6143 §7.2.1: for None, SecurityResult is sent only from 3.8 on; 3.3/3.7 go straight to
ClientInit (libvncclient checks minor > 7). Current rfb.c and fake are correct. #2 low, deferred (macOS uses ZRLE). #3 accepted (cheap, next fix round).
#4 documented/accepted. #5 test gaps noted for a later round. Kimi ran via its own OAuth endpoint (api.kimi.com), proxy config untouched.
