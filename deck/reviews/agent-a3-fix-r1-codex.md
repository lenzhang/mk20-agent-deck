# A3 r1 fixes — Codex, 2026-10-19

Implemented only the requested TLS/configuration and CA-file fixes; no commit or push.

- `deck/agent/src/mk20_mbedtls_config.h:39`: enable X.509 KU/EKU checks and TLS Extended Master Secret; compile-time guards at `deck/agent/tests/test_net.c:13` require all three.
- `deck/agent/tests/test_net.py:63`: add trusted-CA-signed, current, localhost leaves with clientAuth-only EKU or keyCertSign-only KU. The existing FakeTLS verification matrix (`:130`) requires `tls_verify` and the specific usage detail; valid RSA/ECDSA cases still pass. Flag descriptions added at `deck/agent/src/net.c:569`.
- `deck/agent/src/net.c:459`: open CA files nonblocking, fstat the opened descriptor, and reject non-regular, empty, or >4 MiB files before reading. Reads are bounded by the checked size; growth is rejected. `deck/agent/tests/test_net.py:222` covers FIFO (no writer), directory, and oversized file rejection as `config`, with a two-second subprocess timeout.
- `deck/agent/NOTES-agent-a3.md:2`: exactly one added status/deferral line.

Verification on macOS sandbox:

- `make -C deck/agent test`: **PASS**, all four C suites under ASan/UBSan: OAI 3,145 checks, RFB/scale/image suite, network/clock 342 checks (zero invalid-config socket calls), and CLI regressions.
- `python -m pytest deck/agent/tests -q --tb=no`: **44 passed, 135 failed** in TCP mode.
- A3-only TCP diagnostic run: **6 passed, 41 failed**; JUnit confirms all 41 failures are `PermissionError: [Errno 1] Operation not permitted` from loopback binds. All three new CA-file regressions passed in this run.
- `MK20_TEST_SOCKETPAIR=1 MK20_TEST_NO_NETWORK=1 python -m pytest deck/agent/tests/test_rfb.py deck/agent/tests/test_oai.py deck/agent/tests/test_fakes.py::test_des_known_vector deck/agent/tests/test_net.py -q --tb=short -k 'not unreachable_proxy and not fallback_order'`: **169 passed, 1 existing OAI HTTP skip, 2 TCP-only deselections**. Both new TLS usage rejections and existing valid-cert/TLS-version cases passed.
- `git diff --check -- deck/agent`: **PASS**.

Pytest used Python 3.13 with cached pytest/Pillow via `uv --offline`; nothing was downloaded. Logs are in ignored `deck/agent/build/fix-r1-logs/`, with A3 TCP diagnostics in `deck/agent/build/fix-r1-tcp.xml`. The earlier Linux result (174 passed, including the two TCP routing proofs) is user-supplied evidence; no Linux rerun is claimed here. Vendor sources, agentcfg.py, devices, ADB, serial, and external network were untouched.
