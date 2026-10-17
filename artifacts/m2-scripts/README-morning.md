# Morning one-shot tests (prepared overnight 2026-10-18)

**Do not arm automatically.** User/agent decides after reading NOTES.

## What is already on the device (as of 03:05 SGT)

- One-shot `lunch.sh` + `mk20-add-adb.sh` (ACM+ADB add-beside) — crc was `2551892939`
- Prior `m2-adb.done` + `m2-adb-status.txt` (content unread; no Protocol A file-read)
- ACM healthy V2.32

## Clarification: 02:50 M2 window

**Did NOT collect.** Evidence:
- No `~/mk20-backup/m2/DEVICE_ONLINE`
- No `collect-*` dirs
- Waiter log: `02:47:29 ACM port gone` then ACM returned; never a new MK20 `adb devices` entry
- Only CD Watch `WATCH-SERIAL` remained on adb

So: gadget may have blipped; **adb never enumerated on the Mac**; nothing was pulled.

## Scripts in this folder

| File | Purpose |
|---|---|
| `lunch-morning-combo.sh` | One-shot: dump prior status, try ACM+ADB, log EP/gadget, 90 min watchdog → serial |
| `mk20-add-adb.sh` | Helper used by staged hook |
| Host waiters (Mac `~/mk20-backup/m2/`) | Prefer composite-aware waiter (ACM stay + new adb) |

## Optional later (not armed): Protocol A over adb-forward

Binary: `artifacts/m2-pty-bridge/ttygs_tcp_bridge` (static armhf).
Requires working adb first. See NOTES overnight addendum.
