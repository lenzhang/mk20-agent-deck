# P2 computer-use feasibility spike (2026-10-18)

**Branch:** `feature/p2-computer-use` (from `main` @ 59ec906, PRD v1.3 approved 01:18 SGT)  
**Method:** box-first read-only on T113 firmware dump (`/workspace/mk20-research/fw/rootfs`), KeyboardDevice V2.32 Ghidra decomp (`/workspace/ghidra/`), OSHWHub P4 notes. **No device writes yet.** Daemon on Mac left alone (watchdog → 01:45).  
**Rules honored:** no ESP32 ports, no official app, no flash, no proxy changes, prefer rollback-safe paths.

---

## Executive correction to PRD §14 assumptions

| PRD §14 claim | Spike finding |
|---|---|
| Live gadget is `1d6b:0104` **ECM+ACM** | Live product string is **"Serial"**; `setusbconfig serial` builds **ACM only** (`acm.usb0`). Same VID:PID is the Linux CDC-ACM convention, **not** ECM. |
| Stock path = CDC-ECM for VNC IP | Kernel strings / modules show **`usb_f_rndis` only** — **no `usb_f_ecm` / `usb_f_ncm`**. Gadget funcs are built-in (not `.ko`). |
| Endpoint budget "ECM 3 + ACM 3 + HID 1–2" | UAC1 stock script comments **"lack of ISO(IN) endpoint"** → sunxi UDC EP budget is tight. Live ACM alone uses ~3 EPs. |

**Implication:** §14.2 "VNC over CDC-ECM" is the right *Mac-native* target, but the **shipping kernel cannot do ECM today**. First network milestone is either (A) RNDIS + Mac-side RNDIS (fragile on modern macOS), (B) cross-build `usb_f_ecm` against Tina 5.4.61, or (C) RFB framed over ACM / Wi-Fi fallback while ECM is built. Do not treat ECM as already present.

---

## (a) Shell / code execution on T113 (no brick)

### What the rootfs already has

| Asset | Path / detail | Use |
|---|---|---|
| `adbd` | `/bin/adbd`, `/etc/init.d/adbd` (START=80), rc.d `S80adbd` / `K99adbd` | USB shell via FunctionFS |
| `setusbconfig` | `/data/setusbconfig` (configfs g1) | Modes: `none`, `mtp`, `adb`, `mass_storage`, `mtp,adb`, `mass_storage,adb`, `rndis`, `uac1`, `accessory`, `hid2048`, `hid`, `loopback`, `gser`, `printer`, `uac1,hid`, **`adb,hid`**, **`serial`** |
| Serial login | `etc/inittab`: `/bin/ash --login` on `tts/0`, `ttyS3`, `tty1` | Physical UART console (pads) |
| Boot hook | `etc/init.d/qt_app1`: copies `/mnt/SDCARD/overlayDir/*` → `/`, runs `/mnt/SDCARD/lunch.sh` if present | **Userland code exec without flashing** |
| File API over ACM | JSON `saveToFile` / `getFilesBySuffix` / `deleteFiles` / `setFileCRC` | Write/read paths from host **today** (replaceDataAtPosition opens arbitrary path + mkpath) |
| OTA | `S99swupdate_autorun` → `swupdate_cmd.sh` (fw_printenv `swu_*`) | How V2.32 was likely pushed; **do not use for spike** |
| Tools | `curl`, `nc`, `wget`, `adbd`; **no** dropbear/ssh/vnc/python/socat | Minimal userspace |

### Boot path that matters

1. `qt_app1` **stops and disables `adbd`**, stops `wpa_supplicant`, then makes `/data/{setusbconfig,KeyboardDevice,appLunch.sh}` executable.  
2. `qt_app2` procd-runs `/data/appLunch.sh` → `/data/KeyboardDevice`.  
3. KeyboardDevice `ConfigManager::updateUSBtype()`: default → `killall arecord/aplay; /data/setusbconfig serial` → opens `/dev/ttyGS0`.  
So stock = **ACM serial for protocol A**, adbd off.

### Ranked access options (safe → risky)

| # | Method | Brick risk | Needs human? | Notes |
|---|---|---|---|---|
| **1** | **`/mnt/SDCARD/lunch.sh` + `overlayDir`** via `saveToFile`, then **one reboot** | Low (delete lunch.sh / empty overlayDir to roll back; rootfs untouched) | Yes: reboot + confirm deck still answers getInfo | Preferred first shell. Probe-only script: dump `free`, `/proc/meminfo`, `ls /sys/class/udc`, `ls /sys/kernel/config/usb_gadget/g1/functions`, `cat /sys/.../usb_device`, `ls /dev/hidg* ttyGS* ttyS*`, `dmesg | tail`, append to `/mnt/SDCARD/probe.log` — **do not** change USB mode on first boot. |
| **2** | Same lunch.sh later enables **temporary `adb` or `adb,hid`**, starts `adbd`, restores `serial` on timer / next boot | Medium (ACM goes away while adb is up → deck daemon loses port) | Yes: stop daemon first; timed window | Stock script already composes `adb,hid`. Rollback: lunch.sh that only `setusbconfig serial` + kill adbd. |
| **3** | UART ash on `ttyS3` / `tty1` | None (read-only console) | Yes: probe pads, 3.3 V 115200 | Best for live debug; not automated. |
| **4** | `setusbconfig rndis` / custom composite from shell | Medium (same as 2) | After 1 or 3 | Needed for IP path. |
| **5** | `swupdate` / full firmware rewrite | **High** | Avoid | Only with verified recovery image + known-good TF. |
| **6** | `bootloader_jump` / GD32 DFU | **Out of scope** | Never for spike | Legacy JSON key — do not send. |

### Rollback checklist (before any device write)

1. Confirm daemon stopped / not holding `/dev/cu.usbmodem1101`.  
2. `saveToFile` a copy of any existing `/mnt/SDCARD/lunch.sh` and `overlayDir` listing to Mac backup.  
3. Write **probe-only** lunch.sh; reboot once; pull `probe.log` via `getFilesBySuffix` or TF reader.  
4. Only then consider a second lunch.sh that flips gadget mode, with a **boot-count or time-boxed** restore to `serial`.

**Do not** kill KeyboardDevice from the host without a restore path — it owns ACM.

---

## (b) USB gadget capabilities & endpoint budget

### Stack

- **Configfs** gadget `g1` (not legacy `g_*` modules as the live path).  
- UDC: `ls /sys/class/udc` (script fallback `4100000.udc-controller`); kernel strings reference `sunxi_usb_udc`.  
- Built-in functions seen in kernel: `usb_f_acm`, `usb_f_rndis`, `usb_f_hid`, `usb_f_mass_storage`, `usb_f_fs` (FFS/ADB), `usb_f_uac1`, `usb_f_serial`, plus accessory/printer/loopback paths in script.  
- **No ECM/NCM** in this build.

### Stock composites that already work in script

- `serial` → ACM (`1d6b:0104`) — **production**.  
- `adb`, `hid`, **`adb,hid`**, `uac1,hid`, `rndis`, `mass_storage[,adb]`, …

### Target composite for §14 (desired)

`ACM (protocol A) + CDC-ECM (VNC IP) + HID keyboard + HID absolute pointer`

### Realistic composites on *this* kernel

| Composite | Feasible now? | EP rough cost | Mac notes |
|---|---|---|---|
| ACM alone | Yes (live) | ~3 (INT-IN + BULK-IN + BULK-OUT) | Deck protocol |
| ACM + HID kbd | Likely (custom setusbconfig; not stock) | ~4 | Matches "type via T113 HID" |
| ACM + HID kbd + HID abs pointer | Maybe | ~5 | Needs live UDC EP count from probe |
| `adb,hid` | Stock script | FFS≈2–3 + HID 1 | Shell + kbd; **drops ACM** |
| RNDIS alone | Stock | ~3 | Needs Mac RNDIS (not native) |
| RNDIS + 2×HID | Custom | ~5 | No ACM |
| ACM + RNDIS + 2×HID | Unlikely on sunxi UDC | ~8 | Almost certainly over EP budget |
| Anything + ECM | **Blocked** until `usb_f_ecm` built | ECM≈3 | Preferred for macOS |

**UAC1 script already documents missing ISO IN** — treat EP budget as a **hard gate**. First device probe must report:

```text
ls /sys/class/udc
# after binding a known config:
find /sys/kernel/config/usb_gadget/g1 -name 'ep*' 
cat /sys/kernel/debug/usb/devices   # if debugfs mounted
```

### HID descriptors

- Stock `hid` mode = boot keyboard, 8-byte report (inline in `setusbconfig`).  
- Stock `hid2048` = vendor 2048-byte.  
- **Absolute pointer:** borrow OSHWHub idea but rewrite as **Generic Desktop Mouse** with logical max 0–32767 (PRD §14.7) — **not** Digitizer TouchScreen (macOS pointer semantics differ). Two `hid.usb0` / `hid.usb1` functions (kbd + mouse) as in script’s dual-hid teardown paths.

### GL852G hub

Device already enumerates through an internal hub; composite changes must be tested for partial enumeration / macOS sleeping the port. Spike does not touch hub firmware.

---

## (c) VNC client on T113 + Mac Screen Sharing

### Device constraints

- **SoC:** Allwinner T113, 2×A7, Tina Linux 5.4.61 (`tina.syk…` / `t113-pro/generic`, **glibc**).  
- **RAM:** ~128 MB class (sys_config dram block; confirm with `/proc/meminfo` on device). KeyboardDevice (Qt) already resident — free RAM for a client likely **tens of MB**, not hundreds.  
- Rootfs: **no** libvnc / turbojpeg / python. Have `curl`/`nc` only.  
- Display path already does JPEG decode + ARGB→RGB565 for the key LCD — JPEG encode for cloud upload can reuse similar code later; VNC client is separate.

### Client options

| Option | Fit | Notes |
|---|---|---|
| **libvncclient** (LibVNCServer) | Best API | Cross-compile on box once armhf toolchain + Tina sysroot available; strip unused encodings |
| **Tiny / custom RFB** | Smallest | Implement Auth + FramebufferUpdateRequest + ZRLE/Hextile only |
| Full desktop viewer | No | No X11 on device |

### Cross-compile on the box

- Box currently has **no** `arm-linux-gnueabihf-gcc` installed (as of spike time).  
- Need: armhf glibc toolchain matching Tina, or build against extracted rootfs sysroot (`--sysroot=/workspace/mk20-research/fw/rootfs`).  
- Tina SDK / kernel headers for `usb_f_ecm.ko` are a **separate** larger ask (kernel module build).  
- Deliverable for later milestone: static `vnc-pull` binary → drop via `overlayDir` or `saveToFile` + lunch.sh `chmod +x`.

### macOS Screen Sharing (RFB) — what to plan for

| Topic | Practical choice for T113 |
|---|---|
| Protocol | RFB 3.8 |
| Auth | Prefer **VNC password** (“VNC viewers may control screen with password”) over Apple ARD/DH — simpler embedded client. ARD is a stretch goal. |
| Encodings | Prefer **ZRLE** or **Hextile**; avoid Raw at Retina sizes. Request **scaled** desktop if server supports it; else client-side scale to ≤1280 long edge before JPEG-out. |
| Encryption | System Screen Sharing may offer TLS/ARD crypto; many embedded clients use unencrypted RFB on link-local only — **acceptable only on USB point-to-point**, never on Wi-Fi without explicit opt-in (PRD R-CU-3). |
| Permission | Screen Recording / Remote Management must be enabled on the Mac; agent must not log the password. |
| Lock screen | Per PRD: **HID only** when locked; VNC input may be blocked. |

### Bandwidth sanity (ECM/RNDIS @ USB HS share)

- 1280×720 RGB24 raw ≈ 2.7 MB/frame; at 2 fps raw ≈ 5 MB/s — too fat shared with ACM.  
- ZRLE/JPEG’d tiles at 1–2 fps + dirty-region requests is the budget that matches R-CU-5 (P95 ≤ 800 ms to JPEG-ready).

---

## (d) GD32 keyboard / mouse fallback (`/dev/ttyS1`)

### Role

- T113 ↔ GD32 UART: `STM32CommunicationObject` on **`/dev/ttyS1`** (not the host ACM).  
- In **V1 (offline)** mode, local key handling calls `request_upload_key` / `request_upload_string` → GD32 emits **USB HID** to the Mac.  
- In **V2 (host)** mode after `getInfo`, keys are reported to the Mac as JSON `keyStateChanged`; GD32 HID typing is **not** the host-mode path for matrix keys (PTT KC_RALT experiment confirmed no HID effect in host mode).

### Frame format (from `parseByte` / `packFrame`)

```text
AA 55 | sum8 | len | (~len) | payload[len] | F5 5F
```

- `sum8` = low 8 bits of sum(len byte + payload).  
- Trailer `F5 5F`.  
- Known command bytes seen in decomp: **`0x18`** = `request_mouse_control` (payload = mouse report bytes); **`0x1A`** = `request_upload_string` (UTF-8, max ~0xEE bytes after trunc).  
- `request_upload_key(modifiers?, keycode)` packs a short key payload (exact layout still to dump from packFrame constants — treat as VIA/QMK-style key send).  
- Also present: `dynamic_keymap_set_keycode(s)`, `dynamic_keymap_macro_*`, **`request_bootloader_jump`** — **never call** from spike.

### Fallback policy (aligned with PRD R-CU-2)

- **Preferred input:** T113 `f_hid` keyboard + absolute pointer (no GD32 dependency).  
- **GD32 fallback:** keyboard / relative mouse via ttyS1 if gadget HID missing.  
- **Do not** send host JSON `controlMouse` / `bootloader_jump` / `qmk_string`.  
- Absolute pointer on GD32 is unlikely (QMK mousekeys are relative); absolute stays on T113 HID or VNC PointerEvent.

### Open detail for next RE pass

- Exact `upload_key` payload bytes (modifier + keycode endianness).  
- Whether GD32 ACKs frames (parseByte collects; need `processData` dispatch).  
- Safe to probe with **read-only** UART sniffer once shell exists (`cat /dev/ttyS1` while pressing keys in V1) — do not write bootloader commands.

---

## Recommended plan & first milestone

### Plan (ordered)

1. **M1 — Probe (this spike’s next action, needs one human reboot)**  
   Write probe-only `/mnt/SDCARD/lunch.sh` via ACM `saveToFile`, reboot, collect `probe.log`. No USB mode change.  
2. **M2 — Shell window**  
   Timed `adb` (or UART) with automatic restore to `serial`; confirm `adbd` shell; snapshot EP/UDC facts live.  
3. **M3 — HID composite**  
   Custom `setusbconfig` mode: `acm,hid,hid` (kbd + abs mouse) **or** temporary `adb,hid` for bring-up; Mac `ioreg` + click test.  
4. **M4 — Network path decision**  
   Measure RNDIS on this Mac **or** start `usb_f_ecm` cross-build; pick one IP transport.  
5. **M5 — RFB client MVP**  
   Cross-compile minimal VNC password + ZRLE puller; 1 fps scaled frame to JPEG on device.  
6. **M6 — Arbitration / e-stop** per PRD R-CU-2…4 (after M3+M5).

### First milestone (concrete)

**M1: read-only boot probe via `lunch.sh` (no gadget change).**

**Human steps when ready (daemon must be stopped first):**

1. Stop deck daemon / any holder of `/dev/cu.usbmodem1101`.  
2. Agent writes (via protocol A `saveToFile`) `/mnt/SDCARD/lunch.sh` with contents that only append diagnostics to `/mnt/SDCARD/probe-YYYYMMDD.log` and exit 0; also backups any pre-existing lunch.sh to `lunch.sh.bak`.  
3. User power-cycles the MK20 once.  
4. After re-enumeration, agent `getInfo` + `getFilesBySuffix` (or TF read) to pull the probe log.  
5. Report: MemAvailable, UDC name, existing gadget functions, whether `usb_f_ecm` appears, `/dev/hidg*` presence, tty list.  
6. Rollback: `deleteFiles` / saveToFile empty remove of `lunch.sh` (restore `.bak` if any).

**Exit criteria:** probe log on box artifacts; EP/RAM numbers recorded; go/no-go for M2 adb window.

### Explicitly deferred / blocked until M1

- Any `setusbconfig` other than observing current `serial`.  
- Flashing GD32 or T113.  
- Opening official ScreenKey app.  
- Installing Mac RNDIS drivers.  
- Cross-compiling VNC (needs toolchain install on box — can start in parallel on box without device).

---

## Artifact index (box)

- Firmware rootfs: `/workspace/mk20-research/fw/rootfs/` (`data/setusbconfig`, `etc/init.d/qt_app{1,2}`, `etc/inittab`, `sbin/swupdate_cmd.sh`)  
- Decomp: `/workspace/ghidra/{stm32,pack,save,kbd,keys}.c`  
- Prior protocol notes: `deck/NOTES-protocol-re-2026-10-17.md`  
- OSHW P4 borrow: `/workspace/oshw-p4-usbdisplay/NOTES.md` + `fw/main/tusb/usb_descriptors.h`  
- PRD: `PRD-mk20-agent-deck.md` §14 (approved v1.3 on main @ 59ec906)

---

*Spike author: Grok Bot executor · 2026-10-18 ~01:25 SGT · device not modified.*
