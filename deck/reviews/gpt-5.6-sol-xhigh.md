# Independent read-only review

## Executive verdict

- No path here can permanently brick the T113 hardware: the scripts do not flash firmware or modify `/rom`.
- However, the revised hook can leave T113 USB unavailable across power-cycles because it persistently replaces `/data/setusbconfig`.
- The repository indicates the device may still be armed with the older, unfixed hook. Do not power-cycle expecting the new race fix until the on-device version is confirmed.
- The `qt_app2` race is plausible, not proven. The FunctionFS ordering correction is sound, but helper validation still has holes.
- Ranked design #1 absolutely depends on reliable ADB, and proving ACM+ADB is necessary but not sufficient for ADB+HID.

## 1. Blockers — fix before an intentional M2 power-cycle

### B1. The hook recorded as staged is not the revised hook

The device-state record describes a 3-hour watchdog and the old add-beside behavior:

```257:264:deck/NOTES-computer-use-spike.md
| Staged boot hook | **One-shot self-recovering** `lunch.sh` (crc `2551892939`) + `/mnt/SDCARD/mk20-add-adb.sh` |
| Hook behaviour | Deletes itself **before** USB change → next power-cycle is always plain serial; on this boot: wait for KeyboardDevice, add `ffs.adb` beside existing `acm.usb0`, start `adbd`; on failure immediately `setusbconfig serial`; 3 h watchdog also restores serial |
```

The revised working copy instead stops `qt_app2`, installs a wrapper, and uses a 90-minute watchdog:

```122:132:deck/m2-scripts/lunch-m2test.sh
  if [ -x /etc/init.d/qt_app2 ]; then
    /etc/init.d/qt_app2 stop >> "$LOG" 2>&1 || true
  fi
  killall KeyboardDevice 2>/dev/null || true
  killall appLunch.sh 2>/dev/null || true
  sleep 1
  echo "qt_app2/KD stopped; remaining KD=$(pgrep -f KeyboardDevice)" >> "$LOG"
  echo "phase=kd_stopped" >> "$STAMP"

  install_serial_block_wrapper
```

The artifact copy also remains the old implementation. Therefore the new fix should be treated as not deployed until verified by CRC/hash or restaged.

Consequence: the currently armed test may repeat the same race and old FunctionFS timing problem.

### B2. Persistent `setusbconfig` wrapper breaks the one-shot safety guarantee

The revised hook copies and overwrites a persistent boot-critical executable:

```94:109:deck/m2-scripts/lunch-m2test.sh
install_serial_block_wrapper() {
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
    chmod +x /data/setusbconfig
```

On success that wrapper remains installed for 90 minutes:

```144:158:deck/m2-scripts/lunch-m2test.sh
  if /data/mk20-add-adb.sh; then
    echo "RESULT=composite-ok (acm+adb); qt_app2 left STOPPED for adb window" >> "$STAMP"
    # ...
    (
      sleep 5400
      echo "watchdog 90m fire" >> "$LOG"
      restore_serial
      [ -x /etc/init.d/qt_app2 ] && /etc/init.d/qt_app2 start >> "$LOG" 2>&1 || true
      dump_diag_to_acm
    ) &
```

A power-cycle during that window kills the watchdog but leaves the wrapper. On the next boot, KeyboardDevice calls `setusbconfig serial`; the wrapper returns success without constructing ACM. Thus “next power-cycle always plain serial” is false for this revision.

There is also a narrow power-loss window while `cat > /data/setusbconfig` is truncating the original path.

For the morning probe, the safest fix is to avoid persistent replacement entirely. Since `qt_app2` is intentionally stopped, verify it and KeyboardDevice are actually gone and run the short test without a wrapper. If a wrapper is essential later, use a temporary bind mount backed by `/tmp`, so reboot exposes the untouched original file.

### B3. Documented rollback does not recover the wrapper

The ADB rollback calls the wrapper, which silently blocks `serial`, and never restores `.real`:

```145:151:deck/RECOVERY.md
If the device is in an **adb** window instead of ACM:

```bash
adb shell 'rm -f /mnt/SDCARD/lunch.sh /mnt/SDCARD/mk20-add-adb.sh /data/mk20-add-adb.sh; /data/setusbconfig serial; killall adbd'
```
```

The Protocol-A rollback list similarly omits `/data/setusbconfig.real`.

Before the revised hook is deployable, recovery must restore the real executable first, then run `setusbconfig serial`, and verify:

- `g1/UDC` is non-empty.
- `/dev/ttyGS0` exists.
- `qt_app2` and KeyboardDevice are running.
- ACM enumerates on the Mac.

### B4. `mk20-add-adb.sh` can report false success

Unchecked operations:

```18:32:deck/m2-scripts/mk20-add-adb.sh
log "BEGIN udc=$UDC_NAME links=$(ls "$G/configs/c.1" 2>/dev/null | tr '\n' ' ')"
echo "" > "$G/UDC" 2>/dev/null || true
sleep 0.5

mkdir -p "$G/functions/ffs.adb"
mkdir -p /dev/usb-ffs/adb
# ...
[ -e "$G/configs/c.1/ffs.adb" ] || ln -s "$G/functions/ffs.adb" "$G/configs/c.1/ffs.adb"
```

If unbinding fails, the old UDC value remains. Linking then fails because the configuration is still bound, but the script continues. At the end, `BOUND` can still contain the old UDC name, yielding success even though ADB was never added.

The timeout also validates only `ep1`, despite claiming both endpoints are required:

```56:60:deck/m2-scripts/mk20-add-adb.sh
if [ ! -e /dev/usb-ffs/adb/ep1 ]; then
  log "FAIL timeout waiting ffs ep1/ep2 (adbd still=$(pgrep -x adbd))"
  ls -la /dev/usb-ffs/adb >> "$LOG" 2>&1 || true
  exit 5
fi
```

Required checks:

1. Fail unless unbind succeeds and `UDC` becomes empty.
2. Fail unless the `ffs.adb` link exists.
3. Require both `ep1` and `ep2`.
4. Check the bind write itself.
5. Recheck `ffs.adb`, both endpoints, bound UDC, and live `adbd`.
6. Do not call the result “composite-ok” until the Mac sees ADB; device-side binding alone is not host enumeration.

Otherwise a false success leaves `qt_app2` stopped and no usable ADB for up to 90 minutes.

## 2. Risks — non-blocking

### ADB root cause remains unproven

The proposed race is credible:

1. UDC unbind removes `/dev/ttyGS0`.
2. KeyboardDevice may error or exit.
3. `qt_app2` respawns it.
4. Startup calls `setusbconfig serial`, replacing the composite.

But there is no captured evidence that KeyboardDevice exited or respawned at 02:47. ACM returning is also consistent with:

- Explicit failure restoration.
- An earlier timer.
- FunctionFS/adbd startup failure.
- The old helper rebinding ACM without a usable ADB interface.

The older helper had a particularly concrete problem: mount failure was logged but not fatal, and it bound after a fixed 1.5-second sleep rather than waiting for descriptors.

Therefore `DIAGNOSIS-adb-enum.md` correctly says “most likely,” but it should not be treated as confirmed root cause.

### The new fix addresses two real failure classes

It does correctly:

- Stop the supervisor before UDC surgery.
- Mount FunctionFS.
- Start `adbd`.
- Wait for endpoint files.
- Bind UDC last.

That order is the sound part of the diagnosis. The remaining validation holes and deployment mismatch prevent claiming the failure is fixed.

### `qt_app2 stop` is ignored and not verified

The script logs any remaining KeyboardDevice PID but proceeds regardless. If stopping the service fails, safety depends entirely on the wrapper being installed correctly.

`qt_app2` is not disabled, so stopping it should not persist across reboot. The persistent risk is the wrapper, not the init-service state.

### Diagnostic preservation is defective

The script initially writes prior statuses to `m2-diag-dump.txt`, but `dump_diag_to_acm` later overwrites that same file:

```31:38:deck/m2-scripts/lunch-m2test.sh
{
  echo "==== prior m2-adb-status ===="
  cat /mnt/SDCARD/m2-adb-status.txt 2>/dev/null || echo "(none)"
  echo "==== prior m2test-status ===="
  cat /mnt/SDCARD/m2test-status.txt 2>/dev/null || echo "(none)"
} > "$DUMP" 2>/dev/null
```

```55:76:deck/m2-scripts/lunch-m2test.sh
dump_diag_to_acm() {
  {
    # ...
    echo "===== MK20 M2 DIAG END ====="
    echo ""
  } > "$DUMP"
```

Thus the inaccessible prior `m2-adb-status.txt` will not actually be included in the ACM dump after failure. Also, `m2test-status.txt` is overwritten before being labeled “prior.”

### Stale helper execution

Copying the SD helper is allowed to fail, after which an old executable `/data/mk20-add-adb.sh` may still be used. The staged helper should have a verified hash or a unique versioned filename.

### Restore is not verified

`restore_serial` does not check `setusbconfig` exit status, UDC, `/dev/ttyGS0`, or host enumeration. A failed restore can leave the current boot silent, although a clean reboot should recover if the original `setusbconfig` remains intact.

### Watchdog duration

Ninety minutes is unnecessarily long for a first enumeration probe. A false-positive success means no KeyboardDevice/Protocol A for the entire period. A 5–10 minute window would reduce operational exposure.

## 3. Ranked design review — Wi‑Fi excluded

### Rank #1: ADB + combined HID + PTY bridge

This is a plausible target, but not a “do now” design.

It depends on proving ADB first—absolutely. In fact, ACM+ADB proof is only the first gate; ADB+HID is a different gadget composition and must be proven separately.

Additional unproven dependencies:

- Combined keyboard/absolute-pointer descriptor and `report_length`.
- Correct HID subclass/protocol; a report-ID composite should not blindly retain boot-keyboard settings.
- KeyboardDevice successfully opening a PTY instead of a gadget tty.
- PTY raw-mode behavior and reconnect handling.
- Safe `/dev/ttyGS0` symlink lifecycle.
- Host daemon migration from `cu.usbmodem*` to localhost.
- Recreating `adb forward` after every reconnect.
- ADB remaining the sole recovery/control channel after ACM is removed.
- A safe replacement for the persistent `setusbconfig` wrapper.

The screen paths are also conflated:

- Stock `adb forward` supports host→device connections.
- A T113 VNC client connecting to Mac Screen Sharing needs device→host connectivity, requiring `adb reverse`, which stock `adbd` apparently lacks.
- “Host-pushed RFB over adb forward” is really a new host capture/push architecture, not the existing Mac VNC server consumed by a device-side client.

Recommendation: describe #1 as the preferred gated target, not the next morning operation.

### Rank #2: ACM + ADB

This should be rank #1 for morning experimentation.

It fits the stated stock endpoint budget and preserves an ACM function. However, the current hook stops KeyboardDevice, so the morning test does not prove Protocol A remains usable—it only proves USB composition and ADB.

After host ADB enumeration succeeds, a separate gated test must restart KeyboardDevice safely and demonstrate simultaneous:

- `getInfo` over ACM.
- `adb shell true`.
- Stable operation through reconnects.

GD32 input and host-pushed screen transport remain separate unproven components.

### Rank #3: PPP over ACM

The ranking as a fallback is reasonable, but it is much less ready than the table suggests:

- The device has no `pppd`.
- Mac PPP presence is not a live interoperability result.
- Protocol A and PPP cannot simultaneously consume the same raw ACM byte stream.
- Sharing requires a multiplexer, moving Protocol A elsewhere, or time-slicing.
- Screen Sharing bind/firewall/auth behavior over the PPP peer link still needs testing.

It should remain research-only until ADB transport options are exhausted.

### Rank #4: patched UDC/kernel

There is an endpoint-math error in “ACM + ADB + combined HID (+ optional ECM).”

After changing one ISO endpoint into an interrupt endpoint, the system has two interrupt endpoints:

- ACM consumes one.
- Combined HID consumes one.
- ECM consumes another.

Therefore ACM+ADB+HID+ECM still does not fit. Valid two-INT choices include:

- ACM + ADB + HID, or
- ACM + ADB + ECM, or
- ADB + HID + ECM.

The claim that changing `ep3-iso` to `ep3-int` is sufficient also needs validation against the exact Tina vendor source, FIFO configuration, endpoint allocator, and kernel configuration.

RECOVERY §5 properly states that MK20-specific FEL entry is unverified. Consequently, it is recovery research, not yet a verified recovery drill. No kernel write should occur until a whole-card TF backup has been validated and restoration rehearsed.

## 4. What looks sound

- No flash, `/rom`, `swupdate`, or GD32 operations occur in the reviewed scripts.
- Self-deleting before UDC manipulation prevents the test hook from rerunning indefinitely.
- Configfs links, FunctionFS mounts, and UDC binding are runtime state and normally disappear on a true power-cycle.
- `qt_app2 stop` does not disable the service, so it should return on reboot.
- Stopping the respawning owner of `/dev/ttyGS0` before gadget surgery is the correct way to eliminate the proposed race.
- Starting `adbd` and waiting for FunctionFS descriptors before binding is the correct ordering.
- ACM+ADB is consistent with the documented stock endpoint budget.
- RECOVERY correctly prioritizes a whole-TF image and explicitly warns that FEL details are not MK20-verified.
- The addendum correctly says not to attempt PTY/ADB+HID until ADB enumeration is proven.

## 5. Suggested morning user steps

1. Do not power-cycle expecting the revised fix yet.
2. While ACM is healthy, determine which `lunch.sh` and helper are actually staged by comparing CRCs/hashes with the old artifact and revised files.
3. If the staged version is old or uncertain, delete/disarm it through Protocol A, then power-cycle once to confirm stock ACM and `getInfo`.
4. Before restaging:
   - Remove the persistent wrapper design.
   - Harden all helper checks.
   - Repair RECOVERY instructions.
   - Synchronize `deck/m2-scripts`, `artifacts/m2-scripts`, and the device copy.
5. Run only the ACM+ADB enumeration probe first, with the host deck daemon stopped and a short watchdog.
6. Treat success as requiring both Mac-visible ADB and expected ACM enumeration—not merely a non-empty device-side UDC.
7. Do not attempt ADB+HID, PTY substitution, PPP, ECM, or kernel changes during this first power-cycle.

If the sole goal is safe stock recovery rather than testing, disarm the existing hook before reboot.
