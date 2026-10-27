All verification is complete. Here is my review.

---

# Review: feature/ncm-gadget vs release/1.7.1

Verified against real upstream sources (f_ncm.c and u_ether.c at v5.4.61; `/tmp/ncmp/a/f_ncm.c` is byte-consistent with upstream, and `/tmp/ncmp/b/f_ncm.c` matches the repo patch).

**Verified sound (no finding):**
- **u_ether contract for `wrap` returning NULL**: u_ether.c v5.4.61 jumps to `multiframe:` (f_ncm sets `supports_multi_frame = true` in `ncm_alloc()`) and does **not** free the skb nor count a drop. The patch's guard frees the skb exactly once (`dev_kfree_skb_any`), so no double-free, no leak, no double `tx_dropped`.
- **OUT sizing**: f_ncm sets `is_fixed = true`; u_ether sizes OUT requests to `max(ETH_HLEN + mtu + RX_EXTRA + header_len(0), fixed_out_len=512)` = `max(496, 512)` = 512 — exactly at the sunxi PIO limit, as designed. IN requests are the NTB skb (≤ 508) — PIO-safe.
- **ZLP**: u_ether's `req->zero = 1` plus `if (req->zero && !dev->zlp && (length % maxpacket) == 0) length++;` would pad a 512-multiple NTB to 513 (DMA path). IN_CAP 508 ⇒ built NTBs ≤ 504, never a multiple of 512 ⇒ pad never triggers. Correct.
- **Locking**: guard runs under u_ether's `dev->lock` (irqsave), touches only skb + stats; `dev_kfree_skb_any` is atomic-safe. No new state is mutated before the drop, so the pending-NTB invariant holds.
- **INFO ratelimit hack works**: `INFO` comes from `include/linux/usb/composite.h`, not from f_ncm.c itself, so the patch's `#undef INFO` + redefinition applies file-wide with no redefinition conflict.
- **gadget.sh `set -u`/BusyBox 1.27 audit of new code**: `USE_NCM`, `NCMKO`, `NETFN`, `NETMTU`, `RFN`, `RMTU` are all assigned before every use (`RMTU` at gadget.sh:461 precedes the `rearm_main` fork at :479; `ra_*` helpers run only in that path). `{ ...; } && mkdir` in an `if` condition, `elif` chains, `tr`, multi-arg `rmdir`, `grep -q` — all fine in ash. Re-arm refusal, `stock_ok`, cleanup, `composite_up`/`net_fn` in mk20d all handle both functions. Failsafe interaction: every new failure (`die "stale ..."` , ECM insmod failure, `no usb0 netdev`) funnels into the existing EXIT→ACM+adb failsafe — **no brick / adb-loss path found**.
- The re-arm tests (`test_rearm_ncm_*`) run the *real* extracted rollback/rearm block under BusyBox 1.27 with `set -u`, with the real `net_fn`/`fn_mtu` — genuine coverage, not tautologies. `build-usb_f_ncm.sh` mirrors the proven ECM recipe with a post-patch sanity grep.

## BLOCKERS

**B1. The mk20 guard drops every full-MTU frame — device→host bulk TCP is dead at MTU 462.**
`deck/kernel/stock-patch/f_ncm-mk20.patch`, guard hunk (`@@ -1041,6 +1057,14 @@`; patched file `f_ncm.c:1062-1068`), shipped in `deck/firmware/release/lib/usb_f_ncm.ko`.

The guard evaluates `opts->nth_size(12) + div(4) + rem(0) + skb->len + ndp_size(8) + ndp_align(4) + 2*dgram_idx_len(8)` = **skb->len + 36 > max_size(508)** ⇒ drop when `skb->len ≥ 473`. But those 36 bytes over-charge the padding: for a *fresh* NTB the first datagram sits at offset 12 (already 4-aligned ⇒ dgram_pad 0) and `ndp_pad ≤ 3`, so a maximum 476-byte frame (`wMaxSegmentSize = MK20_MAX_SEG = 14 + MTU 462`) produces an NTB of exactly **12 + 476 + 0 + 8 + 4 + 4 = 504 ≤ 508 — it fits**. Net effect: frames of 473–476 B are dropped although they fit, and 476 B is precisely every MSS-sized segment (MSS 422 + 40 = 462 IP + 14 eth). TCP retransmits never shrink segments, so device→host bulk transfers stall and eventually reset; only sub-419-B-payload traffic flows. This is silent (just `tx_dropped`) and passes a ping/health smoke test — consistent with this branch having only re-arm evidence, no throughput evidence. Fix: charge actual padding (`ALIGN()`-style arithmetic mirroring the append path, i.e. drop only when `nth_size + rem + ALIGN(nth_size+…, div) + … > max_size`, equivalently `skb->len + 31 > max_size`), keep IN_CAP 508; also note that with host CRC mode `skb->len` includes +4 (480-B frame ⇒ 508-byte NTB — exactly at the cap), and under NTB32 the overhead grows to 56 — derive the overhead from `opts`, not constants. The committed `.ko` must be rebuilt after the fix.

**B2. Stale-function removal runs *after* the new function dir is created — switching functions on a live composite registers the new netdev as usb1 and dies at the usb0 check.**
`deck/firmware/release/share/gadget.sh:555-566` (chosen function mkdir at :558/:563, stale loop at :566), dying at :606 `[ -d /sys/class/net/usb0 ] || die "no usb0 netdev"`.

A configfs function instance owns a registered netdev from `mkdir` (`gether_setup_name` at alloc_inst) until `rmdir` — the comment at :565 itself asserts this. So on any live switch (drop `/mnt/SDCARD/mk20-no-ncm` on a running NCM device + `--run`; or `MK20_NCM=0`; or the ECM fallback with a leftover `ncm.usb0`), the sequence is: unbind+unlink (:551-553) → **mkdir of the new function while the old instance still owns usb0** ⇒ new netdev = usb1 → stale rmdir frees usb0 → only usb1 exists → `die "no usb0 netdev"` → EXIT failsafe → ACM+adb instead of the requested ECM/NCM composite. Fail-closed (no brick, adb preserved via `readd_adb`), and a *reboot* switch works (clean configfs) — but the documented live remediation path ("MK20_NCM=0 or /mnt/SDCARD/mk20-no-ncm forces ECM", header :10) cannot work, and the in-repo test's own docstring claims the property the ordering breaks ("usb0 would then not be the linked function's interface"). Fix: remove the *other* function's stale dir before creating/insmod-ing the chosen one (i.e. before :558), e.g. decide the preference first, then `[ rmdir the not-wanted instance ] || die`, then mkdir the wanted one.

## NITS

**N1.** `deck/tests/test_ncm_gadget.py:698-712` (`test_patch_keeps_every_ntb_within_one_pio_request`) — the arithmetic models the *intended* NTB layout (`12 + 3 + seg + 8 + 4 + 4 ≤ 508`) and then does string-presence checks; it never transcribes the shipped guard formula (`seg + 36 > 508 ⇒ drop`), which contradicts the asserted "fits" conclusion — that's how B1 got through. Assert `MK20_MAX_SEG + <the guard's exact overhead expression> ≤ MK20_NTB_IN_CAP` (fails today) alongside the layout bound.

**N2.** `deck/tests/test_ncm_gadget.py:570-577` (`test_the_other_functions_stale_instance_is_removed`) — models function dirs as plain directories, so it validates the `rmdir` mechanics but cannot see the netdev-registration consequence that produces B2 (the harness has no usb0/usb1 naming model). Worth a comment or a name-aware fake.

**N3.** `deck/tests/test_ncm_gadget.py:560-566` (`test_ncm_disabled_builds_the_ecm_composite_without_touching_ncm[flag]`) — the `flag` param only re-runs the `knob` body plus a static string assert; the real flag-file read (gadget.sh:57) is never executed. Cheap fix: include the `USE_NCM=...mk20-no-ncm` line in the extracted body (the `/mnt/SDCARD` → `$T/sd` rewrite already exists) and drop `$T/sd/mk20-no-ncm` in the test.

**N4.** `deck/kernel/stock-patch/f_ncm-mk20.patch` (guard hunk) — NTB32: if a host selects `wValue=32` format, overhead becomes 16+4+0+16+4+16 = 56 ⇒ frames ≥ 453 dropped while `wMaxSegmentSize` stays 476. macOS and Linux `cdc_ncm` use NTB16, so it's latent — but one line in the patch comment (or clamping `wMaxSegmentSize` under NTB32) would close it.

**N5.** `ncm_unwrap_ntb` (upstream code, unchanged by the patch, `f_ncm.c:1227`/`1290` region) has no `skb->len` bounds checks on `ndp_index` / datagram index+length — a buggy or hostile host can trigger OOB reads (later fixed upstream). `dwNtbOutMaxSize=512` at least bounds `wBlockLength`. Pre-existing, not a regression — consider folding the upstream fix into a future patch revision.

**N6.** `deck/firmware/release/sbin/mk20d:78` duplicates `net_fn()` verbatim from `gadget.sh:229` (and the 462/478 table in `fn_mtu` is a second source of truth coupled to the patch constants). Fine at this size; a cross-referencing comment like the MTU one would help.

---

Summary: the shell-side work is solid (fallback logic, rearm/MTU plumbing, knob propagation, failsafe behavior all check out and are genuinely tested), but the kernel guard's conservative padding arithmetic silently kills full-MTU TX (B1), and the stale-instance ordering defeats live function switching (B2). Both must be fixed (and the `.ko` rebuilt) before this ships.

VERDICT: CHANGES_REQUESTED
