Connection lost, reconnecting to https://agentn.global.api5.cursor.sh (attempt 1)...
Retry attempt 1...
**Not ready to ship: 2 blockers and 4 majors.** The awk parsing is mostly correct for the default subnet on macOS 11+. The serious problems are in the cutover design. The link-local fallback can't work while the Mac's MK20 service is set to Manual. And nothing keeps the old address and the new one both reachable while the switch happens.

On your parsing questions:
- **Service names with spaces or parentheses** work. Only the leading `(N) ` is stripped, and `index()` matches `Device: enX)` literally.
- **`(*)` disabled services** are parsed, but treated as eligible (MINOR 1).
- **Unsuffixed routes:** macOS prints 172.30.250.0/24 as `172.30.250/24`. The class B natural mask is /16, so a /24 always gets a suffix. The only unsuffixed form near this subnet is `172.30`, a /16. The awk wrongly treats it as /32, but correctly ignores it.
- **`link#N`** only appears in the Gateway column. `$4` is Netif on macOS 11+.

## BLOCKER

**B1. The link-local fallback can't work with a Manual MK20 service.** Affects `tools/tcp_transport.py:55-61,123`, `tools/mk20-host-net.sh:7-8,35` and `tools/en-relay.py:14`.
- A Manual IPv4 service gives en13 no 169.254 address, so there's no 169.254/16 route scoped to en13. An `IP_BOUND_IF` connect to 169.254.7.2 fails with "no route to host".
- Even if a packet left, its source would be 10.20.0.1 or 172.30.250.1. With the Mac and the device on different subnets mid-cutover, the device can't route a reply.
- The link-local test that passed (`NOTES-crash-20261018-and-tf-backup.md:102`) ran while en13 had a self-assigned address. That was before it was set to Manual 10.20.0.1 (`:176`).
- So the REFUSED message's "use link-local 169.254.7.2" and the plan's "preflight falls back to link-local" (`PLAN-production-firmware.md:175`) are both dead ends.
- To confirm: run `ifconfig en13 | grep 'inet '` and `route -n get -ifscope en13 169.254.7.2`.

Fix:
- Give the MK20 interface its own persistent link-local address: a second service on the same hardware port.
- Create it with `networksetup -createnetworkservice "MK20 link-local" "<Hardware Port>"`, then `-setmanual … 169.254.7.1 255.255.0.0` (or leave it on DHCP for a self-assigned address).
- Do this before installing 1.1.0, and verify with `ping -b "$dev" 169.254.7.2`.
- An `ifconfig alias` won't survive, because en13 detaches on every device reboot.
- Check that `route -n get 169.254.1.1` still goes out the primary interface, so other services are unaffected.

**B2. Nothing keeps both addresses reachable during the switch.** Affects `firmware/release/share/gadget.sh:194-197`, `tools/tcp_transport.py:123` and `feature/p0-mvp:deck/mk20deck/transport.py:30`.
- 1.1.0 drops 10.20.0.2, but the plan says it stays as an alias for one release (`PLAN-production-firmware.md:54,176`).
- The daemon that actually runs (p0-mvp, `DEFAULT_TCP = "10.20.0.2:17890"`) isn't updated. Composite mode has no ACM port to fall back on.
  - So the deck goes offline as soon as 1.1.0 boots.
  - It stays offline after `apply`, because a connect bound to en13 can't reach 10.20.0.2 from 172.30.250.1.
- The new `connect_device` has no 10.20.0.2 candidate:
  - If the host tools ship first, they lose the working 1.0.4 link.
  - They also can't follow a 1.1.0 → 1.0.4 rollback.
- Combined with B1, the only channel left is adb.

Fix:
- **Firmware 1.1.0:** add `usb0:2 10.20.0.2/24` and `--listen 10.20.0.2:$BRIDGE_PORT` (the bridge allows 4 listeners). Make it WARN-only in the self-test and remove it in the next release.
- **Daemon and `connect_device`:** try 172.30.250.2, then 10.20.0.2, then 169.254.7.2, each bound to the interface. Alternatively, derive the device address from the Mac interface's own IPv4 (x.y.z.1 → x.y.z.2).
- Ship the daemon change before `apply`.
- Pick one env-var convention: the daemon uses `MK20_TCP`, the new helper uses `MK20_HOST`/`MK20_BRIDGE_PORT`.

## MAJOR

**M1. The primary connection isn't bound to the MK20 interface.** `tcp_transport.py:69-70,122`
- p0-mvp binds every connection (`transport.py:85-99`); this change binds only the link-local one, so it's a TUN-safety regression.
- Before `apply` or after a revert, 172.30.250.2 goes out the default route. You get a 3 s connect timeout, or you reach an unrelated 172.30.250.2 that receives the deck's protocol traffic.
- A later VPN or TUN profile that captures 172.30/16 slips past the one-time `route get`.
- `MK20_HOST=169.254.7.2` is also unbound.

Fix: set `IP_BOUND_IF` on every automatic candidate, and on overrides inside 172.30.250/24, 10.20.0/24 or 169.254/16. A bound primary also fails instantly instead of after 3 s. The `socket://` URL in `mk20ctl-url.py` is unbound too, so point it at en-relay.

**M2. `apply` never checks the device.** `mk20-host-net.sh:34-42`
- It will run while 1.1.0 is still pending, after a rollback to 1.0.4, or when the device's `mk20.conf` sets a different `ECM_SUBNET`.
- The route check passes with no device involved, and the ping in `show` is informational. So the Mac and the device can end up on different subnets with no revert.

Fix:
- Before changing anything, read `/tmp/mk20/health.json` over adb (`MK20_ADB`, as `mk20-install.sh` does).
- Refuse unless `release.pending` is empty, `release.good` is set, and `net.subnet` is `$NET.0/24`.
- After `setmanual`, require `ping -b "$dev" -c 2 -t 3 "$NET.2"` to succeed, otherwise restore.
- Use ICMP rather than a TCP probe: the bridge serves one client at a time, so a probe would kick the daemon off.

**M3. Revert doesn't restore the previous configuration.** `mk20-host-net.sh:36,41,43`
- Only the IP address is saved; the method, mask and router are lost.
- If the service was on DHCP with a self-assigned address (the one state where link-local works), revert writes Manual `169.254.x.y/255.255.255.0`, which breaks link-local.
- There's no trap. Ctrl-C or an SSH drop during the 10 s check leaves the new address in place, unverified.

Fix:
- On the first `apply`, save the `-getinfo` output (method, IP, mask, router) to a state file.
- Restore with `-setdhcp` or `-setmanual ip mask [router]`, then re-read `-getinfo` to confirm.
- Add `trap restore INT TERM HUP` from `setmanual` until the checks pass, and make `revert` use the saved state too.

**M4. A network regression in 1.1.0 gets confirmed instead of rolled back.** `sbin/mk20d:54-59,138-139`, `gadget.sh:196-197,204`
- If `ifconfig usb0 172.30.250.2` or the bridge bind fails, the gadget script stops via `die`. Cleanup goes back to stock ACM+KD+adb.
- The self-test then reports "WARN composite down" and passes, so 1.1.0 is confirmed good. A missing link-local alias is also only a WARN.
- That fails your auto-rollback requirement for exactly what this release changes. The device stays reachable through stock adb/ACM, which is why this isn't a blocker.

Fix:
- At `mk20d:138`, save `grc=$?` from `gadget-boot.sh --boot`.
- While the release is pending and `MODE=composite`, treat `grc≠0` as a self-test failure. Do the same when the composite is up but nothing listens on link-local.
- Rollbacks from the flap monitor only happen after rc 0, so flaps caused by the host stay WARN.

## MINOR

**1. Disabled and duplicate services (`mk20-host-net.sh:17`).** The first service matching the device wins, including a disabled `(*)` one. You get a no-op change, a revert, and exit 4 with a misleading "TUN?" message, while the enabled duplicate is never touched. Select exactly one enabled service:

```sh
svcs=$(networksetup -listnetworkserviceorder | awk -v d="$dev" '
  /^\([0-9]+\) / { s=$0; sub(/^\([0-9]+\) /, "", s); on=1; next }
  /^\(\*\) /     { s=$0; sub(/^\(\*\) /, "", s);     on=0; next }
  index($0, "Device: " d ")") { print on "\t" s }')
[ "$(printf '%s\n' "$svcs" | awk -F'\t' '$1==1' | wc -l | tr -d ' ')" = 1 ] || { echo "need exactly 1 enabled service on $dev: $svcs" >&2; exit 1; }
svc=$(printf '%s\n' "$svcs" | awk -F'\t' '$1==1 {print $2}')
```

**2. Same MAC on several interfaces (`:15`).** `head -n 1` silently picks one when more than one interface has the MK20 MAC. Two units share the fixed host MAC until per-unit MACs land (PLAN:180). Refuse on more than one match, and capture the Hardware Port name, which the B1 fix needs.

**3. Overlap parsing (`:20-25`).**
- Not idempotent: after a successful `apply`, the local `172.30.250.1 … lo0` entry counts as an overlap, so re-running `apply` refuses.
- For non-default subnets, abbreviated destinations like `10.20/24` and `172.30/24` are missed. The post-apply `route get` on `.2` partly covers this.
- It fails open: if `netstat` fails or its output can't be parsed, the result is "no overlap".
- `$4` is hard-coded as the Netif column.

```sh
overlap() { # rc 2 = unparseable -> caller refuses
  netstat -rn -f inet | awk -v n="$NET" -v d="$dev" '
    $1 == "Destination" { for (i = 1; i <= NF; i++) { if ($i == "Flags") fc = i; if ($i == "Netif") nc = i }; next }
    nc && $1 ~ /^[0-9]/ {
      if ($nc == d || $nc == "lo0") next
      k = split($1, p, "/"); m = split(p[1], o, "."); for (j = m + 1; j <= 4; j++) o[j] = 0
      plen = (k == 2) ? p[2] : ($fc ~ /H/) ? 32 : (o[1] < 128 ? 8 : (o[1] < 192 ? 16 : 24))
      if (plen >= 24 && (o[1] "." o[2] "." o[3]) == n) print }
    END { if (!nc) exit 2 }'
}
```

Call it as `o=$(overlap) || { echo "cannot parse netstat" >&2; exit 3; }`.

**4. `status` can abort (`:28`).** Under `set -e`, `-getinfo | grep` exits 1 when the service has no IPv4 address, which aborts `status`. Add `|| true`.

**5. Misleading diagnostics (`:29-30`).** `route_if 169.254.7.2` always shows the primary interface; use `route -n get -ifscope "$dev" 169.254.7.2` instead. Use `ping -b "$dev"`, and print a TUN warning when the route isn't on `$dev`.

**6. Loose subnet validation (`:14`).** It accepts octets above 255, `127.*`, `169.254.*`, `0.*` and `224+`. Mirror `v_subnet` from `mk20lib.sh:30-39`.

**7. Unverified `setmanual` (`:37-41`).**
- Check for root up front.
- Re-read `-getinfo` after each change rather than trusting networksetup's exit status.
- Check that `$dev` shows `status: active` first, so an unplugged or rebooting device isn't reported as a TUN problem.

**8. Smaller `tcp_transport.py` issues.**
- `:57-61`: `if_nametoindex` and `setsockopt` run outside the `try`, so the socket leaks when they fail.
- `:60-61`: on Linux, `SO_BINDTODEVICE` needs CAP_NET_RAW. On other platforms the code silently connects unbound; raise instead.
- `:40`: the interface-name regex misses names containing `-` or `.`, so their `ether` line is attributed to the previous interface. Use `^(\S+?):\s`.

**9. Tests.**
- `test_tcp_transport.py:77-78`: `dead` and `dport` are unused. On macOS 127.0.0.2 isn't on lo0, so the first candidate times out instead of being refused, and the comment is wrong.
- None of the risky code is tested. Add PATH-shim fixtures like the ones in `tests/test_install.py:128-132`, covering these networksetup/netstat/route outputs:
  - a `(*)` service, names with spaces and parentheses, and two services on one device;
  - `10.20/24`, `172.30.250/24`, the lo0 local entry, `link#N`, and garbled netstat output.
- Also mock `setsockopt` and assert that `IP_BOUND_IF` is set on every automatic candidate.

**Safe cutover order once fixed:**
1. Add the link-local service (B1) and check `ping -b $dev 169.254.7.2`.
2. Ship the daemon with the new candidates while the device is still on 1.0.4.
3. Install 1.1.0 with the 10.20.0.2 alias, using `mk20-install.sh --wait` until it reports `good=1.1.0`.
4. Run `apply`, which now checks the device and pings it bound to the interface.
5. Remove the alias in the next release.
done rc=0
