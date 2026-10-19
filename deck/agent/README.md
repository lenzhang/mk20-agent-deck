# deck/agent — on-device agent (mk20-agent), see ../AGENT-BUILD-PLAN.md

Status: A0 host fakes and A1 control/rule engine implemented in ../kbd (see [CTL-PROTOCOL.md](../kbd/CTL-PROTOCOL.md)). A7 still supplies physical allow/confirm UI and screen rendering; device acceptance remains pending. Release switches for control, scroll and K2 e-stop default off.

- `tests/fakes/fake_vnc.py` — RFB 3.8 server, VNC auth (pure-Python DES in `des.py`), Raw + ZRLE, records input events/ClientCutText.
- `tests/fakes/fake_model.py` — OpenAI-compatible `chat/completions` + `models`, scripted tool calls/errors/raw bodies, request recording.
- `tests/fakes/fake_proxy.py` — HTTP CONNECT proxy, allowlist/refuse/close/delay injection, records non-CONNECT (bypass) attempts.
- `src/oai.c/.h` — bounded offline OpenAI-compatible request/response adapter, separate Authorization header builder, per-call argument errors and coordinate mapping. Uses the pre-existing vendored cJSON 1.7.18; no HTTP transport or key storage.
- `make -C deck/agent test` — ASan/UBSan C tests + CLI build. `make agent-test` (from deck/) runs host fakes and adapter pytest; C/CLI tests skip clearly if cc is absent. Set `MK20_TEST_NO_NETWORK=1` to skip the adapter loopback HTTP test (A0 fake-server tests still require loopback).
- CLI: `build/oai_cli request spec.json`, `parse HTTP_STATUS response.json`, `header key-file`. Request spec: `base_url`, `model`, `system`, `user`, optional `jpeg_file`, `tools` array, `max_tokens` (default 512), `temperature` (default 0), `supports_tool_choice` (default true). Request output is the POST body only; header mode is a fixture tool and prints a credential, so never log its output in production.
- Adapter bounds: body 256 KiB, image 128 KiB, system/user 16 KiB each, tool schemas 32 KiB / 16 tools, response text 32 KiB, 16 calls with 8 KiB argument JSON each, depth 32 / 4096 JSON tokens. Coordinate endpoints are inclusive: norm1000 0..1000 ↔ pixel 0..extent-1. A3 owns HTTP/proxy/TLS/retries; A4 still needs provider acceptance with real keys.
