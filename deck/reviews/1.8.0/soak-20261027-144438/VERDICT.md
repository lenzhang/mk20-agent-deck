# fw 1.8.0-rc1 (CDC-NCM) 24 h soak on the iMac — 20261027-144438

Verdict: PASS (automatic; criteria in ~/mk20-backup/soak/launchd/verdict.py)

- window: 2026-10-27T14:44:38+0800 → 2026-10-28T14:44:38+0800, samples 288, failed 0, first_fail None
- reboots 0, kd_restarts 0, rollbacks [], composite_losses 0
- host_link_stalls 0, self-heals 0, bridge_stalls_total 0, host_flushes_total 0
- host_link per sample: {"idle": 287, "unknown": 1} ("idle" = no deck client connected)
- min MemAvailable 76744 kB, min FAT free 1210 kB, kernel_mem_trend flat, SUnreclaim slope 3.6 kB/h (halves [1.5, 3.4])

Known context: Mac en2 MTU 1500 vs device 462 (unchanged, needs sudo); TCP unaffected.
