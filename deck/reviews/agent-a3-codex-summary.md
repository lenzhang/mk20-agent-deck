# A3 first acceptance review — Codex, 2026-10-19 (host only)

PASS — Scope: only agent/, this review and the A3 plan status line changed; no commit/push, Internet, device/ADB/serial, kbd/firmware/kernel or agentcfg.py edits.
PASS — Vendor sources and Mozilla bundle untouched; own uniquely named MBEDTLS_CONFIG_FILE plus PSA config; selected TUs and separate sanitized/nosan objects.
PASS — TLS client 1.2/1.3; ECDHE x25519/P-256/P-384, RSA/ECDSA, AES-GCM/ChaCha20-Poly1305, SHA-256/384, X.509/PEM/HAVE_TIME_DATE.
PASS — No server, renegotiation, old TLS, DTLS, CBC, SHA-1, SHA-512 or FS_IO; compile/ciphersuite assertions guard configuration.
PASS — 1: net.json v1 writer compatibility for mac_proxy/board_wifi and D1′ usb_wifi direct/proxy extension.
PASS — 1: missing/unreadable/oversize/malformed/duplicate/NUL/invalid UTF-8/schema/URL/route failures; invalid configs create zero sockets.
PASS — 1: proxy URL/fallback validation, exact route gate; direct only explicit usb_wifi or board_wifi domestic; blocked fails before socket creation.
PASS — 1/D2: no egress-IP proof, mihomo API or implicit direct fallback.
PASS — 2: nonblocking TCP/poll, 10 s connect cap, deadline-bounded getaddrinfo child, default 60 s overall request deadline and all I/O/TLS retries.
PASS — 2: CONNECT/Host authority, 8 KiB cap, HTTP/1.x 200 only, statuses retained, surplus rejected; fallback only during proxy establishment.
PASS — 2: SNI, required CA/hostname/expiry checks, injected/default CA, PSA initialization, entropy/CTR-DRBG; all certificate-error details exercised.
PASS — 2: GET/POST, extra headers, Connection: close/Content-Length; 16 KiB headers, length/chunked/close bodies, 8 MiB configurable cap and retained Date.
PASS — 2: regular 0600/0400 key <=4 KiB, trailing whitespace, injection rejection; key/Authorization wipes, sanitized output, all owned resources cleaned up.
PASS — 2: stable config/blocked/dns/connect/proxy/clock/tls_verify/tls/timeout/http_parse/too_large/io classes.
PASS — 3: build-day floor/override plus fresh-boot marker or explicit trust; clock gate precedes TLS; certificate verification always enabled.
PASS — 3: strict IMF-fixdate and proxy/HTTP Date integration; plaintext /time integral JSON, fixed ECM Mac hosts, usb0 binding, 10 s deadline, no Wi-Fi/proxy.
PASS — 3: >120 s skew requires authorized injectable setter; CLI settimeofday only with --set-clock; successful synchronization atomically writes marker.
PASS — 4: both CLI commands/options, one sanitized JSON line, HTTP-status/bytes/via, no body/header dump, response/error exit handling.
PASS — 5/C: URL/config/CONNECT/HTTP/chunk/trailer/Date/key tests; 342 network/clock checks, zero invalid-config socket calls, ASan/UBSan.
PASS — 5/fixtures: offline openssl-generated RSA/ECDSA CA/certs, valid/expired/future/wrong-name/unknown-CA cases; required proxy modes and accounting fixtures.
PASS — 5/IPC: TLS versions, certificate failures, Mac routes, explicit direct routes, refusal fallback policy, proxy/TLS/HTTP deadlines, caps, secrets, POST and clock.
FAIL — 5/TCP runtime acceptance: required standard pytest = 41 passed / 133 EPERM bind failures (39 A3, 86 A2, 7 fake tests, 1 OAI HTTP); Linux rerun needed.
FAIL — Real TCP fallback-order and unreachable-proxy/zero-direct-target proof cannot execute in this sandbox; both tests are written and enabled, never xfailed.
PASS — Supplemental combined IPC suite: 164 passed; one pre-existing OAI HTTP skip, two explicit TCP-only deselections; no new skip/xfail markers.
PASS — 6: VmHWM-missing getrusage fallback and JPEG/image_limit-before-timeout regressions; DES v/row/col scoped and wiped; A2 supplemental tests green.
PASS — 7: nosan local TLS/IPC whole-process RSS: five samples/bundle, max 2048 KiB test CA; 2832 KiB Mozilla+test CA (<16,384 KiB); limitations documented.
PASS — 7: NOTES-agent-a3.md records config/error/net.json rules, memory, sandbox limits, [M][D] proxy /models 200/401 and mihomo reachability, A4 llm.c boundary.
PASS — 8: make -C deck/agent test passes all four C suites; armhf target builds static nosan when available and reports missing compiler without failing tests.
FAIL — Static armhf/Linux runtime and TCP RSS measurement unverified: toolchain absent and loopback binding forbidden; no device acceptance claimed.

Acceptance: implementation and executable host/IPC checks PASS; full TCP/static acceptance remains open for the stated environment limits.
