# A2 host implementation — 2026-10-19

Implemented under `deck/agent/`; no device, serial, adb, real VNC host, release binary, kbd change, commit or push. Vendored zlib 1.3.1 and stb 1.16 are unchanged. DES is reused from MK20's `deck/m2-scripts/vnc/des.c`, with independent known-vector and Python-fake authentication checks.

## Capture and limits

`make -C deck/agent` builds the existing `oai_cli` and ASan/UBSan `build/mk20-agent`. `build/mk20-agent-nosan` omits sanitizers for RSS measurements. Example:

```sh
MK20_VNC_PASSWORD=... deck/agent/build/mk20-agent --snap /tmp/frame.jpg \
  --host 127.0.0.1 --port 5900 --max-w 1280 --max-h 800 --quality 70 --timeout-ms 5000
# Prefer --password-file /path/to/vnc.pass (0600); argv passwords are rejected.
```

Password files may end in one LF; VNC uses only the first eight bytes, padded with zeroes and bit-reversed for DES. The CLI wipes its copies after authentication. Server names, rejection reasons and clipboard contents are never printed. Stdout is one JSON result; errors use fixed identifiers and a nonzero exit status. `oai_cli` is unchanged.

* RFB 3.3/3.7/3.8; prefer type 2, accept type 1; type 30/ARD and all other security types return `unsupported_security`.
* Numeric IPv4/IPv6 and `localhost` only: avoid an unbounded blocking DNS resolver. Nonblocking TCP connect has a 5 s deadline; handshake and each snapshot have independent 5 s operation deadlines, including reads/writes and streamed discards. The monotonic snapshot deadline is also checked at every ZRLE/Hextile tile, each Raw row, and before/after scaler finalization and CLI JPEG encoding. Expiry returns `timeout` before opening the output file, including when the compressed frame is already buffered. `--timeout-ms` changes these deadlines (1–600000). A refresh starts a new deadline, with at most three refresh retries.
* 32bpp little-endian true colour; preferred encodings ZRLE, Hextile, Raw, CopyRect, DesktopSize, Cursor, Extended Clipboard. ZRLE handles raw/solid/packed palette/plain RLE/palette RLE and keeps one inflate stream for the connection. Decoding never buffers a compressed rectangle or a source framebuffer.
* Source geometry is 1–8192 in each dimension. Geometry products, cursor payload lengths and weighted sums use bounded `size_t`/`uint64_t` arithmetic suitable for 32-bit armhf. Rectangles and CopyRect source coordinates are checked before reading pixel data. Cursor coordinates are hotspots; cursor pixels and masks are discarded. Bell and colour-map messages are consumed safely.
* Names/reasons are capped at 1 MiB. Classic and extended ServerCutText bodies beyond 1 MiB are discarded through 4 KiB scratch space; bodies beyond 16 MiB fail `length_limit`. No clipboard body is retained. ZRLE wire and expanded lengths are bounded from the rectangle area, with fixed input/tile buffers.
* `rfb_request()` can send full or incremental requests. `rfb_snapshot()` always requests a full frame. CopyRect and DesktopSize trigger another non-incremental request after consuming the update; the inflate stream survives both. An incomplete snapshot is rejected. This snapshot API assumes a conforming full update supplies every source pixel exactly once in its update message; overlapping/missing regions are not reconstructed from a source cache. It checks the total delivered pixel count and accumulator overflow, but does not allocate a source-sized coverage bitmap. Incremental image reconstruction is deferred.

## Scaling and memory

The scaler fits within the requested box without upscaling, preserving aspect ratio with integer-rounded-down dimensions. Each source pixel contributes its exact overlap with target boxes in a common integer coordinate grid. GCD reduction keeps the numerators small. Rectangles/tiles may arrive in arbitrary order, including full-height right/left strips.

Target channel accumulators and the final RGB888 image share one allocation: finalization compacts forward in place. Identity capture uses one byte/channel; other cases use two or four bytes/channel, or eight for small targets needing wide numerators. This uses more than a scanline-only accumulator, but handles arbitrary rectangle order without retaining source pixels. For rare large coprime geometries needing >32-bit numerators, at most three low numerator bits per contribution are dropped to keep four-byte channels; the accumulated error is <1 channel unit. Small targets retain exact 64-bit sums. Tests compare lossless scaled RGB against an independent Python area-box reference (±2; ordinary geometries are exact).

Peak storage is O(target pixels + one source row/tile + zlib window), independent of source framebuffer area:

| Allocation | 1280×800 identity | 2880×1800 → 1280×800 |
|---|---:|---:|
| Shared target accumulator / RGB allocation | 3,072,000 B | 6,144,000 B |
| Decoder object, 8192-pixel Raw row, 64² RGB tile, zlib and headers | ~93.5 KB | ~93.5 KB |
| Fixed JPEG output capacity | 131,072 B | 131,072 B |
| Measured owned heap high-water | 3,296,592 B | 6,368,592 B |

`heap_hw_bytes` includes decoder-owned fixed buffers, scaler, custom zlib allocations, JPEG output, and allocation headers. It excludes stack, libc/loader/allocator overhead and temporary numeric-address resolver allocations; `peak_rss_kb` measures the whole CLI process and normalizes macOS byte units to KiB. Owned allocations have a 14 MiB hard limit and fail cleanly. Larger requested target boxes may hit this limit. No full-resolution 2880×1800 RGB/RGBA allocation exists.

Linux `peak_rss_kb` now reads `VmHWM` (already KiB) from `/proc/self/status`. This high-water mark belongs to the agent's memory mapping and resets on exec; Linux `getrusage(RUSAGE_SELF).ru_maxrss` can retain the Python launcher's pre-exec peak, causing false budget failures (reported as 73,688/90,792 KiB). `getrusage` is used only if procfs cannot be opened, or on other platforms; macOS retains bytes-to-KiB normalization. Both Linux RSS regressions explicitly touch 64 MiB in a Python launcher, verify its high-water mark, then exec the non-sanitizer agent and require the agent's reported peak to be positive and ≤16,384 KiB.

JPEG uses a fixed 128 KiB callback buffer (`OAI_IMAGE_MAX`), discards further writes on overflow, and retries exactly once at `max(1, quality*2/3)`. A second overflow returns `image_limit` before opening the output file. Tests cover successful retry, persistent overflow, JPEG dimensions and actual JPEG decoding. Only the vendor implementation TU suppresses warnings and uses `-fwrapv` for stb 1.16's signed JPEG bit-buffer shifts; ASan/UBSan remain enabled there and throughout the client.

## Clipboard

`rfb_cut_text()` accepts UTF-8 caller input and sends classic ClientCutText only when every code point is Latin-1, after converting to actual Latin-1 bytes. Chinese, emoji and malformed/non-Latin-1 UTF-8 return `unsupported` before sending any clipboard header. Extended Clipboard is offered in SetEncodings; a server capability pseudo-rectangle or valid UTF-8 Caps message exposes `extended_clipboard` in the API/JSON. This is an advertisement flag, not evidence that macOS supports clipboard synchronization. Extended UTF-8 sending, clipboard readback, restore and HID paste are not implemented. Future paste must also pass A5's lease/allowed/e-stop gates.

## Host verification and measurements

Host: macOS 26 / Darwin 25.6, arm64, Apple clang, C11 `-Wall -Wextra -Werror -O1`. Default ASan/UBSan C tests pass: existing OAI 3145 checks plus RFB/DES/scaler/JPEG checks. The available environment has no Linux GCC/armhf compiler, and access to the local Docker daemon is denied, so Linux glibc and static armhf builds remain unverified here.

The new targets accept `LDFLAGS`; when a cross toolchain is available, build with `make -C deck/agent BUILD=build-arm CC=arm-linux-gnueabihf-gcc SAN= LDFLAGS=-static build-arm/mk20-agent-nosan`.

The execution sandbox rejects even `bind(127.0.0.1, 0)` with `EPERM`. The required standard command was run, using an offline copied uv cache in `/tmp`:

```sh
cd deck && uv run --with pytest python -m pytest -q agent/tests
```

R1 result: **38 passed, 94 failed**, all 94 failures are the same sandbox socket-bind `PermissionError`, before protocol execution. No tests were changed to skip these failures. TCP acceptance is therefore pending a host that permits loopback listeners. The run used `UV_CACHE_DIR=/private/tmp/mk20-a2-uv-cache UV_OFFLINE=1`; the default uv cache is not writable in this sandbox.

To exercise the protocol despite this restriction, `MK20_TEST_SOCKETPAIR=1` selects a fake over local IPC. The fixture explicitly compiles `MK20_RFB_TEST`; only that test build accepts an inherited socket FD. Production CLI builds have no FD/environment transport override. The same handshake, decoders, scaler, encoder and error paths run over the connected stream. The normal fixture continues to use 127.0.0.1 TCP.

```sh
MK20_TEST_SOCKETPAIR=1 MK20_TEST_NO_NETWORK=1 \
  uv run --with pytest python -m pytest -q agent/tests/test_rfb.py \
  agent/tests/test_oai.py agent/tests/test_fakes.py::test_des_known_vector
```

R1 result: **124 passed, 1 skipped** (the existing OAI HTTP test explicitly disabled). A2 alone: **87 passed**. This includes sanitized and non-sanitizer regressions sending a precompressed 8192×8192 solid ZRLE frame (<128 compressed bytes, no delivery delay or source-sized fake allocation) with a 100 ms timeout; both return `timeout` within one second and leave no JPEG. `make -C deck/agent test` also passed (3145 OAI checks plus RFB/DES/scaler/JPEG checks, ASan/UBSan). The Linux procfs branch passed a C11 `-Wall -Wextra -Werror` syntax check with Apple clang and `-D__linux__`; Linux runtime/GCC and armhf remain unverified on this macOS host. Original A0 Raw/ZRLE fake-reader checks passed over socketpair in the initial A2 run, including two updates on the same inflate stream. Socketpair coverage supplements TCP acceptance.

The new compressed-frame regression was also checked against a non-sanitizer build of the pre-R1 `HEAD` client: it correctly failed because that client returned success after 595 ms of framebuffer work with the same 100 ms timeout.

Initial A2 measurements (before R1): five fresh non-sanitizer CLI processes per source size, default quality 70 and target 1280×800, ZRLE raw tiles over socketpair, no colour-map/clipboard prelude. Times below are medians; RSS/heap/total-max are maxima. Source generation before launch is excluded; `fb` includes waiting for the Python fake to produce/compress pixels, decoding and streaming accumulation. `scale_enc` includes in-place finalization and JPEG; `total` includes writing the file. No device or real Mac Screen Sharing was measured.

| Source | connect / auth / fb / scale_enc / total (ms) | max total (ms) | peak RSS (KiB) | heap HW (B) | JPEG (B) |
|---|---|---:|---:|---:|---:|
| 1280×800 | 0 / 0 / 424 / 5 / 430 | 433 | 4,592 | 3,296,592 | 51,212 |
| 2880×1800 | 0 / 1 / 2238 / 9 / 2248 | 2253 | 7,664 | 6,368,592 | 119,451 |

The RSS regression also enforces ≤16,384 KiB for both sizes in non-sanitizer builds. Python fake latency, IPC and these five samples do not establish the ≤800 ms device P95 criterion.

## Remaining [D][M] / integration work

* On an authorized real Mac, record offered security types and whether enabling VNC credentials offers type 2; type 30 remains unsupported.
* Measure Retina and multi-monitor framebuffer shape, desktop resizing, which display(s) are captured, coordinate mapping, locked-session behavior and real server encoding modes.
* On T113/static armhf, measure P95 capture/scale/JPEG latency, peak RSS and CPU, and kbd input latency during capture (P95 increase ≤5 ms). Repeat under resource pressure; integrate supervisor/resource limits separately.
* Measure Extended Clipboard advertisement and actual UTF-8 synchronization without reading user clipboard contents. Until confirmed, Chinese paste remains unsupported; no Mac helper is added.
* Re-run the unmodified loopback pytest command on Linux GCC and macOS with loopback permitted; run static armhf checks before accepting §14.5-2. No claim of device acceptance is made.
