I'll start by reading the notes and scripts, then pull the relevant sunxi_udc and f_hid sources out of the tarball with `tar -O`.Now the sunxi_udc source from the tarball.The `ep_fifo[]`/`ep_fifo_out[]` tables are in hand. Next, the DMA code and the RX paths in `sunxi_udc.c`.The INNER DMA path maps channel `ep_num` via `ep_fifo_out[ep->num]` (ep5out maps to index 8), which looks consistent. Now the main driver.The IRQ loop covers ep1..ep5 (`SW_UDC_EPNUMS`=5) and the DMA IRQ maps through `ep_fifo_out[]`, both of which look correct. Now `ep_enable`, `queue` and `dequeue`.Decompression is fast. Pulling `f_hid.c` and the endpoint-match code.The vendor `#if IS_ENABLED(CONFIG_USB_SUNXI_UDC0)` hunk is confirmed: INT descriptors need `caps.type_int`. Now the truncated middle of `f_hid.c` (bind, set_alt and read).The grep shows lines in `gadget-ecm-hid-adb.sh` that weren't in my first read (a `--rollback` mode, and shifted line numbers), so the file changed while I was working. Re-reading the current version.The rootfs backup is ext4, and `7zz` can read it to stdout without mounting or extracting. Pulling the init scripts.Before writing up I want to check the ECM test's host→device traffic. If every frame was a small default ping, the "DMA never completes on ep5" theory would be weaker.The ECM test log doesn't record frame sizes. The DMA theory therefore stays a hypothesis, and an MTU-478 re-run on ep5 would settle it. Re-reading `bootswap.sh` to state its backup gap exactly.Two details to confirm: whether any init script sorting before `qt_app1` also matches the grep, and where the boot-every `adb.sh` checks its kill switch.**Verdict:** (a) The second swap is a **GO, with conditions**. (b) The first composite run is a **NO-GO**. The new top blocker is that v3's `KDSVC` auto-detect picks `/etc/init.d/qt_app1`, and running that script kills adbd, which unbinds the whole composite.

I reviewed HEAD `2446a6d`, the vendor UDC tarball, `f_hid`/`u_ether`/`f_fs` from tina54, and the init scripts from the p5 rootfs backup (read with `7zz -so`; nothing extracted, nothing edited).

### 1. ep5out stall

The endpoint mapping isn't the bug. `ep_fifo_out[5]=8`, the DMA channel's `ep_num`, the RX interrupt loop and the FIFO layout (7168–8191) are all consistent.

**Leading hypothesis (unverified):** ECM's 1534-byte RX requests make any frame whose first packet is full eligible for DMA (`big_req`). If the inner DMA never completes for EP5, `dma_working` stays set and `read_fifo()` returns early on every later interrupt. The FIFO then fills and the host gets NAKs, which matches "one small PIO frame, then nothing". Writing 1 to `dma_debug`/`read_debug` would show it.

**A worse problem affects every endpoint, not just ep5:**
- u_ether's buffers are 2 bytes misaligned (`NET_IP_ALIGN`), so the driver bounces them.
- `queue()` makes the bounce decision using a stale `actual`.
- `done()` only copies back and frees the bounce buffer if `big_req` still holds for the final `actual`.
- For frames over about 1 KB you get a garbage frame and a leaked bounce buffer. The next completion can then copy into a freed skb and `kfree()` a page-fragment pointer.

That is memory corruption: USB dies, KD hangs, and the rollback never runs. Moving ECM to ep1 doesn't avoid it.

**HID OUT is not affected.** Its 9-byte kmalloc'd requests always use PIO. But `f_hid` only requeues OUT requests on `read()`, so keep `/dev/hidg0` drained.

**Runtime workaround:** run `ifconfig usb0 mtu 478` **before** bringing usb0 up.
- Every request is then at most 512 bytes and goes through PIO only.
- The device's TCP MSS limits the size of the Mac's TCP segments, and oversized UDP from the Mac arrives as runts that get dropped.

The cheapest test needs no swap: re-run `ecm-bind-test.sh` (ECM on ep5) at MTU 478 with a sustained `ping -s 400`. That tests the DMA hypothesis and also checks ep5out PIO, which HID OUT depends on.

### 2. Endpoint budget

Confirmed from the tina54 source: `hidg_bind` autoconfigures both IN and OUT, each with `goto fail`, and there is no IN-only option. With link order ECM, then HID, then ADB:

| Function | Endpoints |
|---|---|
| ECM | ep1 pair + ep3 |
| HID | ep4 + ep5out (needs caps 0x2c) |
| ADB | ep2 pair |

Alternatives that avoid the second swap:
- **HID + ADB only:** HID on ep3/ep4, reach the bridge with `adb forward`, no ECM at all.
- **FunctionFS HID** with an IN endpoint only.
- **ECM + ADB** using the `nohid` mode.

### 3. v3 review

**Blockers:**
- **`KDSVC` resolves to `qt_app1`.** That script contains `/data/KeyboardDevice` (in its chmod list) and sorts before `qt_app2`. It is plain sh that ignores its argument and always runs its whole body, including `killall adbd`.
  - **Stop:** KD stays under procd. After your `killall`, procd respawns KD, and KD's real `setusbconfig serial` (not yet shadowed) races your gadget setup.
  - **Start:** it kills your new adbd. `ffs_closed()` then calls `unregister_gadget_item()`, so ECM and HID unbind seconds after "OK". The 180 s rollback recovers the gadget, but leaves adbd dead.
  - **Fix:** hardcode `/etc/init.d/qt_app2` (a procd service with `CMD=/data/appLunch.sh`) and check that KD stays gone after stop.
- **No MTU guard** (see section 1).
- **Running `--rollback` by hand from `adb shell` is unsafe.** It kills adbd first, which ends its own session. adbd takes the process group with it, so cleanup dies partway and USB stays unbound, the same failure as 12:52. Route it through the watcher unless the detached waiter is the caller.

**Correct as written:** the watcher launch, the preflight, starting adbd before binding (with self-bind detection), the link order, the `setusbconfig` shadow, the ttyGS0 restore order, and the detached waiter.

**Should fix:**
- The bridge has no supervisor. If it dies, KD is left on a dead PTY and the UI will probably hang.
- The bridge also writes to the TCP client with a blocking `write()`, so a stalled client backs up KD's writes. Use a non-blocking socket and drop data on `EAGAIN`.
- Touch `mk20-no-adb` so the boot-every keepalive loop stays out of the way.
- Confirm only after both a TCP `getInfo` and one HID key event work.

### 4. Failsafe

The patched→patched gating is sound. The auto-revert can still silently fail to fire:
- **A power cycle inside the 600 s confirm window disarms it.** The boot-once job is already consumed and the waiter lived in RAM, so the device stays on the unconfirmed kernel and nothing checks it again. Have postswap re-stage itself until `mk20-swap-ok` exists.
- postswap exits 0 without checking anything if `boot-new.img.sha256` is missing or has changed, or if p4 doesn't match it.
- bootswap only prints a warning when `/data/mk20/postswap-check.sh` is missing. Make that fatal.
- If `mk20-no-watch` is set, nothing runs at all.

Separately, a missing `boot-stock.img.sha256` (or a leftover `boot-swap.rebackup`) makes bootswap overwrite **both** on-device stock copies with the baseline.

### GO/NO-GO

**(a) Swap to `boot-221-ep3int-ep5out.img`: GO** once all of these hold:
- `boot-new.img` is restaged with the ep5out image. The ep3 arm script overwrote it with the ep3int image.
- The ep5out image is verified independently: a 3-byte Image diff including caps 0x24→0x2c, the 1-byte FNAME pad, and a gzip round-trip.
- There is a new arm script with the ep5out image's sha.
- A missing postswap script is fatal in bootswap.
- You use `noreboot`, power-cycle once yourself, and don't power-cycle again inside the confirm window.
- The kill switches are clear.

This swap only pays off if you keep built-in `f_hid` alongside ECM, so run the MTU test first.

**(b) First composite run: NO-GO.** The blockers are:
- the `qt_app2` fix
- MTU 478 set before `up`
- an adb-safe `--rollback`
- ECM RX, which is not yet proven on any endpoint

Suggested order:
1. The ep5 MTU test.
2. A `nohid` run on the current kernel.
3. Swap (a).
4. The full composite.