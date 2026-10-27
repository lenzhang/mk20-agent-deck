All verification is complete. Here is the re-review.

---

# Re-review: feature/ncm-gadget vs release/1.7.1 (post-fix)

Re-derived the B1 arithmetic independently from the patched source (`/tmp/ncmp/b/f_ncm.c` = pristine `a` + exactly the 6 hunks of `f_ncm-mk20.patch`, net +29 lines — verified hunk-by-hunk plus tail-line count), and re-checked B2, the new tests, and the requested regression surfaces.

**B1 — fixed correctly.** Guard (`b/f_ncm.c:1067-1072`, after the CRC block, before the pending-NTB check): `ALIGN(nth_size,div) + rem + skb->len + (ndp_align-1) + ndp_size + 2*dgram_idx_len > max_size`. With the file's real constants (`dgram_item_len` is in u16 units → `dgram_idx_len = 4` for NTB16; `nth_size=12, ndp_size=8, div=4, rem=0, ndp_align=4`): fresh-NTB worst case = 12+0+L+3+8+8 = **L+31**, which exactly matches the true layout I traced through the append path (NTH 12, first-dgram pad 0, L, `ndp_pad ≤ 3` in `package_for_tx`, NDP header 8 + entry 4 + terminator 4). A 476-byte frame ⇒ NTB ≤ 507 ≤ 508 → **passes**; drop threshold is L ≥ 478, unreachable at MTU 462 without CRC. No `skb_put` overrun: the NTB skb is `alloc_skb(max_size)` at `b/f_ncm.c:1096` with the *same* `max_size = min(fixed_in_len, 508)` local the guard uses, and the unchanged upstream pending-NTB check (`:1079-1084`) over-charges (`div`/`ndp_align` in full), so the multi-datagram path is also bounded ≤ alloc. IN requests ≤ 508 (PIO-safe, never a 512 multiple → no ZLP pad); OUT sizing/ZLP/u_ether `wrap`-NULL contract unchanged from the previously verified analysis. NTB32 = L+51 is correctly documented in the comment.

**B2 — fixed correctly.** `gadget.sh:561` (`rm_fn ecm`) runs before the NCM insmod/mkdir at `:562`, `:566` (`rm_fn ncm`) before the ECM insmod/mkdir at `:567-568`, so the other function's instance (and its netdev) is gone before the chosen one is created — the new netdev takes usb0 on a live switch. An un-removable stale instance `die`s → EXIT failsafe (ACM+adb), fail-closed; the NCM-fallback path (refused/missing/failed mkdir) correctly reaches `rm_fn ncm` with a no-op (`[ ! -d ]` on a file), and same-function re-runs still reuse the instance (pre-existing ECM semantics). Helpers (`net_fn`/`fn_mtu` at `:229-230`) precede all uses (`:460-461`, `:570`); `set -u` audit of the new lines (USE_NCM:57, NCMKO:61-62, NETFN:559-, NETMTU:570, RFN/RMTU:460-461) is clean; the `{ …; } && mkdir` if-condition, `rm_fn` `||` chain, and `tr a-z A-Z` are all fine in BusyBox ash 1.27.

**Tests are now genuine, not tautologies.** The Fn harness models netdev registration (`net/usbN` owner files in the mkdir/rmdir stubs) — `test_the_other_functions_stale_instance_is_removed` fails under the old ordering (new instance → usb1, usb0 gone); the flag param now executes the real `USE_NCM=` line with `$T/sd/mk20-no-ncm` dropped (N3 ✓); the guard test regex-extracts the shipped expression, substitutes the true constants, sanity-`fullmatch`es it, and compares against the independently-stated worst-case layout for every L in 14..599 — the old `L+36` guard would fail it (N1 ✓); `mk20d` knob plumbing, `stock_ok`, teardown, re-arm (incl. the 478→462 NCM MTU restore), and the `Rearm`/`test_mk20d`/`conftest` APIs used all exist and match. INSTALL.md/RECOVERY.md document `mk20-no-ncm`.

## BLOCKERS

None.

## NITS

1. **CRC-mode conservatism in the guard** — `f_ncm-mk20.patch` guard line (patched `f_ncm.c:1067`): charging worst-case NDP pad (`ndp_align-1`) instead of the exact `ALIGN(nth+rem+len, ndp_align)` makes the guard drop a frame whose actual NTB fits whenever the two differ: with a host that enables NCM CRC mode, a full-MTU frame is L=476+4=480 → guard 511 > 508 → dropped, though the real NTB is exactly 508. That's the B1 symptom resurfacing under a CRC-mode host (Linux definitively doesn't; macOS AppleUSBNCM unverified). One line — `ALIGN(ALIGN(opts->nth_size, div) + rem + skb->len, ndp_align) + opts->ndp_size + 2*dgram_idx_len > max_size` — is exact in both directions. The shipped comment documents the +4, so this is a judgment call, not an error.
2. **Nothing ties the committed `.ko` to the patch text** — `deck/firmware/release/lib/usb_f_ncm.ko`: `test_shipped_module_matches_the_stock_kernel` (`deck/tests/test_ncm_gadget.py:303`) checks vermagic/name/alias/depends, which a stale pre-fix build also passes; no sha256/size from `build-usb_f_ncm.sh` output is recorded anywhere in the tree. Record the build's sha256+size in the release evidence so a stale module is detectable.
3. **Live-switch rmdir now sits on the main setup path** — `gadget.sh:561/:566` is the first main-path rmdir of a netdev-owning instance; if any socket (old bridge listeners, `SO_BINDTODEVICE usb0`) pinned usb0, `unregister_netdev` would block in the kernel. Empirically fine — the shipped `cleanup()` already rmdirs before killing the bridge (`:202` vs `:204-205`) on hardware-exercised paths (failtest, no-confirm rollback), and the r6 supervisor (`:503-533`) bounds any hang to setup-timeout → escalation, not a brick. Worth one comment line noting the dependency.
4. **Carried over (N5), pre-existing upstream**: `ncm_unwrap_ntb` still lacks `skb->len` bounds checks on `ndp_index`/datagram index+length (patched `f_ncm.c:1267-1340` region) — not a regression; consider folding the upstream fix into a future patch revision.
5. **Duplication (N6, slightly grown)**: `mk20d:78` and `gadget.sh:229` both define `net_fn()`; the 462/478 table in `fn_mtu` is a second source of truth next to `MK20_MAX_SEG` — at least the MTU side is now test-enforced (`seg == 14 + mtu`). A cross-referencing comment suffices.

---

Summary: both blockers are fixed correctly and completely (B1's guard is now the exact worst-case bound for a fresh NTB with no allocation overrun possible, and the module-plus-test pair enforces it; B2's rm_fn ordering makes live function switching sound and fails closed), the fixes introduced no set -u / BusyBox ash / failsafe / adb regressions, and the previously weak tests were upgraded to real coverage that would catch both original defects. Remaining items are nits.

VERDICT: APPROVE
