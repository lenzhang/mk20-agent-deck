# MK20 "Agent 增强固件" — handoff (2026-10-22 05:10 SGT)

Goal: production-grade replacement firmware for the Waveshare MK20 keyboard (Allwinner T113, Tina 5.4.61,
BusyBox 1.27.2), stability first; Mac daemon `mk20deck` drives the L1 agent page. Live status: PROGRESS.md (main).

## Current state (2026-10-22 ~05:10)
- **Device: fw 1.5.1-rc1, good**; previous good kept in the other A/B slot. Tar sha256
  `3d7c14700b1bc8c7a172fae3e4078dea3493d012b1ffec04660c1429ef35e41b` (~/worktrees/rel-1.5.1-rc1/), tag `v1.5.1-rc1` on
  **release/1.5.1** (= release/1.5.0 @7c599a3 slot-move + feature/option-mode). Acceptance PASS.
- **User's L1 layout (1.5.0, decided 10-21):** rows 1–3 = 15 sessions (tap = switch + bring to front; long press = pick
  up, tap target = move+pin/swap, tap self = toggle pin, K1/8 s cancel); bottom row 🎤 PTT (Right Option) / ⌫ Backspace /
  ⏎ Return (send = OK) / ✗ Escape / MODE — the three middle keys are device USB-HID. L4 row1 = DESK HERD TMUX ＋新会话 回L1.
  **Option mode (1.5.1):** tapping a hook-tracked session that waits for input clears rows 1–3 and shows its options on
  row 1 (momentary HID digits via `ui.hidKeys`; ignored while PTT held). Only "needs me" states animate.
- **Physical LCD order fixed in 1.4.3** (fb1..4 = rightmost column; K2's old "verified" claim was wrong) — user confirmed.
- **Session arrangement web (127.0.0.1:8781)** is on feature/arrange-web and merged into release/1.5.0 (93c2cc7) but
  **NOT deployed**: Kimi NO-GO (token handout, unbounded queues, preview leak, stale manager, rule save rollback,
  OverflowError, bg thread join) — fix in progress; do NOT deploy the Mac daemon from release/1.5.0.
- **24 h soak** on 1.5.1 started 2026-10-22 05:02 (latest `~/mk20-backup/soak/soak-*.jsonl`); patrol every 2 h.
- **Mac: mk20deck runs as LaunchAgent** `com.leo.mk20-agent-deck` from `~/mk20-deploy` (detached checkout of
  release/1.4.0), venv `~/mk20-deploy/.venv`, config `~/.config/mk20-agent-deck/config.toml` (terminal_app=Ghostty,
  tmux new session = `ccai sg`), logs `~/.config/mk20-agent-deck/logs/deck.err.log`. Connects via
  tcp://172.30.250.2:17890 (usb0 bridge; the gadget has no ACM). Restart: `launchctl kickstart -k gui/$(id -u)/com.leo.mk20-agent-deck`.
- Earlier soaks: 1.3.1 (155 samples): 0 real crashes, 0 composite losses, ping 0 loss. Annotations: ~/mk20-backup/soak/ANNOTATIONS.txt.
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
- `release/1.4.0` — integration (kd-replace-k4 + fw-ux + web-settings + deck-attention + fix/run-tmpfs), tags v1.4.0-rc1, v1.4.1-rc2, v1.4.2-rc3.
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
- Never touch ESP32 /dev/cu.usbmodem1111101 or the watch adb device; adb via ~/mk20-backup/mkadb.sh. GD32: never 0x19 or
  02 AA E2 78, never flash; only 05 00 r r 00 00 (r=100..105). No plain `reboot`. No USB-changing steps under adb.
- Secrets 0600 under /data/mk20/secrets; never print/log/commit. Device installs: authorized by the user (10-20 21:30).
- Each parallel task its own worktree; never stop other agents' containers.

## Open items
1. User device checks for 1.5.1: option mode with a NEW Claude session in tmux (AskUserQuestion / permission menu accept
   digits?), codex approval UI with digits, slot move gesture, ⌫/⏎/Esc keys, K2 → L4 新会话/回L1.
2. Session arrangement web: finish the Kimi NO-GO fixes → re-review → merge into release/1.5.1 → deploy → user test.
3. 24 h soak on 1.5.1 → tag the production release (v1.5.1) or next rc.
4. PRD v1.5 (deck/docs/PRD-1.5-L1-LAYOUT.md) to merge into the main PRD + approval; PRD v1.4 §15 approval.
5. codex hooks: reinstall the updated hook (`deck/tools/hooks/hooks-uninstall.sh && hooks-install.sh`) and re-trust in
   codex — needs the user's OK (changes ~/.codex). Device web link to the arrange page (next firmware).
6. hw/devtest.py HID-leak check still assumes "no HID except PTT" (fb17–19 / option digits are HID now) — update.
7. Measure e-stop visual-feedback latency on device (fb20 face now on bus 0 with the sub-screen; Kimi rc4 note).
8. Not in this delivery: agent A8c merge, USB mic / UVC capture / charger-only / Mac reboot / TF restore rehearsal tests,
   mk20-router 23 s WiFi-dongle reset (router off by default since 1.3), HOME visual polish beyond status text.
