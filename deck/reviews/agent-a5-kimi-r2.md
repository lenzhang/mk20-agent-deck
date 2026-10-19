# A5 fix r1 recheck — Kimi K3 (kimi-code), 2026-10-19 22:09–22:19 +08 — WATCHDOG KILLED at 600 s before formal verdict

Scope: /tmp/a5-kimi/REVIEW-a5-fix.diff (fix r1 vs wip/agent-a5 d3cf276). Extracted from reasoning log: items 1–4 all verified (S_ERROR/non-emergency S_STOPPED resubmit OK, emergency stops still need allow cycle; loop_service aborts after finish; acting test fix OK; kbdc released-without-token safe and also prevents an idle-time lease_lost wedge). No BLOCKER/MAJOR raised. Noted: a resubmit request that never matches would run until the 15 s pytest timeout in real mode (fails loudly, not silently).

Raw log tail:
```
g #1's claim "emergency stops can NOT be cleared without a kbd allow cycle": after emergency finish, allowed=0, cancelled=1. loop_submit requires allowed && !cancelled → -1 ✓. K_ALLOW → allowed=1, cancelled=0 → S_IDLE. K_ALLOW only from kbdc when kbd status allowed && !estopped && !notified_allowed && !require_off. After estop: notified_allowed=0 (cleared by estop event / kbdc_lease LEASE_ESTOP), require_off set if estop rpc failed. kbd side: after estop, kbd disallows (estopped=1). For K_ALLOW to fire, kbd status must show allowed && !stopped — requires physical re-allow on the keyboard ✓. And the loop-side: even if a rogue K_ALLOW were injected (fake events), that's the test harness, not production. ✓.

  Wait, one more consideration for #1: non-emergency S_STOPPED from finish() via the UI-failure path (loop.c lines 27-31): if the ui port fails during finish of a NON-emergency stop → state S_STOPPED, allowed=0, cancelled=1, reason "kbd_lost" — that's treated as emergency-ish (kbd_lost) → requires fresh allow ✓ correct, since kbd is gone.

  And "a new task can be submitted while kbd still allows" — loop_submit from S_STOPPED/S_ERROR requires allowed=1. After ordinary finish, allowed stays 1 ✓. The kbdc side: notified_allowed stays 1 — no new K_ALLOW, but none needed since submit works directly from STOPPED/ERROR now. Hmm wait — was loop_submit's acceptance of STOPPED/ERROR pre-existing? The diff doesn't touch loop_submit, and the review (r1) said "loop_submit only from S_IDLE/S_DONE (line 39)". So between the r1 review snapshot (21:36) and the fix snapshot (21:57), loop_submit was ALREADY changed to accept S_STOPPED/S_ERROR? The current diff is between a5-snap (21:57) and a5-kimi (22:00-22:04). So yes — the 21:57 snapshot already had the loop_submit change; this r1 fix diff adds the K_ALLOW→S_ERROR transition, the loop_service contract, the kbdc released fix, and tests. The user's scope says "ONLY the diff in REVIEW-a5-fix.diff" — loop_submit's wider acceptance is in the current file and part of the overall fix but not in THIS diff. I should still sanity-check it since finding #1's verification depends on it (it IS in the shipped tree). loop_submit line 47: accepts IDLE/DONE/STOPPED/ERROR when allowed && !cancelled ✓ and resets state ✓. From S_DONE: allowed=1, cancelled=0 → resubmit after done works ✓ (pre-existing behavior).

  Now, potential NEW bugs introduced by THIS diff:

  (a) loop_service early-return change: any path where state is IDLE but a task IS active? No — active states are distinct. Any path where loop_service is called with state DONE and the caller needs 0? The loop_test probe is new and expects -1. Runtime callers — need to check runtime.c to be sure. Let me grep.

  (b) The K_ALLOW-from-S_ERROR transition: any risk? If state==S_ERROR and a stale K_ALLOW arrives... K_ALLOW only enqueued by kbdc on a fresh allow edge (notified_allowed logic) — after an ordinary finish, notified_allowed stays 1, so K_ALLOW only after kbd disallow→allow. Then S_ERROR→S_IDLE is correct (fresh allow = user re-enabled). ✓. Also clears cancelled — cancelled would be 0 anyway after non-emergency. If S_ERROR was reached via... all S_ERROR finishes are emergency=0 (clock, wall_budget, step_budget, model_budget, action_timeout, observe, model, policy rejects, unsupported). So cancelled stays 0. K_ALLOW in S_ERROR: allowed=1 (already), cancelled=0, state=IDLE ✓ harmless.

  Edge: S_ERROR + emergency? finish(S_ERROR,...) is never called with emergency=1 — checked all call sites: clock(0), wall_budget(0), step_budget(0), model_budget(0), observe(0), model(0), policy reject(0), unsupported(0), action(0), action_timeout(0). ✓.

  (c) kbdc released-without-token: analyzed above — safe. One more scenario: the agent acquires lease (token=5), then a "released" notif for THAT lease arrives without the agent releasing (kbd-side timeout release with reason "released"?). If kbd uses reason "released" only in response to our release, fine. If kbd can spontaneously release (e.g., TTL expiry on kbd side) and calls it "released"... then token!=0 → not ignored → K_LEASE_LOST → stop ✓ still safe. The ignore only applies when token==0 (we believe no lease) or releasing. ✓.

  Edge: token==0 while a lease.acquire rpc is in flight? During acquire rpc, releasing=0, token=0 (old). A stale "released" notif drained during acquire's reply-wait → ignored ✓ (correct — it's about the OLD lease). Then acquire reply sets token ✓. Good — actually this is a case the fix HANDLES correctly and the old code didn't.

  Edge: double-release? kbdc_lease RELEASE with !token returns 0 early (line 198) — no rpc → kbd won't send notif. ✓.

  (d) loop_test.c changes: 
  - resubmit logic: `cJSON_GetArrayItem(resubmit, next_submit)` with resubmit possibly NULL → NULL ✓. `cJSON_GetArraySize(resubmit)` NULL → 0 ✓.
  - The resubmit match condition fires ONCE per request (next_submit++ after firing) ✓.
  - BUT: potential infinite loop — if a resubmit request never matches (state never reaches), the loop runs until max_ticks=20000 → loop_stop → exits. Tests: run timeout 15s; 20000 ticks non-real is fast; real mode 5ms/tick = 100s > 15s timeout! Could a real-mode test hang? test_real_ctl_new_task_after_finish_ipc: resubmit state error/stopped — reached normally. If a bug prevented reaching it, the test would hit the 15s subprocess timeout → pytest fails (not silent). OK.
  - submissions array: if resubmit is NULL, submissions stays empty array, added to out ✓ harmless.
  - probe_service: two loop_service calls post-run. For DONE state → -1 each ✓. BUT — wait: in real mode, loop_service calls kbdc_poll → poll_inner may do a status rpc every 250ms... After the task, the kbd connection may be closed (client.shutdown in fixture happens AFTER run returns). During probe, connection alive →

WATCHDOG KILLED at 600s
KIMI DONE 605s
```
