# A4 host model adapter — 2026-10-19

`llm.c` supplies one synchronous OpenAI-compatible POST adapter over `net_request()` and `oai.c`. Provider names, model names and origin URLs come entirely from the configuration file. No Internet/provider, device/ADB/serial, kbd, firmware, kernel, vendor or Python configuration-writer changes were made. No commit/push. Earlier §3.5 response-size/retry prose is superseded for this host phase by the explicit A4 requirements: 1 MiB response cap; no adapter retries.

## Provider configuration

`llm_provider_load(path, &cfg)` reads a regular file of 1–16,384 bytes, with a bounded read after fstat (including growth detection). Nonblocking open rejects FIFOs/devices without waiting. Invalid/missing/unreadable/empty/oversized input leaves an invalid zeroed configuration. The shared `agent_json_parse` rejects non-JSON number forms, trailing data, duplicate keys, embedded NUL, invalid UTF-8 and excessive nesting/node count before use. Unknown extra fields are ignored. Parsing never resolves or connects.

Required fields match `deck/mk20deck/agentcfg.py::provider_config` exactly:

| Field | Validation |
|---|---|
| version | numeric value 1 |
| provider, model | nonempty strings, at most 128 bytes each |
| base_url | nonempty, at most 1024 bytes; `net_https_url` validation; no query/fragment/credentials/control characters |
| domestic | JSON boolean (metadata; route is authoritative) |
| route | exactly domestic or foreign; passed to `netcfg_route_for` through net.c |
| api_key_file | nonempty path, at most 4095 bytes; path reference only |

Optional fields:

| Field | Default | Accepted values |
|---|---|---|
| coord_mode | norm1000 | norm1000, pixel |
| max_tokens | 512 | integer 1–32768 |
| temperature | 0 | finite number 0–2 |
| timeout_ms | 60000 | integer 1–600000 |
| supports_tool_choice | true | JSON boolean |
| vision | true | JSON boolean; false rejects images before connecting |

For example, the GLM coding-plan `base_url` `https://open.bigmodel.cn/api/coding/paas/v4` needs no code change. Trailing slashes are removed before appending `/chat/completions`. The adapter overrides transport URL/method/route/key/body/headers/caps. Optional caller transport settings supply CA/trust/deadline and injectable socket accounting; they cannot enable model clock-setting. One `Content-Type: application/json` header is added; net.c reads `api_key_file` via `net_read_key`, serializes Authorization, and wipes the key/header buffers. llm.c has no key buffer.

Nonempty tools plus supports_tool_choice=true sends `tool_choice: required`; a successful response without a tool call is bad_response. False omits tool_choice and permits a text-only reply. Empty tools do not require a call. Unrequested tool names are rejected. The offline `oai_build_body` API still sends auto, preserving existing tests; the model path uses its explicit choice variant. Both now use the shared strict JSON validator.

## Coordinates and ownership

All returned call coordinates are pixels in the supplied image dimensions. norm1000 uses inclusive 0..1000 and `oai_norm_to_pixel`, yielding 0..extent-1 with nearest rounding. Pixel mode uses inclusive 0..extent-1 directly. x/y and optional x2/y2 are checked; x1/y1 are also supported for the plan's drag schema. Any coordinate declared in the matching tool's top-level parameter properties must be present and mathematically integral; null, string, boolean, nonfinite and fractional values fail with coord_type. Present coordinate fields are checked even when undeclared. Dimensions must be 1..1,000,000 for coordinate mapping.

Tolerance on each axis is **floor(maximum / 50)** (at most 2% of that axis's inclusive coordinate range). Thus norm1000 permits -20..1020; an extent of 201 pixels permits -4..204; pixel mode with an extent of one permits only zero. Only values inside this tolerance are clamped to the endpoint. Larger errors fail with coord_range. Validation rejects the entire batch on any bad call, freeing all arguments and clearing content/calls, so no partially validated action escapes. The executor/policy still belongs to A5.

Use a fresh response per request and `llm_response_free` on both success and failure. It frees every retained cJSON argument. Request JSON and the bounded network body are freed within llm_chat; callers own the input image and strings. One request is active at a time, with no history/images retained internally. Request cap is OAI_BODY_MAX=256 KiB, image cap OAI_IMAGE_MAX=128 KiB, model response cap LLM_BODY_MAX=1 MiB. Content/argument/name/id/finish/schema/tool-count bounds remain in oai.h. Requests exceeding serialized-body/image caps use too_large; invalid request/schema fields use config.

## Error contract and CLI

`llm_error_name` returns only stable literals; `reason` is also a fixed literal. Peer error messages/types/codes and raw error bodies are discarded, including bodies that echo Authorization. No key/path contents or headers are included in diagnostics. No retries occur inside llm.c: A5 chooses delay/budget.

| class | Meaning | retryable |
|---|---|---|
| ok | parsed text or validated calls | false |
| auth | HTTP 401/403 | false |
| rate | HTTP 429 | true |
| server | HTTP 500–599 | true |
| bad_request | HTTP 400/404/422 | false |
| timeout | overall network deadline | true |
| network, dns, connect, io | transport failure; net.c names retained | true |
| proxy, tls, tls_verify, clock | net.c class retained | false |
| too_large | response/image/serialized request cap | false |
| bad_response | malformed/missing choices, required call absent, invalid arguments, oversized fields, >16 calls, unknown tool, bad HTTP framing or unexpected status | false |
| coord_range, coord_type | unsafe coordinates; empty returned batch | false |
| blocked | selected route is blocked | false |
| config | invalid configuration/request/key/CA | false |

Kimi A3 #4: llm_chat explicitly sets `clock.set_clock=0`, clears setter/context, and never synchronizes proxy/origin Date. A C test passes a setter plus set_clock=1 and a proxy Date in 2050 through the real CONNECT path; no setter/marker call occurs. Pytest verifies a successful TLS request through the same skewed Date. Time-setting remains on the existing usb0 `/time` path.

Kimi A3 #6: net_response_t reports close_delimited and tls_close_notify. net.c retains A3 net-test semantics, but llm.c rejects until-close completion without authenticated close_notify as **class=bad_response, error/reason=truncated**. Content-Length and chunked completion are unchanged, even without close_notify. C parser/decode tests and TLS pytest cover this distinction. Kimi #5's O_NONBLOCK + fstat S_ISREG CA check already existed at the A4 baseline; an A4 FIFO-CA test confirms failure before any socket call.

```sh
cd deck/agent
build/mk20-agent --llm-test --provider /path/provider.json --net /path/net.json \
  --image /path/frame.jpg --image-w 1280 --image-h 800 --tools /path/tools.json \
  --system 'Control the screen' --user 'Click the target' --ca /path/ca.pem \
  --trust-clock --timeout 60000
```

`--timeout` is milliseconds on --llm-test (the existing --net-test seconds option is unchanged). Output is exactly one JSON line: class, retryable, http_status, finish_reason, content (at most 2000 Unicode characters), calls [{name,args}] with pixel coordinates, elapsed_ms, peak_rss_kb, and an extra stable `error` reason. Exit zero only for ok. Image dimensions may also be supplied without an image for tool-only tests. No --set-clock option exists here.

## Host verification and memory

Host: Darwin arm64, Apple clang, C11 -Wall -Wextra -Werror -O1; ASan/UBSan builds use the existing nonrecovering sanitizer flags. Separate llm/oai sanitized and ns objects are built. No warning suppression was added to owned code. Final results and commands are recorded below.

Five fresh nosan --llm-test processes per CA bundle sent a real **107,382-byte JPEG** (400x400, deterministic cached Pillow noise, quality 80), system/user/tools and key-file auth, and parsed one coordinate tool call. Both proxy and throwaway-CA TLS model were local fakes, carried over socketpairs because TCP binds are denied. Only test builds accept MK20_NET_TEST_FD; production has no FD/environment bypass.

| CA bundle | Peak whole-process RSS samples (KiB) | Maximum |
|---|---|---:|
| Throwaway CA | 2640, 2640, 2672, 2688, 2656 | 2688 |
| Unmodified Mozilla bundle plus throwaway CA in temporary file | 3184, 3200, 3168, 3184, 3248 | 3248 |

Both are below the **16,384 KiB total agent budget**, but this is a host LLM-only measurement. It does not establish armhf/device RSS or simultaneous VNC frame + LLM + future loop/server allocations. The response cap bounds body storage; DOM/cJSON/request serialization/TLS and frame allocations also count toward device total. A5/integration must measure the combined process on device under worst-case responses, not add independent stage RSS numbers as proof.

## Remaining acceptance

* [U] Run the fixed 10-screenshot benchmark with user-provisioned real key files; require at least 8/10 localization per provider and choose a vision+tools-capable default model. No key/provider access was attempted here.
* [D] Run the device/static armhf build and combined RSS/latency tests within the <=16 MiB agent budget. No device acceptance claimed.
* Re-run the unchanged normal TCP suite on a Linux host that permits loopback, especially unreachable-proxy zero-direct and A3 fallback ordering. Socketpair plus injected dial accounting supplements these proofs but does not prove kernel TCP behavior.


## Final verification record

The default Homebrew Python lacks pytest. These commands used the existing cached Python 3.13 pytest environment at `/Users/dev/.cache/uv/archive-v0/vxkKwzR1zHgPnvko/bin/python` and cached Pillow via `PYTHONPATH=/Users/dev/.cache/uv/archive-v0/bo26hwk4TgxG1QEs/lib/python3.13/site-packages`; no packages were downloaded. `python3` below means that cached environment's interpreter.

```sh
make -C deck/agent test
python3 -m pytest deck/agent/tests -q --tb=short
MK20_TEST_SOCKETPAIR=1 MK20_TEST_NO_NETWORK=1 python3 -m pytest \
  deck/agent/tests/test_rfb.py deck/agent/tests/test_oai.py \
  deck/agent/tests/test_fakes.py::test_des_known_vector deck/agent/tests/test_net.py \
  -q -k 'not unreachable_proxy and not fallback_order'
MK20_TEST_SOCKETPAIR=1 python3 -m pytest deck/agent/tests/test_llm.py -q -s
```

* `make test`: PASS, **222 LLM checks**, **3145 OAI checks**, RFB/scale/JPEG checks, **342 net/clock checks** (invalid-config socket calls zero), CLI RSS/deadline regressions. ASan/UBSan reported no error. The nosan executable also builds with the same warning policy.
* Normal TCP suite: **44 passed, 182 failed** in 50.33 s. Every failure is `PermissionError(EPERM)` at a 127.0.0.1 bind before protocol execution: A4 47, A3 41, A2 86, original fakes 7, original OAI HTTP 1. No new skips or xfails. This is **not** a passing TCP acceptance run. The individual blocked test IDs are recorded in [agent-a4-tcp-blocked.txt](../reviews/agent-a4-tcp-blocked.txt).
* A2/A3 socketpair supplement: **169 passed, 1 skipped, 2 deselected**, 58.26 s. The skip is the preexisting OAI HTTP test under its offline environment switch; the deselections are exactly A3 `test_unreachable_proxy_never_direct` and `test_fallback_order`, which require kernel TCP. An earlier run alongside simultaneous compiler suites had one preexisting RFB wall-time assertion at 1.27 s (expected <1 s), while returning the expected timeout; the isolated full rerun passed with unchanged code/thresholds.
* A4 socketpair suite: **47 passed**, 7.84 s, no skips/xfails/deselections. It covers text, required/unsupported tools, images/body equality, malformed/missing/oversized responses/fields, >16 calls, unknown names, invalid arguments, missing/fractional/range coordinates, whole-batch rejection, statuses 400/401/403/404/422/429/500, no retry, slowloris timeout, close-notify versus truncation (fixed/chunked unaffected), skewed proxy Date, auth redaction, invalid config/blocked/oversized-image zero-connect, UTF-8 content truncation and nosan RSS. Its unreachable-proxy supplemental test uses real llm_chat/net_request with an injected failing connect callback, asserts only the selected proxy address/port is attempted, and asserts zero fake-model connections. This supplements but cannot replace the normal TCP test.
* `git diff --check`: PASS. Vendor and agentcfg.py unchanged; only the authorized A4 plan status line was edited outside the agent/review/ignore scope.
