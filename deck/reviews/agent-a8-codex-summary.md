# A8 part 1 — first acceptance self-review

Status: host implementation complete; unrestricted listener/Linux/armhf acceptance
remains pending. No commit/push, hardware access, external network or downloads.
All repository changes are under deck/; protected paths were not edited.

## Tasks 1–4 against the diff

1. **DNS:** new dns.c/h, linked everywhere net/RFB is used, replaces getaddrinfo and
   the net resolver child. Literals, three resolv.conf servers, A/AAAA order, random
   ID/ephemeral port, full response identity/question checks, bounded answer-only
   CNAME traversal, compression cycle detection and deadline/cancellation are
   implemented. No hardcoded DNS or TCP fallback. Parser/config/literal tests run
   under ASan/UBSan; fake UDP cases cover A/AAAA/CNAME/NXDOMAIN, SERVFAIL failover,
   ID/source spoof rejection, malformed/TC/loop rejection and timeout. The UDP
   fixture cannot bind in this sandbox. Proxy endpoint identity guard and a DNS
   accounting trap/CONNECT socketpair regression preserve never-direct policy.
2. **Release:** script adds stripped static -O2 mk20-agent using Makefile lists,
   fresh objects, prefix maps for every binary, and pinned UTC source time. Public
   CA payload is tracked/refreshed; automatic MANIFEST enumeration needs no release
   builder edit. MK20_REL supplies the runtime CA default, explicit CA wins. Binary
   matching includes all four binaries. No armhf build was attempted; box must
   build and commit release binaries before release acceptance.
3. **A7 minors:** busy idle expiry, active SSE lifetime, raw receive/body erasure,
   terminal stop preservation and asynchronous bounded connection probe all have
   regressions. NUL was already rejected by strict preflight JSON scanning; cJSON
   exposes no decoded-length member, so the regression verifies rejection before
   decoding instead of modifying forbidden vendor code. `/stop` and MCP initialize
   respond while a 900 ms test child runs; watchdog and disconnect reap tests pass.
   Other Kimi minors remain deferred.
4. **Docs:** §3.8b declares --web 172.30.250.2:8780 the sole usb0 admin service for
   K4 maintenance and mk20deck mirroring; U3 must reuse it. A8 status/remainders and
   A7 SSE/probe notes updated. Maintenance operations are declared future scope,
   not claimed implemented. NOTES-agent-a8.md records limits and handoff.

Self-review corrected two boundary issues before final acceptance: a maximal
253-character hostname needs a 271-byte DNS query buffer, and a deadline crossing
between checking time and poll must never become poll(-1). A maximal-name ASan
regression now covers query construction. Review found no remaining direct-target
proxy resolution path, unbounded packet traversal, or secret receive-buffer free.

## Verification

- `make -C deck/agent test`: non-listener suites pass (DNS 180, web 707, MCP 268,
  A5 262, LLM 256, OAI 3145 checks; HTTP wipe/RFB/CLI and net 366 also pass).
  Final DNS UDP fixture aborts at bind(127.0.0.1), errno=EPERM. The complete make
  gate therefore exits 2; it is **not reported green**. See agent-a8-agent-test.log.
- `make -C deck/agent build/mk20-agent-nosan`: PASS, host link without sanitizers.
- Offline `make -C deck test`: BLOCKED at uv cache initialization (sandbox denies
  the existing cache lock path). No dependency fetch was attempted. See
  agent-a8-deck-test.log. Cached Python supplemental sim run: 24 PASS.
- Cached Python web/MCP tests: 60 PASS, seven FAIL solely at real TCP/Unix listener
  binds with EPERM; no new skips/xfails. See agent-a8-web-mcp-pytest.log.
- Supplemental RFB/net/LLM/OAI socketpair selection: 215 PASS, one existing skip,
  three explicit deselections, one compressed-frame elapsed-time assertion at
  1.07 s versus <1 s. Isolated rerun: **2 PASS**, 85 deselected. The broad run remains
  recorded as a failure, not silently replaced. See agent-a8-transport-pytest.log
  and agent-a8-rfb-timeout-rerun.log.
- Release checks: CA payload check PASS; existing binary-match skip because cross
  gcc is absent (one passed, one skipped, eleven deselected). Shell syntax and
  git diff --check PASS. shellcheck is unavailable on this Mac.

Commands use the already cached Python at
`/Users/dev/.cache/uv/archive-v0/vxkKwzR1zHgPnvko/bin/python`, with the cached Pillow
site-packages path from A7 where needed; no environment installation was performed.

## Residual risks / box handoff

- Run make test with UDP listeners permitted; verify UDP/source spoof/failover and
  deadlines on both Darwin and Linux. No success claim for blocked fixture cases.
- Build all four release binaries and run binary-match with the same compiler and
  SOURCE_DATE_EPOCH; verify static armhf/NSS-free execution, bundle manifest and
  MK20_REL startup path. Release binaries intentionally remain unchanged here.
- Measure probe fork/startup/RSS on the T113; its parent watchdog bounds network
  work, while kill/reap still depends on normal OS scheduling.
- Deliberate resolver limits: 4 KiB/128 records/eight addresses, no TCP on TC,
  DNSSEC/search suffixes/mDNS/scoped IPv6/cache. Provision resolv.conf on the box.
- Remaining A8 integration is explicitly listed in the plan; existing fail-closed
  physical pairing/rendering and device-only A7/A9 gates are still outstanding.

Changed file groups: agent src/{dns,net,rfb,mcp,web,httpd} and corresponding
headers/tests, Makefile/SOURCE_DATE_EPOCH; tools/mk20-build-bins.sh,
tests/test_mk20d_router.py, firmware/release/share/ca-bundle.pem; the build plan,
A7 correction notes, A8 notes and this review/logs. The pre-existing untracked
reviews/prompt-agent-a8.txt was left untouched.
