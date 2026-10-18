## Findings

1. ep5out stall

Most likely: ep5 RX PIO/double-FIFO handshake or an ep5 hardware erratum. The mappings look correct:

- `ep_fifo_out[5] = 8` → `sunxi_udc.ep[8]` (ep5out).
- RX IRQ scans endpoints 1–5 and dispatches through that mapping.
- DMA channel records array index 8 while hardware DMA uses physical endpoint 5.
- FIFO 7168–8191 is exactly the valid final 1024-byte slot.

DMA remains a candidate only for full 512-byte packets: `big_req` permits DMA for ECM’s large RX request, but DMA starts only when FIFO count equals maxpacket. Short ARP/ping frames stay PIO. Since one frame succeeded, endpoint configuration and at least one RX interrupt worked.

HID OUT is lower risk: f_hid queues 9-byte requests, so it never enters DMA. Its 9-byte FIFO bank rounds to 512 bytes; double buffering exactly fits the 1024-byte ep5out slot. Nevertheless, it still shares ep5 RX interrupt/PIO/double-FIFO logic, so risk is low, not zero.

Safe workaround: never place ECM bulk on ep5. Both new configurations do this—ECM gets ep1, ADB ep2, and only HID OUT uses ep5. There is no evident runtime knob to disable ep5 DMA/double FIFO. Binary-patching the FIFO table or forcing PIO is possible but not yet safe or justified.

2. Endpoint budget confirmed

Tina54 `f_hid.c` unconditionally autoconfigures both interrupt endpoints: IN at line 768 and OUT at 773; bind fails if either is unavailable. Therefore:

- ECM + HID requires three interrupt endpoints even without ADB.
- Full ECM+HID+ADB requires ep3-int + ep4-int + ep5out-int.
- Link order ECM → HID → ADB produces the intended allocation.
- Alternatives without the second swap: ECM+ADB+bridge; HID+ADB without ECM; or a custom IN-only HID function using EP0 `SET_REPORT` for output. The latter requires kernel/function work, not configfs alone.

3. Gadget v3

Core sequencing is correct: watcher queue, untouched preflight, procd KD stop, adbd before bind, link order, PTY bridge, shadowed `setusbconfig`, and EXIT rollback.

Remaining hazards:

- The rollback copy and detached waiter are unchecked; unlike `postswap-check.sh`, there is no `cp` result or `kill -0` validation.
- `gadget-ok` is published before the old confirmation marker is removed, creating a narrow lost-confirmation race.
- Directly invoking `--run` or `--rollback` through adb is unsafe: cleanup kills adbd and can kill its own session group. Invoke with no arguments so it queues, or queue rollback explicitly.
- Rollback’s preflight occurs before the EXIT trap and unnecessarily requires g1/adbd/ECM availability; an abnormal state can prevent rollback from starting.
- “KD running” does not prove screens/protocol work. Confirm only after visual output and a successful bridge command.

4. Boot swap/failsafe

Patched→patched handling is correct: it verifies the recorded baseline, preserves both stock copies, and resets the revert hash to stock. The second image independently hashes to `1fc8525a…` and contains ep3 `0x38/INT` plus ep5out caps `0x2c`.

The postswap detach fix is sound in watcher context. Remaining fail-open cases:

```55:58:deck/m2-scripts/remote/bootswap.sh
if [ -f $PC ]; then mkdir -p $S/mk20/boot-once; cp -f $PC $S/mk20/boot-once/postswap-check.sh
  sha256sum $S/mk20/boot-once/postswap-check.sh | awk '{print $1"  postswap-check.sh"}' > $S/mk20/boot-once/postswap-check.sh.sha256
  echo "staged postswap-check"; else echo "WARNING: no $PC (no auto-revert)"; fi
```

It still writes/reboots when the checker is absent, and copy/hash failures are unchecked. `postswap-check.sh` also exits without reverting when SHA metadata is missing or p4’s prefix differs. A watcher checksum failure merely quarantines the job.

## Decisions

- **(a) Second swap: NO-GO as currently automated.** Blocker: failsafe staging is fail-open. It becomes acceptable if those checks are made fatal, or if `boot-swap.noreboot` is used and the staged checker, checksum, stock copies, and boot target are independently verified before power-cycle.
- **(b) First full composite: NO-GO now.** Prerequisites: successful no-HID test, confirmed second boot, and hardened rollback launch. Then run as a controlled 180-second test; validate HID OUT specifically before confirming.
- **(c) No-HID run: GO.** It fits the current ep3-only kernel and avoids ep5 entirely. Invoke without arguments, keep physical power-cycle access, and confirm only after ECM RX/TX, ADB, TCP bridge/KD protocol, and screens all pass.