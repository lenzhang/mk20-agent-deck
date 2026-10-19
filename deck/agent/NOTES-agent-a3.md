# A3 host network implementation — 2026-10-19
R1 fixes: enforce certificate KU/EKU and TLS EMS; bound regular-file CA loading to 4 MiB; Kimi #4, #6 and #7 remain deferred.

Host work only. No Internet, device/ADB/serial, kbd, firmware, kernel, proxy configuration, git commit or push was used. Vendored mbedTLS 3.6.7 and the CA bundle were not edited. `mk20deck/agentcfg.py` is unchanged. `agent/CTL-PROTOCOL.md` is absent.

## Routing and configuration

`netcfg_load()` accepts a regular file of 1–16,384 bytes. Missing, unreadable, oversized or malformed input fails before socket creation. JSON grammar, UTF-8, duplicate keys, embedded NUL, version, profile, route values, proxy URL and all fallbacks are checked. Unknown descriptive fields such as the writer's `note` are ignored. No socket or resolver is used by configuration parsing.

| Profile | Domestic | Foreign |
|---|---|---|
| `mac_proxy` | proxy | proxy |
| `board_wifi` | direct | proxy if configured, otherwise blocked |
| `usb_wifi`, `mode: direct` | direct | direct |
| `usb_wifi`, `mode: proxy` | proxy | proxy |

The first two forms match the existing Python writer, including `proxy.url: null` and empty fallbacks for blocked board Wi-Fi. `usb_wifi` is the D1′ extension. Optional routes on that profile must agree with mode. Proxy routes always require a primary URL. URLs are exactly `http://host:port`: no credentials, path, query, fragment or IPv6 literal; ASCII DNS labels or an IPv4 literal, hostname at most 253 bytes, port 1–65535. Fallbacks are tried in file order only while establishing TCP to the proxy; neither a CONNECT refusal nor a TLS/HTTP failure advances to another proxy. There is no automatic direct fallback.

`netcfg_route_for()` is the only model-transport routing gate. Direct model sockets are reachable only for the explicit profiles above. The separate plaintext clock bootstrap is restricted to the configured numeric ECM Mac address and usb0. No exit-IP proof, egress file, mihomo API, or proxy configuration modification exists. D1′ and this task's explicit profile rules supersede the older contradictory wording in the A3 plan bullets. A launchd forwarder is outside A3 host work.

## TLS and transport

The Makefile uses `MBEDTLS_CONFIG_FILE="mk20_mbedtls_config.h"` and the owned `psa_config.h`; the unique basename prevents accidentally including the vendor's default config from the same include directory. Client TLS 1.2 and 1.3, SNI, required certificate/hostname verification and expiry checks are enabled. TLS 1.2 uses ECDHE only; TLS 1.3 uses ephemeral ECDH, without PSK or early data. Groups: x25519, secp256r1, secp384r1. Verification: RSA PKCS#1 v1.5/PSS and ECDSA. Ciphers: AES-GCM and ChaCha20-Poly1305; hashes: SHA-256/384. PSA initialization, entropy and CTR-DRBG run before handshake. Server TLS, DTLS, renegotiation, tickets, obsolete TLS, CBC, SHA-1 and FS_IO are disabled. C tests assert these exclusions and the nine enabled ciphersuites.

Only the selected library translation units are built, in separate `tls-*` sanitized and `ns-tls-*` unsanitized sets. The `sha512.c` translation unit supplies SHA-384; SHA-512 itself is disabled. There are no new warning suppressions on owned code. CA files are read by our code, at most 1 MiB, default `vendor/ca/cacert.pem` relative to the working directory. The Mozilla bundle includes roots outside the selected algorithm/curve set: mbedTLS partial parsing retains supported roots; a bundle with no usable root fails. Unsupported certificate chains still fail verification. Bundle SHA-256 matches `VENDOR-A3.md`.

TCP uses nonblocking connect/poll with a 10-second connect limit and an overall monotonic deadline (60 seconds by default). Hostname getaddrinfo runs in a short-lived child because POSIX offers no resolver deadline; the parent polls the result pipe against the same deadline, kills on timeout and reaps the child. Proxy targets are resolved by the proxy, never locally dialed. Every send, receive and handshake retry checks the overall deadline. SIGPIPE is suppressed per socket/send.

CONNECT is HTTP/1.1 with matching Host authority (443 for normal HTTPS URLs; explicit HTTPS ports are retained for local services/tests). Reply headers are capped at 8 KiB, only HTTP/1.0 or 1.1 status 200 is accepted, and surplus bytes are rejected. A parsed refusal status is retained even if surplus bytes make the reply invalid.

HTTP supports GET/POST, caller headers and bodies, one request per connection, Connection: close and Content-Length. Header injection and caller overrides of framing/Authorization are rejected. Response headers are capped at 16 KiB; bodies use Content-Length, strict chunked framing including extensions/trailers, or connection close. Duplicate framing, TE+CL ambiguity, malformed chunks and forbidden trailer framing fail. The default body cap is 8 MiB; a smaller caller cap is recommended for A4. The Date header is retained separately from body bytes. Response ownership ends with `net_response_free()`; the synchronous API assumes one active request.

Key files are opened without following symlinks, must be regular, exactly mode 0600/0400 and at most 4096 bytes. Trailing whitespace is removed; empty, embedded-control and oversized keys fail. Key and serialized Authorization buffers are wiped with `mem_wipe` immediately after use and on every cleanup path. Diagnostics use fixed messages/codes; peer bodies, header values, key paths and credentials are never echoed by `--net-test`.

## Clock

TLS requires wall time at least the UTC build-day floor (`MK20_TIME_FLOOR` can override it) and either a regular marker newer than boot and no later than now, or explicit `--trust-clock`. Trust never overrides the floor or certificate verification. Linux uses CLOCK_BOOTTIME (MONOTONIC fallback); macOS uses KERN_BOOTTIME. Explicit trust does not depend on that OS query, which the sandbox denies on macOS.

The strict IMF-fixdate parser checks exact punctuation, English names, calendar ranges, leap years, weekday consistency, GMT and time_t representability; obsolete dates and leap seconds are rejected. `agent_clock_sync_date()` is used for CONNECT/HTTP Date when an API caller explicitly enables setting. Such a caller can bootstrap from a proxy Date before TLS; the clock gate is checked again before handshake. The CLI does not silently set time during a model request.

`--time-sync` sends only `GET /time` to the selected ECM Mac: 172.30.250.1, 10.20.0.1 or 169.254.7.1. It uses numeric addressing, a 10-second deadline and binds usb0 (SO_BINDTODEVICE on Linux, interface binding where supported on macOS); inability to bind fails closed. It does not use the model profile/proxy or Wi-Fi. The body must be a single-object integral JSON epoch. For absolute skew >120 seconds, setting requires the explicit option and a successful injectable setter. Production CLI supplies settimeofday only with `--set-clock`; tests use a fake setter. Smaller skew validates the clock without changing wall time. The marker is written through a 0600 temporary file, fsynced and atomically renamed only after success. Neither tests nor this task changed host time.

## CLI and error classes

From `deck/agent/` (or pass an explicit CA path):

```sh
build/mk20-agent --net-test https://localhost/models --net /path/net.json \
  --route foreign --key-file /path/provider.key --ca /path/ca.pem \
  --timeout 60 --trust-clock
build/mk20-agent --time-sync --mac-host 172.30.250.1 --port 80 --set-clock
```

These are examples only; no external request was run. `--body-cap BYTES` and `--clock-marker PATH` expose the documented test/configurable limits. Output is one JSON line with ok, class, detail, http_status, bytes, via, and peak_rss_kb. Any completely received HTTP response (including 401) succeeds for `--net-test`; its body/headers are not printed. Malformed/truncated HTTP or a prior transport error exits nonzero.

| Stable class | Meaning |
|---|---|
| config | invalid/missing configuration, URL, headers, key file, CA or time source |
| blocked | selected provider route is blocked |
| dns | name resolution failed |
| connect | TCP could not reach the selected endpoint |
| proxy | CONNECT refused/malformed/oversized or surplus reply bytes |
| clock | floor/trust gate failed, invalid source or synchronization failed/not authorized |
| tls_verify | certificate failed; detail distinguishes expired, not-yet-valid, name mismatch, unknown CA |
| tls | other TLS initialization/handshake failure; numeric code is safe to report |
| timeout | overall deadline or connect timeout |
| http_parse | invalid headers, ambiguous framing, chunk/trailer errors or truncated body |
| too_large | response body exceeded the selected cap |
| io | stream I/O or allocation failure |

Success uses an empty class/detail. CONNECT error statuses are retained as http_status; the proxy's successful 200 is not confused with the origin's status.

## Host verification and memory

Host: macOS 26/Darwin arm64, Apple clang, C11 -Wall -Wextra -Werror -O1. `make -C deck/agent test` passes ASan/UBSan: 3145 existing OAI checks, RFB/scaler/JPEG checks, 342 network/clock checks (invalid-config socket count zero), and CLI regressions. The A2 fixes cover procfs missing VmHWM fallback, JPEG/image_limit taking precedence over a later deadline, and DES S-box temporary wiping; known-vector/clipboard/capture tests remain intact.

The default Python interpreter lacks pytest. Commands below used the already cached Python 3.13 pytest environment on PATH and cached Pillow on PYTHONPATH; nothing was downloaded. The required standard `python3 -m pytest deck/agent/tests -q` was run: **41 passed, 133 failed**. All 133 failures are PermissionError(EPERM) from binding 127.0.0.1, before protocol execution: 39 A3 TCP cases, 86 A2 TCP cases, seven original fake tests and one OAI HTTP test. No new test is skipped or xfailed. TCP acceptance, notably real fallback ordering and unreachable-proxy/no-direct-target accounting, must run on Linux with loopback permitted.

For supplemental protocol execution, `MK20_TEST_SOCKETPAIR=1` selects explicit fake IPC transports; only builds with `MK20_NET_TEST`/`MK20_RFB_TEST` accept inherited FDs. Production builds have no environment/FD bypass. This runs the same CONNECT, mbedTLS handshake, HTTP parser and CLI, without TCP dial coverage.

```sh
MK20_TEST_SOCKETPAIR=1 MK20_TEST_NO_NETWORK=1 python3 -m pytest \
  deck/agent/tests/test_rfb.py deck/agent/tests/test_oai.py \
  deck/agent/tests/test_fakes.py::test_des_known_vector deck/agent/tests/test_net.py \
  -q -k 'not unreachable_proxy and not fallback_order'
```

Combined supplemental result: **164 passed, one existing OAI HTTP skip, two explicit TCP-only deselections**. The A3-only supplemental run passes 40 tests, with the two TCP-only cases deselected. Coverage includes both TLS versions, RSA/ECDSA, all five certificate outcomes, both Mac proxy routes, explicit direct profiles, 407/403/garbage/slowloris/extra/oversized proxy replies, no fallback on refusals, TLS/HTTP deadlines, body caps/framing, secret redaction, POST/extra headers, clock gate and plaintext epoch validation. Normal TCP tests remain enabled in the standard run.

Five fresh nosan `--net-test` processes per bundle, local Python TLS server, proxy over socketpairs, 200/2-byte body, `--trust-clock`, no key, same O1 build (only test FD transport compiled in):

| CA bundle | Peak RSS samples (KiB) | Maximum |
|---|---|---:|
| Injected throwaway CA | 2032, 2032, 2048, 2048, 2032 | 2048 KiB |
| Unmodified Mozilla bundle plus throwaway CA in a temporary test bundle | 2784, 2736, 2832, 2736, 2736 | 2832 KiB (~2.77 MiB) |

These are whole-process host peaks, below 16,384 KiB. They measure actual TLS/HTTP over IPC because TCP listeners are forbidden. They do not establish device RSS, combined VNC+LLM peak or large-body peak; A4 must choose a body cap and measure combined allocations against the total <=16 MiB device budget. Linux procfs RSS normalization and the cross-platform getrusage fallback are retained.

`make -C deck/agent armhf` reports the arm-linux-gnueabihf-gcc toolchain unavailable without failing host tests. Its available-toolchain branch builds a static nosan binary in build-armhf; Linux runtime/static armhf remain unverified here.

## Remaining [M][D] and A4

* On an authorized device/Mac, read-only check mihomo reachability at 172.30.250.1 and configure the user-selected proxy endpoint. No mandatory forwarder or proxy edits are part of this work.
* Verify the usb0-bound Mac /time service and marker lifecycle across boot, then obtain /models 200 or 401 through the Mac proxy. Confirm an unreachable proxy creates zero direct target connections. No device/Mac acceptance is claimed here.
* Re-run the unmodified TCP suite on Linux, build static armhf when available, and measure device total RSS under combined capture/network load.
* A4 implements llm.c on net.c's GET/POST, sanitized error classes and response ownership, then adapts the existing OAI serializer/parser. Provider/model/key references remain configuration; no provider call or A4 adapter was added.
