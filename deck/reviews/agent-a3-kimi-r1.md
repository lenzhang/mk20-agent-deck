# A3 cross-review r1 — Kimi K3 (kimi-code), 2026-10-19

Scope: REVIEW-a3.diff (agent A3 network: CONNECT proxy + mbedTLS + clock), read-only. Prompt: ~/worktrees/prompt-agent-a3-kimi-r1.txt (Mac).

# Verdict: GO (host) — conditional

No BLOCKER found. The fail-closed routing, TLS verification, deadline coverage, parser memory safety, and secret hygiene all hold up under static review, and the C/pytest suites are genuinely adversarial. The condition: A3's two most safety-critical runtime proofs have **never executed** (sandbox EPERM + IPC deselection), so device/[M][D] acceptance must stay gated on the Linux TCP rerun and the armhf static build, exactly as the notes already disclose.

## Findings

1. **MAJOR — core safety proofs never executed** (`deck/agent/tests/test_net.py:2346` `test_unreachable_proxy_never_direct`, `:2355` `test_fallback_order`; NOTES-agent-a3.md "two explicit TCP-only deselections"). "Proxy unreachable ⇒ zero direct target connections" and "fallbacks in order" are the load-bearing guarantees of A3; the sandbox blocked all 39 A3 TCP cases and the supplemental run deselects precisely these two. Static review of `net_request` (net.c:518-538) shows no direct fallback exists, but there is zero executed evidence. Fix: run `python3 -m pytest deck/agent/tests -q` on Linux and gate device acceptance on it.

2. **MINOR — X.509 KU/EKU not enforced** (`src/mk20_mbedtls_config.h`, whole file). The custom config omits `MBEDTLS_X509_CHECK_KEY_USAGE` and `MBEDTLS_X509_CHECK_EXTENDED_KEY_USAGE`, both on in mbedTLS's default config. A chain signed by a trusted root whose leaf lacks `serverAuth` EKU or proper keyUsage passes (hostname/expiry/chain still enforced, so practical exposure is low). Fix: add both defines.

3. **MINOR — TLS 1.2 without Extended Master Secret** (`src/mk20_mbedtls_config.h`). `MBEDTLS_SSL_EXTENDED_MASTER_SECRET` (default on upstream) is missing → TLS 1.2 handshakes skip EMS (3SHAKE session-binding). Fix: add the define.

4. **MINOR — unauthenticated proxy Date can set the clock on the API path** (`src/net.c:541` and `:563`). With `clock.set_clock` enabled, the plaintext CONNECT/HTTP `Date` drives `settimeofday` + trust-marker write; for `board_wifi` the proxy is LAN-reachable, so whoever controls the proxy can roll the device clock to defeat cert validity windows. The CLI never wires this (`--set-clock` only on `--time-sync`), but A4's `llm.c` could. Fix: keep `set_clock` off the model path; source time-setting only from the usb0 `/time` endpoint.

5. **MINOR — `read_ca` can block indefinitely** (`src/net.c:459`). `fopen`/`fread` on `--ca` without an `S_ISREG` check: a FIFO/device path hangs outside any deadline. Fix: `fstat` + regular-file check, as `netcfg_load` already does.

6. **MINOR — until-close bodies accept plain TCP EOF** (`src/net.c:596-601`; mbedTLS maps `CONN_EOF`→0 at `ssl_msg.c:6053`). For `Connection: close`-delimited bodies, a proxy can truncate the response without close_notify undetectably. CL/chunked paths are strict; impact is a truncated body (JSON parse failure downstream), not memory safety. Fix (optional): flag missing close_notify for H_CLOSE completions, or document.

7. **MINOR — static armhf caveats** (`Makefile` armhf target). glibc static `getaddrinfo` can't resolve hostname proxies (NSS dlopen) — fine for the planned IP-literal configs (172.30.250.1 etc.), but document "IPv4 literal only" or switch to musl; also confirm toolchain time_t (32-bit time_t makes >2038 dates fail closed, which is safe).

## Verified clean (no findings)

- **Fail-closed/direct-path**: `netcfg_route_for` (netcfg.c:125) is the only gate — `direct` impossible for `mac_proxy`, `board_wifi` foreign, or any proxy-route-without-URL; invalid config ⇒ zero `socket()` calls (C test with counting hook covers every malformed case); fallbacks advance only on proxy TCP/DNS failure, never to direct.
- **TLS**: `MBEDTLS_SSL_VERIFY_REQUIRED` + SNI + `mbedtls_ssl_set_hostname` + CA bundle + `HAVE_TIME_DATE` expiry; all five cert outcomes (valid/expired/not-yet-valid/name/unknown-CA) tested; no SHA-1/CBC/server/DTLS/PSK compiled (compile-time asserts in test_net.c).
- **Memory safety vs hostile peers**: CONNECT 8 KiB cap + in-buffer and `MSG_PEEK` surplus rejection; headers 16 KiB; body cap with `SIZE_MAX`-checked digit parsing and no 32-bit overflow paths; strict chunked (bad hex/oversize/missing CRLF/trailer CL+TE rejected), obs-fold and TE+CL rejected; per-byte split-feed tested.
- **Deadlines**: 10 s connect cap, monotonic overall deadline honored in connect/send/recv/TLS WANT_* retries and the forked resolver (poll-bounded, SIGKILL+reap); slowloris/handshake-delay/response-delay tests.
- **Secrets**: key file `0600/0400` regular `O_NOFOLLOW` ≤4 KiB; key and serialized request wiped on every path; caller can't inject `Authorization`; CLI prints fixed strings only — echo-server test proves the key reaches the server but never stdout/stderr.
- **Clock**: floor + marker-newer-than-boot or explicit `--trust-clock`; trust never bypasses floor or verification; gate re-checked immediately before handshake; untrusted clock ⇒ zero CONNECT/TLS (tested); `/time` restricted to three ECM IPs, usb0-bound, integral single-key JSON, ±120 s rule, atomic 0600 marker.
- **A2 fold-ins**: rss_kb VmHWM→getrusage fallback, img_jpeg rc-before-deadline ordering, DES S-box temps scoped + wiped — all correct, with regression tests.
