# MK20 1.6.1 install — FAIL

- PASS installer exit 0 (OK: 1.6.1 confirmed good) - rc=0
- PASS loader: good=1.6.1 pending= active=1.6.1 - current=B 26 1.6.1 good=1.6.1 pending= attempts= active=1.6.1 last_rollback=1.3.0->1.2.1 not confirmed after 1 attempt(s)
- FAIL acceptance: all checks pass (mode-aware keyboard check) - ping device (primary); ping link-local 169.254.7.2 (bound)
- PASS stock KeyboardDevice not running
- PASS mk20-kbd running
- PASS settings web running (mk20-agent --web)
- PASS maint state normal, 0 restarts - {"mode":"normal","restarts":0,"recent_crashes":0,"last_rc":0,"reason":"normal","version":"1.6.1"}
- PASS selftest: no FAIL line - 15 lines
- PASS health: status ok, web block present - {"enabled": true, "running": true, "url": "http://172.30.250.2:8780"}
- PASS health: /run/mk20 on tmpfs (run_tmpfs) - True
- PASS kernel: no WARN/Oops-class lines after boot
- FAIL auto-L1 after boot with no key presses (host frames decoded: jpg_ok > 0) - jpg_ok=0 key_events=0 json_in=0
- PASS Mac daemon reconnected with ui.hello
- FAIL web: / 200 with the right Host
- FAIL web: wrong Host -> 403 (DNS rebinding guard) - 000
- FAIL web: catalogue needs a session (401 unauthenticated)
- FAIL web: /pair/start 200 and pair-code file 0600 on the device - ls: /run/mk20/pair-code: No such file or directory
- FAIL web: /pair with the keyboard code -> 200 + session
- FAIL web: /api/status 200 (paired)
- FAIL web: maintenance action (debug off) accepted - 000
- FAIL web: maintenance action without session/CSRF refused - 000
- FAIL web: logout 200 and pair-code file removed - ls: /run/mk20/pair-code: No such file or directory

Not automatable here: physical fb20 mode key, fb19 open-settings press, visual check, 24 h soak.
