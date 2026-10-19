# A4 cross-review r1 — Kimi K3 (kimi-code), 2026-10-19

Scope: REVIEW-a4.diff (e2085c7..working tree, vendor excluded), read-only throwaway copy. Prompt: ~/worktrees/a4-kimi-prompt.txt (Mac).

# Cross-review: A4 (llm.c on net.c) — Verdict: **GO**

Host-phase implementation matches the spec. No BLOCKER or MAJOR findings. Secret hygiene, hostile-response handling, coordinate safety, clock/truncation carry-overs, and fail-closed config all verified in code, not just in NOTES. GO is conditional on the already-documented gate: the 182 TCP-bind EPERM failures are sandbox environment failures (disclosed, no xfail), and the normal TCP suite must be rerun on Linux before TCP acceptance — same condition the first reviewer recorded.

Verified clean (evidence):

1. **Secrets**: llm.c never holds the key — only `api_key_file` path passed via `o.key_file` (`llm.c:535`); net.c wipes key + Authorization-bearing request buffer (`net.c:595,597,619-620`), masks key errors with `(***)` (`net.c:521,590`). `fail()` in llm.c zeroes the whole message, so oai's peer-echo `error.message` can never reach stdout (`llm.c:455-460`). CLI prints only stable literals + model content/calls; pytest asserts key/"Authorization" absent from stdout+stderr on every call plus a dedicated 401-echo test.
2. **Hostile response**: strict pre-scan (`json_util.c`: duplicates, UTF-8, NUL, depth 32, 4096 nodes) before cJSON; arguments must parse to an object else `invalid_arguments` caught via `call->error` before `coordinates()` (`oai.c:161-163`, `llm.c:507`); NULL-safe cJSON lookups confirmed in vendor `get_object_item` (`cJSON.c:1902`); caps: name 64, args 8 KiB, ≤16 calls, content 32 KiB, body 1 MiB; int64 math in `oai_norm_to_pixel` — no armhf overflow.
3. **Coordinates**: tolerance `floor(max/50)` ≤2% both modes, boundary clamp vs reject correct (±20@norm1000, ±4@201px, extent-1 pixel mode), non-integral/missing → coord_type, whole batch rejected on any bad call (`llm.c:471-489`). Tests cover both modes, boundaries, x1/y1/x2/y2, duplicates, 1e99.
4. **set_clock**: cleared explicitly with setter/ctx (`llm.c:532-533`); net.c gates sync on `set_clock` (`net.c:522,547,614`); C spy test + skewed-Date pytest.
5. **Truncation**: `close_delimited`/`tls_close_notify` set only on the final response path (`net.c:180,604`; CONNECT uses separate `net_connect_reply`), reject only close-delimited-without-notify (`llm.c:498`); CL/chunked unaffected; C + 4-way pytest.
6. **No direct path**: transport struct has no direct/fallback knob (`net.h:12-27`); route via `netcfg_route_for` (blocked→blocked, zero dials tested; injected-dial test asserts only proxy addr:port, count==1).
7. **provider.json fail-closed**: O_NONBLOCK+S_ISREG+size, growth past 16 KiB rejected via `n>max` in `agent_json_parse`, writer schema matches `agentcfg.py:50-55` exactly, '?' rejected in llm.c, '#'/'@'/credentials/control chars via net_https_url/oai_endpoint/string().

Findings (all MINOR):

1. **MINOR — fail-open retry on unknown net classes** — `llm.c:446-449` (`llm_net_error`): any unrecognized `class_name` maps to `LLM_NETWORK`, which is retryable. A future net.c class for a permanent failure would be retried by A5. Fix: keep mapping unknown→network but force `retryable=0` for the fallthrough, or whitelist known literals.
2. **MINOR — retryable superset vs spec text** — `llm.c:432-433`: dns/connect/io are retryable; prompt spec lists only "(rate, server, timeout, network)". Operationally right (they are the network family) and documented in NOTES-agent-a4.md's error table, but it's a strict-reading deviation — confirm intended, else restrict the flag.
3. **MINOR — `llm_decode` re-parse of tools_json unchecked** — `llm.c:502-504`: no `cJSON_IsArray`/NULL check on the re-parsed tools; malformed/non-array tools_json via the public `llm_decode` API yields `required=0` or bad_response/"unknown_tool" instead of config. Unreachable via `llm_chat` (oai_build_body_choice validates first, `oai.c:87-93`), so internal-API hardening only. Fix: `if (q->tools_json && !cJSON_IsArray(tools)) return fail(r, LLM_CONFIG, "request");`
4. **MINOR — test gaps** — (a) no test for provider.json growing past 16 KiB between fstat and read, though NOTES-agent-a4.md:99 claims growth detection; (b) no pytest for a malformed `--tools` file → class config with zero connects (oai path covers it, untested end-to-end); (c) unlisted HTTP statuses (e.g. 302→bad_response) only covered in the C table, not over TLS. Add one case each.

No style nits reported per instructions.
