#!/usr/bin/env python3
"""Verdict for the MK20 fw 1.8.0-rc1 24 h soak: verdict.py <summary.json> <soak.jsonl>  -> VERDICT.md on stdout.
PASS = >= 270 samples (24 h / 5 min = 288, minus slack) AND failed 0, reboots 0, kd_restarts 0, no new rollbacks,
composite_losses 0, host_link_stalls 0, kernel_mem_trend != growing. Else FAIL (a fault counter > 0) or
INCONCLUSIVE (too few samples / the run did not finish). Generated automatically; no tag is created."""
import json, sys
sm, jl = sys.argv[1], sys.argv[2]
try:
    s = json.load(open(sm))
except Exception as e:  # noqa: BLE001
    print("# fw 1.8.0-rc1 24 h soak\n\nVerdict: INCONCLUSIVE (summary unreadable: %r)" % (e,)); sys.exit(0)
rows = [json.loads(l) for l in open(jl) if l.strip()]
hl = {}
for r in rows:
    k = r.get("host_link") or "n/a"; hl[k] = hl.get(k, 0) + 1
faults = {k: s.get(k) for k in ("failed", "reboots", "kd_restarts", "composite_losses", "host_link_stalls")}
faults["rollbacks"] = len(s.get("rollbacks") or [])
bad = {k: v for k, v in faults.items() if v}
trend = s.get("kernel_mem_trend")
if bad or trend == "growing":
    v = "FAIL"
elif s.get("running") or (s.get("samples") or 0) < 270:
    v = "INCONCLUSIVE"
else:
    v = "PASS"
km = s.get("kernel_mem") or {}
print("# fw 1.8.0-rc1 (CDC-NCM) 24 h soak on the iMac — %s\n" % s.get("start"))
print("Verdict: %s (automatic; criteria in ~/mk20-backup/soak/launchd/verdict.py)\n" % v)
print("- window: %s → %s, samples %s, failed %s, first_fail %s" % (rows[0].get("time") if rows else "?", s.get("ended") or s.get("last"), s.get("samples"), s.get("failed"), s.get("first_fail")))
print("- reboots %s, kd_restarts %s, rollbacks %s, composite_losses %s" % (s.get("reboots"), s.get("kd_restarts"), s.get("rollbacks"), s.get("composite_losses")))
print("- host_link_stalls %s, self-heals %s, bridge_stalls_total %s, host_flushes_total %s" % (s.get("host_link_stalls"), s.get("host_link_selfheals"), s.get("bridge_stalls_total"), s.get("host_flushes_total")))
print("- host_link per sample: %s (\"idle\" = no deck client connected)" % json.dumps(hl, sort_keys=True))
print("- min MemAvailable %s kB, min FAT free %s kB, kernel_mem_trend %s, SUnreclaim slope %s kB/h (halves %s)" % (s.get("min_mem_kb"), s.get("min_fat_free_kb"), trend, km.get("sunreclaim_kb_h"), km.get("sunreclaim_half_kb_h")))
if s.get("selftest_warn"):
    print("- selftest WARN/FAIL: %s" % json.dumps(s.get("selftest_warn"), ensure_ascii=False))
if bad:
    print("- fault counters > 0: %s" % json.dumps(bad))
print("\nKnown context: Mac en2 MTU 1500 vs device 462 (unchanged, needs sudo); TCP unaffected.")
