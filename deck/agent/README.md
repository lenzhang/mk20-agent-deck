# deck/agent — on-device agent (mk20-agent), see ../AGENT-BUILD-PLAN.md

Status: A0 (host fakes) done; A1 (mk20-kbd control socket) in progress in ../kbd (see ../kbd/CTL-PROTOCOL.md).

- `tests/fakes/fake_vnc.py` — RFB 3.8 server, VNC auth (pure-Python DES in `des.py`), Raw + ZRLE, records input events/ClientCutText.
- `tests/fakes/fake_model.py` — OpenAI-compatible `chat/completions` + `models`, scripted tool calls/errors/raw bodies, request recording.
- `tests/fakes/fake_proxy.py` — HTTP CONNECT proxy, allowlist/refuse/close/delay injection, records non-CONNECT (bypass) attempts.
- `make agent-test` (from deck/) runs `tests/test_fakes.py`; part of `make test`.
