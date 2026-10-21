# MK20 1.4.0 install — FAIL

- PASS installer exit 0 (OK: 1.4.0 confirmed good) - rc=0
- PASS loader: good=1.4.0 pending= active=1.4.0 - current=A 19 1.4.0 good=1.4.0 pending= attempts= active=1.4.0 last_rollback=1.3.0->1.2.1 not confirmed after 1 attempt(s)
- PASS acceptance: all checks pass (mode-aware keyboard check) - 12 checks
- PASS stock KeyboardDevice not running
- PASS mk20-kbd running
- PASS settings web running (mk20-agent --web)
- PASS maint state normal, 0 restarts - {"mode":"normal","restarts":0,"recent_crashes":0,"last_rc":0,"reason":"normal","version":"1.4.0"}
- PASS selftest: no FAIL line - 14 lines
- PASS health: status ok, web block present - {"enabled": true, "running": true, "url": "http://172.30.250.2:8780"}
- PASS kernel: no WARN/Oops-class lines after boot
- FAIL auto-L1 after boot with no key presses (host frames decoded: jpg_ok > 0) - jpg_ok=0 key_events=0 json_in=0
- PASS Mac daemon reconnected with ui.hello
- PASS web: / 200 with the right Host
- PASS web: wrong Host -> 403 (DNS rebinding guard) - 403
- FAIL web: catalogue 200
- PASS web: /pair/start 200 and pair-code file 0600 on the device - -rw-------    1 root     root            31 Jan  2 07:04 [0
- PASS web: /pair with the keyboard code -> 200 + session
- PASS web: /api/status 200 (paired)
- PASS web: maintenance action (debug off) accepted - 202
- PASS web: maintenance action without session/CSRF refused - 401
- FAIL web: logout 200 and pair-code file removed - ls: /run/mk20/pair-code: No such file or directory

Not automatable here: physical fb20 mode key, fb19 open-settings press, visual check, 24 h soak.
