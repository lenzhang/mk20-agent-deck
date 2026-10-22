# MK20 "Agent 增强固件" — handoff (2026-10-23 05:40 SGT)

Goal: production-grade replacement firmware for the Waveshare MK20 keyboard (Allwinner T113, Tina 5.4.61,
BusyBox 1.27.2), stability first; Mac daemon `mk20deck` drives the L1 agent page. Live status: PROGRESS.md (main).

## Current state (2026-10-23 ~05:40) — user away for several days since 10-22 10:30 (autonomous mode)
- **Production: tag `v1.5.1`** on release/1.5.1 f8de052 = fw 1.5.1 build rc1 (tar sha256
  `3d7c14700b1bc8c7a172fae3e4078dea3493d012b1ffec04660c1429ef35e41b`, reproducible from the tag) + Mac mk20deck f8de052.
  24 h soak PASS (10-22 05:02 → 10-23 05:02, 288 samples, all zero; memory flat).
- **Device now: fw 1.6.0-rc4, good** (installed 10-23 05:26, slot A; 1.5.1 kept in slot B), tag `v1.6.0-rc4` (a8dd423)
  on **release/1.6.0**; tar ~/worktrees/rel-1.6.0-rc4/mk20-fw-1.6.0.tar sha256
  `a40b997d62724932c3edce219fb6a24cf679c3bd1f1c1fa5aabfa33d5869ecfe` (reproduced). Acceptance PASS (evidence
  deck/reviews/1.6.0/install-1.6.0-rc4-20261023-052415). 1.6.0 = same-page `ui.page` keeps held keys (lost-up guards:
  PTT 120 s window / 10 min absolute, kept keys 10 s / 60 s, cut latch until the GD32 up), `ui.hello` reports
  `page` and resets generations, LS page key **会话安排** (`ui.openArrange` → Mac opens the arrange page), e-stop pause
  keeps key owners (also fixes two 1.5.1 edge bugs), Mac epoch fencing. Kimi K3: rc1 NO-GO → rc2/rc3/rc4 GO.
- **24 h soak on 1.6.0-rc4** started 10-23 05:28 (`~/mk20-backup/soak/soak-20261023-052849.*`, run from
  ~/worktrees/mk20-rel-160/deck with `MK20_ADB=~/mk20-backup/mkadb.sh`), selftest watcher, 30-min slab logger
  (`soak/slab-log.txt`); patrol every 2 h (`~/worktrees/mk20-patrol.sh`); verdict job 10-24 05:47 (session-only cron).
- **Mac: mk20deck** LaunchAgent `com.leo.mk20-agent-deck` from `~/mk20-deploy` (detached at release/1.6.0 a8dd423; state
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
- **PRD v1.5 draft** on branch docs/prd-1.5 (027c443) — awaiting the user's approval.
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
- **`release/1.6.0`** — current (feature/fw-1.6.0 + install evidence), tag v1.6.0-rc4. **`release/1.5.1`** — production
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
1. Approve PRD v1.5 (「批准 PRD v1.5」, covers v1.4 §15). Integrator decisions needing an explicit OK: momentary option
   digits, digits ignored while PTT held, option mode only for hook prompts, ⌫/⏎/Esc 10 s auto-release, **PTT 120 s cap
   (10 min absolute)**, "after a lost-up cut the key ignores downs until its up".
2. Hands-on checks: option-mode digits in a NEW Claude/codex session, long-press pick-up/place, ⌫/⏎/Esc, MODE long press →
   L4 and back (pages stay in sync), `mk20deck arrange` + the LS 会话安排 key (1.6.0), PTT hold across a reconnect.
3. codex hooks reinstall (changes ~/.codex). 4. Decide: production build with adb OFF by default (adb = unauthenticated root
   over USB; maintenance toggle instead).
**Autonomous:**
5. 1.6.0-rc4 24 h soak → tag v1.6.0 (verdict job 10-24 05:47).
6. fix/soak-slab (soak records /proc/meminfo + 480-byte adb guard in soak/accept tools) → review → merge.
7. 1.6.1 candidates: 60 s cap for an owner-1 host key with a lost up (UI.md residual), device settings-web link to 会话安排.
8. Measure e-stop visual-feedback latency on device (needs a physical press).
9. Not in this delivery: agent A8c merge, USB mic / UVC capture / charger-only / Mac reboot / TF restore rehearsal tests,
   mk20-router 23 s WiFi-dongle reset (router off by default since 1.3), HOME visual polish beyond status text.
