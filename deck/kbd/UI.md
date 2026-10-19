# On-board UI: U2 host integration

U2 is implemented for host validation. **no device acceptance claimed**. The shipped
`mk20-kbd --run` now boots on-board HOME with allowed=0, including `--no-host`.
Replacement selection, stock-KD coexistence, rescue/OTA rules, GD32 write kill files,
and the optional control socket/scroll descriptor remain unchanged. This does not
turn on FEATURE_KBD in a release or install anything. `--ctl-allow` remains a dev-only
exception; releases never pass it. fb20 and K2 safety are intrinsic to the replacement;
`--k2-estop` is retained for launch compatibility. `--k2-release` still requires known
release-capable firmware; otherwise the existing three-press fallback applies.

`onboard.c` owns the compositor and route epoch; `ui.c` remains the accepted pure
HOME/LS renderer. HOME/LS/offline use ctl page 0, L5 uses 1, L1/L4 use 2, and
connection-owned agent pages retain ids >=3. Internal routes never renumber ctl ids.
HOME buttons enter LS/L5/L1/L4; short K2 toggles L1/L4, short K1 on L4 enters LS,
and K1 hold returns HOME (LS draft discard requires explicit confirmation). LS Back
returns to its prior route. Knob short actions happen only on release below 1 s.
K2 long/triple stops, and its release cannot perform a short action. Nonzero GD32
keycodes display an incompatible-mapping warning and never synthesize duplicate HID. E-stop is
available on every route, independent of a Mac or listening control socket.

The compositor preserves fb20 on every page, fb16 on L5/agent pages, and fb21
rows112..141. Host JPEGs only copy host-owned faces and the sub-screen body.
Tiles overlapping owned regions return cmd200 `E` without mutation. Entire ctl pages
0/1 are reserved even during a grant; agent fb16/fb20 binds/draws and any fb21 draw
intersecting the footer are reserved. fb21 `screen.text` implicitly addresses only the y0..111 body viewport;
permitted text still returns `rendered:false`, with no pixel mutation. No new
agent pixel transport is invented. Builtin show/delete restrictions are retained.

At a route switch, pending display content is replaced and prior receive bytes are
marked stale, including partial frames and a snapshot of unread fd bytes. Their eventual cmd100 ack retains its id;
cmd200 returns `E`. A decoder result carries its original epoch and cannot publish
on a later route. A fresh full JPEG is required after every host-route switch/loss;
tiles cannot initialize a page. One in-flight writer may finish, then its replacement
follows. Host handshakes/loss never blank or navigate local pages or discard drafts.
Press-time routing consumes local releases; leaving Mac PTT sends modifier-up.
Mac PTT edges are not also forwarded as host key events. Host K1 effort and short
K2 toggle are forwarded as complete short taps only after the release decision.
Physical host-page navigation synchronizes the known host mode with a K2 tap; L4
Back is decided locally on release. Accepted host ui.page requests do not synthesize
a second navigation tap.

Optional cmd101 **flat JSON** methods (<=1023 JSON bytes, strict duplicate/type checks):

- `{"method":"ui.hello","version":1}` returns version1, heartbeat_ms1000,
  capabilities `ui.page`/`ui.heartbeat`, and the last accepted generation.
- `{"method":"ui.heartbeat"}` at 1 Hz: three consecutive samples spanning >=2 s
  establish ONLINE. Samples closer than 1 s do not advance the count; a gap >1500 ms
  restarts the probing count. At 3 s silence, SUSPECT freezes host input/content and
  adds a footer badge; at 5 s, OFFLINE replaces visible host content with a placeholder.
  EOF/link-down is immediate OFFLINE. A new hello restarts negotiation, never navigation.
- `{"method":"ui.page","page":"L4","generation":7}` is accepted only while
  ONLINE and already in an explicitly entered L1/L4 route. Page is L1 or L4;
  generation is 1..999999999 and strictly increases across hello/loss until process
  restart. The reply echoes the accepted page/generation; even same-page acceptance
  invalidates prior content and requires a fresh full frame. Home/LS/L5/agent routes
  cannot be claimed. cmd201 remains reserved/unimplemented; no widget generation
  transport is claimed yet.

Replies retain the KD `ack_method`/`success`/`result` envelope and frame id. `getInfo`
and backlight work on all routes and grant no ownership. Legacy hosts need no new
methods: valid decoded full frames in an explicitly entered host route establish
liveness (three samples over >=2 s); valid permitted tiles maintain it after the first
full frame. After loss, `getInfo` serves as the legacy fresh handshake. No cached
pre-switch frame is reused, and host recovery never enables/resumes an agent.

JPEG decoding runs in one worker, reusing the existing host canvas. There is one
compressed job <=1 MiB plus at most one <=1 MiB fresh replacement while an older
epoch is still decoding (no unbounded frame queue), a 1 MiB operational parser payload cap, and
one 121,552-byte local panel scratch. Decode/full-page production are capped at4 Hz;
local redraws coalesce and run one panel per poll boundary. Safety/ctl deadlines run
before HID/host work and after each render slice. Echo waits during the unchanged
allow-listed knob initialization service the safety/UI loop every <=10 ms; GD32
encode/tx gates and outbound bytes are unchanged. Writers stay asynchronous with
200 ms retry backoff; failure to start them exits instead of blocking on SPI in the
safety loop. A successful confirmation presentation is acknowledged only when all
required pending panels are successfully written and both writers are idle.

Host output has a bounded 16 KiB nonblocking buffer; partial writes are retained.
Space for 64 empty cmd100 acks is reserved against JSON reply floods. This preserves
the stop-and-wait host's ack under backpressure without delaying safety. A physically
lost link cannot promise delivery; unbounded noncompliant floods may exhaust the
fixed buffer. Ack means handled, never physically displayed.

The L5 scaffold provides physical allow/disable, guarded confirm/cancel, HOME/LS,
fixed safety/PTT faces and ctl state/summary/risk. Durable Apply/device Test consumers
remain U3: production Apply fails visibly and retains its draft, never falsely
reports an in-memory change as durable. Full pause/voice behavior remains U4/U6;
PTT/ASR and pause are visibly unavailable, with no transcript or paused wire enum.
The simulator explicitly uses injected in-memory Apply/test results, not production
I/O. Real status probes, device timing/RSS/SPI budgets and physical acceptance remain U7.

`mk20sim.harness.CSim` / cases with `"backend":"c"` use the sanitized
`tests/onboard_headless` process, fake monotonic time, input packets and the real
compositor. C produces P6 RGB pixels with the prescribed RGB565 floor expansion;
Python only drives the DSL/crops, and JPEG encodes browser previews. CLI and browser
case replay select this backend. `cases/onboard.json` includes nine offline cases;
unknown expectations/injections fail explicitly. The default Python L1/L4 cases
remain unchanged. No key files are read, and no device/network is used by the C driver.

U2 validation (2026-10-19, macOS arm64/clang; offline cached Python dependencies):

- `make -C deck onboard-ui-test`: 599 U2 ASan/UBSan checks; 1,130 U1 checks;
  85 preset/model/profile cases, 170 exact files through actual agent loaders,
  four escaping cases; all 286 P6 goldens unchanged. The C simulator selection is
  11 passed / 24 deselected (nine cases plus two adapter checks).
- Full simulator: 35 passed, including all 24 prior regressions. Separate Python
  settings/controller/render/protocol/docs/agentcfg regression selection: 92 passed.
- `tests/onboard_test_tsan`: 599 checks, no TSan warnings. Slow regular-file/missing-framebuffer
  tests prove in-flight replacement on HOME/LS/L5 and also exposed and fixed the two-writer shared error-counter race.
- `tests/test_kbd --ctl-unit`: 2,324 checks, zero failures; reservations expectations
  updated to the new ownership contract.
- Requested `make -C deck test` with UV_OFFLINE=1 and a writable cache: simulator
  35 passed; U2 checks passed; core suite 3,999 checks, 3,985 passed / 14 failed.
  A pristine HEAD archive reproduced exactly the same 14 assertions: two Linux
  /proc fd-identity assumptions and twelve denied AF_UNIX listener/connect assertions.
  The make target stops there; remaining full-target recipes are not claimed passed.
- Additional agent Unix-socketpair-only selection: 213 passed, 1 failed, 1 skipped,
  3 deselected initially; the two parameterizations of the failing wall-clock timeout
  test passed on isolated retry (2 passed / 85 deselected). No agent source changed.
- No static armhf toolchain is installed. Release compiler source lists include U2;
  no binary was installed or released. No physical timing/resource acceptance claimed.

The following U1 notes are retained as historical foundation documentation and counts.

U1 is a C99 library, without main-loop, device, HID, GD32 or network integration.
`ui_key()` takes 1-based framebuffer ids, and `ui_knob()` takes semantic knob
edges/rotation with an injected int64 monotonic millisecond clock. Call `ui_tick()`
while a knob is down. `UI_PRESS` is a synthetic short press for replay; firmware
capability detection and the existing K2 press-only fallback remain U2 integration.
All state and drafts are bounded. Apply is injected; `NULL` applies only in memory.
A callback may call `uicfg_apply()`; it returns zero only after the pair is durable.
Never call this blocking writer from a future safety/input poll loop.

`ui_render_panel()` draws into caller-owned RGB565 storage sized using `PANELS`.
It allocates nothing and performs no I/O. It supports HOME and LS only. L1/L4/L5
navigation reports pending integration. E-stop input changes render state only;
there is no control-protocol action in U1. Test results are explicitly injected
fake results. Versions, IPs, audio support and clock default to unknown/unavailable.
U3 must enforce model capability/adapter support before actual tasks/tests; selection
of Anthropic is retained as an unsupported preset, without asserting OAI compatibility.

`uicfg_apply(root, ...)` validates the preset/model and host/ports before mutation,
walks directory components without following symlinks, and writes no key material.
Logical secret references are fixed to `/data/mk20/secrets/<id>.key`. The configurable
output root must be an actual canonical directory path, without symlink ancestors
(e.g. macOS `/private/tmp`, not `/tmp`). It creates 0700 directories and 0600 files,
uses exclusive no-follow temporary files, full writes, file/directory fsync, and
rename. `.apply-lock` uses a nonblocking process lock; a single owning worker must
serialize threads (U2/U3). `.apply-in-progress` survives failure, including mixed-pair
failure; explicit full re-Apply repairs it. This is not two-file transactional rollback.
Existing agent readers do not check this marker yet; U3 must gate consumers before
any deployment. The optional Mac Python writer gains directory fsync in U1; its
pair coordination also belongs to U3. WiFi routing retains the Python oracle:
domestic direct, foreign proxy with wifi_proxy or blocked without. Explicit D1′ mode
selection is deferred to U3, with no proxy-to-direct fallback.

The source catalogue is `../share/providers.json`; the Python package resource is a
symlink to that source, copied into wheels by setuptools. The Python page preserves
legacy physical provider positions through catalogue `legacy_order`; the C page
uses the normative GLM Coding-first order. Run `python3 ../tools/gen-providers.py`
when editing the catalogue; `--check` is part of `make ui-test`.

Fonts/icons are committed bitmap assets. ASCII uses DejaVu Sans Mono (license in
`font-LICENSE-DejaVu.txt`); fixed CJK derives from the macOS bundled STHeiti Light
font (proprietary macOS font license, not OFL). Full source hashes and rasterizer
versions are recorded in `ui_font.c`. Icons and the visible unsupported-glyph marker
are explicit original bitmaps. Regenerate on a host with the same source fonts and
Pillow/FreeType versions (generation only, no runtime dependency):

```
python3 ../tools/gen-ui-font.py --font '/System/Library/Fonts/STHeiti Light.ttc' \
  --ascii-font /path/to/DejaVuSansMono.ttf
```

`--check` verifies byte reproducibility with those same inputs. Fixed strings live
in `ui_labels.inc`; every fixed label and catalogue label/model passes glyph coverage.
Unknown dynamic codepoints draw the explicit replacement marker; long text ends
in a bitmap ellipsis. All panel clipping uses integer pixels.

`make ui-test` builds ASan/UBSan C units, compares both writers across every preset,
loads all outputs with the real agent loaders, and compares headless PPMs. If present,
arm-linux-gnueabihf-gcc additionally compiles the renderer static. Tests use temporary
files and injected clocks/hooks, with no network sockets/devices. Only `make ui-golden`
updates `tests/golden/ui/`: losslessly compressed P6 files, expanded and compared
byte-for-byte, including P6 headers, dimensions and RGB565 floor expansion. The
manifest records scene source hash, fixed labels, glyph inventory and all paths.
`ui_headless OUTPUT_DIRECTORY` also writes raw per-panel PPMs plus 640x656 canvases
for review. The old simulator harness drives Python/controller JPEG output; adapting
it to this C state/pixel backend and adding onboard.json are explicitly U2 work.

Validation on 2026-10-19 (macOS arm64, clang; no device or network sockets):

- `make -C deck/kbd ui-test` and `make -C deck onboard-ui-test`: 1,002 ASan/UBSan
  C checks; 85 preset/model/profile cases / 170 byte-identical files, real agent
  loaders; 4 escaping cases; 286 byte-exact P6 comparisons across 13 scenes.
  Catalogue entries, capabilities and labels are also compared against the C table.
- `gen-ui-font.py ... --check`: reproduced the committed font bytes with the
  recorded source hashes and Pillow/FreeType versions. Installed-wheel import of
  `agentcfg.PROVIDERS` and `settings.MODELS` passed, including GLM Coding.
- Python regression selection (`test_agentcfg`, `test_settings`, `test_controller`,
  `test_render`, `test_protocol`, `test_keytrace`, `test_docs`, `test_codex_desktop`,
  `test_link_layout`, plus `tests_sim`): **116 passed**. Simulator alone contributes
  24 tests; the earlier settings/agentcfg/sim selection was 41 passed.
- `MK20_TEST_SOCKETPAIR=1 MK20_TEST_NO_NETWORK=1 python3.13 -m pytest -q
  deck/agent/tests/{test_oai,test_llm,test_rfb,test_net}.py -k
  'not test_unreachable_proxy_never_direct and not test_fallback_order'`:
  **214 passed, 1 skipped, 3 deselected**. Those exclusions avoid TCP sockets;
  traffic in the selected transport tests uses anonymous Unix socketpairs only.
- Existing sanitized kbd units, using only regular temporary files/Unix IPC:
  **1,634 core/GD32 allow-list checks**, **2,324 `--ctl-unit` checks**, zero failures.
  The core subset was invoked by a temporary wrapper including the unchanged
  test_kbd.c, calling gd32_roundtrip/corrupt/fuzz/allowlist, knob_frames,
  host/screen, knob_init/jpeg/kbd_core/writers/hid tests from the correct kbd cwd.
- The initial full kbd invocation from the repo root returned 3,991 checks /
  45 failures: relative JPEG fixtures were missed; Linux /proc fd assumptions and
  sandbox-denied Unix listener binds also failed. Correct-cwd offline subsets above
  were then verified. This does not claim a full macOS kbd-test pass.
- Broad `make -C deck test` was intentionally not run: it includes TCP and pty/serial
  e2e paths excluded by the task. No static armhf toolchain is installed; ui-test
  will compile the standalone renderer with `-static` when one is available.
- `git diff --check` passed. No GD32 code/allow-list or mk20_kbd.c changes; no commit.

Changed-file inventory (8 modified, 309 new, including 287 golden assets):

- `.gitignore`, `deck/{Makefile,ONBOARD-UI-PLAN.md,AGENT-BUILD-PLAN.md,pyproject.toml}`.
- `deck/share/providers.json`, `deck/tools/{gen-providers.py,gen-ui-font.py}`.
- `deck/mk20deck/{agentcfg.py,settings.py,catalogue.py,providers.json}` (the last is
  the relative package-resource symlink to the shared source).
- `deck/kbd/{Makefile,ui.c,ui.h,ui_labels.inc,ui_font.c,ui_font.h,ui_font_render.c,
  uicfg.c,uicfg.h,providers.inc,font-LICENSE-DejaVu.txt,UI.md}`.
- `deck/kbd/tests/{ui_test.c,ui_headless.c,uicfg_cli.c,ui_load.c,ui_conformance.py,
  ui_golden.py}`, plus 286 `tests/golden/ui/*/*.ppm.gz` files and manifest.json.
