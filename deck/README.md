# mk20deck — MK20 Agent Deck P0 (PRD v1.2)

Code lives in `deck/` so it does not collide with the old untracked skeleton
(`src/`, root `pyproject.toml`) on the Mac. PRD §14 (v1.3 VNC/AirPlay addendum)
is **not** implemented (not approved).

## Layout
| module | what |
|---|---|
| `protocol.py` | Protocol A codec (`A1A55A5E`, crc32 of size & data), stream parser with resync, method allow-list (no `keyboardInput`) |
| `transport.py` / `link.py` | pyserial transport (CDC `1d6b:0104`, alt `1234:5678`), `MockDevice`, handshake (1 MiB `'0'` preamble + `getInfo`), SHOW_JPG stop-and-wait using the device ack as display ACK, latency stats |
| `layout.py` | canvas/rect model; default guess 640×656 (sub 428×142 @106,0; keys 128² @ (c·128, 144+r·128)); replaced by `getInfo` |
| `adapters/codex_desktop.py` | read-only Codex Desktop source: `state_5.sqlite` (`mode=ro`), rollout tail, `.codex-global-state.json` unread, `codex://` deep links |
| `adapters/tmux.py`, `adapters/herdr.py` | tmux (generation identity, states unknown) / herdr stub |
| `slots.py`, `controller.py` | slot manager (sticky, strict-higher replace, pins, per-mode memory), gestures, stop long-press, roles L1/L4 |
| `render/` | key & sub-screen renderer, ≤8 fps global cap, single attention animation |
| `daemon.py`, `cli.py` | daemon loop, heartbeat, offline frame on exit, CLI |
| `sim.py` | headless screenshots + interactive web sim |

## Dev
```bash
python3 -m venv .venv && .venv/bin/pip install -e . pytest
.venv/bin/python -m pytest -q                 # 72 tests, Linux-safe
.venv/bin/python -m mk20deck sim-shots --out ../artifacts
.venv/bin/python -m mk20deck sim-serve        # http://127.0.0.1:8765 (--source real on the Mac)
python3 -m mk20deck probe-codex [--json]      # read-only Codex Desktop snapshot (Mac)
.venv/bin/python -m mk20deck [--config ~/.config/mk20-agent-deck/config.toml] run [--port /dev/cu.usbmodemXXX]
.venv/bin/python -m mk20deck launchd-plist > ~/Library/LaunchAgents/com.leo.mk20-agent-deck.plist
```
Config: copy `config.example.toml` → `~/.config/mk20-agent-deck/config.toml`
(`MK20DECK_HOME` overrides the dir). `max_fps` is clamped to 8.

Golden frames: `tools/golden_gen.cpp` reproduces the vendor `packet_cmd_ack`
with the vendor `CRC32.cpp`; output is `tests/fixtures/golden_frames.txt`.

## Codex hook probe (optional, reversible)
`tools/hooks/hooks-install.sh` writes a logging-only `~/.codex/hooks.json`
(refuses if one exists); the hook logs metadata only (no prompt/tool input) to
`~/.config/mk20-agent-deck/hooks.log` and never returns a decision.
`tools/hooks/hooks-uninstall.sh` removes it if unchanged. Codex requires hooks to
be trusted (App: Settings → Hooks) before they run in Desktop sessions.

## P0 deviations / known limits
- Canvas geometry is a guess until `getInfo` on the real device.
- Desktop states: `error` from rollout `event_msg:error`; `turn_aborted` → idle;
  running older than 600 s without completion → unknown. All Desktop sessions are
  tagged 来源推定 (source=vscode + originator heuristic; codex-tui also writes vscode).
- Desktop send / stop / effort disabled; accept/decline only explain (no approve via deck).
- Skills prefill a new thread via `codex://threads/new?path=&prompt=` — never submit; long prompts fail, not truncated.
- Knob event mapping unverified (`knob_map` in config). PTT path B not implemented (PTT = device-side KC_RALT).
- Sleep detection not implemented (offline frame on SIGTERM/exit only). tmux new-session off unless configured. herdr stub. L2/L3 are P1.

## On-device checklist
1. `getInfo` → real canvas & rect coordinates (0- vs 1-based), resolution reject behaviour.
2. SHOW_JPG ack timing / max sustainable fps at full canvas; JPEG size vs quality.
3. Key / knob event JSON shape (`keyStateChanged`, knob rotate/press) → `knob_map`.
4. PTT KC_RALT hold/release while deck owns display; anti-misfire timing (600 ms long press, 350 ms double tap).
5. Sleep/wake & USB re-enumeration → reconnect + offline frame.
