# A8 part 2 — Codex host summary (2026-10-20)

Host implementation complete. Full BusyBox/Linux acceptance remains open; this
Mac has no BusyBox and its sandbox denies setpriority and socket binds. No device,
adb, serial, external network, downloads, release-bin rebuild, commit or push.
The pre-existing untracked `prompt-agent-a8b.txt` was left untouched.

## Changes

- `FEATURE_AGENT=0` is a release default and whitelisted config/printed field.
  FAT `mk20-no-agent` and `MK20_AGENT=0` force it off; environment `=1` cannot
  enable it. No runtime allow/grant option or remote permission path was added.
- Boot launches `mk20d agent` detached only when enabled and executable. The
  supervisor owns `agent.lock`, waits for composite + kbd.sock existence, and
  polls all gates during a running child and backoff. Dependency loss stops the
  owned child and waits; stop/config/FAT disable ends it. A pre-existing stop
  marker is preserved. Backoff is 1→2→4→8→16→30, capped at 30; ≥60 seconds resets
  it. TERM cleanup gives its child a bounded grace then KILL/reap.
- The actual persistent CLI is `--web --kbd-sock PATH --web-config-dir DIR
  --web-secrets-dir DIR`. There is no `--run` mode. No option enables run-level
  permission. The current fixed usb0 web bind and absence of preloaded VNC/model
  arguments are unchanged runtime limitations, not a new runtime configuration
  interface. Log/priority diagnostics go to tmpfs `agent.log`.
- A small child wrapper requests/verifies oom_score_adj=500 and absolute nice=10
  via procfs; fixed warnings and continued execution cover failures. This Mac
  exercises the denied-priority fallback; a scoped synthetic procfs test checks
  both verification success branches without claiming actual kernel application.
- Agent selftest messages only INFO/WARN; the existing return code still drives
  confirmation/rollback. Even a crashing optional agent on probation confirms.
  The loader's existing MANIFEST verification handles missing/mismatched binaries.
- Health adds only enabled, allowed, state, provider, route, last_error_class and
  running. The optional status reader requires a non-symlink regular file ≤4096
  bytes, parses conservative flat JSON scalars, rejects duplicates/malformed or
  unsupported input, and exports only bounded identifier strings and allowed
  boolean. All strings still use jstr; no raw extra fields or URLs are emitted.
  Missing/unsupported status defaults to null/unknown. The C runtime writes no
  status today; no C status writer was added.
- Maint trims agent.log in place at >256 KiB to its last 64 KiB, like router.log.
  Natural loader/installer tests now assert directory preservation; portable
  loader tests also exercise real install, rollback and uninstall with synthetic
  archives and directory/sentinel metadata. No real secret/key content was read.
- RFB uses inet_pton only for IPv4/IPv6, preserving exact localhost; hostnames and
  zone suffixes are rejected as vnc_host_not_literal before any socket. Darwin
  accepts `%zone` in inet_pton, so explicit rejection is necessary. The RFB-only
  test no longer links dns.c. Model/proxy resolution and never-direct policy are
  unchanged.
- The UDP fixture remains in default make test. Only socket()/bind() EPERM/EACCES
  at its loopback setup emits `SKIP: UDP sockets not permitted (EPERM/EACCES)` and
  exits 0. Other failures still assert; permitted hosts run all UDP assertions.

## First-acceptance self-review against requested items

PASS here means implemented and checked within the authorized host scope. FAIL
means a required verification gate could not run, not a silently waived check.

| Item | Result | Evidence / remaining limit |
|---|---|---|
| 1 — release switch | PASS | default/config/FAT/env precedence and invalid values tested; header documented |
| 2 — agent supervision | PASS | singleton, dependency waits/rechecks, stop/disable, cap/reset, real CLI argv, detached boot tested; priority success simulated, actual Linux application pending |
| 3 — selftest | PASS | disabled/missing/stale/malformed/crash-loop states never FAIL; rc unchanged with/without another failure; probation crash confirms; normal manifest errors retained |
| 4 — health | PASS | valid JSON for absent/malformed/oversize/hostile status, strict field whitelist, synthetic api_key/token/password/credential URL never emitted |
| 5 — maint/preservation | PASS | in-place trim tested; real loader install/rollback/uninstall preserves directory + sentinel inodes; natural legacy assertions added |
| 6 — VNC literal-only | PASS | hermetic socket trap checks hostname rejection before sockets and zero DNS sockets, IPv4/IPv6/localhost accepted as stream attempts |
| 7 — UDP permission handling | PASS | default make test retains --udp; actual sandbox bind EPERM produces exact SKIP, exit 0; other errno paths assert |
| 8 — test execution | FAIL | new portable tests and C suites pass; original BusyBox firmware suites cannot initialize on this Mac (no BusyBox). No skip/xfail added for that failure |
| 9 — docs/handoff | PASS | plan A8 status dated 2026-10-20, appended NOTES “A8 part 2”, this summary and raw logs |

## Exact final test commands and results

All commands ran from the worktree root using the existing cached Python; no
package installation was attempted.

```sh
/Users/dev/.cache/uv/archive-v0/vxkKwzR1zHgPnvko/bin/python -m pytest -q deck/tests/test_mk20d_agent.py
```

Result: 38 passed. Raw output: `agent-a8b-agent-pytest.log`.
The harness uses the real scripts under dash on this Mac (BusyBox when present),
scoped flock/pgrep adapters, proc-uptime/stat adapters, BSD wc count normalization
and a BusyBox-format tar listing adapter (real extraction still runs). A regular
placeholder tests kbd.sock's existence gate; this does not claim a successful real
Unix connection, whose bind is sandbox-blocked. Virtual uptime/sleep only test
backoff/cap/reset; actual dependency waits and TERM/stop handling also run.

```sh
/Users/dev/.cache/uv/archive-v0/vxkKwzR1zHgPnvko/bin/python -m pytest -q deck/tests/test_mk20d_agent.py -k 'backoff or boot_confirms_despite'
```

Result: 2 passed, 36 deselected. After the full run, the synthetic crash fixture
was tightened to exit 7 explicitly (rather than cleanly exit through its stop
handler); both affected tests were rerun, including an explicit rc=7 log check.
Raw output: `agent-a8b-crash-pytest.log`.

```sh
/Users/dev/.cache/uv/archive-v0/vxkKwzR1zHgPnvko/bin/python -m pytest -q deck/tests/test_mk20d_agent.py -k 'loader or manifest or priority'
```

Result: 6 passed, 31 deselected (before the final boot-crash regression was added).
Raw output: `agent-a8b-loader-priority-pytest.log`.

```sh
/Users/dev/.cache/uv/archive-v0/vxkKwzR1zHgPnvko/bin/python -m pytest -q deck/tests/test_agent_release.py deck/tests/test_mk20d_router.py -k 'release_switches or default_descriptor_bytes or agent_release_ca_bundle'
```

Result: 6 passed, 21 deselected. Raw output: `agent-a8b-release-pytest.log`.

```sh
make -C deck/agent test
```

Result: exit 0 with ASan/UBSan. DNS parser/literals/config 180 checks; HTTP/Web/MCP,
A5/LLM/OAI, RFB/scale/image, net/clock and CLI suites passed; the new RFB literal
trap passed. Net/clock reports 366 checks, zero invalid-config sockets; OAI reports
3145 checks, zero failures. The final real UDP fixture emitted the requested
permission SKIP, so its real UDP spoof/failover/deadline assertions did not run
here. Raw output: `agent-a8b-make-test.log`.

```sh
/Users/dev/.cache/uv/archive-v0/vxkKwzR1zHgPnvko/bin/python -m pytest -xq deck/tests/test_mk20d.py deck/tests/test_mk20d_router.py deck/tests/test_mk20d_kbd.py deck/tests/test_agent_release.py deck/tests/test_loader.py deck/tests/test_install.py
```

Result: exit 1, 1 fixture error, stopped at test_defaults_unchanged: conftest.BB is
None; _bbdir cannot symlink an absent BusyBox. These legacy full suites are
**not** reported as passing and were not converted to skips. Raw output:
`agent-a8b-native-pytest.log`.

```sh
sh -n deck/firmware/release/sbin/mk20d
sh -n deck/firmware/release/share/mk20lib.sh
sh -n deck/firmware/release/share/mk20-agent-child.sh
/bin/dash -n deck/firmware/release/sbin/mk20d
git diff --check
```

Result: all exit 0. A read-only Python comparison against `git show HEAD:...`
verified both router/kbd supervisor case bodies are byte-identical; tracked
changes stay inside deck/, avoid all protected paths, and leave release/bin
unchanged. Shellcheck is not installed. No armhf compiler/build/execution used.

## Remaining acceptance gates

Run the original firmware pytest files with actual BusyBox 1.27 on Linux; verify
real procfs OOM/nice values, real Unix connection and listener behavior, and all
UDP assertions with loopback sockets permitted. Box builds the release binaries
and verifies binary matching/MANIFEST; hardware/A9 soak remains outside this
host-only task. No agent crash/status condition was added to rollback decisions.
