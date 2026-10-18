# Key latency measurement (run AFTER the 24 h soak)

Script: `deck/tools/mk20_key_latency.py` (Mac side). Not run yet; refuses without `--yes-after-soak`.

## What it measures
| Leg | From → to | Clock | Source |
|---|---|---|---|
| A | GD32 report seen on T113 USB bus → router receive | T113 monotonic | usbmon text log (optional, `--usbmon`) |
| B | router receive → HID report written to the gadget | T113 monotonic | router key trace |
| C | HID report written → Mac receives it | Mac monotonic + offset | hidapi read on Mac; offset from min-RTT `/proc/timer_list` samples over adb |
| E2E | A+B+C (B+C without usbmon) | | |

Two phases: **idle** and **load** (N busy-loop shells + `dd` from `/dev/urandom` to tmpfs `/tmp/mk20`, with
`trap 'kill 0'` and a hard `sleep`-based kill). Output: p50/p90/p95/p99/max per leg, JSON in `--out`.
GD32's own matrix scan/debounce (QMK, a few ms) is before leg A and cannot be measured without
instrumenting GD32 firmware — out of scope (we never flash GD32).

## Prerequisites
1. Soak finished; release with the router installed (Phase 2 key routing).
2. Start the router with `MK20_KEYTRACE=1` (implemented: `mk20deck/keytrace.py`, hooked in `daemon.py`). It writes
   `"<CLOCK_MONOTONIC ns> <recv|routed|hid_written|agent|local> <code> <0|1>"` to `/tmp/mk20/keytrace.log`
   (tmpfs, rotates to `.1` at 256 KiB; `MK20_KEYTRACE_PATH` / `MK20_KEYTRACE_MAX` override). Off = no cost.
   Note: `hid_written` is currently inferred when the PTT/HID state flips; once the T113 HID writer lands it
   should emit the stage right after its `write()` to `/dev/hidg*`.
3. Device reachable via `~/mk20-backup/mkadb.sh` (existing bridge/adb). Respect the adb rules: do not run
   USB-changing scripts in the foreground; this tool does not change USB state.
4. Mac: `pip install hidapi`; know the T113 HID gadget VID/PID (`--vid/--pid`).
5. Optional leg A: start usbmon capture on the T113 into `/tmp/mk20/usbmon.txt` (debugfs read only), pass `--usbmon`.

## Run
```bash
cd deck
python3 tools/mk20_key_latency.py --yes-after-soak --n 50 --key A1 --vid 1d6b --pid 0104 --out key-latency.json
```
Press the named key ~50 times at ~0.5 s intervals per phase when prompted.
Before running confirm: Mac proxy/route rules are irrelevant here (no model traffic); never touch the
unrelated ESP32 or the CD watch adb device; no GD32 flash; no legacy commands.

## Acceptance (proposal)
Idle E2E p95 ≤ 15 ms, under load p95 ≤ 30 ms; PRD gestures (350 ms double / 600 ms long) keep a ≥ 10×
margin. Feed measured A/B/C into the simulator's latency injection to try the feel.
