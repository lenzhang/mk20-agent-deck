BLOCKER

- `deck/firmware/release/sbin/mk20d:49-63,160-165`, `deck/firmware/release/share/gadget.sh:193-195` — A failed composite or missing link-local alias is only a `WARN`; `selftest` returns success and confirms 1.1.0 as good. Thus failures will not roll back to 1.0.4. Make expected composite state, `172.30.250.2`, `169.254.7.2`, and both listeners mandatory host-independent checks during probation; alias setup failure should be fatal.

- `deck/tools/mk20-host-net.sh:35-42`, `deck/tools/tcp_transport.py:55-63` — The cutover removes the Mac’s old manual address without ensuring it has a `169.254/16` source address. `IP_BOUND_IF` selects an interface, not a source address; during the mixed 1.0.4/1.1.0 state, the device may have no return route to the Mac’s opposite RFC1918 address. Provision a stable link-local address such as `169.254.7.1/16` on the matched interface and verify a bound TCP connection before changing the primary address, or retain `10.20.0.2` as a transitional device alias.

MAJOR

- `deck/tools/mk20-host-net.sh:20-25` — Overlap detection is incomplete and fail-open. Unprefixed `172.30.250` happens to match, but is incorrectly treated as `/32`; supernets such as `172.30/16` are missed. `$4` hard-codes one `netstat` layout, and a failed/unparseable `netstat` produces an empty “safe” result. Parse the header to locate `Destination`/`Netif`, normalize abbreviated destinations, perform numeric range intersection for every prefix, and fail closed. Treat `link#N` strictly as the gateway column.

- `deck/tools/mk20-host-net.sh:16,36-43` — `(*)` disabled services are accepted after the marker is stripped, but remain disabled, so no route appears. Failure recovery saves only the old IP and restores it as manual `/24`, losing DHCP/manual mode, mask, router and enabled state. Interruptions after `setmanual` also bypass recovery. Detect disabled services explicitly; snapshot the complete IPv4/enabled state and install an EXIT/INT/TERM rollback trap until verification succeeds.

- `deck/tools/tcp_transport.py:120-130` — The primary `172.30.250.2` attempt is unbound, so it can traverse a changed TUN/overlapping route or contact an unrelated host. Also, `MK20_HOST=169.254.7.2` disables interface binding. `IP_BOUND_IF=25` with `IPPROTO_IP` is correct; apply it to every automatic MK20 candidate and to link-local overrides.

- `deck/tools/mk20-host-net.sh:35-42` — `apply` exits successfully even when its final ping fails. Running it before upgrading 1.0.4 therefore intentionally destroys primary reachability while relying on an untested rescue path. Require successful link-local preflight before mutation; after mutation, route verification may succeed without device reachability, but that state should be reported distinctly from a completed cutover.

MINOR

- `deck/tools/mk20-host-net.sh:14` — `MK20_SUBNET` accepts invalid octets such as `999.999.999`. Validate each octet as decimal `0..255`.

- `deck/tools/test_tcp_transport.py:65-85` — Tests exercise only unbound loopback fallback. Add mocked tests for `IP_BOUND_IF`, `MK20_HOST` link-local, service names with spaces/parentheses, disabled services, abbreviated/link routes, supernet overlaps, and exact rollback restoration.
done rc=0
