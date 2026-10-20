# mk20d maintenance hooks (K4 / 1.3.0-rc1)

The usb0 admin web (`http://172.30.250.2:8780`, `mk20-agent --web`, implemented
on the agent track) uses this local file contract. Per AGENT-BUILD-PLAN §3.8b,
`mk20-agent --web` is the ONLY usb0 admin web service; U3 adds its config/key/
maintenance pages to that service, never a second server on 8780. K4 implements
the supervisor side only. The web service owns authentication, pairing/CSRF
protection and usb0-only binding. No Mac app or keyboard combo is required. No hook starts stock KD, changes
the USB gadget, installs a release, accepts a command/path, or reads secrets.

## Maintenance page in the web

The default-on web (`FEATURE_WEB=1`, supervised by `mk20d web`; user guide
[../SETTINGS.md](../SETTINGS.md)) has a 维护 (maintenance) section, available only to a
paired browser session:

- **Status (read-only)**: `GET /api/status` copies an allow-list of fields from
  `/tmp/mk20/health.json` (`mk20d health`, refreshed by the maint loop about every 300 s:
  version, `release.slot/good/pending/last_rollback`, memory/FAT/data free, kbd mode/
  restarts, selftest pass and its WARN/FAIL lines as printable ASCII) plus the live
  uptime, the bound usb0 address, `state.json` below and the `debug.conf` verbose flag.
  Raw `kbd.status`, config paths, agent/provider state and every other field are dropped;
  nothing under `/data/mk20/secrets` is read.
- **Actions**: 重启键盘程序 (`restart-kbd`), 退出安全模式 (`exit-safe-mode`) and 详细调试日志
  (`debug`, `verbose=0|1`). Each is `POST /api/maint/<name>` with the session CSRF token,
  a same-origin request and a body that confirms the action by name
  (`{"confirm":"restart-kbd"}`; debug adds `"verbose":true|false`); the page asks first.
  The web publishes only into the runtime inbox `/tmp/mk20/maint/` with the contract
  below (exclusive `.web-<random>.tmp`, mode 0600, fsync, rename, directory fsync). It
  never creates or chmods the inbox: a missing, symlinked or non-0700 inbox returns
  `maint_unavailable`. Its own abandoned temporaries are removed at web startup.
  The page then re-reads `state.json`, and lists a request as pending while its fixed
  basename is still unclaimed.
- There are no reboot, rollback, install, keytrace or log-file actions in this version.

## Inboxes and publication

- `/data/mk20/state/maint/`: persistent inbox (survives reboot), mode 0700.
- `/tmp/mk20/maint/`: runtime inbox, mode 0700. Prefer it for immediate actions.
- Both are private, root-owned local directories, not shared writable directories.
  The supervisor rejects symlink inboxes. Web code must not replace the directories
  or touch internal dotfiles, `safe`, `debug.conf` or `state.json`.
- Publish a regular file mode 0600 using an exclusive temporary file in the **same
  inbox**, full write, fsync, close, then atomic rename to one of the names below.
  For durable publication fsync the directory too. Never publish symlinks, devices,
  FIFOs, directories or hard links. User input must never determine a filename.
- The single kbd supervisor checks every ~200 ms during a run/backoff, claims each
  fixed basename by rename to `.<name>.consume`, validates it, then unlinks it.
  Before claiming, existing regular/symlink consume destinations are unlinked;
  directories and other non-regular destinations are refused without moving the
  request. Claimed hard links are rejected before reading or changing permissions.
  Bodies are bounded to 128 bytes before reading. Invalid requests are discarded
  with a fixed diagnostic; contents are never logged. Unknown names are ignored;
  actual directories are not traversed/removed. Clean abandoned producer temps in
  the producer. A claimed dotfile left by a supervisor crash is not replayed.
- Repeated publications of a basename coalesce; requests from both inboxes observed
  in one poll cause at most one restart. Requests express idempotent desired actions,
  not an exactly-once job queue. A power loss during consumption can lose a request;
  observe state and re-submit if necessary. With kbd developer-disabled there is no
  consumer. Requests cannot bypass teardown stop markers or KD/device ownership.

| Basename | Exact body | Action |
|---|---|---|
| `restart-kbd` | Empty (0 bytes) | TERM the daemon, wait up to 3 s for graceful release, then KILL if necessary; restart with current mode/settings only after the child is gone (never overlap a stuck child). Not a crash; no crash counter increment. |
| `exit-safe-mode` | Empty (0 bytes) | Clear the persistent safe latch and recent crash timestamps, reset backoff, restart normally. The since-boot total/last real crash rc are retained for diagnosis. Repeating while normal keeps normal mode. |
| `debug` | `verbose=0` or `verbose=1`, optional trailing newline | Replace persistent `debug.conf` (0600). On **next start**, `verbose=1` adds `--verbose`; 0 removes it. No implicit restart. Every other key/value, flags, paths and shell expressions are rejected. |

Verbose logging contains mode and error counters only. It does not trace keys,
provider configuration, HTTP bodies or `/data/mk20/secrets/*`. K4 exposes no keytrace
hook. Use `restart-kbd` after a debug change if immediate application is needed.

## Read-only state

`/tmp/mk20/maint/state.json` is an atomic, mode-0444 snapshot produced on supervisor
start/transitions and about once per second while running. Its directory is private.
Snapshots use an exclusively created temporary file in the private runtime inbox;
symlink destinations are unlinked and directories refused before rename.
0444 signals producer ownership, not an authorization boundary against root. The web
service reads it and never modifies it. It is absent before the supervisor starts;
check process health/status freshness separately if the supervisor dies.

```json
{"mode":"safe","restarts":3,"recent_crashes":3,"last_rc":139,"reason":"crash-loop","version":"1.3.0"}
```

- `mode`: `normal` or `safe`; persistent latch or the daemon status determines safe.
- `restarts`: real unexpected daemon exits since boot (including failed exec), not
  maintenance restarts. Restarting the supervisor does not reset this counter.
- `recent_crashes`: exits in the inclusive last 300 s (last 20 timestamps retained).
- `last_rc`: last real exit status since boot; 0 if no crash has been recorded.
- `reason`: fixed `normal`, `crash-loop`, `kd-stop`, or `missing-binary` diagnostic.
- `version`: release version, not request data.

`/run/mk20/kbd.status` independently reports actual `mode`, `estopped`, GD32,
framebuffer and HID health. `mk20d health` includes mode, counters and status. Neither
snapshot contains secrets. Log viewing is a separate web concern: allow-list log
files; never offer arbitrary paths or the secrets directory through this contract.

## Safe mode and recovery

At >=3 exits in 300 s the supervisor latches `state/maint/safe` and keeps restarting
`mk20-kbd --safe-mode` with backoff 1,2,4…30 s (healthy runs >=60 s reset backoff).
The latch survives reboot until `exit-safe-mode`. All 21 panels show static rescue
text; normal GD32-owned typing and the existing zero-keycode PTT-to-Right-Option
mapping remain, and K2 e-stop releases HID. Safe mode opens no host link, JPEG
renderer, on-board navigation or agent control socket. It uses the same GD32 TX
allow-list: only `05 00 r r 00 00`, r=100..105; the read-only switch sends nothing.

Safe mode itself is WARN even on probation. Missing/dead kbd, stale status, missing
GD32 liveness, incomplete panels or panel errors remain FAIL on probation and WARN
after confirmation. KD alongside kbd is always FAIL. The loader retains A/B rollback
for unconfirmed releases; entering safe mode never tears down ECM/usb0 or adb.
If KD reappears during a run, the supervisor stops its kbd child first, then retries
`qt_app2 stop` with 1,2,4…30 s backoff. A failed service stop blocks kbd relaunch
even when no KD PID is visible; relaunch requires a successful stop and KD gone.
A missing binary cannot render a rescue page: supervision retries, selftest fails
on probation, and the loader restores the previous slot.

Restarting after Tina's HID re-bind may leave hidg0 ENXIO or a modifier held until
USB reset/reboot. Prefer watcher `cmd-reboot` when HID is lost; see [RECOVERY.md](RECOVERY.md).
The agent web service is an agent-track dependency extended by U3; adb plus A/B
remain usable while that service is unavailable. No hook changes rootfs p5 or the stock binaries.
