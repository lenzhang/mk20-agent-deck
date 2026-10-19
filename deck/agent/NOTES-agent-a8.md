# A8 part 1 — host implementation

Scope: agent sources/tests, release build/CA payload, and plan/review notes under
`deck/`. No hardware, adb, physical serial, external network, dependency download,
commit or push. Protected kbd/mk20deck/vendor/ONBOARD sources remain unchanged.

## DNS and routing

`dns.c` replaces both libc resolver calls; net no longer forks for DNS. Numeric
IPv4/IPv6 bypass configuration and queries. Net/RFB retain the localhost loopback
alias. The resolver reads at most three literal IPv4/IPv6 nameservers from a
bounded regular `/etc/resolv.conf`; no nameserver means a DNS error, with no
public fallback. Tests inject a file and explicitly enable `literal:port` or
`[IPv6]:port`; no HTTP input controls these options.

A then AAAA use fresh /dev/urandom IDs and kernel ephemeral UDP ports, at most
2 seconds per server/type within the caller's monotonic deadline. Cancellation
and cooperative service checks run at most 25 ms apart during polling. Responses
must match source address/port, ID, QR/opcode/RCODE and the single echoed question
(name case-insensitive, type/class exact). Parsing bounds all records, limits
packets to 4096 bytes/128 records and results to eight addresses, detects pointer
cycles, and follows at most eight CNAME edges in the answer section only. A results
precede AAAA. TC responses are rejected: **no TCP fallback**. No search suffixes,
NSS, mDNS, /etc/hosts lookup, cache, DNSSEC or scoped IPv6 literals are implemented.
A second-family timeout can retain an already validated A result; callers still
check the original deadline before connecting.

The proxy route explicitly permits resolution only of an endpoint object from
`config.proxies`. Target names go unchanged to CONNECT and TLS SNI. The socketpair
regression traps every DNS socket attempt (zero for the target), verifies
`CONNECT open.bigmodel.cn:443`, and proves the DNS trap catches a direct hostname
lookup. Proxy failure never turns into a direct target connection.

## Release

`mk20-build-bins.sh` reuses the Makefile's complete agent source/crypto lists in a
fresh object directory, with C11, -Wall/-Wextra/-Werror, -O2, static linking and no
sanitizers. All four release binaries get file/debug prefix maps, strip/chmod,
and the existing `sha size bin/name` output format. Source time is pinned by
`agent/SOURCE_DATE_EPOCH` (2026-10-19 UTC; update with releases, explicit environment
override allowed). UTC avoids a local-midnight build date falsely exceeding the
current UTC clock floor. The binary-match test now includes the agent.

The public vendored roots are copied to `release/share/ca-bundle.pem`; the existing
release builder enumerates all regular files into MANIFEST, including this bundle
and the future box-built `bin/mk20-agent`. An explicit CA option takes precedence;
otherwise `MK20_REL/share/ca-bundle.pem` is used when MK20_REL is set, failing closed
if absent/invalid. Host builds without MK20_REL retain the existing vendor path.
No armhf compiler invocation or release binary update was attempted on this Mac.

## A7 fixes

- Busy MCP sessions refresh idle age while owning a live task/pending direct call;
  they start aging again when idle. Capacity eviction/deletion behavior is intact.
- A connected authenticated SSE stream keeps its session active. Absolute twelve
  hours and clock rollback still expire it; disconnect removes the activity hold.
- All `/secret/` body ranges are wiped after handling, including validation/auth
  failures. HTTP receive allocations are wiped in full on drop and half-close free.
- Unmodified cJSON has no decoded string-length field. Existing strict JSON scanning
  already rejects escaped U+0000 before allocation. Dedicated secret regressions
  prove 400/no truncated installation and raw-buffer wiping for embedded/start/end
  NUL; no vendor changes or weaker strlen-only decoding were introduced.
- Stop keeps done/error terminal states while still calling loop_stop/estop.
- `/test` runs one short-lived child with a nonblocking result pipe and 1.5-second
  parent watchdog. Disconnect, shutdown and timeout kill/reap it. The child closes
  inherited service descriptors; test-only synthetic model streams are reserved
  before fork. Socketpair HTTP/MCP tests prove stop and initialize respond during
  a slow probe, and cover watchdog/disconnect reaping. No enable/grant path added.

Other Kimi minors remain deferred: development Origin derivation, cookie comment,
and unused-startup-service-fd cleanup. The new §3.8b reserves the single usb0 admin
service for the future K4 maintenance entry; these maintenance actions themselves
are outside part 1.

## Verification and remaining gates

See `../reviews/agent-a8-codex-summary.md` and its raw logs. All non-listener C
suites pass with ASan/UBSan; make test's final real UDP fixture fails visibly at
127.0.0.1 bind with EPERM. No skip/xfail masks it. Web/MCP IPC: 60 passed, seven
real-listener EPERM failures. Sim: 24 passed. Release CA check passes; existing
cross-compiler-dependent binary-match check is skipped on this Mac.

Remaining A8: mk20lib FEATURE_AGENT, mk20d supervision, selftest INFO/WARN,
health.json agent block, maintenance log rotation. Linux/Darwin unrestricted UDP,
static armhf execution/binary matching and device integration remain acceptance
gates. The probe child's memory/startup cost on T113 needs measurement.
