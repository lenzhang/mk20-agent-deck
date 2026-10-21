# MK20 "Agent 增强固件" — handoff (2026-10-21 SGT)

Goal: production-grade replacement firmware for the Waveshare MK20 keyboard (Allwinner T113, Tina 5.4.61,
BusyBox 1.27.2), stability first; Mac daemon `mk20deck` drives the L1 agent page. Live status: PROGRESS.md (main).

## Current state (2026-10-21 ~11:30)
- **Device: fw 1.4.0-rc1, good (slot A)**, 1.3.1 kept as A/B fallback. Tarball sha256
  `b3071a20001142c0f1dd0e04b361ea1b3cb5639897f7f466a67aacb2f71b1e04` (~/worktrees/rel-1.4.0-rc1/,
  ~/mk20-backup/releases/). Tag `v1.4.0-rc1` on release/1.4.0. Acceptance: 12/12 + 1.4 checks PASS
  (deck/reviews/1.4.0/install-1.4.0-rc1-*/RESULT.md + ADDENDUM.md).
- **Mac: mk20deck runs as LaunchAgent** `com.leo.mk20-agent-deck` from `~/mk20-deploy` (detached checkout of
  release/1.4.0), venv `~/mk20-deploy/.venv`, config `~/.config/mk20-agent-deck/config.toml` (terminal_app=Ghostty,
  tmux new session = `ccai sg`), logs `~/.config/mk20-agent-deck/logs/deck.err.log`. Connects via
  tcp://172.30.250.2:17890 (usb0 bridge; the gadget has no ACM). Restart: `launchctl kickstart -k gui/$(id -u)/com.leo.mk20-agent-deck`.
- **24 h soak on 1.4.0** started 2026-10-21 11:24 (`~/mk20-backup/soak/soak-20261021-112446.*`, annotations in
  ANNOTATIONS.txt). 1.3.1 soak (155 samples): 0 real crashes, 0 composite losses, ping 0 loss, mk20-kbd RSS 2.3 MB flat.
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
- `release/1.4.0` — integration (kd-replace-k4 + fw-ux + web-settings + deck-attention), tag v1.4.0-rc1.
- `feature/kd-replace-k4` — K4 base (1.3.1 fix, soak/accept/crash-loop gate, build image).
- `feature/fw-ux` (mk20-kbd UX), `feature/web-settings` (usb0 web), `feature/deck-ui-hello` → `feature/deck-attention` (mk20deck).
- Older tracks unchanged: feature/agent (A8c, agent off by default, not merged), feature/hidfix (run 11 PASS), feature/uvc, uac-mic, onboard-ui, p2-computer-use, sim.

## Build / test / release (all on dev-mbp now; iMac optional)
- Image: `docker build -t mk20-build:trixie deck/tools/build-image` (pinned debian digest, uv 0.13.3, procps).
  BusyBox 1.27.2: `deck/tools/build-image/build-busybox127.sh` → `~/mk20-build-local/tools/bb127/busybox`.
- Full tests: `cd deck && docker run --rm -v "$PWD":/work/wt/deck -v ~/mk20-build-local/tools/bb127:/bb127:ro -w /work/wt/deck -e MK20_TEST_BUSYBOX=/bb127/busybox mk20-build:trixie make test`
  (1.4.0: tests 863, agent 398, onboard 11348, kbd 4018×2, ui 41396 — all pass). Tests expose only device commands
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
1. Physical checks when the user is back: fb20 = mode key on L1 (cycles Desktop→herdr→tmux), fb19 opens the web on the
   Mac, PTT on L1, K1 rotate/press with real Claude hooks in tmux, HOME look & feel.
2. 24 h soak on 1.4.0 → then promote rc1 to 1.4.0 final (or rc2 if anything shows up).
3. PRD v1.4 §15 approval.
4. codex hooks: reinstall the updated hook (`deck/tools/hooks/hooks-uninstall.sh && hooks-install.sh`) and re-trust in
   codex — needs the user's OK (changes ~/.codex).
5. Not in this delivery: agent A8c merge, USB mic / UVC capture / charger-only / Mac reboot / TF restore rehearsal tests,
   mk20-router 23 s WiFi-dongle reset (router off by default since 1.3), HOME visual polish beyond status text.
