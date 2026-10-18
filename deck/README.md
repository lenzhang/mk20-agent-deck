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
| `supervisor.py` | find port → open → handshake → run; reconnect on unplug / ACK loss / wake; busy-port hint (never kills) |
| `hw/devtest.py` | on-device validation harness (`devtest`), markdown + JSON report |
| `hw/via.py` | VIA raw-HID client: read-only backup, diff-only verified restore, mock keyboard |
| `hw/gd32dfu.py` | GD32 DFU (`1eaf:0003`) re-flash wrapper, dry-run by default |
| `hw/usbenum.py`, `hw/power.py` | read-only USB enumeration; sleep/wake (clock jump + `CGDisplayIsAsleep`) |

## Dev
```bash
python3 -m venv .venv && .venv/bin/pip install -e . pytest
.venv/bin/python -m pytest -q                 # 95 tests, Linux-safe
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
- Canvas geometry is a guess until `getInfo`; firmware 0/1-based coords are inferred from getInfo rects (`fw_coord_base = "auto"`).
- Desktop states: `error` from rollout `event_msg:error`; `turn_aborted` → idle;
  running older than 600 s without completion → unknown; rollout compressed to
  `.jsonl.zst` (Codex does this for inactive threads) → idle (or done-unread if in
  the App unread set). All Desktop sessions are
  tagged 来源推定 (source=vscode + originator heuristic; codex-tui also writes vscode).
- Desktop send / stop / effort disabled; accept/decline only explain (no approve via deck).
- Skills prefill a new thread via `codex://threads/new?path=&prompt=` — never submit; long prompts fail, not truncated.
- Knob event mapping unverified (`knob_map` in config). PTT path B not implemented (PTT = device-side KC_RALT).
- Sleep: display sleep → "Mac 已休眠" frame + pause; wake (clock jump) → reconnect + re-handshake, gestures dropped. tmux new-session off unless configured. herdr stub. L2/L3 are P1.
- Knob events that arrive as a non-`keyStateChanged` JSON method are recorded by devtest but not yet mapped by the daemon.

## On-device checklist → `devtest`
Stop the daemon first (only one process may own the CDC port). Then on the Mac:
```bash
python3 -m mk20deck usb-list                 # read-only enumeration
python3 -m mk20deck via-backup               # read-only VIA keymap backup (pip install hidapi)
python3 -m mk20deck devtest                  # guided, ~5 min; add --with-sleep for the sleep/wake step
python3 -m mk20deck devtest --auto           # non-interactive subset (USB, port, getInfo, display timing, VIA)
python3 -m mk20deck devtest --headless       # no terminal input: guided on the device screens (see below)
```
`--headless` shows each step on the sub-screen with the target key highlighted (done keys turn green),
waits for "press any key" (`--start-timeout`, default 180 s), then runs the guided steps within
`--overall-timeout` (default 300 s), each `--step-timeout` (20 s) — a step that times out is skipped and
recorded.  Questions become automatic checks: the HID-leak gate watches macOS HID activity
(`CGEventSourceSecondsSinceLastEventType`: keyDown / flagsChanged / systemDefined / scroll, no
permission needed, no characters, no source device) and correlates it (±0.35 s) with MK20 key events;
the visual check and PTT→IME are recorded as `needs_manual_confirmation`.  It sets the backlight
(default 80) because host mode starts dark; `progress.json` in the report dir shows the current step.

### Offline typing vs host mode
Out of the box the MK20 is in *offline* mode (protocol V1): each key types its keycap character
through the GD32 as an ordinary keyboard.  The first answered `getInfo` (daemon, devtest, ScreenKey)
switches it to *host* mode (V2): keys are reported to the host over CDC and stop typing, and the
backlight starts at 0.  **It stays in host mode until the MK20 is rebooted / replugged** — quitting
the daemon does not restore offline typing.  The daemon therefore sends `setBacklight`
(`[deck] backlight`, default 80; `false` to disable) right after every handshake, including after
replug and wake.
Covers PRD §10.1: USB roles (T113 CDC / GD32 VIA / DFU / FEL), CDC port + ScreenKey contention
(read-only lsof/pgrep), getInfo canvas + 0/1-based coords, SHOW_JPG ACK P50/P95 + full-screen fps
(pattern vs noise JPEG, q60/85/95 sizes), wrong-resolution reject, visual check, 20 keys with on-screen
highlight (offset/transposition analysis), knobs (raw JSON, suggested `[knobs]` map), HID-leak question
(P0 hard gate), PTT CDC visibility + hold time + IME, tap/double/long timing vs 600/350 ms,
unplug/replug latency, optional sleep/wake, VIA read-only audit (+ backup copy).
Writes beyond images need `--allow-device-writes` (only `--backlight N` today).
Report: `~/.config/mk20-agent-deck/devtest/<time>/devtest.{md,json}` with a suggested config block.

Backup / recovery (VIA restore, GD32 DFU, T113 TF image): see `RECOVERY.md`.

## Transport: ECM/TCP first, ACM fallback (P2 composite)

With the P2 firmware setup the MK20 boots as **USB ECM (network) + HID (keyboard + absolute pointer) + ADB**,
and KeyboardDevice is reached through an on-device PTY↔TCP bridge instead of the CDC-ACM port.
`find_ports()` therefore returns the first answering bridge address (`tcp://172.30.250.2:17890`, then the 1.0.x
`10.20.0.2`, then link-local `169.254.7.2`, all bound to the ECM interface), then any CDC-ACM
`/dev/cu.*` port (stock / rollback mode). `open_transport()` picks `TcpTransport` or `SerialTransport`.

- Addressing (firmware >= 1.1.0): device `172.30.250.2/24` on `usb0` (plus link-local alias `169.254.7.2/16`),
  Mac `172.30.250.1/24` (firmware 1.0.x: `10.20.0.2` / `10.20.0.1`).
  Fixed MACs (device `02:11:22:33:44:55`, host side `02:11:22:33:44:56`) keep macOS on the same `enN`
  interface and its saved settings. One-time Mac setup (no router, so the default route is untouched):
  `tools/mk20_host_net.py sync` on the p2 branch (sets only the MK20 service, matched by MAC, to the device's
  active subnet with TUN/overlap checks and automatic restore), or by hand
  `networksetup -setmanual "<ECM service, e.g. Serial>" 172.30.250.1 255.255.255.0`.
- Env: `MK20_TCP=host:port` (override) / `MK20_TCP=off` (ACM only); `MK20_ECM_IF=en13` or
  `MK20_ECM_MAC=…` to pick the interface explicitly.
- The bridge serves one client at a time and a new client replaces the old one (so a probe or `mk20ctl`
  over TCP kicks the daemon; it reconnects).

### VPN / proxy TUN compatibility (any TUN, not only mihomo)

TUN-mode proxies (mihomo/Clash, Surge, sing-box, some VPNs) install broad routes on a `utun` interface.
The deck daemon is TUN-proof by itself:

1. It finds the ECM interface by its fixed MAC and **binds its socket to it** (`IP_BOUND_IF`, macOS's
   `SO_BINDTODEVICE`), so packets to the MK20 leave via the ECM link even if a TUN owns the route.
2. On connect it runs a **self-check** (`route -n get <device-ip>`): if the route goes via `utun*` it logs a
   warning with the bypass to add, e.g. mihomo/Clash `tun.route-exclude-address: [172.30.250.0/24]`,
   Surge `tun-excluded-routes = 172.30.250.0/24` (+ `skip-proxy`), sing-box `route_exclude_address`,
   WireGuard/Tailscale: keep the subnet out of AllowedIPs. It **never modifies proxy configs**.
   Other tools (ping, ssh, `adb connect`) are not bound and do need that bypass.
3. Most TUNs already exclude RFC1918 ranges (this Mac's mihomo excludes `172.16.0.0/12` and `10.0.0.0/8`, so
   no change was needed). If `172.30.250.0/24` collides with a corporate network, the device also answers on link-local
   `169.254.7.2` (bind to the ECM interface; macOS otherwise routes 169.254/16 via the primary interface).
