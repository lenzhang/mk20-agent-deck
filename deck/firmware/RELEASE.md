# MK20 Agent firmware: releases

Applies to: Waveshare MK20 (Allwinner T113, Tina Linux 5.4.61 #221, BusyBox 1.27.2). The firmware is a userland
layer on `/data` (ext4/UDISK). It never writes the rootfs. Kernels are only swapped as whole boot images (p4,
see RECOVERY.md level 5).

## 1.8.0: production `v1.8.0` (2026-10-28) = build rc1 + Mac 52ae248

- **Firmware**: `mk20-fw-1.8.0.tar`, `firmware/VERSION` **1.8.0**, build id **rc1** (MANIFEST `version=1.8.0`,
  `build=rc1`), sha256 `294e15445d225e3eddc28758b60025a32b986f427d354c24ce2f3f2957b3ecc5` (3020800 B), built from
  f957d74. Not rebuilt for the tag: the payload (`deck/firmware`, kbd/agent/UI sources) is unchanged between f957d74
  and this commit (only Mac-side files, docs and reviews differ). Installed 2026-10-27 14:27 SGT on slot A, auto-confirmed
  by the probation gate (`PASS ecm host traffic`); device evidence 666e69c / 4eb998f (`deck/reviews/1.8.0/`).
- **24 h soak PASS** `soak-20261027-144438` (2026-10-27 14:44:38 → 2026-10-28 14:44:38 SGT, iMac launchd; verdict
  `deck/reviews/1.8.0/soak-20261027-144438/VERDICT.md`, 8fb7041): 288 samples, failed 0, reboots 0, kd_restarts 0,
  rollbacks none, composite_losses 0, host_link_stalls 0, self-heals 0, bridge_stalls_total 0, host_flushes_total 0,
  min MemAvailable 76744 kB, min FAT free 1210 kB, kernel_mem_trend flat (SUnreclaim 3.6 kB/h). host_link was
  `idle` in 287 samples: no deck client during the soak (the Mac daemon was blocked, see next point).
- **Mac host fix (mk20deck only, merged 52ae248 from fix/deck-py39 450269a + 4d12625)**: macOS 15 Local Network
  privacy silently blocks the unsigned uv python3.13 (connect 172.30.250.2:17890 → EHOSTUNREACH 65, no prompt);
  the LaunchAgent now runs Apple's `/usr/bin/python3` 3.9.6 (`~/mk20-deploy/.venv-sys`): `tomllib` → `tomli`
  fallback, `requires-python >=3.9`, hooks merge `parse_intermixed_args`, Codex adapter `immutable=1` fallback when
  the system SQLite cannot open the WAL db read-only. Deployed and connected 2026-10-28 16:47:49 SGT (heartbeats
  accepted, host_link ok). Notes/rollback: `deck/docs/deploy-system-python.md`.
- Known: Mac en2 MTU 1500 vs device 462 (ICMP > 462 B lost, TCP fine; fixing needs sudo). `make mac-test` on the iMac
  (py3.9/3.13): Darwin timing failures only (`test_agent_backoff_and_healthy_reset`, see
  `deck/reviews/1.8.0/mac-test-4eb998f-failure.md`; kbd `test_hook_interrupts_backoff_and_resets_next_delay` and the
  py3.9 `selftest[crashing]` fixture clock, see the deploy doc). FAT free ~1.2 MB.

## 1.8.0-rc1: CDC-NCM USB network function (macOS 15 Intel: the AppleUserECM dext asserts, no enX)

Build id **rc1**: `sh tools/mk20-release-build.sh firmware out rc1`; `firmware/VERSION` is **1.8.0** (MINOR: the
function the host sees changes). Base: 1.7.1-rc1.

- `lib/usb_f_ncm.ko` (stock 5.4.61 #221 vermagic; source `kernel/stock-patch/f_ncm-mk20.patch`, pinned by
  `usb_f_ncm.ko.sha256`): every NTB fits one 512-byte bulk request (sunxi_udc PIO only): OUT NTB 512, IN NTB cap 508
  (never a ZLP pad), wMaxSegmentSize 476 = device MTU 462 (ECM keeps 478); an oversize TX frame is dropped and counted.
- `gadget.sh`: NCM first; module missing/refused or the function not created -> the ECM function (`WARN NCM
  unavailable … -> ECM fallback`). `mk20-no-ncm` (FAT), `FEATURE_NCM=0` (mk20.conf) or `MK20_NCM=0` force ECM. The
  other function's stale instance is removed before the chosen one is created (usb0 keeps its name on a live switch).
  Host MAC / device IP / HID / ADB / bridge unchanged; the ECM watchdog, re-arm and probation gate work on either
  function (`health` `net.fn`).
- Reviews: `deck/reviews/1.8.0/ncm-glm-r{1,2,3}.md` (r1 2 blockers fixed; r2, r3 APPROVE).

## 1.7.1-rc1: same content as 1.7.0-rc3, new version for the device

Build id **rc1**; `firmware/VERSION` is **1.7.1**. 1.7.0 (build rc2) is installed and good on the device and the loader
refuses a different 1.7.0 ("different 1.7.0 is good/current"; RELEASE.md: never rebuild a released version with
different content). 1.7.1-rc1 therefore carries exactly the 1.7.0-rc3 tree (ECM watchdog + probation gate + the
current-boot flap record fix, Kimi K3 rc3 delta GO) with only VERSION/docs changed; PATCH because nothing visible
to the host changes against 1.7.0.

## 1.7.0-rc3: ECM host→device watchdog + host-traffic confirm gate (incident 2026-10-23 23:07)

### 1.7.0-rc3: re-arm refused after every reboot (hardware finding 2026-10-24)

Build id **rc3**: `sh tools/mk20-release-build.sh firmware out rc3`; VERSION stays 1.7.0. Changed against rc2:
`share/gadget.sh` and `share/flapmon.sh` (`sbin/mk20d`: one defensive guard); the binaries are identical.

- **Finding** (1.7.0-rc2 installed: PASS, the probation gate logged `PASS ecm host traffic (usb0 rx 5636 -> 5839,
  answered)`, health `usb.ecm_state` ok; evidence `deck/reviews/1.7.0/rearm-test-20261024/`): the manual re-arm from
  RECOVERY.md was refused, `rc=4 up=165.68 refused: flapmon link drop within 65s`. rc2's `flap_recent` took the last
  `flapmon N: link drop` line of the **persistent** `/data/mk20/logs/gadget.log(.1)`; it was from the previous boot
  (`flapmon 71272: …`, that boot's uptime) → `165 - 71272` < 0 passed `-le 65` → "recent": every re-arm (watchdog and
  probation gate) was refused until the new uptime passed ~71 337 s (~20 h) — right after a reboot, exactly when
  the ECM fault happens, 1.7.0 could not act (the gate would only have WARNed "not judged").
- **Fix:** flapmon records each counted drop in tmpfs (`/tmp/mk20/flapmon.last-drop`: `<uptime>`, this boot only;
  gadget.sh removes it whenever it starts a new monitor); `--rearm-now` reads only that record, never a log, and an
  age < 0 is stale.
- **Audit** of every comparison of a stored stamp against now, for the same cross-boot bug: the install-inbox check
  compared wall-clock mtimes (rc2: changed within 120 s) — with no RTC the clock restarts at every boot, so an mtime
  cannot tell boots apart either: it is now boot-independent (adbd holds a file open in an inbox = a push in
  progress; the loader's staging as before; a leftover file no longer blocks). mk20d's ECM state is on tmpfs
  (`/tmp/mk20/ecm.state`); `ecm_sane` now also drops any stamp ahead of now (re-arm hour window, the last re-arm,
  the stall window, the answer-check cadence). Fine as they are: `ecm-reboots` (a counter, no stamps), the
  kbd/agent crash windows (tmpfs, `now >= stamp` checked), the loader's deadline (computed and polled in the same
  boot; `deadline-reset-<v>` is presence only), watcher/hardreset (log/info lines only), kbd/agent status ages
  (wall clock on the `/run/mk20` tmpfs, negative = stale since 1.3.1).
- Tests: `test_ecm_watchdog.py` +7 — persistent log lines of an earlier boot with uptimes larger and smaller than
  now never block (both fail on rc2), the tmpfs record outside its window or in the future does not block, a
  leftover inbox file does not block (fails on rc2), the device's refusal replayed, future ECM-state stamps stale
  (2 cases); the push refusals now use an adbd stand-in holding the inbox file open. `test_flapmon.py` +2 (the
  record holds the last counted drop's uptime; a long suspend is not recorded).

rc3 host validation only (Docker mk20-build:trixie, BusyBox 1.27.2, nothing else running beside it): `make test` green
on the first run — tests 1407 passed / 1 skipped (rc2 1398: +7 `test_ecm_watchdog.py`, +2 `test_flapmon.py`), agent
398, onboard 16683, kbd 4219 ASan + 4219 TSan, safe mode 2 scenarios, e2e OK, ui 41405, 374 goldens unchanged, sim
38, tools 83 passed / 1 skipped, shellcheck clean, release binaries match source. `make mac-test` (macOS, native, on
a `git archive` export of 08f880a): 824 passed / 584 skipped, no failures. E-stop latencies unchanged (socket e-stop
-> all-up 1.2 ms; stalled host 0.1–0.2 ms, its K2 hold 1003.0 ms; blocked status worker 0.8 / 1001.2 ms). The
earlier-boot (larger and smaller uptime) and leftover-inbox tests fail on the rc2 gadget.sh. `sbin/mk20d` sha256
`c2a85e1558c7ff977c8034fa10c96456f315d91be062f0e0a491d44e910f8c27`, `share/gadget.sh`
`087b800a6a8d13b24c4bfbbb3766a89156acfafebe373ea166f8d72b77bf87db`, `share/flapmon.sh`
`1c334a4ff4500e70311bf5b512ebb3d07cc745812308f23008855483b79408bf`; the binaries and the other 9 files are identical to
rc2. `mk20-fw-1.7.0.tar` (rc3, 14 files, 2754560 B; two builds from independent `git archive` exports of 08f880a are
byte-identical) sha256 `15d1ff9e8c09471aebe6e2206637615a05846b5262ebb836e14c00fd6c4d5caa` (`~/worktrees/rel-1.7.0-rc3/`).
**No device acceptance claimed for rc3** (rc2 is installed and passed acceptance; the manual re-arm from RECOVERY.md is
the open check: `rearm.last` rc=0, `REARM OK`, and `/tmp/mk20/flapmon.last-drop` absent or old).

### 1.7.0-rc2: Kimi K3 1.7.0-rc1 delta (GO) MAJOR + MINORs

Build id **rc2**: `sh tools/mk20-release-build.sh firmware out rc2`; VERSION stays 1.7.0 (rc1 never installed).
Review records: `deck/reviews/1.7.0/fw162-rc1-kimi-k3.md` (1.6.2-rc1 NO-GO), `fw170-rc1-kimi-k3.md` (1.7.0-rc1 GO, all
1.6.2 items FIXED, no USB-bricking path, ecm-reboots bounded). Changed against rc1: `sbin/mk20d` and
`share/gadget.sh`; the binaries are identical to rc1.

- **MAJOR (mk20d probe):** the rc1 probe wrapped each ping in a looping subshell and killed only that subshell, so
  a ping that does not exit by itself (the non-fancy `ping HOST`) was orphaned, one per host address every
  verification round (process/memory growth over a long episode; late replies inflating usb0 RX). Now each round
  backgrounds every ping as itself (its pid is the ping), kills and reaps exactly those at the end of the round,
  and reads the reply from its exit status. `ping -c 1 -W 1` is preferred (confirmed on the device: BusyBox 1.27.2
  `ping -c 1 -W 1 127.0.0.1` rc 0); plain `ping HOST` stays the fallback and cannot orphan either.
- **MINOR (adb push / install):** besides the install inboxes (`/data/mk20/incoming` — where
  `tools/mk20-install.sh` pushes — and `/tmp/mk20/incoming`), the loader's staging (`releases/.staging.tar`,
  `releases/.incoming-*`, present only while `loader install` runs) refuses a re-arm, so an install in progress is
  never interrupted.
- **MINOR (alias):** setup_main only WARNs when the `usb0:1` link-local alias fails (the bridge then listens on the
  ECM address only); it now records what it achieved (`/tmp/mk20/usb0.setup` `ll=1|0`), and a re-arm restores and
  requires exactly that (the alias and its listener only with `ll=1`; without a record: as it was before the
  re-arm), so a board running without the alias no longer gets rc 2 → reboot on its first re-arm.
- **Accepted, documented:** a permanent adbd child keeps the re-arm refused (logged once per episode; the watchdog
  keeps observing, the gate reports WARN, nothing fails).
- Tests: `test_ecm_watchdog.py` +9 — no ping survives a probe, fancy and non-fancy (a ping that never exits by
  itself; both fail on the rc1 mk20d), the fancy argv; loader staging (`.staging.tar`, `.incoming-*`) refused; a
  setup without the alias (record `ll=0`, usb0 kept and re-created) re-arms with no reboot; without a record the
  alias is required exactly if it was up; setup records the alias result before the bridge starts.

rc2 host validation only (Docker mk20-build:trixie, BusyBox 1.27.2, nothing else running beside it): `make test` green
on the first run — tests 1398 passed / 1 skipped (rc1 1389: +9 `test_ecm_watchdog.py`), agent 398, onboard 16683, kbd
4219 ASan + 4219 TSan, safe mode 2 scenarios, e2e OK, ui 41405, 374 goldens unchanged, sim 38, tools 83 passed / 1
skipped, shellcheck clean, release binaries match source. `make mac-test` (macOS, native, on a `git archive` export
of 841bf74): 824 passed / 575 skipped, no failures. E-stop latencies unchanged (socket e-stop -> all-up 1.4 ms;
stalled host 0.1–0.2 ms, its K2 hold 1011.8 ms; blocked status worker 0.8 / 1008.4 ms). The two orphan tests fail on
the rc1 mk20d. `sbin/mk20d` sha256 `84114c573eadafd0708cffed5a4b0461d447e1b5ad65fbac97df7338b1a67348`,
`share/gadget.sh` `984ad3297440ec621bf4008ca9bbbfed98e5d827f621481473304905ede2aa9c`; the binaries and the other 10
files are identical to rc1. `mk20-fw-1.7.0.tar` (rc2, 14 files, 2754560 B; two builds from independent `git archive`
exports of 841bf74 are byte-identical) sha256 `07fdb48077f05543cdb728122dfcc22a202ff0247c8ec1778be4d4a254ae3876`
(`~/worktrees/rel-1.7.0-rc2/`). **No device acceptance claimed** (the hardware checks listed under rc1 below apply).

### 1.7.0-rc1

`firmware/VERSION` is **1.7.0** (MINOR by the rule below: a new self-test gate); build id **rc1**:
`sh tools/mk20-release-build.sh firmware out rc1`. feature/fw-1.6.2 = release/1.6.1 (8d08ca9: 1.6.1-rc2 + its
install evidence) + this work. The same work was first built as 1.6.2-rc1 (`mk20-fw-1.6.2.tar` sha256
`3487bd43b24002cd5db9d81e6f6f08fd637fa88b5f1bfd46ed54eee786c8d4ee`, never installed): Kimi K3 NO-GO, renamed to 1.7.0
(section "1.7.0-rc1: Kimi K3 1.6.2-rc1 review fixes" below). Changed in the release: `sbin/mk20d`, `share/gadget.sh`,
`bin/ttygs_tcp_bridge`, `bin/mk20-kbd` (both rebuilt static armhf); `bin/mk20-router`, `bin/mk20-agent`,
`share/flapmon.sh`, `share/mk20lib.sh`, the other scripts and the rescue tier are byte-identical to 1.6.1-rc2. No host
protocol change (mk20deck untouched).

**Incident** (1.6.1-rc2 install, `deck/reviews/1.6.1/incidents/incident-20261023-2307-ecm`,
`install-1.6.1-rc2-20261023-230504`): after the probation reboot the gadget was CONFIGURED and usb0 carrier 1 / up,
but host→device was dead: device usb0 RX frozen at 1 packet (28 B, the Mac's first ARP) and TX 3 (all 3 reached
the Mac: its Ipkts 3), the Mac's Opkts 161 and its ARP for 169.254.7.x incomplete; mk20deck could not connect. The
selftest passed (nothing in it sees host→device traffic) and the loader confirmed 1.6.1; a watcher hardreset fixed
it (re-acceptance PASS).

### ECM RX watchdog (`mk20d maint`)

- **Home: the mk20d maint loop**, not flapmon. flapmon stays the minimal per-generation monitor whose only output is
  a rollback request (0.3 s polls, started by gadget.sh in every mode, its own lock): a re-arm inside it would make
  the process that counts link drops cause and judge its own drops, and a hung probe would blind the flap
  protection. mk20d owns config, health and selftest, the counters live there, and it already uses the gadget
  transition mutex the same way (boot's mk20-kbd launch). The loop ticks every `MK20_ECM_TICK_S` = 5 s (integer
  1–60; anything else = 5; a floor sleep of 1 s); rotation + health keep their 300 s period. During probation the
  boot gate (below) runs the same functions.
- **A stall (all of it, every time):** the host is present and awake — UDC state `configured` (a sleeping Mac
  suspends the bus: `suspended`; unplugged / a charger is not configured; flapmon's suspend rule reads the same
  state file) — its ECM driver runs the data interface (usb0 carrier 1), usb0 RX has not grown for **60 s**, and a
  probe proves the host reads the device while nothing it sends arrives: BusyBox `ping` toward the host addresses
  (learned on usb0 from ARP, the subnet's `.1`, `169.254.7.1`; ARP first; `ping -c 1 -W 1` when the BusyBox has
  FEATURE_FANCY_PING — tested on loopback, the device has it — else plain `ping HOST`; rc2: rounds of 1 s, every
  ping the probe's own child, killed and reaped each round) for ≤ 8 s makes usb0 TX grow — u_ether counts a
  TX packet only when its IN transfer completed, i.e. the host polled the bulk IN endpoint — and RX stays put. A
  healthy host answers the ARP in milliseconds (an idle Mac without mk20deck is probed, answers, and nothing
  happens); a host that completes no TX (not polling usb0, e.g. asleep without a USB suspend) is `host-idle`: no
  action. The incident satisfies all of it (the device's TX reached the Mac, RX stayed at 1).
- **Why 60 s:** mk20deck sends at least every 2 s (heartbeat 1.2 s connected, TCP candidate probes every 2 s while
  not), so 60 s is ~30 missed host packets — immune to App Nap, a daemon restart or a busy Mac — and it equals
  flapmon's default window. The window restarts whenever the host is not configured / carrier 0 / a gadget
  transition (`gadget.busy`) runs.
- **Action and recovery:** `gadget.sh --rearm-now` = ONE soft re-enumeration of the existing composite, usb0 /
  bridge / web restored (next section). **Recovered = an end-to-end answer from the host**: an ICMP echo reply to
  the probe, or a new bridge client (mk20deck reconnecting = a TCP round trip; covers a Mac whose firewall drops
  pings) — never RX growth alone (a bare usb0 still receives the Mac's ARPs). Within an episode the answer is
  checked every `ECM_VERIFY_S` = 15 s, a further re-arm follows 60 s after the last one without an answer.
- **Limits and escalation:** at most **3 re-arms per rolling hour**; when a 4th would be needed the soft path is
  exhausted: the watcher's `cmd-reboot` (WDT hardreset, the incident's proven fix), **at most once in a row across
  boots** (`/data/mk20/state/ecm-reboots`; reset to 0 by any proven link: an answer, a gate PASS, host traffic
  outside an episode). A boot that is still dead after such a reboot keeps observing (WARN) and does not reboot
  again; after 2 ECM reboots in a row (gadget.sh's own rc-2 reboots count) re-arms are off until a link is proven.
  Never a reboot from the watchdog on probation (the loader decides there). Only re-arms that touched the USB/HID
  count (rc 0–3); a refused one (rc 4/5/6) is retried and logged once per episode. Deferred while the keyboard is in
  use (kbd.status `key_events` + `knob_events` changed in the last tick: a re-enumeration drops the HID keyboard for
  ~2 s). FAT `mk20-no-ecm-rearm`: observe and report only (no re-arm, no reboot; the gate does not judge a stall).
- **Counters** (`mk20d health` → `usb`): `ecm_state` (idle / absent / suspended / inactive / ok / host-idle /
  stalled / gave-up), `ecm_rx_packets`, `ecm_tx_packets`, `ecm_rx_stalls` (episodes), `ecm_rearms`,
  `ecm_recoveries` (answered after a re-arm), `ecm_probes`, `ecm_gave_up`, `ecm_reboots` (consecutive, persistent).
  Selftest line (INFO/WARN only, never FAIL): `INFO ecm usb0 rx N tx M (udc configured)`, `INFO ecm host->device
  stalls S, re-arms R, recovered K`, `WARN ecm host->device stalled (usb0 rx N; no answer yet; …)`, `WARN ecm
  host->device stalled: soft path exhausted after R re-arm(s), watcher reboot requested | no reboot`. mk20d.log
  (component `ecm`): stall start, every re-arm with its rc, the answer, deferrals once per episode, the escalation.
  State per boot in `/tmp/mk20/ecm.state`; `health` / `selftest` only read it.

### Probation gate (`mk20d boot`)

- **Rule:** on probation (state/pending = this version), with the composite set up (gadget rc 0) and the 3-sample
  selftest passed, `ecm_gate` decides:
  - UDC not `configured` → `INFO ecm gate skipped: no host (udc <state>: asleep, unplugged or charger)`; carrier 0 →
    `INFO … host has not enabled ECM`; no usb0 counters → `WARN ecm gate: no usb0 counters (not judged)`;
  - probe 1: usb0 RX grew (host→device frames arrive — exactly what the incident lacked) or an answer → `PASS ecm
    host traffic`; TX did not complete → `WARN ecm gate: host not reading usb0`;
  - TX grew and RX not = a stall → the gate's re-arm (deferred while the keyboard is in use; waits ≤ 90 s while it
    is busy/deferred): re-arm rc 0 → ≤ 30 s for the host to configure the re-enumerated device → probe 2 requires
    an **answer** → `PASS ecm host answered after re-arm`, else `FAIL ecm host->device dead after re-arm: no answer
    from the host` (**not confirmed**); rc 2/3 (USB not restored, reboot requested) → FAIL; a re-arm that could not
    be tried (rc 1 aborted before any USB change, 4 busy/keyboard for the whole wait, 5, 6, 7 = the FAT flag) →
    `WARN ecm gate: host->device stall not judged (…)`: the other checks decide (confirm). A host that leaves or
    falls asleep during the gate = INFO (skipped).
  - FAIL therefore only when the stall is proven AND the gate's own re-arm ran (or could not restore USB) AND the
    host still does not answer. The gate's re-arm counts in the watchdog's 3/h budget; worst case it adds ~4 min,
    inside the loader's 600 s.
- **Outcome of a FAIL = the loader rolls back, intended and safe:** no confirm → the loader's deadline hard reset
  (≤ 600 s) → next boot `attempts 2 > 1` → the good release, via a fresh boot that re-enumerates USB — the proven
  fix of the incident. A release is never confirmed with a host link that is present and provably dead after the
  soft fix; the device is never left on an unconfirmed release. Cost of a spurious FAIL: one rollback
  (`last_rollback` says why); it needs an awake Mac with ECM active that neither runs mk20deck nor answers
  ARP/ICMP on any probed address (outside the INSTALL §5 setup: Serial = 172.30.250.1/24) — accepted, documented.
- **Mac asleep or unplugged during an install:** the bus is suspended / not configured → skipped, the other checks
  decide as before (the Mac running the installer is awake anyway; closing the lid during the reboot is the case).

### `share/gadget.sh --rearm-now` (the re-enumeration)

- On the live composite, in setup_main's bring-up order, without any configfs function/link change, KD, flapmon or
  mk20-kbd restart: quiesce HID (router stop file + SIGUSR1: mk20-kbd closes every hidg0 fd and keeps running; never
  an fd across a UDC change), stop adbd (closing ep0 unbinds a bound FunctionFS gadget), unbind, start adbd
  (descriptors before bind), let adbd bind the UDC (stock adbd does) or bind it once ep1 exists, then **the ECM
  netdev**: wait for `/sys/class/net/usb0`; unchanged ifindex + address + MTU 478 + `usb0:1` alias = kept as it
  is; otherwise (the re-bind re-registered it bare: no address, MTU 1500 — losing the sunxi_udc ≤ 512 B PIO
  workaround — new ifindex) setup_main's lines again: `ifconfig usb0 mtu 478`, `ifconfig usb0 $ECM_IP netmask
  $ECM_MASK up`, and `ifconfig usb0:1 $LL_IP netmask 255.255.0.0 up` if setup had the alias (rc2: its record
  `/tmp/mk20/usb0.setup`; `MK20_LL_IP` in gadget.env and the GENV whitelist), verified; a re-created usb0 (or a missing bridge listener) restarts the bridge through its own
  loop (SIGTERM; the loop starts a new one after 1 s; both listeners verified — the old ones were SO_BINDTODEVICE on
  the old ifindex; mk20-kbd reopens its pty on EIO) and asks `mk20d web` to re-bind mk20-agent (also
  SO_BINDTODEVICE usb0; `/tmp/mk20/web.rebind`, not counted as a crash); wait for hidg0, remove the stop file
  (mk20-kbd reopens hidg0, all-keys-up first). A mk20-kbd still holding hidg0 after ~3 s = abort before any USB
  change (rc 1), never a kill. Under the transition mutex (one try) for the whole run, owner-local bounded child
  (90 s) like setup/rollback. Result line in `/tmp/mk20/rearm.last` (`rc=N up=… <what>`).
- rc 0 re-enumerated · 1 aborted before any USB change (or the owner timed out with the UDC bound and adbd up) ·
  2 USB not restored (UDC not bound again / never unbound, usb0 not configurable, bridge listeners not back) →
  `cmd-reboot` (the watcher's WDT hardreset; `mk20-no-composite` is not set) · 3 stuck (unkillable child) →
  escalated like setup/rollback · 4 busy · 5 stale generation · 6 not applicable (no release composite,
  `FLAP_DROPS` < 4, developer router mode, no adbd).
- **Refused with nothing touched (rc 4)** while the transition mutex is held (setup/rollback), a watcher job /
  `cmd-rollback` / `cmd-reboot` is queued, the loader install or state lock is held, **adbd has any child** (an
  `adb shell` or any adb command), **adbd holding a file open in an install inbox (`/data/mk20/incoming`,
  `/tmp/mk20/incoming`)** (an `adb push`, which has no adbd child; killing adbd would cut it; rc1/rc2 used a 120 s
  mtime window, see rc3) or the loader's staging exists (rc2: `releases/.staging.tar`, `.incoming-*`), a teardown
  marker exists (`gadget.busy`, flapmon/kbd/router stop files), the host is not configured, or flapmon logged a
  link drop within FLAP_WINDOW + 5 s (rc3: this boot's tmpfs record). A permanently present adbd child would keep it refused: logged once per
  episode, the watchdog keeps observing and the gate does not judge.
- **flapmon:** one re-enumeration is 1–2 configured→other transitions (the unbind; a later self-re-bind of adbd if
  it does one). Refused while flapmon's window holds any drop and with `FLAP_DROPS` < 4, so a re-arm never trips
  its rollback (default 5 drops in 60 s); a host that genuinely flaps ≥ 3 more times in that minute is rolled
  back as before.
- Operator / first hardware test: RECOVERY.md "Manual ECM re-arm" (queued to the watcher: the device BusyBox has no
  `setsid`, and adbd kills an adb shell's process group, which `--rearm-now` would hit when it restarts adbd).

### `bin/ttygs_tcp_bridge`

- A run-time `--stall-file` write failure was silent (the soak's `bridge_stalls` then froze unnoticed). Now one
  line per stall streak: `stall file PATH write failed: <errno> (count N not recorded; logged once per streak)`;
  the file keeps the last good count; errno survives the cleanup `unlink`. Stall handling unchanged.

### `bin/mk20-kbd`

- The documented residual (kbd/UI.md, "1.6.1 candidate"): a host key (owner 1) whose GD32 up is lost and that no
  same-page request bounded had no time bound. Now `HOST_KEY_MAX_MS` = **60 s from its press**, ended like the
  other lost key-up guards: the host up is sent, the key is inert (cut latch) until the GD32 reports its up, log
  `mk20-kbd: host key held N s: released (lost key-up guard)`. Repeated GD32 downs never renew it; a same-page
  bound that is due first ends it first (its own line); a queued up is never repeated. No Mac gesture needs longer:
  mk20deck's long press (slot pick-up, MODE → L4) fires once at 600 ms and nothing happens later in a hold; PTT and
  ⌫/⏎/Esc are HID owners with their own caps (120 s / 10 min, 10 s). Accepted: a same-page request more than 50 s
  into a hold loses part of its 10 s window to the 60 s press cap (kbd/UI.md).

### 1.7.0-rc1: Kimi K3 1.6.2-rc1 review fixes (NO-GO)

Review record: the coordinator's relay of Kimi K3 on `release/1.6.1..feature/fw-1.6.2`.
- **BLOCKER (gadget.sh rearm, mk20d gate/watchdog):** the re-arm only did the UDC unbind/bind and never redid
  setup_main's `ifconfig usb0 mtu 478` / ECM address / `usb0:1` alias; a re-bind that re-registers usb0 leaves it
  bare (no address, MTU 1500), the bridge listeners on the vanished ifindex, and — because the Mac's ARPs for .2
  still arrive — RX growth made the gate's second probe PASS and the watchdog record a recovery. Fixed: usb0
  re-configured and verified, bridge restarted through its loop and verified on both addresses, the web re-bound;
  recovery and the gate's post-re-arm PASS require an answer from the host; a soft path that does not help ends in
  one rate-limited `cmd-reboot`. Tests drive a fake kernel that unregisters usb0 on unbind and registers it bare on
  bind (fake sysfs + ifconfig + bridge loop).
- **MAJOR (gate):** a re-arm refused for the whole 90 s wait (rc 4) and unreadable counters were treated as a dead
  link (FAIL → rollback of a good release): now WARN, not judged. **MAJOR (FAT flag):** under `mk20-no-ecm-rearm`
  the gate FAILed a stall (rc 7): now WARN, not judged (observe only, as documented).
- **MINORs:** the gate's re-arm is deferred while the keyboard is in use, and keyboard use includes knob events;
  `MK20_ECM_TICK_S` is a strict integer 1–60 (`1.2.3` used to pass, `sleep` failed and the loop spun) with a 1 s
  floor; adb pushes are protected (refused while adbd has any child or an install inbox changed within 120 s, see
  above); the kbd cap truncating a late same-page `keep_until` is accepted and documented.
- Accepted residual (as documented): a spurious gate FAIL needs a host without mk20deck and without the INSTALL §5
  address.

### Tests

- `tests/test_ecm_watchdog.py` (BusyBox 1.27.2, fake `/sys`, `/proc/net/dev`, `/proc/net/arp`; `tests/shims/ping`
  never sends anything: it moves the fake counters and answers only when told to — fancy and non-fancy BusyBox
  forms, RX-only "frames arrive, nobody answers"): watchdog — RX flowing never probes, an idle host answers the
  probe, probe targets, non-fancy ping, the incident stall → one re-arm → answer → recovery with
  health/selftest/log lines, **RX growth without an answer is no recovery**, a new bridge client is an answer,
  **soft path exhausted → one watcher reboot, not again on the next dead boot, reset by a proven link**, never on
  probation, re-arms off after 2 ECM reboots in a row, rolling hour, never without an awake host (6 cases) and a
  fresh window after it returns, host not reading, deferral while typing (keys / knobs), FAT flag, rc accounting
  (incl. gadget.sh's rc-2 reboot counted), counter reset, state-file whitelist, strict tick period (10 cases),
  health defaults, the real `mk20d maint` loop; gate (real `mk20d boot`) — traffic confirms (and gadget.env carries
  `MK20_LL_IP`), one re-arm + an answer confirms, dead link never confirmed, **RX growth after the re-arm without an
  answer fails**, no host never fails (3), probation only, host not reading warns, **no counters / busy for the
  whole wait / rearm rc 1, 5, 6 / FAT flag → not judged**, rc 2/3 fail, the gate's re-arm waits for the keyboard,
  shared budget; `gadget.sh --rearm-now` (extracted block, real locks and bounded supervisor, `set -u`) —
  re-enumeration order with usb0 kept, **a re-created bare usb0 re-configured with setup_main's three lines, the
  bridge restarted on the new ifindex, the web asked to re-bind**, a missing listener restarts the bridge, usb0 not
  configurable / no bridge loop → `cmd-reboot`, binds itself when adbd does not, no adb/HID, mk20-kbd keeping
  hidg0 → abort without a kill, 18 refusals with nothing touched (incl. any adbd child and an adb push into either
  inbox), old drop / old inbox file do not block, the operator queue (its own adb command exempt, a push not),
  5 not-applicable cases, stale generation, bind / unbind that cannot complete → `cmd-reboot` only, owner timeout
  (USB kept / adb lost → reboot path), static checks (setup_main's ifconfig lines, no configfs change, no `reboot`
  anywhere in mk20d/gadget/flapmon/mk20lib, the GENV whitelist, locks closed for gadget.sh/ping, `--rearm` only
  queued). `tests/test_mk20d_agent.py` +1 (the web re-binds once on `web.rebind`, not a crash).
  `tests/test_rollback_guard.py`: the rearm child keeps fd 7 on purpose (owner-local, like setup_main).
- `tests/test_bridge.py` +2: run-time write failure logged once per streak, again on the next streak, silent and
  current once writable (fails on the 1.6.1 bridge); the write result is checked.
- `kbd/tests/tests_onboard_r8.inc` (onboard_test): the 60 s cap for a session key, fb20, a slot cut while another
  key came and went, the PTT-ignored digit and an e-stop host half under a phantom-down storm; holds up to 59 s, the
  600 ms long press, a same-page bound due first, never a second up. 19 checks fail on the 1.6.1 onboard.c.

rc1 host validation only (Docker mk20-build:trixie, BusyBox 1.27.2, nothing else running beside it): `make test` green
on the first run — tests 1389 passed / 1 skipped (1.6.1-rc2 1283: +103 `test_ecm_watchdog.py`, +2 bridge, +1 web
re-bind), agent 398, onboard 16683 (1.6.1-rc2 16545), kbd 4219 ASan + 4219 TSan, safe mode 2 scenarios, e2e OK,
ui 41405, 374 goldens unchanged, sim 38, tools 83 passed / 1 skipped, shellcheck clean, release binaries match
source. The rearm block's 61 tests ran twice in a row (61/61 each). `make mac-test` (macOS, native, on a `git archive`
export of e459085): 823 passed / 567 skipped, no failures (the new BusyBox, gadget and bridge tests are Linux-only).
E-stop latencies unchanged (socket e-stop -> all-up 1.4 ms; stalled host 0.1–0.5 ms, its K2 hold 1008.3 ms; blocked
status worker 0.2 / 1002.1 ms). The new bridge test fails on the 1.6.1 source and 19 r8 checks fail on the 1.6.1
`onboard.c`. `sbin/mk20d` sha256 `e65763500895797f35f7d825b639d0b4e7ef45bc38fa39c9edb4d4958ef96fa7`,
`share/gadget.sh` `4ddeb63f1115d263c6aed6f152b4c1af607e635404abb0aecb7ddedbf13b9974`, `bin/ttygs_tcp_bridge`
`776b17285e416f7b90011fe4404b2ea4630c5b8b8a7d81bb2a9d5d6ae0a27147` (398572 B), `bin/mk20-kbd`
`8f04f7ac1b652a56d00a694ac8f67bd17b652cd515e6c7db482612f0d543c2ca` (595288 B), both static ARM EABI5 (the binaries
are those of 1.6.2-rc1; the review fixes are in the two scripts); the other 10 files identical to 1.6.1-rc2.
`mk20-fw-1.7.0.tar` (rc1, 14 files, 2754560 B; two builds from independent `git archive` exports of e459085 are
byte-identical) sha256 `1321c4853b6533026378c4884c5aa5922b94e50ade59cceece0f855cc4f7f6dc` (`~/worktrees/rel-1.7.0-rc1/`).
**No device acceptance claimed.** On hardware, before trusting the watchdog: the manual `--rearm-now` run from
RECOVERY.md once (`rearm.last` rc=0, `REARM OK … usb0=kept|reconfigured`, usb0 MTU 478 + both addresses, both
bridge listeners and :8780, mk20deck reconnects, HID types, flapmon not tripped), then the install's gate line in
mk20d.log (`PASS ecm host traffic …`) and `usb.ecm_state` `ok` in health.

## 1.6.1-rc2: host link self-healing (pty input stall, incident 2026-10-23)

### 1.6.1-rc2: Kimi K3 rc1 (GO) MINORs

Build id **rc2**: `sh tools/mk20-release-build.sh firmware out rc2`; VERSION stays 1.6.1 (rc1 never installed).
Review record: `deck/reviews/1.6.1/fw161-rc1-kimi-k3.md`. Changed against rc1: `bin/ttygs_tcp_bridge` and
`share/gadget.sh`; `bin/mk20-kbd` is identical to rc1 (comment only).

- **Replacing client (MINOR 1).** A new client drops the previous session's pending bytes (logged on its `client
  connected` line), so a stall can no longer close the new client for the old client's stuck bytes; the stall timer
  starts with the new client's own bytes.
- **Stall log per streak (MINOR 2).** First stall in full; stalls within 60 s of the previous one are silent, with
  `pty input still stalling: S stalls in T s (#k)` every 10 min and `pty input stall streak over: …` after 60 s
  without one: a persistent stall with a Mac reconnecting every few seconds no longer writes a line every ~2 s.
  mk20d maint already caps `/tmp/mk20/bridge.log` (256 KiB → last 64 KiB every 5 min, `test_maint_rotates_and_trims`).
- **Accepted race (MINOR 3)**, documented in `kbd.c` and kbd/UI.md: host bytes arriving between mk20-kbd's
  "not readable" poll and its TCIFLUSH are dropped; only the first bytes after ≥ 5 s of silence can be hit (the
  Mac's preamble, or an ack miss → reconnect within seconds).
- **Self-healing visible in the soak (MINOR 4).** Bridge `--stall-file /tmp/mk20/bridge.stalls` (total stall count,
  passed by gadget.sh to the release bridge only: a legacy `/data` bridge would exit 2 on the flag); the soak reads
  it in its meminfo call (≤ 400 bytes), marks a non-stalled sample `host_link: "self-healed"` (still ok) when it
  grew or when `host_flushes` grew while connected, and sums `host_link_selfheals`, `bridge_stalls_total`,
  `host_flushes_total` (connected intervals only).
- Tests: `tests/test_bridge.py` +6 (replacing client not closed for the old bytes — fails on rc1; streak log + stall
  file with a short-timer build of the same source; release timer values; unavailable stall file; 2 bad
  `--stall-file` arguments) and the rc1 stall test updated (the replacing client's own bytes stall it);
  `tests/test_mk20d_kbd.py` +1 (gadget.sh flag only with `MK20_REL`); `tools/test_mk20_soak.py` +4.

rc2 host validation only (Docker mk20-build:trixie, BusyBox 1.27.2): `make test` green — tests 1283 passed / 1 skipped
(rc1 1276: +6 bridge, +1 gadget), agent 398, onboard 16545 (TSan build 16545), kbd 4219 ASan + 4219 TSan, safe mode
2 scenarios, e2e OK, ui 41405, 374 goldens unchanged, sim 38, tools 83 passed / 1 skipped (soak +4), shellcheck
clean, release binaries match source. `make mac-test` (macOS, native, on a `git archive` export of the commit): 817
passed / 467 skipped, no failures. E-stop latencies unchanged (socket e-stop -> all-up 1.3 ms; stalled host 0.2–0.5
ms, its K2 hold 1010.3 ms; blocked status worker 0.2 / 1001.4 ms). The new bridge tests were run 3x in a row (29/29
each); the replacing-client test fails on the rc1 source. `bin/ttygs_tcp_bridge` sha256
`0dbb85719d1b3509737467546c6fce34d95ed70e12a2d03067a00352a83c0c1a` (398572 B, static ARM EABI5), `share/gadget.sh`
`59874785bf918c2c8a5b7dddcaa71d3b0efec5a1bc783206974585ab1593ae80`; `bin/mk20-kbd` unchanged from rc1
(`3cda8c67…6c2f`). `mk20-fw-1.6.1.tar` (rc2, 14 files, reproducible: a rebuild from a `git archive` of the commit is
byte-identical) sha256 `4ba75ecf4aa7a1d69eb34f3bb287849a7297fb2164064cf22a5e57af72d077f1`.
**No device acceptance claimed.**

### 1.6.1-rc1

`firmware/VERSION` is **1.6.1** (PATCH: no host-visible protocol change); build id **rc1**:
`sh tools/mk20-release-build.sh firmware out rc1`. fix/pty-stall = release/1.6.0 (8155e84, 1.6.0-rc4) + this work.
`bin/ttygs_tcp_bridge` and `bin/mk20-kbd` changed (rebuilt static armhf); `bin/mk20-router`, `bin/mk20-agent` and
every script are byte-identical. Rules and the safety argument: kbd/UI.md "1.6.1 host link self-healing".

**Incident** (1.6.0-rc4, 2026-10-23 20:01, after 14.5 h; `~/mk20-backup/incident-20261023-2001/NOTES.md`): the
Mac lost its acks ("连续 3 帧无 ACK") and every reconnect timed out for 51 min. The bridge was blocked in
`write(/dev/ptmx, 4096)` (wchan `wait_woken`): the slave's input flip buffer was full while the slave line
discipline was empty (a 2 s read of `/dev/pts/0` got 0 bytes) and mk20-kbd polled it healthily with POLLIN — the
5.4.61 pty input path stopped pushing the flip buffer to n_tty. Blocked there, the bridge read no client (Recv-Q
65562 on both sockets) and served no new one. A one-off `tcflush(slave, TCIFLUSH)` unstuck it at once (bridge
resumed, Mac re-hello OK). The soak stayed "ok" throughout (no host-link liveness check), and mk20deck logged
nothing after "未找到 MK20 CDC 端口" for 50 min.

### `bin/ttygs_tcp_bridge` v4

- The pty master is `O_NONBLOCK`; the blocking `wall()` for client→pty is gone. Client bytes go through one
  bounded 4 KiB pending buffer written on POLLOUT; the client is not read while it holds bytes (TCP backpressure to
  the Mac, never a silent drop).
- Stall: pending bytes with no write progress for `PTY_STALL_MS` = **2 s** → `tcflush(slave, TCIFLUSH)` on the
  slave fd the bridge holds, one `/tmp/mk20/bridge.log` line `pty input stalled N s: flushed (#k); P pending bytes
  dropped, client closed`, pending bytes discarded, the current client closed (the Mac reconnects and re-hellos;
  frames resync on the magic). KD→client keeps being forwarded and new clients are accepted during a stall.
- Unchanged: multi-listen, client replacement, `--tap`, SO_BINDTODEVICE, the link symlink, strict arguments,
  KD→client drop-on-backpressure.

### `bin/mk20-kbd`

- Host silence backstop: host fd open and no host byte for `KBD_HOST_SILENCE_MS` = **5 s** →
  `tcflush(host_fd, TCIFLUSH)` at most once per 5 s while the silence lasts (skipped while the fd is readable),
  `kbd.status` `host_flushes`, one log line per silence streak (`mk20-kbd: host link silent 5 s: input flushed
  (#k)`). Catches input stuck in the flip buffer while the master still has room (invisible to the bridge).
  Harmless when the Mac is not connected (empty input).
- Safety: TCIFLUSH discards only host→device input that was never delivered; by 5 s of host silence the keyboard
  has already passed SUSPECT (3 s: host-owned keys released, option map cleared) and host loss (5 s: fresh hello
  required). Nothing safety-related is host→device (e-stops and HID cuts are local, HID goes to `/dev/hidg0`,
  device→host output is never flushed). One non-blocking ioctl per 5 s at most, after the pass's safety tick, GD32
  input, control socket and HID service; the poll timeout is unchanged.

### Host tools / mk20deck

- `tools/mk20_soak.py`: per sample `kbd_link` (`json_in`, `jpg_ok`, `host`, `host_flushes`) from the kbd status
  already inside the health JSON (no new adb call; every request still ≤ 400 bytes). `bridge_clients` ≥ 1 in two
  consecutive samples with `json_in` not increased (the Mac heartbeats every 1.2 s) → `"host_link": "stalled"`,
  sample not ok; summary `host_link_stalls` / `first_host_link_stall` (reboot / mk20-kbd restart / missing data:
  `unknown`; no client: `idle`). INSTALL.md §6.
- mk20deck `Supervisor`: while it cannot (re)connect, one line at least every 10 min with the elapsed time and the
  last reason (`仍未连接 MK20：已 N 分钟（最近：…）`), counted from the loss; nothing while connected.

### Tests

- `tests/test_bridge.py` +2 on the real binary: an unread slave + a flooding client → master→client still forwarded
  and a new client accepted before the flush, flush 2–5 s after the flood began with the exact log line, current
  client closed, slave input empty, then 256 KiB client→slave and KD→client with a reader, one stall in total; a
  reader that pauses 1.2 s at a time never triggers it (every byte in order). The stall test fails on the 1.6.0
  bridge (master→client not forwarded while it is blocked).
- `kbd/tests/tests_k3.inc` `test_kbd_host_silence` (test_kbd 4191 → 4219 checks, ASan + TSan): threshold, once per
  5 s, log once per streak, readable bytes kept, undelivered input discarded (canonical-mode model), status
  counter, closed / non-tty host fd; fails with the flush removed. `kbd/tests/e2e_run.py` on the real binary:
  heartbeats every 1 s → no flush in 7 s, then one flush in 6.5 s of silence, the log line once, the link answers.
- `tools/test_mk20_soak.py` +4 (status parsing incl. a 700-byte cut, state rules, sample recording without extra
  adb calls, summary/ok accounting over reboot and restart), `tests/test_reconnect_devtest.py` +2 (10 min lines,
  counted from the loss, silent while connected).

rc1 host validation only (Docker mk20-build:trixie, BusyBox 1.27.2): `make test` green — tests 1276 passed / 1 skipped
(rc4 1272: +2 bridge, +2 supervisor), agent 398, onboard 16545 (TSan build 16545), kbd 4219 ASan + 4219 TSan (rc4
4191), safe mode 2 scenarios, e2e OK (incl. the host silence backstop), ui 41405, 374 goldens unchanged, sim 38,
tools 79 passed / 1 skipped (soak +4), shellcheck clean, release binaries match source. `make mac-test` (macOS,
native, on an export of the committed tree): 817 passed / 460 skipped, no failures. E-stop latencies over three e2e
runs (one beside a concurrent mac-test): socket e-stop -> all-up 1.3–1.6 ms; stalled host 0.1–1.2 ms, its K2 hold
1005.4–1015.6 ms; blocked status worker 0.1–0.9 / 1001.1–1021.9 ms (`--no-host`: the backstop is not even reached).
`bin/ttygs_tcp_bridge` sha256 `6fa0fd8ea1b16386000f9ee48c71ecbd9a85089caa1fd01bb0a38fc303dcf24f` (398572 B),
`bin/mk20-kbd` sha256 `3cda8c67477188e4a62ecf0fe9d3a5fa0fc15b2d0de657c164232570ebde6c2f` (595288 B), both static
ARM EABI5. `mk20-fw-1.6.1.tar` (rc1, 14 files, reproducible: a rebuild from a `git archive` of the commit gives the
same sha) sha256 `36c5b3987b12c8de2887b16245b06f9325c8e6bd7d04f8eb637532aa6e37ee02`.
**No device acceptance claimed** (a real stall cannot be provoked on demand; on hardware check that the bridge
log stays free of `pty input stalled` and `host_flushes` stays 0 while the Mac is connected, and the soak's
`host_link`).

## 1.6.0-rc4: host protocol additions (same-page ui.page, hello page, generation reset, 会话安排 key)

`firmware/VERSION` is **1.6.0** (MINOR: host-visible protocol additions); build id **rc4** (rc1–rc3 below,
superseded): `sh tools/mk20-release-build.sh firmware out rc4`. feature/fw-1.6.0 = release/1.5.1 (5f2ed0d, incl. fix/page-sync)
+ fix/macos-test-gate (61cca87) + this work. Only `bin/mk20-kbd` changed in the release (rebuilt static armhf); the
other binaries are byte-identical. Rules, compatibility and the safety arguments: kbd/UI.md "1.6.0 host protocol
additions". Both directions stay compatible: an older mk20deck ignores the new hello field and the new event (and
gets the K2 tap of a page changed while it was away ~2 s earlier, at the hello instead of at ONLINE); a newer
mk20deck keeps its 1.5.1 behaviour when the hello carries no `page`.

### 1.6.0-rc4: Kimi K3 rc3 delta (GO) MINORs

Build id **rc4**: `sh tools/mk20-release-build.sh firmware out rc4`; VERSION stays 1.6.0 (rc1–rc3 never installed).
Only `bin/mk20-kbd` changed in the release.

- **Keep pair cleared wherever an owner ends.** `onboard_host_flush` without a host cleared owner 1 but left the
  same-page bound; the leftover refused to re-arm and, after a reconnect, cut a new press of the same key early
  (and latched it). The flush (no host and flushed ups) and every new press now clear it; release_key / boundary /
  stop / loss already did. The cut latch deliberately survives these paths: it records that the GD32 has not
  reported the key's up since a guard cut, which only that up ends.
- **Repeated down of a held host key** (pre-existing): on live pages it re-forwarded `"pressed":true` each time,
  without bound under a phantom-down storm. It is now the same press (swallowed like owners ≥ 5), the next up
  releases; a genuine long press is unchanged (one down + one up; the Mac's 600 ms pick-up verified on the real
  binary).
- **Log**: the same-page bound line prints the real held time ("key held 60 s across a same-page ui.page").
- Tests: `tests_onboard_r7.inc` + keep pair after a host flush (new press not cut at the old bound, re-arming
  works), host-key phantom storm (one press, owner kept, up releases), long press = one down + one up, PTT-ignored
  digit and Mac mode key, keep-cut log text; `test_page_sync_e2e.py` + real-binary long press with phantom downs
  (one down, one up, Mac pick-up). Each new C test fails with its fix reverted. `kbd/tests/e2e_run.py` now
  releases the fb1 it pressed early (left held, its later option-mode press was only that press's release).

rc4 host validation only (Docker mk20-build:trixie, BusyBox 1.27.2): `make test` green — tests 1272 passed / 1 skipped
(+1), agent 398, onboard 16545 (rc3 16497; TSan build 16545), kbd 4191 ASan + 4191 TSan, safe mode 2 scenarios, e2e OK,
ui 41405, 374 goldens unchanged, sim 38, tools 64 passed / 1 skipped, shellcheck clean, release binaries match source.
`make mac-test` (macOS, native): 815 passed / 458 skipped, no failures. E-stop latencies unchanged (socket e-stop ->
all-up 1.3–3.4 ms; stalled host 0.1–3.3 ms; K2 hold 1005.7–1008.3 ms; blocked status worker 0.5–1.1 / 1008.6–1010.2
ms). `bin/mk20-kbd` sha256 `041dc48eee6a0a49cb60c036ad01127af63ed68435a5b453b338fc8b4cd460d6` (595288 B, static ARM
EABI5). `mk20-fw-1.6.0.tar` (rc4, 14 files, reproducible) sha256
`a40b997d62724932c3edce219fb6a24cf679c3bd1f1c1fa5aabfa33d5869ecfe`. **No device acceptance claimed.**

### 1.6.0-rc3: Kimi K3 delta review MINORs (rc2: GO)

Build id **rc3**: `sh tools/mk20-release-build.sh firmware out rc3`; VERSION stays 1.6.0 (rc1/rc2 never installed).
Only `bin/mk20-kbd` changed in the release.

- **Absolute caps per press** (rc2 renewed them on every repeated GD32 down, so a faulty GD32 emitting phantom
  downs could keep Right Option down indefinitely): the PTT's 120 s window is still renewed by a repeated down,
  but a PTT press ends 10 min after its original press (`HID_PTT_MAX_MS`); a host key / L4 回 L1 kept across a
  same-page request renews its 10 s window on a repeated down, never past 60 s after the first request
  (`KEEP_MAX_MS`). After any lost key-up guard cut (these caps, the 10 s ⌫/⏎/Esc/digit cut) the key is inert until
  the GD32 reports its up (`cut_latch`): otherwise the next phantom down would press it again at once. fb20 as the
  e-stop is checked before the latch. Residual behaviour documented in kbd/UI.md.
- **5 s pause resync releases owners** (pre-existing in 1.5.1): the post-e-stop pause's 5 s resync cleared
  `pressed` but not the press-time owners, so a host key whose GD32 up was lost in the pause stayed pressed on the
  Mac. It now also releases them (after the stop only owner 1 is left: host keys and the host halves of forwarded
  keys / digits; the test asserts owners 2/4/5–7 are already clear, via the new `onboard_key_owner()`).
- Tests (`tests_onboard_r7.inc`): phantom-down storm on the PTT (live page and the guard path after a same-page
  request: released at 10 min, not 120 s, then inert through the storm until its up), on a kept host key and on
  L4 回 L1 (ended at 60 s, inert afterwards), ⌫ storm (one 10 s Backspace, no re-press), fb20 e-stop never
  latched away, pause resync with PTT + forwarded ⌫ + digit + host key held and every GD32 up lost (owners asserted
  after the stop, 3 host ups at 5 s, all owners clear, keys work after the pause), 回 L1 cancelled by the stop.
  Each fails with its fix reverted.

rc3 host validation only (Docker mk20-build:trixie, BusyBox 1.27.2): `make test` green — tests 1271 passed / 1 skipped,
agent 398, onboard 16497 (rc2 16275; TSan build 16497), kbd 4191 ASan + 4191 TSan, safe mode 2 scenarios, e2e OK, ui
41405, 374 goldens unchanged, sim 38, tools 64 passed / 1 skipped, shellcheck clean, release binaries match source.
`make mac-test` (macOS, native): 815 passed / 457 skipped, no failures. E-stop latencies unchanged (socket e-stop ->
all-up 1.6 ms; stalled host 0.2–0.4 ms; K2 hold 1009.3 ms; blocked status worker 1.0 / 1005.2 ms). `bin/mk20-kbd`
sha256 `11fb1cce8c051200ef629d8c93ffed718b533a8b1911941aea092f528e07a406` (595288 B, static ARM EABI5).
`mk20-fw-1.6.0.tar` (rc3, 14 files, reproducible) sha256 `068479e3b39746ae9b1617164db4737f544e45c190a438908a4cbe68c52ba1d4`.
**No device acceptance claimed.**

### 1.6.0-rc2: Kimi K3 review fixes (rc1: NO-GO, never installed)

Build id **rc2**: `sh tools/mk20-release-build.sh firmware out rc2`; `firmware/VERSION` stays **1.6.0** (rc1 was
never installed). release/1.5.1 f8de052 (arrange keep-alive cap, graceful close) merged in. Only `bin/mk20-kbd`
changed in the release.

- **MAJOR 1 — PTT lost-up bound.** With same-page `ui.page` keeping a held PTT, a lost GD32 up left Right Option
  down indefinitely while ONLINE. A 10 s cut is wrong for dictation and the GD32 cannot be asked for the key state
  (its link has exactly one allowed outbound frame shape), so every PTT hold now ends after **120 s**
  (`HID_PTT_HOLD_MS`, logged as a lost key-up guard; press again to continue); a repeated GD32 down of the held PTT
  confirms it pressed and restarts the window. A host key / L4 回 L1 press kept across a same-page request (no
  bound of its own) ends 10 s after the first such request unless a new GD32 down re-confirms it (host up; the
  pending 回 L1 is cancelled, never run late). Owners ≥ 5 keep their 10 s cut.
- **MAJOR 2 — post-e-stop pause.** A repeated GD32 down in the pause cleared the press-time owner of a key held
  from before the stop, so its later up sent nothing (host key / forwarded ⌫⏎Esc stuck on the Mac). The pause now
  only swallows the down and keeps the owner. **Pre-existing in 1.5.1** (reproduced on 5f2ed0d: 0 host ups); it
  needs a repeated GD32 down (lost or duplicated edge) of a key held across an e-stop.
- **MINOR 1 — L4 回 L1 in the pause.** Its up navigated to L1 and sent the K2 tap during the pause. Now the
  e-stop cancels a pending 回 L1 and its release is inert while suspended. **Pre-existing in 1.5.1** (reproduced:
  route L1 + K2 tap).
- **MINOR 2 — generation fencing (mk20deck).** `ui_page` / `ui_hid_keys` take the `ui_epoch` the caller decided
  under; a mismatch inside the generation lock drops the request ("stale", nothing written) instead of renumbering
  it after the hello; the daemon re-decides under the new epoch.
- **MINOR 3 — K2 during PROBING (mk20deck).** A keyboard K2 tap newer than a known page belief is applied
  absolutely: the Mac goes to the keyboard's page (the other page than the belief) instead of toggling its own,
  so a K2 on the keyboard while PROBING (tapped at ONLINE) is followed and never pulled back. Unknown belief / a
  tap already contained in the last ack: the relative toggle as before.
- **MINOR 4 — arrange access log (mk20deck, webui.py).** `log_message` cuts every query string (`/auth?t=…` ->
  `/auth?…`) before the DEBUG line, also for malformed request lines quoted by `send_error`.
- Tests: `tests_onboard_r7.inc` + pause owners (with / without a same-page request), L4 回 L1 in the pause,
  lost-up bounds (host key 10 s after the request, PTT 120 s cap, re-confirmation, repeated requests, 回 L1
  cancelled) and a randomized release invariant (120 runs x 45 steps over PTT / ⌫⏎Esc / host keys / MODE /
  回 L1, same-page and real `ui.page`, K2, e-stops, option maps, frames, time: HID all-up and every key released
  on the host at the end); `test_page_sync.py` +5 (fencing in the link and the daemon, K2 while PROBING with the
  Mac on the other / the same page, target rules); `test_ui_open_arrange.py` +2 (redaction). Each new test was
  checked to fail with its fix reverted.

rc2 host validation only (Docker mk20-build:trixie, BusyBox 1.27.2): `make test` green — tests 1271 passed / 1 skipped
(+22 from the release/1.5.1 merge, +7 new), agent 398, onboard 16275 (rc1 13383; TSan build 16275), kbd 4191 ASan +
4191 TSan, safe mode 2 scenarios, e2e OK, ui 41405, 374 goldens unchanged, sim 38, tools 64 passed / 1 skipped,
shellcheck clean, release binaries match source. `make mac-test` (macOS, native): 815 passed / 457 skipped, no
failures. E-stop latencies unchanged (socket e-stop -> all-up 1.7 ms; stalled host 0.2–3.6 ms; K2 hold 1005.5 ms;
blocked status worker 0.6 / 1009.8 ms). `bin/mk20-kbd` sha256
`a18d7d81ef054eda27b2618dde140f56af9bdbb20fe1dd0534a0ca7f76a37d34` (595288 B, static ARM EABI5).
`mk20-fw-1.6.0.tar` (rc2, 14 files, reproducible) sha256 `f9ce9152a2f5c740e47661e789db4d4b5f74eb7cd1abfaadf3f62ab7e396030a`.
**No device acceptance claimed.**

### `bin/mk20-kbd`

- **Same-page `ui.page` releases nothing.** For the route the keyboard already shows it only clears the option map
  and invalidates content (`invalidate()`: fresh full frame required, grace window, stale receive barrier, epoch,
  staged tile, redraw); held PTT / ⌫ / ⏎ / Esc / option digit / host keys keep their press-time owners and end on
  their physical release, the 10 s cut, SUSPECT, every e-stop or a real boundary. A repeated GD32 down of such a key
  while the page waits for its frame is the same press. A route change keeps the full boundary.
- **`ui.hello` names the route**: `"page":"L1"|"L4"|"none"` (none = HOME/LS/L5/agent) before the generations; a
  route changed while no host was ONLINE is announced with the K2 tap before the reply (was: at ONLINE), so the
  host's tap mirror starts at the reported page. `ui.page`'s reply already names the page.
- **A fresh `ui.hello` resets `generation` and `hid_generation` to 0** (reported as before; strictly increasing
  within a session). Safe: nothing written before a hello can be accepted after it (ordered stream; the hello's
  loss boundary makes everything already received/queued behind it stale; `ui.page` / non-empty maps need ONLINE
  = ≥ 3 heartbeats over ≥ 2 s after it), and mk20deck serializes hellos against its numbered requests.
- **LS fb17 会话安排** (font: one new glyph 排, generator check reproduces the old file): sends exactly
  `{"method":"ui.openArrange"}` (frame id 0xFFFFFFFF, ONLINE only, own 3 s limiter, like `ui.openSettings`), toast
  "已请求 Mac 打开会话安排" / "Mac 端未运行：无法打开会话安排"; LS stays. Live L1/L4 fb17 is still Backspace.
- Tests: new `kbd/tests/tests_onboard_r7.inc` (same-page L1 and L4 with PTT + ⌫ + Esc + host key + MODE held: no
  HID report, no host up, page invalidated + grace, repeated downs, ordered releases; option digit + forwarded fb17
  held: map cleared, ups on release; different page = full boundary in both cases; 10 s cut / socket e-stop / K2
  hold / SUSPECT / hello still release; same-page during the e-stop pause; hello page on HOME/LS/L5/L1/L4, tap
  before the reply for an offline change, none at ONLINE, PROBING change still tapped at ONLINE; generation reset
  incl. exhaustion recovery and stale `ui.hidKeys` / `ui.page` queued behind a hello; openArrange gate, payload, id,
  limiter vs openSettings, no other key, live L1 fb17 = Backspace, L4 → K1 → LS). Adjusted: r6 clear-path mode 4
  (same-page ui.page now keeps held keys), r6 generations, r5 PROBING case (hello-time tap), ui_test LS fb17. e2e on
  the real binary: hello `page`/`generation`, same-page `ui.page` with PTT + DELETE held = no report, released on
  the physical up. Goldens: 330 → 374 (LS fb17 + canvas changed in the 5 LS scenes; new scenes ls-arrange-opened /
  ls-arrange-offline).

### mk20deck

- **Page sync** (daemon): the hello's `page` seeds the page belief (rx of the hello ack: the hello-time K2 tap is
  contained in it; input queued before the hello reply is applied first), so after a reconnect nothing is sent when
  the Mac page matches; at ONLINE the step forces the full frame the keyboard needs. `"none"` / no field (older
  firmware): unknown as before (first request at ONLINE, retries).
- **Generations** (link): one lock (`_gen_lock`) across `ui.hello` and every `ui.page` / `ui.hidKeys`
  (allocate + write + ack), and the hello's values are taken as they are (also lower than ours): exhaustion
  recovers at the next hello on 1.6.0 firmware (one log line); older firmware re-reports its persistent counters
  (unchanged behaviour, now exactly the keyboard's values).
- **`ui.openArrange`**: whitelisted with `ui.openSettings` in `protocol.DEVICE_UI_EVENTS` (exact payload only);
  the daemon mints a one-time link FILE through the in-process `ArrangeServer.mint_file()` (0600 in the 0700 run
  dir) and runs `open <file>` in a thread — the token never appears in an argv, a log line or on the cable; at
  most once per 3 s; page off (`arrange_port = 0`), not running or mint failure → keyboard toast (5 s). No new
  capability is needed (the keyboard initiates).
- `MockDevice(fw160=True)` models the 1.6.0 hello (page, reset, hello-time tap; `toggle_away()`, `ui_event()`).
- Tests: `tests/test_page_sync.py` +11 (hello seeding, Mac page differs, change while away, seed/tap order, own
  page, legacy hello, ONLINE frame without request, exhaustion recovery, authoritative re-seed on older firmware,
  hello waits for a request in flight), `tests/test_page_sync_e2e.py` +3 on the real binary (reconnect on L1 and L4
  sends no ui.page; keyboard K2 while the Mac was away followed without a request; LS 会话安排 reaches the Mac),
  new `tests/test_ui_open_arrange.py` (whitelist, link file 0600/0700 + redeem, macOS argv = path only, no token in
  logs, rate limit vs openSettings, payload dropped, off / not running / mint failure toasts, Supervisor wiring).

rc1 host validation only (Docker mk20-build:trixie, BusyBox 1.27.2): `make test` green — tests 1242 passed / 1 skipped
(release/1.5.1 + gate: 1219 collected; +24), agent 398, onboard 13383 (was 12957; TSan build 13383), kbd 4191 ASan +
4191 TSan, safe mode 2 scenarios, e2e OK (incl. hello page, same-page `ui.page` with PTT + DELETE held), ui 41405 (was
41396), 374 goldens (was 330: 10 LS files changed, 44 new), sim 38, tools 64 passed / 1 skipped, shellcheck clean,
release binaries match source. `make mac-test` (macOS, native): 785 passed / 457 skipped / 1 failed — only the known
pre-existing `test_arrange_hardening.py::test_connection_cap_answers_503_beyond_the_limit`. E-stop latencies unchanged
(socket e-stop -> all-up 1.3–1.6 ms; stalled host 0.0–1.0 ms; K2 hold 1001.2–1009.6 ms; blocked status worker
0.1–0.6 / 1000.2–1013.2 ms). `bin/mk20-kbd` sha256 `43004be69951d0f0f7bb467fb715e8c14e8bebba2d9b10681724a88796c3d109`
(595288 B, static ARM EABI5). `mk20-fw-1.6.0.tar` (rc1, 14 files, reproducible) sha256
`314ee2635835cff14c2c92fae6ba96107b8e40b8d43b6e42876c3e291e7ea436`.
**No device acceptance claimed** (same-page request with a held PTT on hardware, reconnect without a same-page
request, 会话安排 from the keyboard).

## 1.5.1-rc1: L1 option mode (answer an agent's choice with 1–5)

`firmware/VERSION` is **1.5.1**; build id **rc1** (release/1.5.1 = 1.5.0 slot-move 7c599a3 + feature/option-mode). User decision 2026-10-22:
tapping a needs_input / needs_approval session on L1 brings it to the front and turns row 1 into its numbered
choice (AskUserQuestion labels, the fixed permission menu 是 / 是，且不再问 / 否, or bare digits 1–5; rows 2–3
dark, row 4 unchanged); the keys type the digit on the Mac. PRD delta: deck/docs/PRD-1.5-L1-LAYOUT.md §3.2.

### `bin/mk20-kbd`: host cmd101 `ui.hidKeys`

- `{"method":"ui.hidKeys","generation":G,"keys":{"1":"1",...}}`: up to 9 of fb1..fb15 -> digits "1".."9"
  (HID 0x1E..0x26), strict validation (one nested flat object via new `ctl_json_*1` helpers; kbd.sock stays
  flat-only), strictly increasing own generation (`hid_generation` in the `ui.hello` result, capability
  `ui.hidKeys`), `{}` clears. Non-empty maps only while negotiated + ONLINE + on L1 + not in the post-e-stop
  pause; stale tty requests refused like `ui.page`. Cleared on every page boundary, `ui.hello`, host loss,
  SUSPECT, every e-stop, and 30 s after the last request (mk20deck refreshes every 10 s).
- A mapped digit on the live L1 page uses the fb17–19 gate (`HOST_HID_KEYS`, lease preemption) but is
  momentary (integrator decision): the press types it (down + up, two reports, no macOS auto-repeat), the release
  sends no HID; ownership is still tracked (`key_owner` 7: repeated down = same press, 10 s guard, every release
  path). While the Mac PTT is held a digit is ignored (nothing typed). The press is forwarded as
  `keyStateChanged` with `"hid":usage` (or `"ignored":"ptt"`; fb17–19 with `"hid"` while a map is active, still
  held keys); the release is an ordinary up. Rules and the safety argument: kbd/UI.md "L1 option mode".
- Tests: new `kbd/tests/tests_onboard_r6.inc` (accept/reject/validate incl. 21 malformed maps and 12 malformed
  requests, generations across hello/loss/ui.page, momentary digit reports per key and row, a held digit =
  one digit, forwarding marks, PTT held = digit ignored + fb17–19 still combine with RAlt, `--ptt` on a mapped
  key, `--no-hid`, lease preemption, gate L4/HOME/LS/L5/SUSPECT/offline/PROBING/grace/e-stop pause, 14 clear
  paths with held Backspace + digit, 30 s expiry and refresh, 10 s guard sends the host up, host EOF mid-press),
  parser checks in `tests_ctl.inc`, e2e on the real binary (hello capability, digits 1–3 typed once each, 1.5 s
  hold = one digit, Escape, PTT held = ignored, marked forwarding, unmapped slot, stale generation, clear).

### mk20deck + hook

- Controller option mode (enter only for hook-reported prompts, i.e. tmux Claude/codex — never Desktop/herdr —
  after a confirmed focus on L1; exits: state leaves needs_*, Esc, K1, a blank key, page/mode switch, 30 s
  without input, 3 s after a choice, reconnect), amber choice keys + dark blanks, sub-screen line, toasts
  ("已选 k", "先松开按住说话"); legacy firmware -> "固件不支持数字键，请在 Mac 上按数字".
- Daemon sends `ui.hidKeys` before the frame that shows the choice, refreshes every 10 s, clears on exit,
  retries a refused map every 1 s, resends after a re-hello; `DeviceLink.ui_hid_keys()`, `MockDevice(hid_keys=)`.
- Hook record: `options` (AskUserQuestion labels, <= 5 x 24, control/format characters stripped) or
  `prompt_kind: "permission"`, gone with the next record; never the question text. Re-run the installers
  (the installed hook is a copy).

Host validation only (Docker mk20-build:trixie, BusyBox 1.27.2): `make test` green — tests 1000 passed / 1
skipped (was 943), agent 398, onboard 12957 (was 11928; TSan build 12957), kbd 4191 ASan + 4191 TSan (was 4173),
safe mode 2 scenarios, e2e OK (incl. option mode), ui 41396, 330 goldens unchanged, sim 38 (was 37), tools 64
passed / 1 skipped, shellcheck clean, release binaries match source. E-stop latencies unchanged (socket e-stop ->
all-up 1.7 ms; stalled host 0.1 ms; K2 hold 1007.5 ms; blocked status worker 0.7 / 1008.9 ms). `bin/mk20-kbd`
sha256 `690909f0b98e02c7e019560125f3d6db64b447ce344c65ad6f1a3be7b0a80777` (595288 B, static ARM EABI5).
**No device acceptance claimed** (digits in Claude Code / codex menus, codex approval UI).

## 1.5.0-rc1 (user's L1 layout: 15 sessions + voice/delete/send/Esc row)

`firmware/VERSION` is **1.5.0**; build id **rc1**. release/1.5.0 = 1.4.3-rc4 + feature/l1v2-hid (device HID keys)
+ feature/l1-layout-v2 (mk20deck: 15 session slots, single tap = select + bring to front, L4 row1 = DESK HERD TMUX
＋新会话 回L1) + L4 local Back moved from fb14 to fb5 (fb14 is session S14 now). PRD delta:
deck/docs/PRD-1.5-L1-LAYOUT.md.

### L1/L4 bottom row = Mac keyboard keys (`bin/mk20-kbd`)

User decision 2026-10-21 (new L1 layout; replaces the PRD rule "non-PTT keys never produce HID" for exactly these
keys): on a live L1/L4 page fb17 DELETE = USB HID Backspace (0x2A), fb18 SEND/OK = Return (0x28), fb19 NO =
Escape (0x29). fb16 PTT (Right Option, `--ptt`) and fb20 MODE are unchanged; rows 1–3 become 15 session slots
rendered by the Mac (no device change); L4 keeps the same bottom row. One table, `HOST_HID_KEYS` in
`kbd/onboard.c`; key table and rules in kbd/UI.md.

- Same gate as the Mac PTT: L1/L4, host ONLINE with a fresh frame (never in the grace window), not
  PROBING/SUSPECT/offline, not in the post-e-stop pause, GD32 keycode 0; the press preempts an agent lease first
  (`ctl_human_input`). Down on press, up on release, press-time ownership (`key_owner` 5); never forwarded as
  `keyStateChanged`; HOME/LS/L5/agent pages and the offline placeholder keep fb17–19's on-board meaning.
- `hid.c` unchanged (boot report via `hid_key`): RAlt + keys combine, releasing one keeps the others. Ups/all-up
  on every e-stop (fb20/K2/socket), page boundary (K2, `ui.page`, K1 hold, ctl page, `ui.hello`), host
  loss/SUSPECT, hidg0 replacement (all-up, then the still-held state), stop file/SIGUSR1 and exit.
- Added safety: a hold of fb17–19 longer than 10 s is cut (GD32 reports are edge-triggered without state
  resync, so a lost key-up would otherwise auto-repeat Backspace/Return on the Mac). The PTT is not cut.
- Tests: new `kbd/tests/tests_onboard_r5.inc` (reports per key on L1 and L4, hold = one down/one up, repeated
  down, 10 s cut, not forwarded, inert on HOME/LS/L5/agent and when PROBING/SUSPECT/offline/grace/e-stop pause,
  release on 9 paths, real-FIFO hidg0 replacement/stop file/exit, PTT+keys combinations, `--ptt` elsewhere,
  `--no-hid`, agent lease preempted); e2e on the real binary (RAlt + Backspace/Return/Escape reports, 1.5 s hold
  = one report, no `keyStateChanged`, Escape+PTT held at SIGTERM -> all-up). Older tests that used fb17/fb19 as
  ordinary host keys on a live L1 now use fb12 or expect no host event.

Host validation only (Docker mk20-build:trixie, BusyBox 1.27.2): `make test` green — tests 877 passed / 1
skipped, agent 398, onboard 11927 (was 11348), kbd 4173 ASan + 4173 TSan, safe mode 2 scenarios, e2e OK, ui
41396, 330 goldens unchanged, sim 35, tools 64 passed / 1 skipped, shellcheck clean, release binaries match
source. E-stop latencies unchanged (9 e2e runs, base 8: socket e-stop -> all-up 1.1–1.3 ms, base 1.2–1.4;
stalled-host socket/reserved key/human/K2 triple 0.0–0.2 ms, base 0.1–0.7; K2 hold 1000.8–1005.0 ms, base
1005.7–1013.2; blocked status worker socket 0.1–0.3 ms with one 5.5 ms outlier, base 0.1–1.6). `bin/mk20-kbd`
sha256 `44997ce9f3355f7f37e456f678e47b86d82045bf921ae537d9549be7d03b317f` (595288 B, static ARM EABI5); the
other binaries are byte-identical. **No device acceptance claimed.**

## 1.4.0-rc1 (UX revision + single settings entry)

`firmware/VERSION` is **1.4.0**; build id **rc1**: `sh tools/mk20-release-build.sh firmware out rc1`.
Three tracks merged on release/1.4.0: on-device UX (`bin/mk20-kbd`), the usb0 settings web as the only
settings entry (`bin/mk20-agent --web`, default on via FEATURE_WEB=1, agent still off; mk20d supervises
it; see deck/SETTINGS.md), and the Mac side (mk20deck `ui.hello`/`ui.heartbeat`, `ui.openSettings`,
"which agent needs me" hook states, K1 = next agent that needs me). Cross-reviews: Kimi K3 GO on all
three (deck/reviews/1.4.0/). PRD v1.4 §15 lists the interaction changes.

### 1.4.3-rc4: physical LCD order

Build id **rc4**: `sh tools/mk20-release-build.sh firmware out rc4`; `firmware/VERSION` is **1.4.3**. Only
`bin/mk20-kbd` changed (rebuilt static armhf; the other binaries are byte-identical).

Bug (every page since mk20-kbd replaced KD, 1.3.0, HOME included): `kbd/panels.c` wrote logical key L to `/dev/fbL`, assuming the key LCDs
are numbered row-major like KD's getInfo rects. They are not. A photo of 1.4.2 L1 (2026-10-21 23:00, Mac canvas
verified correct) shows all 20 keys, the 8 agent slots in their known order included, exactly where
**/dev/fb(k) = column 4-(k-1)/4, row (k-1)%4** puts them (fb1..fb4 = rightmost column top->bottom, ...,
fb17..fb20 = leftmost column). Display therefore did not match function: e.g. the physical bottom-right key is
logical fb20 (e-stop, GD32 row 3 col 4) but showed logical fb4. K2's "fb mapping verified" checked orientation only
(kbd/K2-RESULTS.md, corrected).

- `panel_t` gains `dev`, the physical node, from one documented table in `kbd/panels.c`: logical key L
  (col c, row r) -> `/dev/fb((4-c)*4+r+1)`; fb21 stays /dev/fb21. `dev` is used only to open the node
  (`screen.c`); the SPI bus is derived from it (/dev/fb1-9 + fb21 = bus 0: 9 keys + sub-screen; /dev/fb10-20 =
  bus 1: 11 keys), so writer threads, the onboard per-bus budget and ctl `screen.layout` `bus` follow the wiring.
- Everything else stays logical and unchanged: key input (`fb = row*5+col+1`), UI tables, host protocol, getInfo
  rects (byte-identical, KD V2.32 parity), canvas rects, cmd 200/ctl `fb`. Goldens/headless images are logical
  canvases and did not change.
- `mk20-kbd --pattern` draws per logical key "R<row> C<col>" (large), "fb<dev>" and "L<n> bus<b>", a TL square
  and a bottom strip with <dev> white bars on the bus colour, and prints the map: one photo verifies it (labels
  must read R1 C1 ... R4 C5 left->right, top->bottom).
- New `test_panel_map` (permutation, uniqueness, inverse formula, bus split 9+1/11); screen, writer, onboard,
  ctl-layout and e2e tests now read the physical node of a logical panel; e2e checks `--pattern` bar counts.

Host validation only (Docker mk20-build:trixie, BusyBox 1.27.2): `make test` green — tests 877 passed /
1 skipped, agent 398, onboard 11348, kbd 4173 ASan + 4173 TSan (was 4019: +test_panel_map/screen checks), safe mode
2 scenarios, e2e OK (incl. `--pattern`), ui 41396, 330 byte-exact goldens unchanged, sim 35, tools 64 passed / 1
skipped, shellcheck clean, release binaries match source. `bin/mk20-kbd` sha256
`4a84bbd74cd976361f6701cdf574ab3562c1c3aed7574121e6746e62c4607ec3` (595288 B, static ARM EABI5).
**No device acceptance claimed** (needs one photo of `--pattern` or of HOME on the device).

### 1.4.2-rc3: cap the debug adbd log (soak follow-up)

Build id **rc3**; `firmware/VERSION` is **1.4.2**. The 1.4.1 soak patrol saw MemAvailable fall ~0.75 MB/h: the
stock debug adbd was started with `>/tmp/adbd.log` (tmpfs = RAM) and logs every adb command (2.4 MB after 2.5 h of
soak/watch polling), never trimmed. adbd now appends (`>>`) and mk20d maint trims `/tmp/adbd.log` like the other
`/tmp` logs (>256 KiB -> last 64 KiB, every 300 s). Only `sbin/mk20d` and `share/{gadget,mk20-add-adb}.sh` changed.

### 1.4.1-rc2: `/run/mk20` on tmpfs (soak follow-up)

Build id **rc2**: `sh tools/mk20-release-build.sh firmware out rc2`; `firmware/VERSION` is **1.4.1** (the loader keeps one MANIFEST per version: a changed 1.4.0 cannot be reinstalled over the confirmed 1.4.0). Only
`sbin/mk20d` changed in the release; binaries are byte-identical to rc1. The rc1 soak patrol found that `/run` is not tmpfs
on the MK20 (rootfs = overlay with its upper dir on the TF card; only `/tmp` is tmpfs). mk20-kbd rewrites
`/run/mk20/kbd.status` (tmp + rename) every second from the main loop that also owns e-stop/HID all-up:
~86 400 TF writes/day, a stalled card could stall that loop, and the files survived reboots (the old
negative-age "stale").

- `mk20d boot` mounts `tmpfs /run/mk20` (`size=512k,mode=0755,nosuid,nodev,noexec`) first, before gadget
  setup, mk20-kbd or the web start. Decided from `/proc/mounts` (canonical path, longest mount prefix):
  already tmpfs (mk20d re-run, or a tmpfs `/run`) -> never stacked; a symlink, another mount at that path,
  or a running mk20-kbd/web (bound `kbd.sock`) -> not mounted. Every refusal and a failed mount are WARN in
  mk20d.log and boot carries on with `/run/mk20` on the overlay as before. Files from earlier boots are hidden.
- `mk20d health`: `"run_tmpfs": true|false`; selftest `INFO /run/mk20 on tmpfs` or
  `WARN /run/mk20 not on tmpfs (<fstype>)` — never FAIL, never blocks confirm.
- The selftest line `kbd status stale (<age>s, up <uptime>s)` now carries the uptime: the age is the
  wall-clock mtime (no RTC, clock sets), the uptime lines a stale sample up with mk20d.log.
- `tools/mk20_soak.py`: a sample whose health status is not `ok` keeps `selftest_warn` (the WARN/FAIL lines of
  `selftest.detail`, <= 8 x 160 chars); the summary counts samples per message (durations folded to `Ns`).
- mk20-kbd's main loop has no other periodic file writes: panel `fsync` goes to `/dev/fb*`, backlight to
  debugfs, the lock to `/tmp`, `kbd.sock` is bound once, its log is `/tmp/mk20/kbd.log`; HOME status probes
  only read, on a worker thread.

Host validation only (Docker mk20-build:trixie, BusyBox 1.27.2): `make test` green — tests 875 passed /
1 skipped, agent 398, onboard 11348, kbd 4018 ASan + 4018 TSan, ui 41396, sim 35, tools 64 passed / 1 skipped,
shellcheck clean; a real BusyBox 1.27.2 `mount -t tmpfs -o size=512k,mode=0755,nosuid,nodev,noexec` was
checked in a container. **No device acceptance claimed.**

### On-device UX (`bin/mk20-kbd`)

On-device UX revision of `bin/mk20-kbd` (rebuilt static armhf; other binaries byte-identical).
mk20-kbd reads `MK20_VERSION` (exported by mk20d) and `/tmp/mk20/gadget.env` for HOME status.

- HOME shows real status instead of "unknown" (new `kbd/devstat.c`: firmware version, usb0 IP,
  USB/Mac link, WiFi interface presence, net/provider config and marker, key presence/permissions
  only — key content is never read). Navigation tiles get a chevron, no value line.
- LS is read-only; the usb0 web page `http://172.30.250.2:8780` is the only settings entry. fb19
  网页设置 shows the URL and, with the Mac ONLINE, sends `{"method":"ui.openSettings"}` (<=1/3 s).
- Boot follows the Mac to L1 once if it comes ONLINE before any key/knob input.
- fb20 on L1/L4 is the Mac mode key unless the keyboard agent is allowed/leased (then e-stop);
  K2 hold/triple always e-stops; an e-stop stays on L1/L4 (Mac PTT released, 10 s footer banner).
- Page-switch grace window swallows keys; confirmation requests no longer auto-switch to L5.
- Live L1/L4 publish the host's full 142-px sub-screen; the device footer appears only for
  attention (pairing code, stop, confirmation, stale Mac, held PTT, GD32 mapping warning).
- Web pairing code from `/run/mk20/pair-code` (0600, <=64 B, <=600 s ahead) shown on every page.
- Follow-up (cross-review): status probes on a worker thread only (snapshot mailbox, stale after
  10 s, bounded shutdown); host forwarding pauses after an e-stop on L1/L4 until all released + 1 s;
  fb20-held/agent-activation race -> host up then e-stop; persistent "智能体已急停" notice when the
  agent was active; grace window also swallows K1/K2 short and is capped; safe mode shows the usb0
  IP and the web pairing code.

Host validation only (Docker mk20-build:trixie, gcc 14): `make -C kbd test` (test_kbd 4018 ASan
+ 4018 TSan, onboard 11337, safe mode 2 scenarios, e2e OK incl. stalled-host and blocked-status-
worker stop latencies), `make -C kbd ui-test` (ui 41396 checks, 85/170 conformance, 330 byte-exact
goldens), onboard TSan 11337, `tests_sim` 34 passed, release-binary/docs/mk20d-kbd pytest. **No device
acceptance claimed.** See kbd/UI.md "On-device UX revision" and ONBOARD-UI-PLAN.md decisions.

## 1.3.1-rc3 (K4 field fix)

`firmware/VERSION` is **1.3.1**; build id **rc3**: `sh tools/mk20-release-build.sh firmware out rc3`.
Binaries are unchanged from 1.3.0-rc2 (6f2788b); only `sbin/mk20d` changed.

1.3.0-rc2 field install (2026-10-20 21:08 SGT) booted mk20-kbd fine (21/21 panels, knobs 6/6, 0 kernel
WARN/Oops) but never confirmed: the device BusyBox has **no `stat` applet**, so `stat -c %Y` was empty, the
kbd status age became "now" (38525 s) -> `FAIL kbd status stale` on probation -> loader deadline reset ->
rollback to 1.2.1. Host tests passed because PATH fell back to GNU stat. Fixes: file mtime via
`date -r FILE +%s`, link count via `ls -ld` (the maintenance hook hard-link guard rejected every hook on the
device for the same reason), kbd status age < 0 is stale (`/run` persists across boots on the overlay and there
is no RTC), and `tests/shims/stat` fails like the device so the suite cannot fall back again.

## 1.3.0-rc1 (K4)

`firmware/VERSION` is **1.3.0**; package with build id **rc1**:
`sh tools/mk20-release-build.sh firmware out rc1`. Rebuild release binaries first
on Linux with `sh tools/mk20-build-bins.sh` (`arm-linux-gnueabihf-gcc`); source-only
changes are not ready to install until `bin/mk20-kbd` is rebuilt. Never hand-edit it.

This release defaults `FEATURE_KBD=1` and starts only mk20-kbd. Stock binaries stay
untouched on rootfs, p5 is never written, and no KD start remains in release scripts.
`MK20_KBD=0` / config `FEATURE_KBD=0` is developer-only; it never starts KD. Legacy
`use-kd`, FAT `mk20-use-kd` and `state/kbd-fallback` are ignored (one boot notice).

>=3 exits in 300 s latch safe mode and keep supervised restarts/backoff. Safe mode
renders static rescue text on all panels, retains GD32 typing/local PTT HID/K2
e-stop, uses the unchanged GD32 TX allow-list, and needs no JPEG/host/agent service.
ECM/usb0 and adb remain up. Selftest marks safe mode WARN even during probation;
missing kbd/no GD32/incomplete panels/stale status/panel errors remain FAIL during
probation and WARN after confirm. Loader A/B rollback is retained; no stock-KD
selector or keyboard combo exists. Restarting after HID re-bind may require reboot.

Rescue is safe mode + usb0 admin web + adb + A/B. K4 implements the validated
file-based [maintenance contract](MAINT-HOOKS.md); the web service is separate U3
work. The optional Mac mirror is out of scope. Hardware acceptance, forced-fail
rollback and 24 h soak remain release gates; this worktree makes no device claims.

## What is a release

A release lives in `/data/mk20/releases/<semver>/`:

| path | what |
|---|---|
| `sbin/mk20d` | entry point: gates, gadget launch, self-test, confirm, `health`, `selftest`, `config`, `maint`, `version`; 1.7.0+: ECM RX watchdog (maint) and the probation host-traffic gate |
| `share/mk20lib.sh` | whitelisted config loader (`/data/mk20/config/mk20.conf`, never sourced) + structured logs/rotation |
| `share/gadget.sh` | ECM + HID + ADB, PTY↔TCP bridge; release rollback requests loader A/B, never starts KD; 1.7.0+: `--rearm-now` soft re-enumeration of the existing composite (usb0, bridge, web restored) |
| `share/flapmon.sh` | flap monitor (1.1.2+): ≥5 link drops in 60 s → rollback; a long host USB suspend (Mac sleep) is not a flap; 1.7.0+: this boot's last drop in tmpfs for the ECM re-arm |
| `share/mk20-add-adb.sh` | stock-mode adb add (fallback) |
| `share/vendor.sha256` | expected hashes of the 4 vendor files the KD shims depend on |
| `bin/mk20-kbd` | Default UI/HID daemon; static safe-mode panels after crash loops, K2 e-stop retained |
| `bin/mk20-router` | Legacy router for developer kbd opt-out only |
| `bin/ttygs_tcp_bridge` | static ARM PTY↔TCP bridge (bound to `usb0`, explicit `--listen` only; 1.6.1+: non-blocking pty master, 2 s stall flush; 1.7.0+: a failed stall-file write is logged) |
| `lib/usb_f_ecm.ko` | ECM function module for the #221 kernel |
| `MANIFEST` | `name/version/build/kernels` header, `--`, then `sha256 mode path` for every file |

The **rescue tier** (`watcher.sh`, `hardreset.sh`, `loader.sh`, `lunch.sh` stub) is *not* part of any release. It
lives in `/data/mk20/rescue/` and `/mnt/SDCARD/mk20/`, each file with a `.sha256` next to it. It is updated
separately with `tools/mk20-rescue-update.sh`.

## Versioning

- `MAJOR.MINOR.PATCH`, each part 0–999 (`firmware/VERSION`, one line).
- PATCH: fixes with no behaviour change visible to the host. MINOR: host-visible change, such as the default
  subnet, new knobs or new self-test gates. MAJOR: a layout or rescue-protocol change that needs a newer loader.
- Never rebuild a released version with different content. Bump PATCH instead. The installer reuses an identical
  installed version and refuses a pending one.
- `firmware/KERNELS` lists the p4 sha256 values a release supports. These are copied into the MANIFEST
  `kernels=` line. At run time, mk20d also needs `/data/mk20/kernel/ALLOWLIST` to name the running p4 and its
  features.

## Build

```sh
cd deck
make test                                   # shellcheck + box tests (BusyBox 1.27.2) + host tests: must be green
sh tools/mk20-release-build.sh firmware out <build-id>   # or: make release (build id = git short sha)
# -> out/mk20-fw-<v>/ , out/mk20-fw-<v>.tar , out/mk20-fw-<v>.tar.sha256 , out/rescue/ (+ .sha256)
```

The build needs GNU tar and GNU stat (the box, or any Linux). It is reproducible: same tree and build id give the
same tarball sha. It rejects symlinks, unsafe names and a bad VERSION.

## Release checklist

1. `make test` is green on the box, and `make mac-test` is green on the Mac (mk20deck runs on macOS; socket and
   timing behaviour differ from Linux, e.g. macOS answers unread request bytes on close with RST).
2. Code review of the milestone diff: check the mihomo AI route first (US-Exit-Hy2), then the reviews in
   `deck/reviews/`. Fix every BLOCKER and MAJOR before building.
3. Build. Copy the `.tar` and `.tar.sha256` to the Mac (`~/mk20-backup/releases/`) and check them with
   `shasum -a 256 -c`.
4. Install on hardware (INSTALL.md §3) with `--reboot`. Watch for `good=<v>` in `loader status` and a
   `mk20d health` status of `ok`.
5. Run the acceptance script (`tools/mk20_accept.py`, INSTALL.md §6).
6. Commit and push: the code, the review files, and the NOTES entry with the hardware evidence.

## Changelog

| version | date (SGT) | changes |
|---|---|---|
| 1.8.0 | 2026-10-27 | **Production `v1.8.0` (2026-10-28)** = build rc1 (sha256 294e1544…ecc5) + Mac 52ae248 (mk20deck on macOS system python 3.9) after a 24 h soak PASS (288 samples, all zero; kernel memory flat). **1.8.0-rc1**: CDC-NCM network function (`lib/usb_f_ncm.ko`, NTBs <= 512 B, MTU 462) with automatic ECM fallback and the `mk20-no-ncm` / `FEATURE_NCM` / `MK20_NCM` switch; re-arm/watchdog/gate on either function |
| 1.7.1 | 2026-10-25 | **1.7.1-rc1** = the 1.7.0-rc3 tree under a new version (1.7.0 build rc2 is installed and good, the loader refuses a different 1.7.0): current-boot-only flap record for the re-arm (rc2 refused every re-arm for ~20 h after a reboot), inbox check via adbd open fds, stale future ECM stamps dropped. |
| 1.7.0 | 2026-10-24 | **1.7.0-rc1** (first built as 1.6.2-rc1: Kimi K3 NO-GO, never installed; renamed: a new self-test gate is MINOR): ECM host→device fault of the 1.6.1-rc2 install (2026-10-23 23:07: after the probation reboot the gadget was configured, usb0 carrier 1, usb0 RX frozen at 1 packet while the Mac sent; the selftest passed and 1.6.1 was confirmed; a hardreset fixed it). mk20d maint ECM RX watchdog: host present and awake (UDC configured, never suspended) + ECM active (carrier 1) + usb0 RX frozen 60 s + a ping/ARP probe whose TX completes while RX stays put → `gadget.sh --rearm-now` (ONE soft re-enumeration of the existing composite: HID quiesced, adbd restarted, unbind/bind, then usb0 re-configured if the re-bind left it bare (MTU 478, address, link-local alias), bridge and web re-bound; under the transition mutex; refused during setup/rollback/install/adb sessions or pushes, within flapmon's window or with FLAP_DROPS < 4); recovered only on an answer from the host (echo reply or a new bridge client); ≤ 3 re-arms per rolling hour, then one watcher `cmd-reboot`, at most once in a row across boots; health `usb.ecm_*`, selftest INFO/WARN; FAT `mk20-no-ecm-rearm` = observe only. Probation gate: with a host present and ECM active, confirm only after proven host→device traffic, or an answer after the gate's own re-arm; no host = skipped; a stall the gate could not try to fix = WARN, not judged; FAIL → no confirm → loader rollback. Bridge: a run-time stall-file write failure is logged once per streak. mk20-kbd: an owner-1 host key with a lost GD32 up ends 60 s after its press (host up + cut latch). `sbin/mk20d`, `share/gadget.sh`, `bin/ttygs_tcp_bridge`, `bin/mk20-kbd` changed. **rc2** (Kimi K3 rc1 delta GO + MAJOR/MINORs): every probe ping is the probe's own child, killed and reaped each 1 s round (rc1 orphaned a non-exiting `ping HOST` per round), `ping -c 1 -W 1` preferred; the loader's staging also blocks a re-arm; a re-arm restores/requires the link-local alias only if setup had it (setup records `ll=`). `sbin/mk20d` + `share/gadget.sh` changed against rc1. **rc3** (hardware: the manual re-arm was refused after a reboot): the re-arm's flap window read the last drop line of the persistent gadget.log, from the previous boot (uptime 71272 vs 165: a negative age counted as recent, ~20 h of refusals after every reboot); now flapmon's tmpfs record of this boot only, a negative age is stale; the install-inbox check no longer uses wall-clock mtimes (no RTC) but adbd's open fds; stale future stamps in the ECM state are dropped. `share/gadget.sh` + `share/flapmon.sh` + `sbin/mk20d` changed against rc2. |
| 1.6.1 | 2026-10-23 | **1.6.1-rc1**: host link self-healing after the 1.6.0-rc4 incident (2026-10-23 20:01, 51 min without a host link: the bridge blocked in a pty master write while the 5.4.61 slave input path stalled with its flip buffer full and n_tty empty; a one-off `tcflush(slave, TCIFLUSH)` recovered it). Bridge v4: non-blocking master, bounded 4 KiB pending buffer on POLLOUT with TCP backpressure, 2 s without write progress → TCIFLUSH on the slave + one log line + pending dropped + client closed; KD→client and accepts keep working. mk20-kbd: 5 s host silence → TCIFLUSH at most once per 5 s (`host_flushes`). Soak: `host_link` liveness (json_in must grow while a client is connected). mk20deck: a line every 10 min while it cannot connect. `bin/ttygs_tcp_bridge` + `bin/mk20-kbd` changed. **rc2** (Kimi K3 GO + 4 MINORs): a replacing client drops the old session's pending bytes (a stall can no longer close it for them); stall log once per streak (+ count every 10 min / at the end); bridge `--stall-file /tmp/mk20/bridge.stalls` (gadget.sh, release bridge only) and soak `host_link: "self-healed"` / `host_link_selfheals` / `bridge_stalls_total` / `host_flushes_total`; the accepted µs flush race documented. `bin/ttygs_tcp_bridge` + `share/gadget.sh` changed against rc1. |
| 1.6.0 | 2026-10-22 | **1.6.0-rc1**: host protocol additions — a same-page `ui.page` only redraws (held PTT / ⌫ / ⏎ / Esc / digit keep their owners and HID state; a route change keeps the full boundary); `ui.hello` names the route (`"page":"L1"\|"L4"\|"none"`), a route changed while no host was ONLINE is K2-tapped before that reply; a fresh `ui.hello` resets `generation` / `hid_generation` to 0; LS fb17 会话安排 sends `{"method":"ui.openArrange"}` (mk20deck opens the 会话安排 page through a one-time link file). Mac: hello page seeds the page belief (no same-page request after a reconnect), hello generations taken as authoritative under a lock (exhaustion recovers), ui.openArrange handler. Only `bin/mk20-kbd` changed. **rc2** (Kimi K3 NO-GO fixes): PTT lost-up cap 120 s (kept host key / 回 L1 across a same-page request: 10 s after it); post-e-stop pause keeps owners through a repeated GD32 down and 回 L1 is inert there (both pre-existing in 1.5.1); mk20deck drops numbered requests decided before a hello, follows a keyboard K2 made while PROBING (no pull-back), arrange access log without query strings. **rc3** (Kimi K3 GO + 2 MINORs): absolute caps per press (PTT 10 min, kept key 60 s) that repeated GD32 downs cannot extend, cut keys inert until their up; the 5 s e-stop pause resync also releases held owners (pre-existing in 1.5.1). **rc4** (Kimi K3 GO + 2 MINORs, 1 nit): same-page bound cleared on every owner-ending path; a repeated GD32 down of a held host key is not forwarded again; keep-cut log shows the real held time. |
| 1.5.1 | 2026-10-22 | **Production `v1.5.1` (2026-10-23)** = build rc1 + Mac f8de052 after a 24 h soak PASS (288 samples, all zero). **1.5.1-rc1**: option mode — tapping a hook-tracked session that waits for input/approval clears rows 1–3 and shows its options on the top row (labels from Claude AskUserQuestion / fixed permission labels / digits); host→device `ui.hidKeys` maps fb1..fb15 to momentary HID digits (allow-list 1–9, L1 only, cleared on page/host/e-stop, 30 s expiry); digits ignored while PTT is held. |
| 1.5.0 | 2026-10-22 | **1.5.0-rc1**: user layout — L1 rows 1–3 = 15 sessions (tap = switch), bottom row 按住说话 / ⌫ 删除 (HID Backspace) / ⏎ 发送·可以 (Return) / ✗ 不行 (Escape) / 模式; L4 row1 DESK HERD TMUX ＋新会话 回L1 (local Back fb14 -> fb5); fb17–19 holds cut at 10 s. |
| 1.4.3 | 2026-10-21 | **1.4.3-rc4**: physical key-LCD order. Logical key L (col c, row r) is `/dev/fb((4-c)*4+r+1)` (column-major from the top-right, photo of 1.4.2 L1), not /dev/fbL: every page was displayed permuted (display != function). `panel_t.dev` (one table in kbd/panels.c) is used only to open the node and to derive the SPI bus; input, UI, host protocol and getInfo stay logical. `--pattern` labels R/C + fbN. Only `bin/mk20-kbd` changed. |
| 1.4.2 | 2026-10-21 | **1.4.2-rc3**: debug adbd log on tmpfs capped (append + mk20d maint trim, was unbounded ~1 MB/h under adb polling). |
| 1.4.1 | 2026-10-21 | **1.4.1-rc2**: `/run/mk20` on a 512 KiB tmpfs mounted first thing in boot (mk20-kbd wrote kbd.status 1/s to the TF-card overlay from its e-stop/HID main loop: wear + stall risk; files no longer survive reboots); refuses when a writer or its supervisor lock is live; health `run_tmpfs`, selftest INFO/WARN only; stale-status line carries uptime; soak records selftest WARN lines. Only `sbin/mk20d` changed. |
| 1.4.0 | 2026-10-21 | **1.4.0-rc1**: on-device UX revision (real HOME status, read-only LS, fb19 opens the web on the Mac, boot follows the Mac to L1, fb20 = Mac mode key on L1/L4 unless the keyboard agent is active, e-stop host pause with bounded resync, status probes on a worker thread, full host sub-screen, pairing code incl. safe mode); usb0 settings web default-on as the only settings entry (pairing via /run/mk20/pair-code, maintenance page, catalogue-driven provider/model); Mac mk20deck heartbeat + needs-me hook states. **rc2**: `/run/mk20` on a 512 KiB tmpfs mounted first by `mk20d boot` (kbd.status no longer rewritten on the TF card every second; WARN + overlay fallback), health `run_tmpfs` + INFO/WARN selftest line, stale line carries the uptime, soak `selftest_warn`. Release: only `sbin/mk20d` changed. |
| 1.3.1 | 2026-10-20 | **1.3.1-rc3**, build id `rc3`: K4 field fix — device BusyBox has no `stat`: kbd status age and maintenance hook link-count checks used `stat -c` and always failed on hardware (rc2 never confirmed, rolled back to 1.2.1); now `date -r` / `ls -ld`, negative age is stale, test shim removes `stat`. Binaries unchanged. |
| 1.3.0 | 2026-10-20 | **1.3.0-rc1**, build id `rc1`: K4 default mk20-kbd, no stock-KD starts/overrides, crash-loop safe mode, maintenance file hooks, unchanged verified one-switch A/B installer; no rootfs writes. Web handlers/device acceptance separate. |
| 1.0.0 | 2026-10-18 | M1: first release layout (A/B slots, MANIFEST, loader, rescue tier) |
| 1.0.1 | 2026-10-18 | M1 review fixes (upgrade 1.0.0 → 1.0.1 on hardware at 17:35) |
| 1.0.2 | 2026-10-18 | M2: mk20d (gates, health/self-test, logs, whitelisted config), bridge bound to usb0. **Bug:** an inline comment disabled bridge cleanup in gadget.sh rollback |
| 1.0.3 | 2026-10-18 | M2/M3 review fixes. **Bug:** `pgrep -x KeyboardDevice` never matches on BusyBox 1.27, so the self-test failed and the release auto-rolled back to 1.0.2 (rollback proven on hardware) |
| 1.0.4 | 2026-10-18 | KD count via `pgrep -f '^/data/KeyboardDevice'`, cleanup fix, installer/prune/uninstall |
| 1.1.0 | 2026-10-18 | M4: default subnet 172.30.250.0/24 (device .2, Mac .1); link-local listener 169.254.7.2 required by self-test |
| 1.1.1 | 2026-10-18 | During probation, a composite setup failure (gadget rc≠0) is a self-test FAIL, so the release rolls back |
| 1.1.4 | 2026-10-18 | M7-fixes reviews: `gadget.sh --rollback-now` guard: all transitions under flock (closed before cleanup), owner = pid + start time + 150-s deadline (dead/reused/hung owners reclaimed), `done` only after stock is verified (ACM+UDC+KD), exit 0 only on verified stock, composite path never clears a live owner; stronger stock check (no ECM/HID links, real ttyGS0, shims unmounted, anchored KD pgrep, 30-s poll) and status file `rollback-verified`/`rollback-NOT-verified`; a run refuses to start while a rollback is in progress; staged script copies replaced atomically; flapmon retries a failed rollback 3x then sets `mk20-no-composite` and hard-resets; gadget.sh waits for flapmon's lock-held ready marker. Host tools (not in the tarball): tf-restore-boot.sh whole-image needs exact card size, valid and identical primary+backup GPT (geometry, disk GUID, entry table) with slots 1-6 = the exact MK20 layout, and a matching `.sha256`; accept/soak write nothing to the device and never connect to the single-client bridge by default (soak never; accept only with the maintenance flag `--tcp`, bound, fail-closed client check), no HID writes, every ping bound to the MK20 interface, soak route check + single-instance lock; accept `--reboot` checks boot_id; tf-restore `--image` accepts only the three known p4 shas, whole-card needs typed confirmation, fast readback |
| 1.2.0 | 2026-10-19 | On-board router (plan steps 1-2): `ttygs_tcp_bridge` v3 `--tap /tmp/mk20/kd-tap.sock` (0600 unix socket, one client, KD→host copy, MSG_DONTWAIT drop-on-backpressure, never stalls KD or the Mac link; a tap that cannot be created is skipped); static armhf `bin/mk20-router` (non-blocking HID writes, ≤500 ms wait); `mk20d router` supervised loop (flock single instance, backoff 1→30 s, reset after a 60-s run; ends on `/tmp/mk20-router-stop`, composite down, or `FEATURE_ROUTER=0` / FAT flag `mk20-no-router` / `MK20_ROUTER=0`), launched by boot only on a clean composite with HID; selftest `INFO router …` lines only (never FAIL/WARN: no effect on confirm or status); health.json `router` {enabled, running, loop, tap}; maint trims `/tmp/mk20/router.log`. Binaries live in the release on /data (FAT unchanged: boot stub only); `make bins` / `tools/mk20-build-bins.sh` rebuilds both from source (test checks they match). r2 (r120 review blockers): gadget.sh `router_stop` (stop file, validated supervisor kill, SIGTERM ≤2 s, SIGKILL) runs before any UDC/configfs change in `cleanup()` and `setup_main` (f_hid cdev UAF on this kernel); router state changes only after a successful HID write, undelivered reports are resent on POLLOUT, all-keys-up at startup and (bounded) on EOF/SIGTERM/SIGINT; the bridge closes a tap client on any incomplete send (router EOF → release) |
| 1.1.3 | 2026-10-18 | M5–M7 review fixes: flapmon counts a short suspend that ends in a reset/detach (a long suspend = Mac sleep is not counted), single instance (flock), re-checks the stopfile before rolling back, validates its arguments; `gadget.sh --rollback-now` once-guard (`/tmp/mk20/rollback.once`), confirm waiter honours the flapmon stopfile, a re-run stops the old monitor; rollback env via `export` |
| 1.1.2 | 2026-10-18 | M7: flap monitor moved to `share/flapmon.sh` (unit-tested); a host USB suspend/resume (Mac sleep) is logged, not counted as a flap |

Decision record: adb stays on in stock fallback even when the vendor hashes don't match (remote reachability wins).
See NOTES-crash-20261018-and-tf-backup.md, "Decision (user, 18:32 SGT)".
