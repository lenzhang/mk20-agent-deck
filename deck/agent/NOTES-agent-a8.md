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


## A8 part 2

Host state, 2026-10-20. Release-level `FEATURE_AGENT` defaults to 0, accepts only
0/1 from mk20.conf, and is forced off by FAT `mk20-no-agent` or `MK20_AGENT=0`.
`MK20_AGENT=1` does not opt in. No runtime grant/allow switch was introduced;
`loop_init` still starts disabled and MCP/web cannot enable it.

`mk20d boot` launches the optional supervisor detached only with an enabled,
executable payload. It does not change router/kbd supervision or the confirm
path. `mk20d agent` owns `$TM/agent.lock`, polls composite and the existence of
`/run/mk20/kbd.sock` (`MK20_AGENT_KBD_SOCK` override), and launches the supported
`--web --kbd-sock ... --web-config-dir ... --web-secrets-dir ...` CLI. No `--run`
mode exists. The runtime itself validates/connects the Unix control socket.
The current web CLI has its fixed usb0 bind and starts without preloaded VNC/model
configuration; this patch does not add runtime configuration or permission paths.

Crashes back off 1, 2, 4, 8, 16, 30 seconds; a run of at least 60 seconds resets
the delay. While waiting, running or backing off, the supervisor polls the stop
marker/config/FAT kill switch. Dependency loss stops/reaps the child and waits;
it is not recorded as a crash. `/tmp/mk20-agent-stop` is never erased by the
supervisor or boot. TERM cleans up the owned child; an unresponsive child gets a
bounded TERM grace followed by KILL. These operations never signal kbd/router.
The child helper writes/verifies oom_score_adj=500 and requests absolute nice=10,
verified from procfs stat (not `pgrep -x` or platform-specific `nice` output).
Failure logs a fixed WARN and still execs the runtime. All agent output is on
`/tmp/mk20/agent.log`; maint trims it in place exactly like router.log.

Selftest agent lines are INFO/WARN only, including missing/stale/malformed status
and crash loops on probation; its return code and loader confirmation are not
changed. Release integrity remains the loader's existing all-MANIFEST-file check,
including bin/mk20-agent; no new selftest FAIL path was added. Installer, uninstall,
manual rollback and release pruning paths only affect releases/slots/state;
agent and secrets directories remain in place. Tests inspect synthetic directory
metadata, never actual secret/key content.

The C runtime currently does not write `/run/mk20/agent.status`. Health reads that
optional file (`MK20_AGENT_STATUS` override), bounded to a non-symlink regular
file of at most 4096 bytes. The conservative awk reader accepts flat JSON scalar
objects, rejects duplicates/malformed/unsupported nested or unicode-escaped
objects, and exports only allowed(bool), state/provider/route/last_error_class
(identifier strings, max 80 characters). Extra keys are ignored. Arbitrary text,
URLs and credential-bearing URLs are not exported. Missing/unsupported fields
become null/unknown; all exported strings still pass jstr. No status C writer was
added; enabled/running come from config/process discovery.

RFB now accepts only literal IPv4/IPv6 and the existing exact `localhost` alias.
Hostnames, brackets/ports and IPv6 zone suffixes fail with
`vnc_host_not_literal` before socket creation; RFB never calls DNS/NSS.
Darwin inet_pton accepts `%zone`, hence the explicit rejection. The new hermetic
socket trap checks hostname rejection and zero DNS sockets for all VNC attempts.
Proxy/model DNS behavior and fail-closed never-direct routing are unchanged.

`make test` still runs the real UDP DNS fixture. Only EPERM/EACCES from creating
or binding its 127.0.0.1 UDP sockets prints the exact requested SKIP and exits 0;
all other setup errors fail. On unrestricted hosts all original UDP assertions
still execute. No other skip/xfail was added.

Verification commands, counts, logs and the item 1–9 self-review are recorded in
`../reviews/agent-a8b-codex-summary.md`. The portable integration harness runs the
real shell scripts with scoped host utility/process shims; it uses BusyBox if
available, otherwise dash. This Mac has no BusyBox and denies socket binds and
setpriority; real Linux procfs priority application, BusyBox 1.27 behavior,
armhf execution and A9 hardware gates remain open. No external network,
downloads, device/adb/serial, release-bin rebuild, commit or push was used.
