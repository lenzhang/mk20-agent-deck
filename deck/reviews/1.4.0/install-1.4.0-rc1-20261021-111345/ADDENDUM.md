# 1.4.0-rc1 acceptance addendum (11:20-11:30 SGT)

The 3 FAILs in RESULT.md were re-examined:

1. `auto-L1 ... jpg_ok > 0` — **environment, then a Mac-side bug, now PASS.** The Mac display was asleep (user away);
   mk20deck paused and its paused loop skipped the link check, so after the install reboot it never reconnected
   (json_in=0). Fixed in mk20deck (ebe8814: reconnect while paused, start paused if already asleep; 0d…: re-send the
   "Mac 已休眠" frame every 10 s). After redeploy: device `jpg_ok=5, key_events=0` → with no key press since boot the
   keyboard followed the Mac to L1 by itself (frames are only decoded on L1/L4). See l1-kbd-status-after-deck-fix.txt.
2. `web: catalogue 200` — **check was wrong**: /api/catalogue requires a paired session (401 unauthenticated is correct);
   paired → 200, 8 providers (web2/).
3. `web: logout 200` — **check was wrong**: curl POST without a body sends no Content-Length → 411; with `-d '{}'`
   (as browsers send) → 200, and the session is invalid afterwards (401).

Web re-run (web2/): unauth 401, pair/start 200 + 0600 code file, wrong code 401, pair 200 (HttpOnly, SameSite=Strict),
code file removed after pairing, catalogue 200 (8 providers), status 200, write without CSRF 403, cross-origin write
403, logout 200 → 401. Verdict for 1.4.0-rc1 on the device: **PASS** (physical key presses, visual check and 24 h soak
remain).
