The `claude-opus-5-5-max.md` review is empty (it errored out); the `gpt-5.6` review is substantive and I'll note where I concur or diverge. My findings below are independent — I traced the actual staged copies in `~/mk20-backup/m2/`, the prior 02:47 attempt script, and the M1 probe, which surface a few things the prior review didn't.

# MK20 P2 overnight review — read-only

## Verdict (TL;DR)

No reviewed script flashes firmware, touches `/rom`, `swupdate`, or GD32, so the **SoC itself cannot be bricked** by these hooks. But there is a real path to leaving the device **USB-dark across power-cycles** (no ACM, no adb), and the severity depends entirely on *which* of three script variants is actually armed on the device — which I could not confirm from artifacts. **Confirm the armed variant by CRC before any morning power-cycle.** The FunctionFS ordering fix is correct and worth keeping; the `qt_app2` race is likely *not* the primary cause of the 02:47 failure.

A key fact driving most blockers: **`/data/setusbconfig` lives on the UDISK/overlay partition (non-FAT, likely ext4), not on the FAT `/mnt/SDCARD`.** The M1 probe confirms `/data/setusbconfig` is a real 15530-byte file and that `/` is `overlayfs:/overlay` on `/dev/by-name/UDISK`, while SDCARD is a separate 8 MB FAT at `mmcblk0p1`. So anything that corrupts `/data/setusbconfig` is **not** recoverable by the documented "pull TF card, delete files on SDCARD" step.

---

## Three divergent variants exist — this is the root problem

| Variant | Where | Wrapper safety | Watchdog |
|---|---|---|---|
| **V-old** (`crc 2551892939`) | described in NOTES "Device state left for morning" | full teardown, no wrapper | 3 h |
| **V-repo** (focus files, working tree) | `deck/m2-scripts/lunch-m2test.sh` | wrapper **always blocks `serial`, no flag, no self-heal** | 90 min |
| **V-staged** (`crc 3052787980`, "self-diag hook 03:15+") | `~/mk20-backup/m2/lunch-m2test.sh` | **flag-gated** `m2-window.flag` + self-heal | 90 min |

The repo focus files you pointed me at are **V-repo** — the *less safe* revision. The Mac-side staged copy (**V-staged**) is strictly safer (flag-gate + self-heal + `restore_setusbconfig`). The repo `DIAGNOSIS-adb-enum.md` is also the older text — it lacks the "Critical safety fix (same night)" section that the staged `DIAGNOSIS` has. So the working tree was never updated with the overnight safety fix.

I cannot compute CRC32 in this mode, so I cannot prove which variant maps to `3052787980` or what is physically on the device. The write-verify provenance is itself inconsistent: `upload_m2test.py` asserts `b"10800" in lunch` (a 3 h watchdog), but **neither** V-repo nor V-staged contains `10800` (both use `5400`), and `m2test-write-verify.json` has a `note` field the current uploader never writes (it writes `listing`). So the uploader, the verify JSON, and the on-disk scripts do not line up. **Treat "which hook is armed" as unknown.**

---

## 1) Blockers (fix/confirm before the morning power-cycle)

### B1 — The persistent `setusbconfig` wrapper can leave the device with no ACM across reboots, and `/data` isn't FAT-recoverable

In **V-repo**, the wrapper has no escape hatch:

```94:111:deck/m2-scripts/lunch-m2test.sh
install_serial_block_wrapper() {
  # Prevent KD/updateUSBtype from wiping composite during the test window
  if [ -x /data/setusbconfig ] && [ ! -f /data/setusbconfig.real ]; then
    cp -f /data/setusbconfig /data/setusbconfig.real
    cat > /data/setusbconfig << 'WRAP'
#!/bin/sh
LOG=/mnt/SDCARD/mk20-gadget.log
echo "setusbconfig-wrap arg=$* $(date 2>/dev/null)" >> "$LOG"
if [ "$1" = "serial" ]; then
  echo "BLOCKED serial during m2 window" >> "$LOG"
  exit 0
fi
exec /data/setusbconfig.real "$@"
WRAP
```

On composite success the script **leaves this wrapper installed and `qt_app2` stopped for 90 minutes**:

```143:161:deck/m2-scripts/lunch-m2test.sh
  if /data/mk20-add-adb.sh; then
    echo "RESULT=composite-ok (acm+adb); qt_app2 left STOPPED for adb window" >> "$STAMP"
    ...
    (
      sleep 5400
      echo "watchdog 90m fire" >> "$LOG"
      restore_serial
```

Only `restore_serial` (same-boot fail path, or the 90-min watchdog) ever restores `/data/setusbconfig.real`. A **power-cycle inside that window** kills the watchdog process but leaves the wrapper on disk. Next boot: KeyboardDevice runs `setusbconfig serial` → wrapper returns `exit 0` → **gadget never built → no ACM, no `/dev/ttyGS0`**, forever, because `lunch.sh` already self-deleted and nothing restores `.real`. Recovery requires editing `/data/setusbconfig` on the **ext4 UDISK partition**, which `RECOVERY.md §4.4`/`§5.5` does **not** describe (it only covers deleting `lunch.sh`/`mk20-*.sh` from the FAT SDCARD).

**V-staged softens but does not eliminate this.** Its wrapper self-heals only when `m2-window.flag` is *absent*, and the flag is `touch`ed on the **persistent FAT SDCARD**:

```90:118:/Users/dev/mk20-backup/m2/lunch-m2test.sh
install_serial_block_wrapper() {
  # Flag-gated: without FLAG, wrapper self-heals (safe across unexpected reboot)
  touch "$FLAG"
  ...
if [ ! -f "$FLAG" ]; then
  ... restore real setusbconfig ...
fi
if [ "$1" = "serial" ]; then
  echo "BLOCKED serial during m2 window" >> "$LOG"
  exit 0
fi
```

After a mid-window power-cycle, `restore_setusbconfig` (which does `rm -f "$FLAG"`) never ran, so **the flag persists → self-heal is skipped → serial still blocked → still no ACM.** The difference is only in recovery cost: V-staged is fixable by deleting one file on the FAT SDCARD (Mac-mountable), V-repo needs ext4 surgery. Either way the "self-heal on reboot" guarantee the comments claim is **false for the realistic power-cycle case**, because the flag lives on persistent storage rather than tmpfs.

Concur with gpt-5.6 B2/B3 on the wrapper; the additional points here are (a) `/data` is not on the FAT partition so V-repo recovery is genuinely hard, and (b) **V-staged's flag-gate doesn't actually save you on a power-cycle** because the flag is persistent.

**Minimum fix before arming:** put the window flag on a tmpfs path (e.g. `/tmp/m2-window.flag`, or `/dev/shm`) so a power-cycle clears it and the self-heal truly fires; *or* drop the persistent wrapper entirely (since `qt_app2`/KD are already killed, the race window is small — see B/▲below); *and* add the wrapper/`.real` restore + flag deletion to `RECOVERY.md`.

### B2 — The "a second power-cycle always restores stock serial" instruction is wrong and will cause the brick

`NOTES-computer-use-spike.md` tells the morning operator:

> Morning action for user: one power-cycle … **A second power-cycle always restores stock serial (hook already gone).**

Given B1, a second power-cycle *during the 90-min window* is exactly the action that strands the wrapper and kills ACM. A sleepy operator who sees an ambiguous result (adb not showing, or `getInfo` not answering — see R1) is likely to do precisely this. This guidance must be corrected to: **do not power-cycle again until the watchdog has restored serial (log line `watchdog 90m fire`) or you have deleted the window flag / restored `.real`.**

### B3 — Documented rollback invokes the wrapper and silently fails

`RECOVERY.md §4.2` adb-path rollback runs `… /data/setusbconfig serial …`. If the wrapper is installed, that call is swallowed (`exit 0`) and ACM is never rebuilt, and `.real` is never restored:

```150:151:deck/RECOVERY.md
adb shell 'rm -f /mnt/SDCARD/lunch.sh /mnt/SDCARD/mk20-add-adb.sh /data/mk20-add-adb.sh; /data/setusbconfig serial; killall adbd'
```

The Protocol-A delete list in `§4.2`/the NOTES rollback also omits `/data/setusbconfig`, `/data/setusbconfig.real`, and `/mnt/SDCARD/m2-window.flag`. Rollback must **restore `.real` first**, then run serial, then verify UDC non-empty + `/dev/ttyGS0` + ACM enumerates. (Concur with gpt-5.6 B3.)

### B4 — Confirm the armed variant before power-cycling

Because of the three-way divergence and inconsistent verify metadata, you do not currently know whether the device holds V-old, V-repo, or V-staged. The consequences differ by variant (3 h vs 90 min exposure; ext4-hard vs FAT-easy recovery). **Action:** while ACM is healthy, pull CRCs for `/mnt/SDCARD/lunch.sh`, `/mnt/SDCARD/mk20-add-adb.sh`, `/data/mk20-add-adb.sh` via the `setFileCRC` probe in `RECOVERY.md §4.1` and compare against known-good hashes of the intended script before trusting any "self-recovering" claim.

---

## 2) Risks (non-blocking)

- **R1 — During a successful composite, Protocol A (`getInfo`) is dead.** The composite-ok path leaves `qt_app2` stopped and does **not** restart KeyboardDevice. ACM (`acm.usb0`) still enumerates so `cu.usbmodem*` appears, but KD is the process that serves the JSON protocol on `/dev/ttyGS0`. So the morning checklist step "verify **both** `cu.usbmodem* getInfo` **and** `adb shell`" cannot pass for `getInfo` while the window is open — only adb will work. This is an expectation bug, not a brick.

- **R2 — `mk20-add-adb.sh` reports success on device-side bind only, which is not host enumeration.** The two prior adb attempts both produced device-side markers yet **never enumerated on the Mac**. "composite-ok" is declared when `g1/UDC` is non-empty; that is necessary but not sufficient. Treat success as *Mac sees adb*, not *UDC bound*. (Concur with gpt-5.6 B4.)

- **R3 — Composite enumeration on macOS is unproven and is a plausible independent failure cause.** `mk20-add-adb.sh` adds `ffs.adb` beside `acm.usb0` but **leaves VID/PID at `1d6b:0104`** and only edits the product string:

```31:33:deck/m2-scripts/mk20-add-adb.sh
[ -e "$G/configs/c.1/ffs.adb" ] || ln -s "$G/functions/ffs.adb" "$G/configs/c.1/ffs.adb"
# Keep acm.usb0 if present
echo "Serial,ADB" > "$G/strings/0x409/product" 2>/dev/null || true
```

  It never sets `bDeviceClass`/IAD for a multi-interface composite. A CDC-ACM-classed device descriptor with an extra FFS interface and the single-function VID/PID can lead macOS to bind CDC-ACM and ignore/mis-parse the adb interface. This may be *the* reason adb won't appear even with correct FFS ordering — and it's not addressed by the race fix.

- **R4 — `mk20-add-adb.sh` false-success holes.** `echo "" > "$G/UDC"` (line 19) is `|| true`; the mount (lines 22–28) and the `ln -s` (line 31) aren't verified; and the final gate only checks `ep1`:

```52:64:deck/m2-scripts/mk20-add-adb.sh
if [ ! -e /dev/usb-ffs/adb/ep1 ]; then
  log "FAIL timeout waiting ffs ep1/ep2 ..."
  exit 5
fi

echo "$UDC_NAME" > "$G/UDC" 2>>"$LOG"
sleep 0.8
BOUND=$(cat "$G/UDC" 2>/dev/null)
```

  If unbind silently failed, `BOUND` can read back the *old* UDC name → false success. Harden: require UDC empty after unbind, require the `ffs.adb` link, require both `ep1`+`ep2`, and re-verify `adbd` alive after bind. (Concur with gpt-5.6 B4.)

- **R5 — Diag dump clobbers the thing it's trying to preserve.** The header write to `$DUMP` (prior `m2-adb-status.txt`) is overwritten by `dump_diag_to_acm`'s `} > "$DUMP"`, so the previously unreadable status is lost from the ACM dump. Append (`>>`) instead. (Concur with gpt-5.6.)

- **R6 — 90-minute window is long.** A false-positive success leaves no Protocol A and the wrapper armed for 90 min. 5–10 min is plenty for a first enumeration probe and shrinks the brick window in B1/B2.

- **R7 — Stale helper / insufficient settle.** The helper copy is `|| true` (lines 29–30 of `lunch-m2test.sh`), so a stale `/data/mk20-add-adb.sh` could run; and the fixed `sleep 5` after KD-seen may fire before `setusbconfig serial` finished building `acm.usb0`, in which case add-beside yields adb-only (ACM lost), contradicting the "keep ACM" goal.

---

## 3) What looks sound

- No flash / `/rom` / `swupdate` / GD32 / ESP32 operations anywhere in the reviewed scripts; the "never bricks the SoC" claim holds.
- One-shot self-delete **before** USB surgery (`rm -f /mnt/SDCARD/lunch.sh` and `/data/lunch.sh`, lines 26–27) correctly prevents the hook from re-running on later boots.
- The **FunctionFS ordering** in `mk20-add-adb.sh` is correct and the real improvement over the stock path: unbind → mount functionfs → link → start `adbd -D` → **wait for `ep1`/`ep2`** → bind UDC. Stock `setusbconfig adb` binds UDC before `adbd` writes descriptors, which is the inverted order.
- Stopping the respawning owner of `/dev/ttyGS0` (`qt_app2`/KD) before gadget surgery is the right idea.
- `qt_app2 stop` is not `disable`, so the service returns on a clean reboot — no persistent UI loss from that alone. The persistent hazard is the wrapper, not the service state.
- Configfs links / FFS mounts / UDC bind are volatile and clear on a true power-cycle.
- `RECOVERY.md` correctly prioritizes a whole-TF `dd` image and explicitly flags that **MK20 FEL entry is unverified** (§5.1) — appropriate caution for the #4 kernel path.

---

## 4) Suggested morning steps (changes from the current checklist)

1. **Do not power-cycle on the assumption the fix is deployed.** First, with ACM healthy, CRC-compare the staged `lunch.sh`/helper against the intended script (B4, `RECOVERY §4.1`).
2. If the armed variant is V-old or V-repo, or uncertain: **disarm via Protocol A, then power-cycle once to confirm stock ACM + `getInfo`.** Don't test on an unverified hook.
3. Before re-arming any test: move the window flag to tmpfs (or drop the wrapper), require both `ep1`+`ep2`, treat success as *Mac sees adb*, shorten the watchdog to 5–10 min, and patch `RECOVERY.md` to restore `.real`/delete the flag.
4. Expect that during a successful composite window, **`getInfo` will not answer** (KD stopped) — verify adb only, and don't interpret dead `getInfo` as failure (R1).
5. **Do not** attempt ADB+HID, the PTY bridge, PPP, ECM `insmod`, or any kernel work on this first cycle.
6. Correct the NOTES line that says a second power-cycle is always safe (B2).

---

## Direct answers

**A) Can the morning hook brick the device / is one-shot + serial-restore + watchdog sufficient?**
It cannot brick the SoC (no flash). It **can** leave USB permanently dark across power-cycles. One-shot self-delete is sound. Serial-restore and the watchdog are **not sufficient**, because both only run on the same boot — a power-cycle inside the window kills the watchdog and strands the `setusbconfig` wrapper. In V-repo the wrapper never self-heals (recovery needs ext4 UDISK surgery, not covered by RECOVERY). In V-staged the self-heal is gated on a flag that lives on persistent SDCARD, so it also does **not** fire after a power-cycle (recovery is at least a FAT-file delete, but still undocumented). So yes, there are concrete paths that leave the `setusbconfig` wrapper stuck, `qt_app2`/KD effectively non-serving, and the UDC unbound after reboot.

**B) Is the `qt_app2`/KeyboardDevice race the correct root cause for the 02:47 ACM-without-adb failure?**
Probably not the primary cause. The actual 02:47 script (`~/mk20-backup/m2/lunch.sh`) **already stopped `qt_app2` and killed KD before switching the gadget**, then called stock `/data/setusbconfig adb` (a full ACM teardown) and started `adbd` *after*. The stronger explanation is the **inverted FunctionFS order in stock `setusbconfig adb`** (UDC bound before `adbd` wrote descriptors) plus the full ACM teardown — exactly what the new `mk20-add-adb.sh` fixes. The FFS order analysis itself is correct; its *ranking* of causes is off. Remaining holes: the diagnosis doesn't account for (i) the 02:47 script having already stopped the supervisor, and (ii) the **macOS composite-descriptor/IAD** issue (R3), which the race fix does nothing for. The new fix addresses the ordering (good) and adds defense-in-depth against a respawn, but has not been shown to produce **host-visible** adb, so "fixed" is unproven. (gpt-5.6 reached the same "plausible, not proven" conclusion.)

**C) Ranked design (Wi-Fi excluded) — critique; does #1 depend on proving adb first?**
- **#1 (ADB + combined HID + PTY bridge):** Yes, it is **entirely gated on proving adb enumerates on macOS first** — the exact thing that has failed twice — and the NOTES says so. Beyond that it stacks several unproven assumptions: that KeyboardDevice (QSerialPort) will accept a bind-mounted PTY at `/dev/ttyGS0`; that the deck daemon can migrate off `cu.usbmodem*` to `localhost:17890`; and the screen path conflates two different things — stock `adbd` has **no `reverse`** (the addendum itself found reverse-count 0), so a device→Mac VNC client needs either an adbd replacement or a host-push architecture. Note the adbd-replacement idea is riskier than stated: Tina userspace is **glibc**, AOSP `adbd` is built against **bionic**, so a dropped-in AOSP adbd likely won't even execute. Proving ACM+ADB is necessary but **not sufficient** for ADB+HID (different gadget composition, different EP use, new HID descriptor). Recommend demoting #1 to "preferred gated target," not "do now."
- **#2 (ACM + ADB add-beside):** This is the right first morning step and should effectively be rank #1 operationally. Caveats: it still inherits R1 (getInfo dead while KD stopped) and R3 (composite may not enumerate on macOS).
- **#3 (PPP over ACM):** Correctly low. Device has no `pppd`; Protocol A and PPP can't share one raw tty without a mux/time-slice; Mac-side `pppd` presence is not an interop result. Research-only.
- **#4 (ep3-iso→ep3-int kernel patch):** Properly gated behind TF backup + FEL, but two cautions. First, the **FEL recovery precondition is unverified on this unit** (RECOVERY §5.1) — don't treat #4 as reachable until FEL entry is physically proven and a whole-card `dd` is validated. Second, there's an **endpoint-math error** in the NOTES: after freeing one INT you have two INT total, so "ACM + ADB + combined HID **+ optional ECM**" still needs 3 INT and does **not** fit; valid 2-INT sets are ACM+ADB+HID, ACM+ADB+ECM, or ADB+HID+ECM. (gpt-5.6 flagged the same EP error.)

**Bottom line:** the single most important action is **B4 — confirm which hook is armed before power-cycling**; then fix the flag/wrapper persistence (B1/B2) and the rollback/recovery docs (B3) before re-arming. The FFS fix is good and worth keeping; the race is likely a secondary cause behind FFS ordering + macOS composite descriptors.

Switched to Claude Opus 4.8

Claude Opus 5.5 hit a safety filter, and the conversation was automatically switched to Claude Opus 4.8. Start a new conversation to continue with Claude Opus 5.5, or continue this conversation with Claude Opus 4.8.


