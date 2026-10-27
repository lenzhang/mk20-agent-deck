All three deltas verified. Final assessment:

---

# Follow-up review: 012bd19..a06c081 (nits 1–3 of glm-review2)

## Nit 1 — exact TX guard: **verified exact, byte-for-byte**

Traced the fresh-NTB path in `/tmp/ncmp/b/f_ncm.c` (which is refreshed to the new patch — guard at `:1067-1068`):

- **Built layout, fresh NTB, one datagram:** `ncm_wrap_ntb` puts NTH (`nth_size`=12) + first-dgram pad (`ALIGN(12,div)+rem−12` = 0) + frame into `skb_tx_data` (data length = `ALIGN(nth,div)+rem+L`); the post-datagram pad at `:1140` is provably 0 for the first datagram. `package_for_tx` (`:987-1024`) then appends `ndp_pad = ALIGN(datalen, ndp_align) − datalen`, NDP (`ndp_size`), one entry (`dgram_idx_len`) and the terminator (`dgram_idx_len`). Final NTB = **`ALIGN(ALIGN(nth,div)+rem+L, ndp_align) + ndp_size + 2*dgram_idx_len`** — character-for-character the new guard's LHS. Exact in both directions: fits ⟺ guard passes. The second `dgram_pad` at `:1140` being 0 (since `ALIGN(nth,div)` is a multiple of `div`) is what makes the guard's single outer ALIGN match `package_for_tx`'s `ndp_pad` exactly.
- **No overrun of `alloc_skb(max_size)`** (`:1096`): same `max_size = min(fixed_in_len, 508)` local (`:1039`) the guard compares against; every `skb_put` on the data skb plus `package_for_tx`'s `ndp_pad`/NDP-copy/terminator puts sum to exactly the guard LHS, so guard-pass ⟹ total ≤ `max_size`. Multi-datagram appends remain gated by the unchanged over-charging upstream check (`:1079-1084`), and a flush-then-fresh lands in an NTB the guard just proved fits.
- **CRC mode:** CRC is appended *before* the guard (`:1051-1059`), so `skb->len` is the true length — full-MTU + CRC: `ALIGN(492,4)+16 = 508 ≤ 508` → sent (the old guard's 511-drop is gone). Without CRC 476 → 504. Drop threshold now L ≥ 481, unreachable (max 476/480).
- **Constants checked against the real v5.4 uapi `cdc.h`:** nth16=12, ndp16=8, dpe16=4 (→ `dgram_idx_len`=4); nth32=16, **ndp32=16** (`dwNextNdpIndex` *and* `dwReserved12` — not the 12 one might misremember), dpe32=8. The comment's NTB32 arithmetic (`ALIGN(16+L,4)+32`, drop >460: L=460 → 508 pass, 461 → 512 drop) is **correct**.
- **Test** (`test_ncm_gadget.py:280-293`): the `(?:ALIGN\()+` regex re-captures the nested expression; substitutions are collision-free in that order; `need(L) = (12+L+3)//4*4+16` is now the exact builder formula; the 14..599 equivalence loop **fails against the old guard** (old formula drops at L≥478 vs true 481) and the new `L = seg+4 → 508 ≤ in_cap` assertion pins the CRC case. Genuine regression coverage.

## Nit 2 — sha pin: mechanism sound, one honesty caveat

`usb_f_ncm.ko.sha256` committed at HEAD (tree clean at `a06c081`, confirmed from the worktree ref files); `test_shipped_module_is_the_build_of_the_current_patch` (`:304-310`) enforces both records — `.ko` raw bytes and patch text (patch is LF-only, so `read_text().encode()` ≡ raw bytes); `KO` is the tree's only `usb_f_ncm.ko` (`deck/firmware/release/lib/`); `build-usb_f_ncm.sh:28` prints the build's `sha256sum` to record. Editing the patch without rebuilding, or shipping a stale `.ko`, now fails the suite.

**Caveat, stated plainly:** this review session has no shell (my subagent's neither), so I could not recompute the two digests against the files — the pin is verified as a *sound, fail-closed mechanism*, not live-verified as matching. The suite enforces it; a PASS run of this test in release evidence closes the gap.

## Nit 3 — gadget.sh comment: added and accurate

New line before `rm_fn` (`gadget.sh:558`): `cleanup()` really does rmdir before stopping the bridge (`:202` vs `:204-205`, hardware-exercised), and the setup supervisor's bound/escalation (`:260-268`) is real. Exactly the documentation line requested.

## BLOCKERS

None.

## NITS

1. **Digests not live-recomputed here** (environment has no shell). Run `shasum -a 256 deck/firmware/release/lib/usb_f_ncm.ko deck/kernel/stock-patch/f_ncm-mk20.patch` once on the release evidence path and record the test PASS; the pin's enforcement is otherwise only as good as the last suite run.
2. **Pin file is hand-recorded**: `build-usb_f_ncm.sh` prints the hash but doesn't write `usb_f_ncm.ko.sha256`; having the build emit the file (and a `sha256sum -c` step) would remove the paste-stale-hashes hole the test can't see (deliberate two-line edits).
3. Carried over, unchanged, still non-blocking: `ncm_unwrap_ntb` host-NTB bounds checks (upstream), `net_fn`/MTU-table duplication across `mk20d` and `gadget.sh`.

The exact guard is provably tight for a fresh NTB in both directions with no `skb_put` overrun possible, the pin couples the shipped module to the current patch text fail-closed, and the supervisor dependency is now documented. Nit 1's CRC-mode edge — the one place the old guard was wrong — is fixed at exactly 508.

VERDICT: APPROVE
