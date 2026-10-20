# K4 rc2 (1.3.0) install — FAIL

- FAIL installer exit 0 (OK: 1.3.0 confirmed good) - rc=3
- FAIL loader: good=1.3.0 pending= active=1.3.0 - current=A 17 1.2.1 good=1.2.1 pending= attempts= active=1.2.1 last_rollback=1.3.0->1.2.1 not confirmed after 1 attempt(s)
- PASS loader: 1.2.1 still installed as fallback
- PASS acceptance adb shell
- PASS acceptance loader: good release current, none pending - current=A 17 1.2.1 good=1.2.1 pending= attempts= active=1.2.1 last_rollback=1.3.0->1.2.1 not confirmed after 1 attempt(s)
- PASS acceptance health: status ok - v=1.2.1 status=ok
- FAIL acceptance [K4 rule] composite + kd=0 + watchdog + selftest (tool expects kd=1, stock-era check) - kd=1 selftest=True
- PASS acceptance health: headroom (FAT >= 1024 KiB, mem >= 20 MiB) - fat=1217 mem=79196
- PASS acceptance mac: MK20 interface present - en13
- PASS acceptance mac: route to device via MK20 interface (not TUN) - 172.30.250.2 -> en13
- PASS acceptance ping device (primary) - loss=0.0% avg=1.438ms
- PASS acceptance bridge listening on primary + link-local (device netstat) - primary=True ll=True
- PASS acceptance ping link-local 169.254.7.2 (bound) - 0.0% packet loss
- PASS acceptance tcp bridge: active connects skipped (use --tcp when no other client uses the bridge) - passive
- PASS acceptance hid: Mac enumerates 1d6b:0104 keyboard
- acceptance total (K4-judged): 11/12
- FAIL K4: stock KeyboardDevice not running
- FAIL K4: mk20-kbd running
- PASS K4: kbd mode normal (not safe)
- FAIL K4: maint state.json normal, 0 restarts - {"version":"mk20-kbd-0.3","mode":"normal","estopped":0,"gd32_rw":1,"knob_init":"ok","knob_ok":6,"knob_timeouts":0,"knob_tx_errors":0,"panels_written":21,"panels_dirty":0,"write_errors":0,"key_events":2,"knob_events":0,"echo_frames":0,"other_frames":0,"jpg_ok":0,"jpg_bad":0,"jpg_size":0,"json_in":0,"host":1,"host_tx_errors":0,"host_tx_timeouts":0,"backlight":-1,"hid_open":1,"hid_reopens":0,"hid_replaced":0}
- PASS K4: mk20d selftest has no FAIL line - 8 lines
- kernel (fresh boot dmesg): 0 WARN/Oops-class lines

Not automatable here (needs user): physical key/knob presses, visual panel check, forced-fail rollback, crash-loop test, 24 h soak.
