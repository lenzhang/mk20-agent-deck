# iMac 24 h soak tooling for fw 1.8.0-rc1 (copies; live files in ~/mk20-backup/soak/launchd/)

- `com.leo.mk20-soak-180` — `caffeinate -i -s /bin/sh soak-run.sh` → `tools/mk20_soak.py --hours 24 --interval 300 --out ~/mk20-backup/soak`
  from the frozen deploy tree `~/mk20-deploy/deck` (4eb998f). One-shot (`started.marker`), then `soak-push.sh final`.
- `com.leo.mk20-soak-180-push` — every 3 h: `soak-push.sh` copies jsonl/summary/log to `deck/reviews/1.8.0/soak-<stamp>/`
  on feature/ncm-gadget and pushes; once the summary says `"running": false` it does the final step itself.
- final: `verdict.py` → `VERDICT.md` (PASS/FAIL/INCONCLUSIVE), push, then one PROGRESS.md line on main via `prog.py`.
- Both plists are bootstrapped from `~/mk20-backup/soak/launchd/` (not ~/Library/LaunchAgents), so they do not come back after logout/reboot.
- Known nit: soak-run.log `soak exit rc=` always prints 0 (`$?` is read after `$(date)`); the summary json is authoritative.
