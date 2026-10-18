**Verdict:** one doc BLOCKER and four MAJORs.

- **New launch line:** it is quoted correctly. Each argument reaches flapmon intact, and `"$@"` runs the rollback command exactly as given.
- **BusyBox:** `flapmon.sh` is safe on 1.27 ash. Fractional `sleep` was already proven on the device.
- **Wedging:** flapmon can't wedge. It only reads sysfs and tmpfs, and the rollback it calls is time-bounded.
- **Your two resume sequences:** `configured→suspended→configured` and `suspended→default→addressed→configured` are both correctly not counted.

`mk20_accept.py` changed on disk at 19:38, after your prompt. Its new `do_reboot` probe already fixes the stale "confirm" detection, so I've left that out.

## BLOCKER

**`firmware/RECOVERY.md:109-112` (and the claim on line 3) — the whole-card restore would destroy the card.**

- **Vendor image:** `MK20_V1.0.img` is a PhoenixCard IMAGEWTY package; `deck/RECOVERY.md:91` says it can't be `dd`'d. `tf-restore-boot.sh --whole-image` (`:40-41,60`) writes it raw to `/dev/rdiskN`. That overwrites boot0, U-Boot, the partition table and p1–p5, leaving an unbootable card.
- **`live-221-parts/`:** it's a folder of per-partition dumps (disk head plus p1–p5), not one image. Any file from it passed as `--whole-image` lands at offset 0 and wipes the partition table.
- **"`/data` comes back empty":** this understates it. p6 (UDISK) is the writable overlay for `/`, so everything written since flashing is lost: `/data/mk20`, the ext4 rescue copy, the kernel inventory and the VNC secret.
- **Line 3:** it says nothing here writes the rootfs (p5); this procedure does.

Fix:
- Allow `--whole-image` only with a raw whole-card `dd` image the user made (`deck/RECOVERY.md` §3).
- For the vendor image, point to PhoenixCard (Windows) or an OpenixCard unpack.
- Say there is no automated restore from `live-221-parts`.
- Make the tool refuse files whose first 8 bytes are `IMAGEWTY`, and images larger than the disk.
- Add a `test_docs.py` check that `--whole-image` never appears next to `MK20_V1.0.img` or `live-221-parts`.

## MAJOR

**1. `flapmon.sh:14-24` — real drops that the poll catches as `suspended` are never counted (1.1.1 counted them).**

Once the state is `suspended`, any way out except straight back to `configured` is ignored. That exemption is meant for Mac sleep followed by a resume reset, but it also hides:
- a host disabling the port and then resetting it;
- a cable glitch that the UDC sees as an idle bus;
- an unplug on a UDC with no VBUS detection.

If the poll catches the suspend phase of a 3-second flap most of the time, it never reaches 5 drops in 60 s.

Your question about unplugging while suspended: `suspended → not attached` is correctly not counted, but it isn't logged either.

Fix: time each suspend. Count it as a drop only if it was short and didn't end back in `configured`:

```sh
elif [ "$prev" = configured ] && [ "$s" = suspended ]; then sus=$(up); echo "flapmon $sus: host suspend" >> "$LOG"
elif [ "$prev" = suspended ] && [ "$s" != suspended ]; then
  t=$(up); d=$((t - ${sus:-0}))
  if [ "$s" = configured ]; then echo "flapmon $t: host resume after ${d}s" >> "$LOG"
  elif [ -n "$sus" ] && [ "$d" -lt "${MK20_FLAP_SUSP_S:-5}" ]; then now=$sus   # short suspend + reset/detach = drop
  else echo "flapmon $t: '$s' after ${d}s suspend (resume reset/unplug, not counted)" >> "$LOG"; fi
  sus=""
```

- **Counting:** move the window count into one `if [ -n "$now" ]` block shared with the existing `configured → X` branch.
- **Sleep exemption:** a real Mac sleep lasts far longer than 5 s, so both resume sequences stay exempt.
- **Tests:** short suspend then `default`/`not attached`; long suspend then `not attached`; the "host resume" log line.

**2. `gadget.sh:137,228-238` + `flapmon.sh:20-21` — nothing guarantees a single rollback.**

This predates the change, but it breaks your "never double-rollback" rule:
- **Confirm waiter (`:238`):** in `--run` mode it checks only `mk20-composite-ok`, never the stopfile. After a flap rollback, it will run a second `--rollback-now` at 180 s. That unbinds the stock gadget the first rollback just restored and removes `acm.usb0`/`ffs.adb`. It also races the first rollback's `readd_adb`/`kd_start` if those are still running.
- **Re-run:** running `gadget.sh --run` again never stops the old flapmon, so two monitors fire on the same flap.
- **Stopfile race:** flapmon doesn't re-check the stopfile between reading the state and running the rollback.

Fix:
- **Once-guard:** at the start of the `--rollback-now` branch, add `mkdir -p /tmp/mk20; mkdir /tmp/mk20/rollback.once 2>/dev/null || { log "rollback already done"; exit 0; }`. Remove it with `rmdir` at `OK=1` (`:221`).
- **Waiter:** add `[ -f /tmp/mk20-flapmon-stop ] && exit 0` to its loop.
- **flapmon:** add `[ -f "$STOP" ] && exit 0` right before `touch "$BUSY"`.
- **Single instance:** use `exec 8>/tmp/mk20/flapmon.lock; flock -n 8 || exit 3`. Run the rollback as `"$@" 8>&-`, otherwise adbd inherits the lock.
- **Old monitor:** `touch` the stopfile during preflight so any old monitor exits.

**3. `mk20_accept.py:143-149` (run hourly by the soak, `mk20_soak.py:66-68`) — the Shift tap can leave Shift stuck on the Mac, and can break HID for the rest of the boot.**

- **No host polling:** the HID driver's first `write()` returns as soon as the report is queued; only the second write waits for the host to read the first.
  - If the host isn't polling (Power Nap/DarkWake, or the HID driver not matched yet), the Shift-down report stays queued and the Shift-up writer is killed after 3 s.
  - When polling resumes, the Mac gets Shift down with no release. macOS keeps that modifier latched until the MK20 sends another report.
- **No HID function:** after a flap rollback, in no-HID mode, or early in boot, `> /dev/hidg0` creates a regular file in `/dev`.
  - The check reports a false HIDOK.
  - For the rest of that boot the kernel can't create the real device node because the name is taken.
  - `gadget.sh`'s `[ -e /dev/hidg0 ]` and mk20d's "PASS hid" then pass on a dead file.

Fix: write two all-released reports instead. They prove the host consumed one just as well, and a leftover queued report releases nothing:

```sh
[ -c /dev/hidg0 ] || { echo NOHIDG; exit 0; }; grep -qx configured /sys/class/udc/*/state || { echo NOTCONF; exit 0; }
z='\001\000\000\000\000\000\000\000\000'; ( printf "$z" > /dev/hidg0 && printf "$z" > /dev/hidg0 && echo HIDOK ) & p=$!; ...
```

- Make the soak's HID probe opt-in.
- `RECOVERY.md:22` calls `mk20_accept.py` a "full read-only check", which it isn't. Use `--no-hid` there until this is fixed.
- Use `[ -c ]` in mk20d's HID check.

**4. `mk20_accept.py:118-136` and `mk20_soak.py:62-65` — every TCP probe kicks the deck daemon off the bridge.**

The bridge serves one client and drops the old one when a new one connects (`ttygs_tcp_bridge.c:6-7,83`).
- Each soak sample makes two connections, plus an hourly getInfo session. Each one disconnects the daemon and can cut a frame it is sending.
- The daemon's reconnect can in turn kill the soak's getInfo, so `getinfo` fails at random.

This breaks "never change device state" on the main data path.

Fix:
- Check the listeners on the device over adb instead (`netstat -ltn`; mk20d health already reports the bridge listener).
- Check the link-local path with `ping -b <iface> 169.254.7.2`.
- Connect over TCP only when `netstat -tn` shows no established client on port 17890.
- In accept, warn before connecting.

## MINOR

**flapmon and gadget.sh**
- **`gadget.sh:231-232` — `env` is a new dependency on the rollback path.**
  - Nothing else on the device uses it, and its presence on the device's BusyBox is unverified.
  - If it's missing, the rollback fails silently because stdout and stderr go to `/dev/null`. flapmon then exits 0 with `gadget.busy` still set, and nothing watches a flapping link.
  - Instead, run `export MK20_REL=... MK20_GADGET_LOG=...` inside the subshell and pass plain `sh /tmp/mk20-gadget.sh --rollback-now`.
  - In flapmon, log the rollback's exit code. On 126/127, remove `$BUSY` and fall back to calling the script directly.
- **`gadget.sh:229-230` — the `flapmon.sh` check runs after the composite is already up.**
  - Without `MK20_REL`, every run builds the composite, then dies and rolls back.
  - `m2-scripts/gadget-ecm-hid-adb.sh` got the identical change (same blob `af23a7d`), so legacy runs are broken too.
  - Move the check into preflight (`:143`) and fall back to a `flapmon.sh` next to the script, or keep the old heredoc in the m2 copy.
- **`flapmon.sh:8` — no argument checks.** A non-numeric window or drop count makes `[ -le ]` error out, which silently disables rollback. Validate the numbers and that a command was given, and exit 2 so gadget.sh's startup check fails safe.

**Tests**
- **`tests/test_flapmon.py:18`:**
  - Missing cases:
    - the stopfile appearing just before the Nth drop;
    - exactly one rollback;
    - real arguments containing spaces;
    - `suspended → not attached`.
  - `write_text` truncates before writing, so flapmon can read an empty state and log a "link drop ()", which makes the tests flaky. Write to a temp file and `os.replace`.
- **`tests/test_docs.py:33-34`:** the regex skips the `ECM_DEV_MAC|ECM_HOST_MAC)` and `FEATURE_*|…)` keys, and `val in doc` (e.g. "5") proves nothing.

**Soak (`mk20_soak.py`)**
- **Can still die or lose the summary:**
  - It raises an uncaught `SystemExit` when `MK20_ADB` is unset; check this at startup.
  - The writes at `:128-134` sit outside the `try`, and the final summary (`:137-140`) isn't in a `finally` and isn't written atomically.
  - A timeout kills only the direct child. That orphans `uv`'s python, which keeps holding the bridge, and the `adb devices` pipeline inside `mkadb.sh`. Use `start_new_session=True` plus `os.killpg`.
  - It doesn't check the stop flag between steps of a sample.
  - `soak.pid` is never removed, so `kill $(cat soak.pid)` (INSTALL.md:144) can hit a reused PID later.
  - `--deep-every 0` turns every sample into an error.
- **Misleading data:**
  - When KeyboardDevice is gone, `kd_pid` (`:50-56`) picks up the `du` output and produces fake "kd-restart" events. Print labelled `kd=`/`logkb=`/`bridge=` fields.
  - `:115-117` reports the historical `last_rollback` as "seen". Snapshot it at start and count changes.
  - Composite loss (true→false), the 1.1.2 behaviour you're soaking, isn't in the summary.
  - Health at `:45` runs the `good` release's mk20d instead of the active one (`/tmp/mk20/rel/sbin/mk20d`).

**Accept (`mk20_accept.py`)**
- **`--reboot` (`:164-165`):** with a release pending, the reboot counts as an attempt and rolls it back. Refuse when `pending` is non-empty.
- **`wait_adb(False)` (`:82-89`):** treats a single adb timeout as "device left USB".
- **Route check (`:182`):** passes when both values are `None`.
- **`tcp_bound` (`:118-125`):** silently connects without binding to the interface when no MK20 interface is found.
- **`get_info` (`:128-136`):** uses an unbound pyserial socket, needs uv to resolve packages, and depends on the p0 worktree path.

**Docs**
- **Kernel statements are wrong.** `KERNELS` lists only ep5out, so on the stock or ep3 kernel the loader refuses every release.
  - Affected lines: `RECOVERY.md:106` and `kernel-notes/9ac68ed98a70/RECOVERY.txt:3`.
  - What actually happens: only `boot-every/adb.sh` runs. You get ACM+KD, plus adb only if that job still exists, and a pending release gets marked failed.
  - "Use the no-HID flag on the ep3 kernel" (`RECOVERY.md:40`, `f2a19d3cb8fd/RECOVERY.txt:3`) therefore does nothing on 1.1.x.
- **`RECOVERY.md:72-73`:** if both rescue copies fail their checksums, nothing runs and there's no adb. `mk20-rescue-update.sh` can't help then, and neither can line 20's `stub.status` read. Give the TF-card/overlayDir route or `mk20ctl.py put` over the serial port.
- **`INSTALL.md:31-34`:**
  - `mk20-rescue-update.sh` installs `lunch.sh` to `/mnt/SDCARD/mk20/lunch.sh`, not to the live stub at `/mnt/SDCARD/lunch.sh`. Add that step.
  - Repeat the tool's warning: never update `watcher.sh` while a job is running.
- **`INSTALL.md:43`:** `shasum -c` run from `deck/` fails because the `.sha256` file holds a relative name. Run `cd` into the releases folder first.
- **`INSTALL.md:140`:** the redirect fails unless `~/mk20-backup/soak` already exists, and a laptop will idle-sleep. Use `mkdir -p … && caffeinate -i nohup …`.
- **`RECOVERY.md:45`:** say to unplug the device before removing the TF card; the rootfs and overlay are on the card.
done rc=0
