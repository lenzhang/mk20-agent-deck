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
