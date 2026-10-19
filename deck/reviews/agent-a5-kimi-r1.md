# A5 cross-review r1 — Kimi K3 (kimi-code), 2026-10-19 — WATCHDOG KILLED at 900 s before final verdict

Scope: WIP snapshot diff at 21:36 (+08), /tmp/a5-kimi/REVIEW-a5.diff. Extracted findings from Kimi's reasoning log (no formal verdict emitted):

1. MAJOR (Kimi) — loop.c: no transition out of S_ERROR (K_ALLOW only from DISABLED/STOPPED; loop_submit only from IDLE/DONE); and S_STOPPED from non-emergency finishes (confirm_denied/no_progress/input_unsupported) never gets a new K_ALLOW because kbdc notified_allowed stays 1 → agent wedged until keyboard allow off/on or process restart.
2. MAJOR (Kimi) — loop_service (guard) returns 0 after a non-emergency finish (wall_budget, action_timeout, confirm_denied...), so in-flight synchronous ports are not told to abort; HID contained today by lease release/token=0, but a future VNC writer could act after termination; test for action_timeout masks it (fake act recorded). Fix: return nonzero once !active(l).
3. MINOR — kbdc 'released' lease_revoked swallow window only covers the release rpc; safe with current kbd ordering (event before reply) but fragile. Fix: ignore reason=released when no token held.

Raw log tail:
```

  BUT there's another subtle one: the estop event during the acquire rpc → event() sets stopped=1, enqueues K_ESTOP → rpc continues (bad=0, continue) → reply processed → token set! Then lease returns 0 with c->stopped=1 and token set. Loop gets K_ESTOP via next poll → finish. act() blocked by stopped ✓. Token exists but acts refused ✓, and kbd side revoked on estop anyway ✓. Safe.

  Now let me also verify the `switch_input` order issue I spotted: it does ALLUP then VNC acquire. But wait — there's an ordering bug candidate: in switch_input for the FROZEN case, it acquires HID lease if !leased, then ALLUP, then LEASE_VNC (release HID + acquire vnc). Per spec §3.3: "连续 2 次失败 → 冻结 → all-up → 切到 VNC" ✓ matches.

  And the lock-screen check `l->frame.locked && l->input.state == INPUT_FROZEN` — prevents VNC switch on lock screen ✓ (spec: lock screen only HID).

  Now, `arbitration_wants_hid` requires healthy>=3 and VNC — switch back at "idle point": switch_input is called from S_OBSERVING (between actions) — that's the idle point ✓.

  One more thing to check: `loop.c` S_OBSERVING calls switch_input BEFORE observe; on switch success returns... `if (switch_input(l)) return;` — switch_input returns 0 on successful switch too! So after switching to VNC, it proceeds to observe immediately. Fine.

  Now the remaining candidate: **S_ERROR dead-end** — confirmed: K_ALLOW only accepted in S_DISABLED/S_STOPPED (loop.c:58); loop_submit only from S_IDLE/S_DONE (line 39). No exit from S_ERROR. Also S_STOPPED (from non-emergency finishes like confirm_denied, no_progress, input_unsupported) requires K_ALLOW — but kbd never revoked allow for those cases (allowed still 1 in kbd), so kbdc_poll's status path: c->allowed=1, notified_allowed is still 1 (was set when first allowed) → NO new K_ALLOW enqueued (requires !notified_allowed). So after confirm_denied (stopped), the agent can NEVER be re-enabled in-process: notified_allowed stays 1, no K_ALLOW, loop_submit impossible. Let me verify: kbdc event estop path clears notified_allowed; but confirm_denied doesn't touch kbdc state — kbd's allowed remains 1. kbdc_poll: `if (c->allowed && !stopped && !c->notified_allowed && !c->require_off)` — notified_allowed=1 → no K_ALLOW. So S_STOPPED (confirm_denied/no_progress) is also a dead-end! The only way out is kbd-side disallow→allow cycle (which clears notified_allowed via the `(!c->allowed || stopped) && c->notified_allowed` branch → sets notified_allowed=0, and `if (!c->allowed) c->require_off = 0`). So the user must toggle allow off/on on the keyboard to run another task after a confirm denial or no-progress pause. Per spec §3.6: no-progress → "暂停并请示用户" (pause + ask user). A stop that requires keyboard re-toggle is arguably per-design ("re-enable only by explicit allow" applies to e-stop; for confirm_denied the spec says "reject and stop task with stopped/confirm_denied"). After stopping the task, starting a NEW task should be possible while still allowed... but loop_submit requires S_IDLE/S_DONE, and S_STOPPED needs K_ALLOW which never comes without a kbd off/on toggle. Hmm. This makes every confirm denial / no-progress a one-way door requiring the user to flip the keyboard allow switch off and on. Is that intended? The spec Part 2: "cancel/timeout → reject and stop task with stopped/confirm_denied" — stops the TASK. It doesn't say the agent must become un-submittable. And "re-enable only by explicit allow" is scoped to e-stop. So confirm_denied → S_STOPPED without kbd disallow = wedged until keyboard toggle. I'd call this MAJOR functional (same dead-end family as S_ERROR). Both stem from: no transition from S_ERROR/S_STOPPED back to S_IDLE while kbd still allows.

  Hmm, but wait — maybe intended design: after confirm denial the task stops; the human then submits a new task via web/MCP (A6/A7). For that, loop_submit must work — it doesn't from S_STOPPED. Unless the design expects kbd allow toggle. The kbd allow toggle is the "运行级允许" — denying one confirmation shouldn't require re-toggling the master allow. I'm fairly confident this is a genuine functional bug. MAJOR.

  Also note loop_stop (local estop) → S_STOPPED + LEASE_ESTOP → kbd disallows → kbd allow toggle needed — that's per spec for e-stop ✓. The bug is specifically non-emergency S_STOPPED/S_ERROR wedge.

  Severity call: The review is security-focused; this is availability/functional. MAJOR.

  Now, the guard-returns-0 finding — let me nail the exact mechanics once more for the writeup:
  - loop.c:65 `if (l->cancelled) return -1;` only catches emergency finishes.
  - loop.c:70 `if (!active(l)) return 0;` returns "continue" after non-emergency finish (wall_budget line 73, action_timeout line 74, clock line 72, and the event-driven confirm_denied line 67).
  - loop.h:35 documents guard contract. Ports calling guard (kbdc guard_action, net service, rfb service) interpret 0 as continue.
  - Mitigation today: finish() releases the lease (non-emergency) → kbdc token=0 → act() refuses (kbdc.c:160). So HID output is contained. But: (a) the in-flight kbdc action completes its current sleep/partial sequence; (b) model/observe ports run to completion (up to 60s llm timeout) after task termination; (c) the future VNC input port (rfb writer) is NOT token-gated — it would keep writing pointer/key events after wall/exec termination. Fix: after the budget checks in loop_service, return nonzero when !active(l) (or set cancelled on every finish).

  Is (c) accurate? VNC write happens via rfb_set_service(r->rfb, g, ctx) — service = guard = loop_service. If loop_service returns 0 after finish, rfb continues writing. Yes. And kbdc_execute's guard is loop_service too — but act() token check saves it. Also note kbdc_execute's guard_action ALSO checks `c->stopped` and deadline — deadline = action_start + exec_ms; after action_timeout finish, now >= deadline → guard_action returns -1 via deadline ✓. Oh wait — that changes the analysis for kbdc: the per-action deadline (exec_ms) IS enforced inside guard_action via `kbdc_now() >= deadline`. So action_timeout mid-execute → the execute aborts at the deadline anyway ✓ (the finish happens when now crosses exec_ms; guard_action's deadline is exactly action_start+exec_ms → also fires). So for kbdc execute, the guard does stop it at the deadline even without the fix. For wall_budget mid-execute: deadline is action_start+exec_ms (2s), wall budget is 10min — after wall finish, acts continue until token check... token=0 (released by finish) → refused ✓. For model port: net service callback = loop_service → returns 0 → TLS request continues until its own 60s deadline after wall_budget — delayed termination, no safety impact (no act possible without policy/confirm; and model reply is discarded since !active). For a future VNC writer port driven through kbdc_execute — n/a since kbdc gates. A hypothetical rfb_write-based execute port would use rfb service = loop_service → continues after finish → would write post-termination. Latent. 

  Given the actual HID path is double-gated (token release + action deadline), I'll grade this MAJOR (contract violation, latent unsafe for VNC writer + delayed cancellation), not BLOCKER.

  Now, any BLOCKER candidates? Let me re-scan the focus list for anything that would allow acting without lease/after estop or bypassing never-do/confirm:

  1. **Type on lock screen**: rejected ✓ (locked && type → P_REJECT; test covers).
  2. **Confirm bypass**: none found. Stale cid ignored ✓; cancel/timeout → stop ✓; early press guarded kbd-side and tested ✓.
  3. **Never-do bypass**: title blacklist covers password/lockscreen/security/proxy titles incl. Chinese ✓; secure_input flag ✓; target-based ✓. One gap: never-do titles are matched only against CURRENT frame title/app/target. If the frame title is missing (empty) but the screen is actually Settings>Security... the hint-based detection is heuristic — spec acknowledges heuristic. Not a code bug.
  4. **policy.json more permissive**: invalid → defaults ✓; clamps ✓; confirm_first/app_switch can be disabled by valid config — that's spec'd configurable. dangerous_keys can be APPENDED only... wait, can a valid policy.json REMOVE default dangerous keys? policy_parse starts from defaults and appends — no removal path ✓. window_blacklist same ✓. **But: a valid policy.json can set "confirm_ms":1000 (min) — confirmation still required, just shorter window. Fine.**
     
     Hmm, actually one more: **policy_load is only called from loop_init when c==NULL. loop_test always passes an explicit policy built from script — fine for tests. In production runtime, whoever calls loop_init(NULL) gets policy_load(NULL) → reads POLICY_PATH ✓.**

  5. **Act without lease**: act() gates token+stopped+expiry ✓. Loop acquires lease before execute ✓ and before ALLUP in switch_input ✓. **What about kbdc_ui / kbdc_confirm — no lease needed per protocol ✓.**

  6. **After e-stop**: rpc refuses all but status/estop ✓; loop guard cancels ✓; finish sends no release ✓; loop_stop sends estop op ✓. **E-stop during rpc reply wait**: event drained → stopped=1 → rpc continues reading until reply/deadline... `if (get(j,"ev")) { bad = event(c,j); ... continue; }` — estop event → event() returns enqueue result (0) → continue → keeps waiting for the reply until deadline. The act reply may still arrive ok (kbd rejected it though — act after estop returns stopped err). Either way no further acts (stopped=1 blocks future rpcs/acts). The current rpc completes bounded by 250ms ✓.

  7. **HID/VNC retry**: no replay ✓.

  8. **Redaction**: whole-line redact on markers/secrets ✓. Log line contains coords + model id — model id is JSON-quoted ✓. **One gap: `agent_redact` is applied to the log line only; loop_test's printed `acts` contain action names only ✓; `reason` literals ✓. history contains only "step N: name;" ✓. ui summary empty ✓. confirm summary static ✓. The task text goes to the model provider (intended) — never logged ✓.**

     Wait — one leak candidate: `runtime.c model()` builds `user` prompt with task+history — fine. `record()` logs `a->x1..y2` for ALL actions — for A_KEY, x fields are 0 (memset) ✓. text never logged ✓ only len+hash ✓.

  9. **Memory safety hostile input**: kbdc line cap ✓, cJSON bounded parse ✓ (agent_json_parse has max + duplicates), confirm cid range ✓, ttl/token ranges ✓. **model output**: action_parse bounds ✓; policy_check coordinate bounds vs frame ✓. **32-bit**: int64 time ✓; `500 << retries` int ✓; sizes ok.

     One thing: `loop_test.c poll_event` — script-driven `cid` up to 10000000 (integer() cap) vs loop's l->cid — mismatch → ignored ✓.

     **`kbdc.c line()` — `c->in[c->used++] = ch` with used < 1023 check BEFORE write?** `if (c->used >= 1023) return -1; c->in[c->used++] = ch;` — max used becomes 1023, in[1024] ✓; then agent_json_parse(c->in, c->used, 1023) — reads c->used bytes ≤ 1023 ✓. OK.

     **`enqueue` — `(c->head + c->count++) % 64` — count==64 checked first ✓.

  10. **Calib**: fit rejects non-corner/center, dup, NaN, singular, residual > 2 logical px ✓; save atomic mkstemp+fchmod 0600+fsync+rename+fsync dir ✓; load checks mode 0600 exact + uid + re-fit fail-closed ✓. **calib_save mkstemp template in target dir ✓ (same fs for rename) ✓.**
      - Minor: `calib_save` doesn't handle `cJSON_CreateDoubleArray` returning NULL (OOM) — AddItem with NULL? cJSON_AddItemToArray with NULL item — would detach? Actually cJSON_AddItemToArray(array, NULL) — item NULL → add_item_to_array returns... cJSON_add_item_to_array checks `if (item == NULL) return false`? In vendored cJSON, likely returns false gracefully. Then JSON has fewer pairs → load re-fit fails → fail closed ✓.
      - **calib_map accepts d in [-1, 32768] then clamps** — allows slightly-out-of-range map results; fine.
      - **Residual computed with `hypot(x * lw / w, y * lh / h)`: x is raw px error at frame scale; lw/w scales to logical. Correct per "logical px".

  11. **Test gaps**: 
      - No test that guard/loop_service aborts in-flight ports on non-emergency finish (the MAJOR above is untested; test_budgets passes because state/reason still asserted; fake records the act — unasserted).
      - No test for S_ERROR/S_STOPPED → re-submit (dead-end untested).
      - Real-socket estop tests exist ✓; early-press tested ✓ via kbd_port_server.
      - No pytest for policy.json path override via env... policy_load(NULL) path tested only via loop_init? test_a5 configs() uses explicit paths ✓.
      - `hid.health` event — kbd doesn't implement it yet (per CTL-PROTOCOL note); arbitration driven by -2 only. NOTES should say.

  12. **A4 minors check**: 
      - known_net whitelist + forced non-retryable unknown ✓ tests ✓.
      - retryable table explicit ✓ NOTES line added ✓.
      - tools_json non-array → CONFIG ✓ + pytest zero-connects ✓.
      - provider growth test deterministic via reader injection ✓.
      - 302 → bad_response test ✓.
      - One nit: `known_net` doesn't include "cancelled" (new class added in this same diff!) → llm_decode maps cancelled → LLM_NETWORK + retryable=0. Behavior fine (no retry), label imprecise. MINOR. Actually wait — is that maybe intentional? A cancelled request shouldn't retry and shouldn't mislabel as network... loop treats it as task error "model". But on e-stop the loop is stopped anyway. The one visible effect: if cancellation fires for non-estop reasons... only estop cancels. So negligible user impact; MINOR.

  13. **Makefile**: test_a5 built with $(SAN) ✓, linked with mem/json_util/cJSON/llm/net/tls ✓. `test` target runs test_a5 first ✓. A5_SRC compiled into mk20-agent

WATCHDOG KILLED at 900s
KIMI DONE 905s
```
