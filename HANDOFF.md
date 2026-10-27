# MK20 "Agent 增强固件" — handoff (2026-10-27 10:10 SGT)

Goal: production-grade replacement firmware for the Waveshare MK20 keyboard (Allwinner T113, Tina 5.4.61,
BusyBox 1.27.2), stability first; Mac daemon `mk20deck` drives the L1 agent page. Live status: PROGRESS.md (main).

## Update 2026-10-27 10:10 SGT — device moved to the iMac
- **Host is now the iMac** (dev-imac, macOS 15.8 x86_64); dev-mbp.lan is offline (its `~/mk20-full` mirror, soak data,
  `~/mk20-deploy`, `~/worktrees` install chain and the original mkadb.sh are there, not on the iMac).
- **Device (read-only, 10-27):** fw 1.7.1 good, slot B gen 28 (A = 1.7.0), health ok, selftest all PASS, mk20-kbd normal,
  21/21 panels, knob 6/6; `ecm_state=inactive`; **FAT free ~1.2 MB**. 5 more boots of 1.7.1 after its confirm (no RTC).
- **1.7.1-rc1 soak verdict unknown**: host-side data on dev-mbp (`soak-20261025-012324.*`), no verdict commit, no v1.7.1 tag.
- **iMac tooling** (chore/imac-tooling 8867fb9, `tools/imac/`): adb **r35.0.2** required (37.0.1 is EXC_GUARD-killed on the
  empty-serial MK20; libusb backend skips it); `~/mk20-backup/mkadb.sh` (MK20 only, transport id) and
  `~/worktrees/route-check.sh` (mihomo API + chatgpt trace, read-only).
- **ECM does not come up on the iMac**: control iface matched by kext AppleUSBECMControl, data iface by DriverKit
  AppleUserECMData which stays !registered → no enX → mk20deck / bridge / web / soak blocked. Next: user replug; else driver-level fix (user decision).
- **route-check**: iMac mihomo AI group has only `US-Exit-SS` (Shadowsocks), no `-Hy2`; exit 203.0.113.10 US. Strict rule
  → ROUTE_FAIL; awaiting the user (`WANT_NODE=US-Exit-SS`). No codex/kimi until decided.
- **After 10-25 (other thread):** feature/gd32-dfu-notice cef750b + mac/1.7.1-gd32-notice 59a8334 (GD32 DFU warning),
  feature/k1-scroll c702ff7 + mac/1.7.1-k1-scroll b21fbda (K1 scrolls session history) — Kimi K3 GO, not deployed;
  feature/gd32-default-keymap a581757 (GD32 KC_NO keymap patch, analysis only, **not flashed**; flashing stays forbidden).
- **PRD v1.5 approved** (2026-10-25, user; main c582581): production keeps ADB; integrator decisions V8/V11–V18 approved.

## Current state (2026-10-23 ~05:40) — user away for several days since 10-22 10:30 (autonomous mode)
- **Production: tag `v1.6.1`** on release/1.6.1 8d08ca9 = fw 1.6.1 build rc2 (tar sha256
  `4ba75ecf4aa7a1d69eb34f3bb287849a7297fb2164064cf22a5e57af72d077f1`, reproducible) + Mac mk20deck 7103da8. 24 h soak PASS
  (10-23 23:15 → 10-24 23:15, 288 samples, all zero incl. host-link stalls/self-heals). Supersedes v1.5.1 (which has the
  latent kernel 5.4 pty input stall fixed in 1.6.1). Older: v1.5.1 (f8de052).
- **Device now: fw 1.7.1-rc1, good** (installed 10-25 01:21, slot B; 1.7.0 build rc2 in A), tag `v1.7.1-rc1` (2b194f1) on
  **release/1.7.1**; tar ~/worktrees/rel-1.7.1-rc1/mk20-fw-1.7.1.tar sha256
  `06da8685530e396aa273dce44d257048fce3f154ff6d947c94ec4bdca1267b9b` (reproduced). 1.7.x = ECM RX watchdog (probe-proven
  host→device stall → soft re-arm restoring usb0 MTU/IP/alias + bridge/web, answer-proven recovery, ≤3/h then one watcher
  cmd-reboot) + probation gate (confirm only with proven host traffic) + bridge stall-file error log + 60 s cap for a lost
  host-key up. Kimi K3: 1.6.2-rc1 NO-GO → 1.7.0-rc1/rc2/rc3 GO. On hardware: 1.7.0-rc2 refused every manual re-arm
  (flap check read a previous boot's log line) → fixed in rc3 = 1.7.1-rc1 (new version: the loader refuses a different
  1.7.0); **manual re-arm test PASS on 1.7.1** (rc=0, usb0 kept, Mac re-hello in ~7 s; deck/reviews/1.7.1/).
- **24 h soak on 1.7.1-rc1** started 10-25 01:23 (`soak-20261025-012324`, run from ~/worktrees/mk20-rel-171/deck with
  `MK20_ADB=~/mk20-backup/mkadb.sh`), selftest watcher, slab logger; patrol every 2 h (`~/worktrees/mk20-patrol.sh`);
  verdict job 10-26 01:43 (session-only cron) → tag v1.7.1 if PASS.
- **Incidents (all in deck/reviews/1.6.1/incidents/ + ANNOTATIONS):** 10-23 00:48 a 661-byte patrol adb request →
  adbd EOVERFLOW → gadget re-enumeration (rule: adb payload < 512 B; tools guard 480 B); 10-23 20:01 host-link stall
  51 min (kernel pty input stall, blocking bridge write) → 1.6.1; 10-23 23:07 ECM dead host→device after reboot → 1.7.x.
- **Mac: mk20deck** LaunchAgent `com.leo.mk20-agent-deck` from `~/mk20-deploy` (detached at release/1.6.1 7103da8; state
  backups ~/mk20-backup/deck-state-*.tgz), venv `~/mk20-deploy/.venv`, config `~/.config/mk20-agent-deck/config.toml`
  (terminal_app=Ghostty, new session = `ccai sg`), logs `~/.config/mk20-agent-deck/logs/deck.err.log`, bridge
  tcp://172.30.250.2:17890. Restart: `launchctl kickstart -k gui/$(id -u)/com.leo.mk20-agent-deck`.
  **会话安排 page** 127.0.0.1:8781: `mk20deck arrange` (wrapper ~/.local/bin/mk20deck) or the keyboard's LS 会话安排 key;
  one-time link in a 0600 .webloc (never argv/logs), keep-alive with per-connection cap, graceful close (no RST on macOS).
  **Page sync** via absolute `ui.page` (hello page seeds the guess on 1.6.0).
- **User's L1 layout (1.5.0):** rows 1–3 = 15 sessions (tap = switch + front; long press = pick up → tap target = move+pin /
  swap, tap self = toggle pin, K1/8 s cancel); bottom row 🎤 PTT (Right Option) / ⌫ / ⏎ (send = OK) / ✗ Esc / MODE — the
  middle three are device USB-HID. L4 row1 = DESK HERD TMUX ＋新会话 回L1. **Option mode (1.5.1)**: tap a session that
  waits for input → options on row 1, digits as momentary HID. Only "needs me" states animate.
- **Tests:** Docker `make test` (Linux box) AND `make mac-test` (native macOS; mk20deck runs there) must both be green.
- **PRD v1.5 approved** (2026-10-25, user) — merged to main c582581 (draft was docs/prd-1.5 027c443).
- **Claude hooks** ("which agent needs me") installed in `~/.claude/settings-glm.json` (source of all ccai modes) and
  `settings.json`; backups `*.bak-mk20-20261021-095159`; script `~/.config/mk20-agent-deck/hooks/`; uninstall:
  `deck/tools/hooks/claude-hooks-uninstall.sh ~/.claude/settings-glm.json ~/.claude/settings.json`.
- **Claude Code statusline** (`~/.claude/statusline.py`): plan usage `用量 5h N% ↻… · 周 N% ↻…` for ccai sg only.

## What 1.4.0 changed (PRD v1.4 §15, draft awaiting approval)
Keyboard web `http://172.30.250.2:8780` = the only settings entry (default on, pairing code shown on the keyboard,
maintenance page; keys only display; fb19 网页设置 makes the Mac open it). HOME shows real status (no "unknown").
Boot follows the Mac to L1. fb20 on L1/L4 = Mac mode key unless the keyboard agent is active (K2 hold always e-stops).
mk20deck: ui.hello/heartbeat, K1 = cycle "needs me" (needs_approval > needs_input > error > done), hook-based tmux
states, other-mode counts, reconnect while the Mac display sleeps. Root causes fixed on the way: device BusyBox has no
`stat` (1.3.0 never confirmed), HOME host-loss livelock, test image lacked procps, soak/accept K4 rules.

## Branches (Gitea ssh://git@192.168.88.6:2222/leo/mk20-agent-deck.git)
- **`release/1.7.1`** — current (v1.7.1-rc1), `release/1.7.0` (v1.7.0-rc2), **`release/1.6.1`** — production tag **v1.6.1** (+ v1.6.1-rc2). `release/1.6.0` (feature/fw-1.6.0 + install evidence), tag v1.6.0-rc4. **`release/1.5.1`** — production
  tag **v1.5.1** (+ v1.5.1-rc1); merged there: feature/arrange-web, fix/page-sync, fix/devtest-hid-leak,
  fix/arrange-keepalive-cap, fix/arrange-linger-minors, fix/macos-test-gate. `release/1.5.0` (v1.5.0-rc1, **do not deploy
  the Mac from it**: pre-fix arrange code), `release/1.4.0` (v1.4.0-rc1 … v1.4.3-rc4). Open: fix/soak-slab, docs/prd-1.5.
- `feature/kd-replace-k4` — K4 base (1.3.1 fix, soak/accept/crash-loop gate, build image).
- `feature/fw-ux` (mk20-kbd UX), `feature/web-settings` (usb0 web), `feature/deck-ui-hello` → `feature/deck-attention` (mk20deck).
- Older tracks unchanged: feature/agent (A8c, agent off by default, not merged), feature/hidfix (run 11 PASS), feature/uvc, uac-mic, onboard-ui, p2-computer-use, sim.

## Build / test / release (all on dev-mbp now; iMac optional)
- Image: `docker build -t mk20-build:trixie deck/tools/build-image` (pinned debian digest, uv 0.13.3, procps).
  BusyBox 1.27.2: `deck/tools/build-image/build-busybox127.sh` → `~/mk20-build-local/tools/bb127/busybox`.
- Full tests: `cd deck && docker run --rm -v "$PWD":/work/wt/deck -v ~/mk20-build-local/tools/bb127:/bb127:ro -w /work/wt/deck -e MK20_TEST_BUSYBOX=/bb127/busybox mk20-build:trixie make test`
  (1.4.2: tests 877, agent 398, onboard 11348, kbd 4019×2, ui 41396, sim 35, tools 64 — all pass; two timing-flaky K3 checks fixed). Tests expose only device commands
  (`tests/fixtures/device-commands.txt`). colima: `colima start default`; TSan may need `colima ssh -- sudo sysctl -w vm.mmap_rnd_bits=28`.
- Release: `sh tools/mk20-release-build.sh firmware out <rc>` inside the image. Install + acceptance:
  `EXP=<sha> ~/worktrees/mk20-install-chain.sh <ver> <rc> <commit> <tar>` (+ install-eval.py). Gates:
  `tools/mk20-crashloop-gate.sh <ver>` (PASS on 1.3.1), `tools/mk20_soak.py --hours 24`.
- Cross-review: codex has been "model at capacity" since 10-20 15:17 → Kimi K3 (`kimi -m kimi-code/k3`), run with
  `env -u HTTPS_PROXY -u HTTP_PROXY -u ALL_PROXY -u https_proxy -u http_proxy -u all_proxy` + route-check per command.
  Records: deck/reviews/1.4.0/ (fw-ux r1/r2 NO-GO → r3 GO, web GO, rc3 GO, interaction x-check).

## Rules that bind
- Route check before every codex/kimi run (AI=US-Exit-Hy2, exit 203.0.113.10); this Claude session's shell has
  HTTPS_PROXY=127.0.0.1:7895 (SG, Claude's own) — strip it per command only, never globally.
- ccai: settings-glm.json is the source; settings-clean.json is regenerated at every launch; settings.json is overwritten.
- **adb shell payloads must stay < 512 bytes** (keep ≤ 400): the device's adbd fails a longer request with
  `bulk_read ... errno=75 (EOVERFLOW)` and the whole USB gadget re-enumerates (HID + ECM drop, 2026-10-23 00:48,
  caused by a 661-byte patrol command). Split long checks into several short `mkadb.sh shell` calls.
- Never touch ESP32 /dev/cu.usbmodem1111101 or the watch adb device; adb via ~/mk20-backup/mkadb.sh. GD32: never 0x19 or
  02 AA E2 78, never flash; only 05 00 r r 00 00 (r=100..105). No plain `reboot`. No USB-changing steps under adb.
- Secrets 0600 under /data/mk20/secrets; never print/log/commit. Device installs: authorized by the user (10-20 21:30).
- Each parallel task its own worktree; never stop other agents' containers.

## Open items
**Waiting for the user (collected while away):**
1. ~~Approve PRD v1.5~~ — DONE 2026-10-25 (user: 「批准 PRD v1.5 生产版也保留 ADB」; integrator decisions approved in §17).
2. Hands-on checks: option-mode digits in a NEW Claude/codex session, long-press pick-up/place, ⌫/⏎/Esc, MODE long press →
   L4 and back (pages stay in sync), `mk20deck arrange` + the LS 会话安排 key (1.6.0), PTT hold across a reconnect.
3. codex hooks reinstall (changes ~/.codex). 4. ~~adb OFF by default?~~ — DECIDED 2026-10-25: production keeps ADB (PRD §8).
**New (2026-10-27, iMac):**
10. User: replug test / decision for the iMac ECM driver-match problem. 11. User: accept `US-Exit-SS` (SS) as the AI route?
12. Deploy mk20deck + build/test env (colima mk20, bb127, install chain) on the iMac. 13. Merge/deploy gd32-dfu-notice and
    k1-scroll; GD32 keymap patch only on an explicit user decision. 14. Device FAT free ~1.2 MB (investigate read-only first).
15. Sync dev-mbp `~/mk20-full` when it is back online.
**Autonomous:**
5. 1.7.1-rc1 24 h soak → tag v1.7.1 — verdict UNKNOWN (data on offline dev-mbp); read it when dev-mbp is back, else rerun on the iMac once ECM works.
7. 1.6.1 candidates: 60 s cap for an owner-1 host key with a lost up (UI.md residual), device settings-web link to 会话安排.
8. Measure e-stop visual-feedback latency on device (needs a physical press).
9. Not in this delivery: agent A8c merge, USB mic / UVC capture / charger-only / Mac reboot / TF restore rehearsal tests,
   mk20-router 23 s WiFi-dongle reset (router off by default since 1.3), HOME visual polish beyond status text.
