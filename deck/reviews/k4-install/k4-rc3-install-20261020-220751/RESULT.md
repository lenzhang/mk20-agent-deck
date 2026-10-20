# K4 (1.3.1) install — PASS

- PASS installer exit 0 (OK: 1.3.1 confirmed good) - rc=0
- PASS loader: good=1.3.1 pending= active=1.3.1 - current=B 18 1.3.1 good=1.3.1 pending= attempts= active=1.3.1 last_rollback=1.3.0->1.2.1 not confirmed after 1 attempt(s)
- PASS loader: 1.2.1 still installed as fallback
- PASS acceptance adb shell
- PASS acceptance loader: good release current, none pending - current=B 18 1.3.1 good=1.3.1 pending= attempts= active=1.3.1 last_rollback=1.3.0->1.2.1 not confirmed after 1 attempt(s)
- PASS acceptance health: status ok - v=1.3.1 status=ok
- PASS acceptance health: composite + kd=0 + mk20-kbd running (normal) + watchdog + selftest - PASS kd=0 (mk20-kbd mode) PASS mk20-kbd running PASS kbd status fresh (1s) PASS kbd gd32 knob_ok=6 frames=0 PASS kbd panels 21/21 PASS udc bound PASS ttyGS0 PASS watchdog armed PASS bridge 172.30.250.2:17890 PASS bridge link-local PASS hid INFO router not running INFO agent disabled
- PASS acceptance health: headroom (FAT >= 1024 KiB, mem >= 20 MiB) - fat=1216 mem=79132
- PASS acceptance mac: MK20 interface present - en13
- PASS acceptance mac: route to device via MK20 interface (not TUN) - 172.30.250.2 -> en13
- PASS acceptance ping device (primary) - loss=0.0% avg=1.081ms
- PASS acceptance bridge listening on primary + link-local (device netstat) - primary=True ll=True
- PASS acceptance ping link-local 169.254.7.2 (bound) - 0.0% packet loss
- PASS acceptance tcp bridge: active connects skipped (use --tcp when no other client uses the bridge) - passive
- PASS acceptance hid: Mac enumerates 1d6b:0104 keyboard
- acceptance total (K4-judged): 12/12
- PASS K4: stock KeyboardDevice not running
- PASS K4: mk20-kbd running
- PASS K4: kbd mode normal (not safe)
- PASS K4: maint state.json normal, 0 restarts - {"mode":"normal","restarts":0,"recent_crashes":0,"last_rc":0,"reason":"normal","version":"1.3.1"}
- PASS K4: mk20d selftest has no FAIL line - 13 lines
- kernel (fresh boot dmesg): 0 WARN/Oops-class lines

Not automatable here (needs user): physical key/knob presses, visual panel check, forced-fail rollback, crash-loop test, 24 h soak.
