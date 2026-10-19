# A4 first acceptance review — Codex, 2026-10-19
Verdict: host implementation PASS, conditional on the remaining TCP/[U]/[D] acceptance below.
- PASS — scope limited to deck/agent, A4 plan status line, this review and .gitignore; vendor/writer untouched; no commit/push/external-network/provider/device/serial work.
- PASS — single llm.c/.h adapter; no provider names/origin URLs hard-coded; coding-plan URL works through configuration.
- PASS — provider writer schema, all required/optional types/ranges, strict bounded JSON, regular <=16 KiB file, ignored extras, fail-closed load.
- PASS — POST base_url/chat/completions; OAI tools/image data URI; JSON Content-Type; key path only, net_read_key and existing mem_wipe lifecycle.
- PASS — route through netcfg_route_for, blocked/config fail closed; no direct fallback added.
- PASS — request 256 KiB, JPEG 128 KiB, response 1 MiB caps; valid >256 KiB response tested.
- PASS — norm1000/pixel returned as pixels; declared coordinates integral/present; x/y/x2/y2 plus x1/y1 checked.
- PASS — floor(range/50) tolerance, <=2%; boundary clamp versus rejection; any unsafe call drops entire batch.
- PASS — HTTP auth/rate/server/bad_request table; transport classes, timeout, caps, malformed/missing/oversized/invalid-call errors; fixed literals only.
- PASS — required tool_choice for supported nonempty tools, missing-call rejection; unsupported mode omits choice; unknown tools rejected.
- PASS — no llm retries; retryable only rate/server/timeout/network family.
- PASS — Kimi #4: set_clock explicitly zeroed and setters cleared; real CONNECT skewed-Date C spy and TLS pytest.
- PASS — Kimi #6: net exposes close-delimited/close_notify; missing notify gives bad_response + truncated; CL/chunked unchanged; C + pytest.
- PASS — Kimi #5: preexisting regular-file CA guard retained; FIFO CA fails before any socket in C regression.
- PASS — response ownership/freeing; partial/error batches cleared; ASan/UBSan execution.
- PASS — --llm-test flags, millisecond override, one JSON stdout line, pixel args, 2000 Unicode chars, timing/RSS, exit zero only on ok.
- PASS — synthetic auth reaches server; echoed peer error cannot leak key/Authorization to stdout/stderr.
- PASS — sanitized/nosan llm/oai objects; test_llm included in make test; build-armhf ignored.
- PASS — make test: 222 LLM checks, 3145 OAI checks, RFB/img, 342 net/clock checks, CLI regressions.
- PASS — A4 socketpair TLS/CONNECT/CLI suite: 47 passed, no skips/xfails/deselections; scripted success/failure/auth/timeout/caps/truncation cases.
- PASS — A2/A3 supplemental suite: 169 passed, 1 existing offline HTTP skip, 2 explicit TCP-only deselections; isolated rerun after load-related timing failure.
- FAIL (sandbox) — normal TCP suite: 44 passed, 182 bind-EPERM failures (A4 47 / A3 41 / A2 86 / fakes 7 / OAI 1); no new tests skipped/xfail; rerun Linux loopback before TCP acceptance.
- PASS — every blocked test ID is listed in agent-a4-tcp-blocked.txt.
- PASS — A4 injected failed-dial test supplements zero-direct proof without TCP; does not replace the kernel TCP test.
- PASS — nosan 107382-byte JPEG: five samples each; peaks 2688 KiB test CA, 3248 KiB Mozilla+test CA (<16384 KiB host budget).
- PASS — NOTES-agent-a4.md records schema, tolerance, errors, retry flag, carry-overs, RSS and remaining gates; one plan line updated.
- FAIL (deferred [U]) — real 10-screenshot/provider >=8/10 benchmark/default model selection; no real keys/provider calls authorized here.
- FAIL (deferred [D]) — device/static armhf and combined VNC/LLM/loop total <=16 MiB proof; host LLM peak is not device acceptance.

## Box Linux verification (final acceptance, 2026-10-19)
- deck `make test` EXIT 0: sim 24, kbd 4018+4018 (tsan) + e2e OK, agent pytest 226 passed (TCP loopback allowed: the 182 sandbox-EPERM cases all pass), tools 58, deck tests 546.
- `make -C deck/agent test`: 222 LLM, 3145 OAI, rfb/img, 342 net/clock (0 invalid-config sockets), CLI checks — all pass.
- `make -C deck/agent armhf`: static ARM EABI5 mk20-agent-nosan builds (2.5 MB, with debug info; glibc static getaddrinfo warning — IPv4-literal proxies only, Kimi A3 #7).
- Kimi K3 r1: GO, 4 MINOR (agent-a4-kimi-r1.md). Live GLM: agent-a4-glm-live.md.
