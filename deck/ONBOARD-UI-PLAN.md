# On-board UI plan (2026-10-19)

Status: user-approved design; U1 host foundation and U2 host integration implemented (2026-10-19); U3–U7 pending.
The keyboard must be fully usable without the Mac app. This supersedes the earlier
“no Mac helper” decision with **optional helper, never required** (2026-10-19).
No AirPlay. Network access uses only the explicitly selected egress: Mac proxy is
proxy-only; USB WiFi may be direct or proxy. A failed proxy never falls back to direct.
Independence from mk20deck does not make a selected Mac proxy work without its Mac.

## 1. Baseline and architecture

Read against `KD-REPLACE-PLAN.md`, `AGENT-BUILD-PLAN.md` §6.2,
`kbd/CTL-PROTOCOL.md`, `kbd/K2-KNOB-CONFIG.md`, `kbd/*.h`, `kbd/screen.c`,
`mk20deck/{settings,agentcfg}.py`, `mk20deck/render/settings.py`, `../sim/SETTINGS.md`,
`mk20sim/{harness,cases}.py`, `mk20sim/cases/settings.json`, `agent/NOTES-agent-a2.md`
through `NOTES-agent-a4.md`, `agent/src/*.h`, `Makefile` and `README.md`.
Historical implementation notes describe their original state; this plan defines the new work.

Current `kbd.c` accepts cmd100 full JPEG and cmd200 tiles without page ownership,
uses `kbd_draw_idle()`/`kbd_draw_key()` for local feedback, and sends Right Option
from the configured PTT position without page scoping. `ctl.c`/`ctlscr.c` already
implement leases, confirmation guards, reserved control page/e-stop, and screen
permission/rate rules; `screen.draw`/`screen.text` still return `rendered:false`.
There is no pause operation or paused `ui.agent` enum today. These are integration
changes, not capabilities to assume already exist.

| Component | Ownership and responsibility |
|---|---|
| mk20-kbd, C, static armhf on T113 | Sole panel and HID owner; home, LS, L5, confirmation/safety overlays; input arbitration; config validation and atomic writing; embedded bitmap renderer. No Python runtime on board. |
| mk20-agent, static C | Device-side network/model test using `net.c`/`llm.c`, task loop, capture-source selection, board-mic/ASR worker later; usb0 config/key/task web service. Actions only through kbd.sock lease. |
| mk20deck, optional Mac Python enhancer | Existing Codex/tmux/herdr session and mode pages L1/L4; optional screen JPEG and AX fast paths. Never authoritative for device configuration, enable, confirmation or safety. |
| mk20d / existing release machinery | Supervision, A/B probation, status; retain current KD-replacement rescue policy. UI failure must not disable OTA. |

Proposed files (not present promises): `kbd/ui.{c,h}` (pure state/render functions),
`kbd/ui_font.{c,h}` (embedded assets), `kbd/uicfg.{c,h}` (bounded config writer),
`share/providers.json` (catalogue), generated `kbd/providers.inc`, and host-only
`kbd/tests/ui_headless.c`. Integrate through `kbd_on_gd32()`,
`kbd_on_host_frame()`, `kbd_safety_tick()` and the poll loop in `mk20_kbd.c`.
Render into RGB565 buffers, then use `screen_set_tile565()` and existing two bus
writers. Do not let the renderer open a framebuffer or HID device itself.

### Screen ownership and host coexistence

UI route and control-protocol page id are distinct. Keep existing numbering:
`CTL_SCREEN_SYSTEM=0` contains home/LS/offline placeholders; control page 1 is L5;
user page 2 contains host L1/L4; agent-owned pages remain ids >=3, at most eight,
bound to their creating connection. `screen.layout` continues to expose these ids;
new route/host-generation metadata must be explicitly documented when added.

| Route | Mac absent | Mac present |
|---|---|---|
| Home (boot default) | On-board | On-board; attach never forces navigation |
| LS settings | On-board | On-board; host cannot replace it |
| L5 agent / confirmations | On-board | On-board; agent supplies state, never the control visuals |
| L1 sessions / L4 modes | On-board “Mac offline” placeholder | Host content with on-board reserved key and fb21 safety/voice strip |
| Agent-owned page >=3 | On-board renderer + validated agent content | Same; host frames cannot enter it |

A single compositor checks route, owner and generation **before** publishing any
panel update. It applies safety overlays last, including when writer threads have
pending frames. Invalidate/discard old pending content on route/generation changes;
a writer already in flight may finish, then the replacement must follow. Confirmation
“shown” is reported only after the required panels finish successfully, not on enqueue.

Retain `host.h` A1 A5 5A 5E framing: cmd100 JPEG, cmd101 JSON, cmd200
`u8 fb + u16le x,y,w,h + RGB565le`, cmd201 widget/state is reserved and currently
unimplemented. Do not confuse cmd201 with the local `screen.*` API or invent an
existing socket pixel transport (JSON lines including LF remain <=1024 bytes).

U2 negotiates optional cmd101 JSON methods `ui.hello` (protocol version/capabilities),
`ui.heartbeat` (1 Hz), `ui.page` (L1/L4 plus monotonically increasing page generation).
These are new methods, not existing KD methods. The device alone accepts the page
request. New hosts echo the accepted generation in new widget metadata; legacy
cmd100/200 have no generation, so accept them only in an explicitly entered current
host route, and require a fresh full frame after a switch. Flush the receive/decoder
work for the prior route at switches; never display a cached pre-switch frame.

For cmd100 retain the empty ack with the same frame id even if hidden/discarded,
so stop-and-wait never stalls; ack means handled, not physically displayed. Decode
only while eligible and copy only permitted panels/regions. cmd200 on an owned
on-board route or reserved region returns its existing error payload `E`; no mutation.
`getInfo` and backlight remain supported without granting page ownership. A legacy
host lacking heartbeat capability is live only while valid frames arrive; it cannot
claim LS/home/L5. No host event forwarding for consumed on-board keys/knobs, and no
second Mac-side action from a physical on-board safety/PTT edge.

Host health: OFFLINE -> PROBING on handshake; ONLINE after three consecutive 1 s
heartbeats (legacy: three valid frames spanning >=2 s). Three seconds without a
heartbeat/frame gives SUSPECT (freeze host actions and show stale badge); five
seconds gives OFFLINE and replaces the visible host page. EOF/link-down is immediate
OFFLINE. Re-entry requires a fresh handshake, the same >=2 s healthy window and a
fresh full frame. Discard staged host work on each loss; keep home/LS/L5 and their
local drafts stable through flaps. Host health and selected proxy health are separate:
mk20deck exiting does not prove the Mac proxy is down. Never resume agent actions
just because a host returns.

## 2. Pages and panel layouts

Use 1-based document `(col,row)` throughout tables; wire/API coordinates are
`col-1,row-1`. `fb = (row-1)*5+col`; fb1–5 row1, fb6–10 row2,
fb11–15 row3, fb16–20 row4. `PANELS` in `kbd/panels.c` is authoritative:
20 keys 128×128; fb21 428×142 at canvas (106,0); keys start y=144;
canvas 640×656. Bus0 = fb1–9,21; bus1 = fb10–20.

Reuse the simulator's dark background (14,16,20), foreground (235,235,240), blue
focus ring, green selected/check, grey read-only and red failure/e-stop, rounded
key faces, title/subtitle and staged-change count. Use fixed integer geometry and
bitmap glyphs instead of Pillow/system-font metrics. Host JPEGs need not match
C rasterization byte-for-byte; new C-rendered goldens must. Reserve fb21 y=112..141
for on-board voice/safety and the two 214-pixel knob hints (two bitmap text lines);
host/agent body drawing is clipped to y=0..111. Local pages use the same footer.

### Home

| row / fb range | col1 | col2 | col3 | col4 | col5 |
|---|---|---|---|---|---|
| 1 / 1–5 | Link status | Proxy status | Agent state | Firmware version | Settings -> LS |
| 2 / 6–10 | Agent -> L5 | Sessions -> L1 | Modes -> L4 | Selected profile | Provider/model |
| 3 / 11–15 | USB IP | WiFi status | Config status | Key set/missing | Device info -> LS/info |
| 4 / 16–20 | Voice target/status (read-only) | Task status | Home (current) | Web/key setup hint | **E-stop** |

Read-only cells have no action. fw/kernel/GD32 version and link state come from
available local release/status data; unknown is “unknown”, never the old sim's
hardcoded fw1.1.4/GD32 V2.32 presented as a live measurement. No new GD32 query.
fb21: home title, link/proxy/agent state, current error; footer split into 214-pixel
K1/K2 hints plus permanent voice-target label.

### LS (settings)

| row / fb range | col1 | col2 | col3 | col4 | col5 |
|---|---|---|---|---|---|
| 1 / 1–5 | Network | Provider | Model | Connection test | Device info |
| 2 / 6–10 | item1 | item2 | item3 | item4 | item5 |
| 3 / 11–15 | item6 | item7 | item8 | item9 | item10 |
| 4 / 16–20 | Back (prior route) | Apply / confirm | Undo draft | Web/key setup | **E-stop** |

This preserves the existing simulator layout, with “Mac/网页” changed to “网页设置”
and the old bottom-right “回 L1” replaced by reserved e-stop. Home is always reachable
by K1 long press. LS entry is available directly from home and short K1 on L4
(including its offline placeholder). Back with a draft requires a second press to
explicitly discard; any unrelated input disarms it. Apply network changes requires
a second Apply; unrelated input/draft edits disarm confirmation. Other edits apply
once. Test uses applied config only; refuse while draft is nonempty.

| Tab | item1..10 in display order (unused cells blank/disabled) |
|---|---|
| Network | Mac proxy; USB WiFi; proxy host; proxy port; WiFi SSID/status; WiFi proxy host; WiFi proxy port; route policy (selected direct/proxy); link status; setup help |
| Provider | qwen; glm-coding (default GLM choice); glm (metered); kimi; deepseek; openai; anthropic; gemini; blank; blank |
| Model | Catalogue models for selected provider, up to ten per view; K2 moves/scrolls through further models if needed |
| Test | Run/cancel test; result/error class; latency; profile; proxy; provider; model; clock state; key presence; blank |
| Info | Brightness; volume; fw; kernel; USB IP; GD32 version if known; selected egress; kbd version; agent version; config health |

Proxy host editor: K2 selects digit/character position (IPv4 or bounded ASCII DNS
label), K1 rotates a constrained alphabet/value, K2 press commits that field,
Back cancels. Ports 1..65535, brightness UI 10..100%, volume 0..100% in steps of 10;
stage values, do not change live settings until Apply. K1 short shows summary
outside a field editor. Host labels/addresses are truncated with an explicit ellipsis;
full bounded value appears on fb21. WiFi credentials and API keys have no keyboard
editor. SSID/association setup uses the on-board web page. Distinguish unavailable
volume/audio support from a successful hardware apply.

fb21: title/tab; profile + provider/model + “未应用”; focused value/toast/error;
K1 adjust/K2 select hints with embedded ⟲⟳; voice target “键盘 agent (L5)” or
“Mac (L1)”, with recording false on LS. Never show any secret fragment.

### L5 and confirmation overlay

| row / fb range | col1 | col2 | col3 | col4 | col5 |
|---|---|---|---|---|---|
| 1 / 1–5 | Allow/disable locally | Agent state | Current goal summary | Step/budget | Input HID/VNC |
| 2 / 6–10 | Pause/resume | Task status | Provider | Model | Network/test status |
| 3 / 11–15 | Confirm (guarded) | Cancel confirmation | Home | Settings -> LS | Screen source |
| 4 / 16–20 | **Hold: keyboard-agent PTT** | Recording/ASR status | New-goal status | Web tasks hint | **E-stop** |

The whole L5 page is system-owned. fb16/doc(1,4) is reserved PTT on L5 and
agent-owned pages; fb20/doc(5,4), API row3,col4 is e-stop on **every** page.
Agent-owned pages reserve both faces, both binds, and the fb21 safety/voice strip;
even a temporary screen grant cannot override them or home/LS/L5. Extend the current
`reserved` validation to enforce this; existing code reserves only control page/fb20.
Confirmation uses the same L5 layout with the summary/risk on fb21 and fb3–4,
confirm/cancel at fb11/12; buttons inactive until physically shown. Call
`ctl_confirm_shown()` once after successful presentation; `ctl_confirm_key()` keeps
its >=300 ms guard and deadline precedence. Agent/host text can never spoof buttons.

fb21: voice target **keyboard agent**, lease/paused/stopped state, goal/recording/
ASR error, source and upload notice; K1/K2 hints. Before ASR exists, show “PTT: ASR
unavailable”; the reserved key and pause state still work, with no fake transcript.

### Host L1/L4 and offline placeholder

| Page / row | col1 | col2 | col3 | col4 | col5 |
|---|---|---|---|---|---|
| L1/L4 row1 fb1–5 | Session1 | Session2 | Session3 | Session4 | Accept |
| L1/L4 row2 fb6–10 | Session5 | Session6 | Session7 | Session8 | Decline |
| L1 row3 fb11–15 | Skill1 | Skill2 | Skill3 | Skill4 | Send |
| L4 row3 fb11–15 | Codex Desktop | herdr | tmux | Back L1 | Send |
| L1/L4 row4 fb16–20 | Mac PTT | Stop session | New session | Focus | **E-stop** |
| Offline row1 fb1–5 | Mac offline | Link state | Last-seen age | Reconnect state | Settings |
| Offline row2 fb6–10 | Home | Agent L5 | blank | blank | blank |
| Offline row3 fb11–15 | blank | blank | blank | blank | blank |
| Offline row4 fb16–20 | Mac PTT unavailable | blank | Home | Setup help | **E-stop** |

The current host Mode key at doc(5,4) moves to short K2 (L1 <-> L4), since fb20
becomes reserved everywhere. Keep existing host roles otherwise. fb21 host body can
show session details; an on-board footer always states “语音目标: Mac / Right Option”
and current safety status. Offline Mac PTT emits no HID. Host reconnect cannot
select a session or execute a stale key press automatically.

## 3. Input, state machine and safety

| Input | Mapping |
|---|---|
| K1 slots100/101/102 | press/ccw/cw; LS numeric/text edit or summary, home status focus, L5 detail scroll; L1 effort behavior retained |
| K1 long >=1 s | Global Home, consumed locally (replaces old long-to-L4); cancel draft needs explicit discard before leaving LS |
| K2 slots103/104/105 | press/ccw/cw; local focus/select, host scroll, short press L1/L4 toggle; action only on release below hold threshold |
| K2 hold >=1000 ms | Global latched e-stop, fires while down; no short action on release |
| fb20 all routes | Immediate latched e-stop; host/agent cannot draw or bind it |
| fb16 on L5/agent pages | Board PTT, consumed locally, cannot be rebound; never sends Right Option |
| fb16 on L1/L4 online | Existing Mac Right Option down/up; revokes agent lease as before |

K2 release support must be validated. Preserve `k2estop.c` press-only fallback:
three presses within <=1500 ms until release observed; `--k2-release`/release switch
only for known release-capable firmware. U7 cannot claim 1 s hold acceptance until
cold-boot hold works with the correct capability flag. Rotation is never a hold.
Reserve key down/up routing at **press time** so switching page cannot reinterpret a
release or leave Right Option held. Leaving a Mac PTT route sends modifier-up first.
Reserved safety processing is before forwarding, HID service and rendering.

Keep `ctl` state `disabled|idle|leased|stopped` and lease token/seq/TTL as defined in
`CTL-PROTOCOL.md`. Add a separate action gate `pause_reason` (none/manual/ptt/asr)
and UI states `paused`, `listening`, `transcribing`; these are proposed protocol
extensions to document/test in U4. Do not silently send these values to the current
`ui.agent` validator. Proposed U4 wire additions: `status` adds `paused` (0/1),
`pause_reason` and monotonic `voice_generation`; holder-only
`{"ev":"input.pause","paused":true,"reason":"ptt","voice_generation":7}` on down,
then the matching paused=false event only after the goal revision is accepted (or
an allowed error recovery). The same generation guards ASR completion and lease
identity; late messages never reopen the gate. Agent updates its extended `ui.agent`
state but cannot clear the keyboard pause gate itself. No remote pause-clear/allow
operation is added. No socket/MCP/host operation can call `ctl_set_allowed()`.

| Event | State/action |
|---|---|
| Boot/restart | Home; allowed=0; no lease/grant; no automatic task resume |
| Physical L5 Allow | `ctl_set_allowed(c,1)` clears stop latch explicitly; idle; lease still must be acquired |
| Acquire/renew | Existing connection-bound token, default TTL3000 ms (200..10000); monotonic int64 deadlines |
| Ordinary human key / Mac PTT | `ctl_human_input()` -> revoke(reason human), purge pending actions, all-up; human takes over |
| L5 PTT down with valid lease | **Exception before generic human preemption**: set pause gate immediately, purge queued pointer/scroll/key work, bounded all-up, notify holder; preserve token, seq and expiry; start board mic if available |
| PTT held | Reject `act` except `allup` with new `paused` error; allow renew/release/status/estop; agent keeps renewing while recording/ASR; no input actions from MCP or VNC either |
| PTT up | Stop recording; transcribing gate until bounded ASR result; submit text as a keyboard-agent goal revision, discard the prior plan, then resume with new goal if same lease/allow/safety generation still valid |
| ASR unavailable/empty/fails | No new goal; show error, resume prior goal only when PTT was its sole pause cause and the original lease remains valid; preserve preexisting manual pause |
| PTT without lease/while disabled | May capture/queue goal only after ASR exists; never enables/acquires by itself; disabled/stopped does not record |
| TTL expires/client disconnect/disable during PTT | Existing revoke/all-up wins; cancel capture/transcription/resume generation; no reacquire on release |
| E-stop (key/K2/socket) | `ctl_estop()` -> cancel pending confirmation, revoke, purge, all-up (50 ms write budget), allowed=0, latch stopped, clear grant/binds, home/system page with stop banner; cancel ASR/network work |
| Confirmation | Existing deadline and >=300 ms shown guard; PTT cancels pending confirmation, later risky actions request a new one |
| Agent failure/takeover | Show error/human status; dead client lease revoked; reconnect cannot inherit token or pages; only explicit local action resumes/re-allows |

If ASR is asynchronous, “release resumes with new goal” means actions resume only
after that transcript is available, never execution of the old plan while ASR is
pending. Bound recording to 30 s and ASR to 15 s; overrun cancels with a visible
error, preserves safety/TTL behavior, and requires release before another recording.
Manual pause also retains a renewable lease; unrelated human input still revokes.
The action gate must be enforced at dispatch **and** before queued HID or VNC output.
An in-flight VNC write cannot be recalled; agent must acknowledge pause and stop
output, otherwise freeze/error. No claimed pause guarantee based only on a UI label.

Hard rules, unchanged unless a stricter rule here supersedes older prose:

- **Never send GD32 0x19 or `02 AA E2 78`.** The only GD32 write ever allowed is
  **`05 00 r r 00 00`, r=100..105 (RAM)** through `gd32_tx()` -> `gd32_encode()` ->
  allow-list/raw gate. No EEPROM rows, keymap updates, firmware commands or queries.
  Preserve `--no-gd32-write` and its kill file. Nonzero GD32 keycodes mean hardware
  may already have sent HID: do not synthesize a duplicate; report incompatibility
  for reserved keys rather than trying to rewrite the matrix.
- **Never log/print keys.** No secret content, prefix/suffix, Authorization header,
  raw echoed provider errors, transcript credentials, debug dump or core dump.
  Keys never enter/render from config; only `/data/mk20/secrets/<provider>.key` paths.
- **Never fall back to direct** from a proxy profile and never use an unselected
  route. D1′ permits explicitly user-chosen USB WiFi `mode: direct | proxy`.
  Mac-proxy is proxy-only; proxy failure stays failure. USB-local web/fast-path/VNC
  traffic is local control/capture. U1 preserves existing agentcfg WiFi route bytes;
  explicit mode selection/consumer validation belongs to U3.
- mk20-kbd is the sole HID writer; physical enable only; e-stop independent of Mac,
  agent, network, renderer and web service. Stop release <=100 ms with writable HID;
  surface degraded HID without promising delivery when transport is unavailable.

## 4. Provider catalogue and configuration

Single source of truth: proposed `deck/share/providers.json`, checked in with stable
ids, label, base_url, domestic flag, adapter capability and models/vision/tools flags.
A deterministic generator produces the C table and Python imports it or a generated
module; a host test rejects stale generated output and compares every entry. Do not
keep separate handwritten lists in `settings.py` and `agentcfg.py`.

| Provider id | base_url | Models / policy |
|---|---|---|
| qwen | https://dashscope.aliyuncs.com/compatible-mode/v1 | Existing qwen3-max/qwen3-coder-plus/qwen-vl-max presets; overall default retained |
| **glm-coding** | **https://open.bigmodel.cn/api/coding/paas/v4** | **glm-5.3 (text/tools), glm-5.3-flash (vision)**; default GLM entry; coding-plan key only works here |
| glm | https://open.bigmodel.cn/api/paas/v4 | Metered keys only; existing legacy model presets, clearly labelled |
| kimi | https://api.moonshot.cn/v1 | Existing kimi presets |
| deepseek | https://api.deepseek.com/v1 | Existing deepseek presets |
| openai | https://api.openai.com/v1 | Existing OpenAI presets |
| anthropic | https://api.anthropic.com/v1 | Retain catalogue entry; disabled/unsupported for the sole OpenAI adapter unless compatibility is separately established |
| gemini | https://generativelanguage.googleapis.com/v1beta/openai | Existing Gemini presets |

These are configured presets, not a claim of live provider benchmarking. No provider
lookup/network call is needed to draw/select them. U1 **fixes agentcfg.py** by adding
`glm-coding` at the coding endpoint and matching model list; plain `glm` remains the
metered endpoint. Do not silently reinterpret an existing `glm` key; require an
explicit choice and web provision of `glm-coding.key`. Selection of a non-vision
model rejects screenshot tasks visibly; do not secretly substitute a model/provider.
Catalogue capability drives optional `vision:false` consistently in both writers;
optional `llm.h` fields remain subject to A4 limits.

C `provider.json` must be **byte-compatible** with Python `provider_config()` plus
`json.dump(ensure_ascii=False, indent=1)`: same field order, booleans, UTF-8 escaping,
whitespace and absence of trailing newline. Required ordered fields:
`version, provider, model, base_url, domestic, route, api_key_file`. Example:

```json
{
 "version": 1,
 "provider": "glm-coding",
 "model": "glm-5.3-flash",
 "base_url": "https://open.bigmodel.cn/api/coding/paas/v4",
 "domestic": true,
 "route": "domestic",
 "api_key_file": "/data/mk20/secrets/glm-coding.key"
}
```

`llm_provider_load()`/`llm_provider_parse()` read this file as written; `llm_chat()`
appends `/chat/completions` and delegates routing/key reads to `net.c`. There is no
provider-specific URL rewrite in llm.c (see `agent/NOTES-agent-a4.md`).

Keep version1 `net.json` shape from `agentcfg.py::net_config()`:
`profile, proxy:{url,fallbacks}, routes:{domestic,foreign}`, plus existing version/note
where applicable. Mac default stays `http://172.30.250.1:7890` and legacy proxy-address
fallbacks `10.20.0.1`, `169.254.7.1` only for the default Mac host. A custom host:port
gets no invented fallbacks. U1 adds an optional proxy_host to the Python oracle,
retaining its existing defaults and WiFi route semantics. USB WiFi serializes as existing `board_wifi`. D1′ allows an explicitly
selected direct or proxy egress, never automatic proxy-to-direct fallback or an
unselected route. **U1 keeps agentcfg.py net semantics unchanged**: domestic direct,
foreign proxy when wifi_proxy is set, otherwise blocked. U3 adds explicit mode/schema
selection and consumer validation without silently changing existing profiles.

### Apply and durability

1. LS owns saved snapshot + in-memory draft; configurable output root defaults to
   `/data/mk20/agent`, configurable logical secrets root defaults to `/data/mk20/secrets`.
   Host tests pass temporary roots, never write real `/data`, and perform no I/O to devices.
2. Validate catalogue/model, host/port, lengths (files <=16 KiB), schema/version,
   path containment and route policy before writing. API key bytes are not inputs.
   Create/validate root-owned config directory 0700, reject symlinks/non-directories;
   do not follow symlinks for files. Serialize before starting commit.
3. Serialize all writers (LS and authenticated device web config) through one kbd
   commit worker/lock; web service hands validated nonsecret edits to that owner.
   It cannot change runtime allow/confirmation. Run fsync off the key/safety poll loop.
4. Each file: unique same-directory temporary `O_CREAT|O_EXCL|O_NOFOLLOW`, mode0600;
   write fully; fchmod0600; fsync file; close; rename over destination; **fsync parent
   directory**. Newly created directories also require their parent fsynced.
   Clean failed temporary files without losing the old saved snapshot.
5. Two renames are **not** a transaction. Quiesce/cancel model/test/ASR work and hold
   reload until both files are durable. Before mutation persist a `.apply-in-progress`
   marker (0600, fsync+directory fsync); all consumers check it before loading any
   pair. Clear it with directory fsync only after both files validate/durably commit.
   On partial failure/power loss leave marker, fail closed and require re-Apply of a
   full validated pair; no mixed pair can launch a request. Existing readers lack this
   marker gate; U3 wires it before device networking is accepted. Provider bytes stay
   unchanged; marker is outside their schemas.
6. Only then update saved state, clear draft/result and publish new config generation.
   Agent reloads both files as one in-memory snapshot. Never mutate an in-flight
   request's profile. fsync/rename/dir-fsync failure means “Apply failed”, retain draft,
   no success toast and no network until a valid coherent snapshot is confirmed.

U1 adds directory fsync to `_atomic_write()` and tests its bytes alongside the C
writer. Python pair coordination and the existing agent consumer marker gate remain U3. `SettingsStore`'s Mac JSON
is no longer device authority; UI preferences may persist separately, but networking
never falls back to its defaults after corrupt runtime config.

Keys enter **only** through the on-board usb0 web page at
`http://172.30.250.2:8780` (not the old `.1` placeholder), accessible from a USB host
browser without mk20deck. No keyboard key entry and no Mac daemon key entry.
Use pairing/CSRF protection and write-only `POST /secret/{provider}`; input is masked,
never prefilled/revealed; response/status only “set/missing”, no content or length.
Write secret files atomically 0600 under directory0700, no-follow and fsync directory;
no GET/readback/export. UI checks presence/permissions only; `net_read_key()` handles
bounded read/wipe when needed. Never render key file contents or embed keys in any
JSON, image, simulator fixture, log, crash dump or HTTP error. WiFi passwords use the
same masked write-only principle.

### Device connection test

A single cancellable worker in mk20-agent uses `netcfg_load()`, `llm_provider_load()`
and `llm_chat()`/`net_request()` with the committed snapshot and explicit profile.
The planned authenticated `POST /test` and LS action share that worker; no Python
`HttpReachability` request from the Mac counts as device success. A minimal text
request with no tools tests authenticated provider/model availability; “reachable”
from a 401/404 is not “success”. Vision/tools need separate capability acceptance.
Set overall deadline10 s, one attempt, no adapter retry; cancellation/timeout discards
late results by config/test generation. Report stable classes (blocked, config,
key missing, clock, dns/connect/proxy, tls_verify, auth, rate, server, timeout) and
latency only. Preserve CA/clock checks; time bootstrap must also work without the
optional helper (RTC/manual authenticated setup or configured proxy Date, explicitly
validated); absent trusted time is a visible clock failure, never TLS bypass.

## 5. Optional Mac fast paths

U5 adds a small mk20deck HTTP service on **172.30.250.1 only** (proposed port8781),
no `0.0.0.0`, loopback relay or WiFi listener. If the USB address is absent, keep
session features alive but do not bind the fast-path service elsewhere. Pairing/token,
USB peer validation, request caps, deadlines and no raw content logging apply.

- `GET /screenshot?display=<id>` or `?window=<id>&max_w=1280&max_h=800&quality=70`:
  macOS `screencapture` or ScreenCaptureKit, scaled JPEG, explicit display/window
  selection; no continuous video. Return bounded image plus source dimensions,
  logical/pixel bounds, scale and capture generation/timestamp metadata. Missing
  Screen Recording permission, lock/session loss or bad target returns a typed error.
  Never claim stale cached content as current. Agent enforces current 128 KiB image
  cap (`OAI_IMAGE_MAX`), body cap and deadlines; helper reduces quality/size to fit.
- `GET /accessibility?display=<id>` or `?window=<id>`: AX API read-only windows,
  buttons, fields, text, role/label/value and bounds in the same coordinate system;
  omit secure/protected field values and cap depth12, nodes2000, body256 KiB,
  execution500 ms. Missing Accessibility permission returns unavailable; no permission
  mutation, input injection or inference of hidden text. AX failure alone can still
  leave screenshot fast path usable. Tree is untrusted observation, never instruction.

Agent screen-source order is **Mac fast path if online -> VNC (A2) -> UVC capture
card (`feature/uvc`)**, including lock-screen/crash fallback. Fast path timeout800 ms
or unavailable advances to VNC under its bounded A2 deadline, then UVC if available;
never loop indefinitely or reuse stale geometry. UVC integration is a dependency,
not implemented by A2. Select one source per observation; record source/generation
and recalibrate/invalidate coordinates on change before input. Require three healthy
probes >=2 s before returning to a higher-priority source at an idle observation
boundary; immediately demote on hard failure. Show capture/upload indicator on fb21.
AX is optional context paired to a compatible capture target/generation, not a fourth
image source or a fallback requirement. No source available -> visible pause/error.
Capture permission does not authorize unlocking or password entry; e-stop and lease
rules are identical for all sources. No AirPlay, and no mandatory Mac helper dependency
for home/settings/agent control, key setup, ASR or VNC/UVC fallback.

## 6. Failure behavior

| Failure | Required result |
|---|---|
| Mac absent / helper missing | Boot home, LS/L5 work; L1/L4 placeholders; VNC/UVC tried independently; Mac proxy failure stays failure |
| Mac flapping | Hysteresis in §1; no route stealing, repeated actions, stale releases or pending-frame leaks; preserve LS draft |
| Missing config directory | Show unconfigured defaults in memory; create0700 only on explicit Apply; no background cloud request |
| Corrupt/oversized/symlink config, incomplete pair marker | Config error; networking/test blocked; retain files for diagnosis without printing contents; web/local re-Apply repairs |
| Missing/insecure/empty key file | “无 Key / key config”; no secret render; no request, web setup hint; never try another provider/key silently |
| Selected proxy absent/fails | Proxy error stays failure; no direct fallback or alternate unselected route. Explicit USB WiFi direct remains permitted by D1′ |
| Test timeout/stale result | Deadline cancels; no UI/safety-loop block; ignore late completion after edit/re-Apply/navigation |
| Board mic/ASR absent or fails | Visible unavailable/error; PTT reservation and pause gate retained; no fabricated task or remote enable |
| Agent dies while recording | TTL/client-loss revokes; stop recording; no automatic resume on restart |
| Panel write/OOM/render failure | Error counters and bounded retry (existing 200 ms backoff), fixed safety path still runs; never mark confirmation shown without presentation |
| HID unavailable/rebind | Existing hid.c recovery/parking policy; report degraded; never start a second HID writer; no claim UI can solve kernel lifetime issue |

## 7. T113 resource and timing budget

128 MB physical RAM is not an application budget: the agent plan observed only ~57 MB
available. Measure the combined kbd+agent peak on device; preserve OTA/kernel headroom.

| Allocation | Bound / accounting |
|---|---|
| One complete panel set | 20*128*128*2 + 428*142*2 = **776,912 B** |
| screen_t shadow + pending | **1,553,824 B** existing allocations |
| Two writer scratch buffers | 2*428*142*2 = **243,104 B** (current screen.c uses max panel for each bus) |
| Existing host canvas | 640*656*2 = **839,680 B**; reuse, no extra full local canvas |
| Local render scratch | <=121,552 B, one largest panel; draw local pages panel by panel |
| Embedded fonts/icons | <=256 KiB read-only: ASCII sizes + bounded CJK subset of every fixed label/toast, check/ellipsis/⟲⟳/PTT/e-stop icons; no runtime host fonts |
| State/draft/config/catalogue/queues | <=256 KiB; bounded strings and one pending latest update per panel, no unbounded frame queue |
| Host parser/JPEG transient | Audit current 2 MiB initial parser and 4 MiB HOST_MAX_PAYLOAD; cap operational host frames at1 MiB, reject oversize before decoding; stb RGB+565/scratch budget <=4 MiB transient |
| kbd whole-process target | <=8 MiB steady RSS, <=16 MiB peak including stacks/JPEG (measure non-sanitized static armhf); new UI incremental <=1 MiB steady |
| mk20-agent | Existing combined <=16 MiB peak goal, not sum of separate A2/A4 measurements; ASR adds <=1 MiB PCM for 30 s at16kHz mono16bit and bounded streaming/upload |

Subset font source/license and reproducible packing are shipped; runtime needs no
font files beyond shipped embedded assets. Unknown dynamic CJK uses an explicit
unsupported-character marker and bounded detail view, not invisible text; all fixed
page strings must pass glyph coverage. Draw ⟲ (U+27F2) and ⟳ (U+27F3) from explicit
bitmaps, never fallback font boxes. Rasterize a second small/large strike only where
needed; no FreeType/Qt/Python on T113.

SPI worst-case full redraw: bus0 9 keys+fb21 =416,464 B, bus1 11 keys =360,448 B.
At80 MHz the ideal slower-bus wire time is41.65 ms; driver/fsync overhead increases
it. Never claim 24 fps from this arithmetic. Cap normal full-page production at4 Hz
(~3.11 MB/s total), status-only at1 Hz, event-driven focus feedback <=8 Hz for changed
panels; inactive pages produce no panel traffic. Worst permitted 4 full refreshes/s
is ~167 ms/s ideal bus0 occupancy, then measured overhead. Agent API retains
10 writes/fb/s and30 global writes/s; compositor coalesces/rate-limits actual SPI work
within the same bus budget across all producers. Safety overlays preempt ordinary
redraws, never input handling. Font/render target <=5 ms/panel on T113, chunk/yield
work to <=2 ms slices between safety polls; display churn must not add >5 ms P95
key->HID latency. Metrics: render CPU, dirty/written panels, bus busy time, RSS/HWM,
input P95, stop latency; target kbd idle <2% of one core and interactive average <10%.
These are acceptance budgets, not measured claims. Miss -> reduce redraw/host
resolution/work, never postpone safety or allocate a full Retina frame.

## 8. Host tests and milestones

Proposed **`make -C deck/kbd ui-test`** builds C unit tests and a host-only headless
renderer, and runs deterministic JSON interaction replay plus config conformance.
Proposed **`make -C deck onboard-ui-test`** delegates to it and the relevant sim cases;
wire it into existing `deck/Makefile test`. Do not use existing broad `make test` as
an offline-only guarantee: it also runs TCP tests/package tooling. U1 target must
use only preinstalled dependencies, temp files, fake clock and injected callbacks;
no network sockets, real tty, adb, serial enumeration, `/dev/fb*` or `/dev/hidg*`.

Headless renderer writes each fb as P6 PPM and a composed 640×656 PPM from the same
C code used on board; optional PNG conversion is for review only. Golden files live
under proposed `kbd/tests/golden/ui/`, include fixed input state and glyph manifest.
Compare PPM header/dimensions and RGB pixels **byte-exact** after specified RGB565
expansion `r5*255/31, g6*255/63, b5*255/31` (integer floor). No platform fonts, time,
JPEG, antialiasing or lossy comparison in C goldens; fix clock/version strings in
fixtures. Golden updates require explicit regeneration/review, never test auto-update.
Cover all LS tabs, focus/selection/draft/network-confirm/discard, missing-key/test
errors, long labels, CJK and both rotation icons, home online/offline and L5
listening/transcribing/stopped/confirmation guard states as they land.

Sim keeps its JSON DSL (`press`, `down`, `up`, `knob`, `wait`, `raw`, `expect`),
settings cases and firmware/doc coordinate translation. Add an offline C-backend
adapter to `mk20sim/harness.py`; Python drives input/state but C renders the tested
pixels. Reuse `cases.py` runner; add explicit host-health/config-fault injection and
lease/pause/voice expectations rather than treating unknown DSL fields as success.
Existing Python L1/L4 behavior cases remain regression coverage. Proposed
`cases/onboard.json` covers home/LS without a host, GLM endpoints/model switch,
knobs/host editing, double Apply/discard, secret masking and fake test classes; later
cases cover flap hysteresis, poisoned cmd100/200, reserved draws/binds, PTT exception,
Mac PTT revocation, TTL during PTT, stop during ASR, takeover and source fallback.

C units: state transitions with fake monotonic int64 clock; all reserved coordinates;
confirmation 299/300 ms and deadline; press-time routing; lease-renew during pause;
no stale queued HID/VNC act after pause; bounded memory; glyph coverage/clipping;
writer short writes/ENOSPC/EINTR/fsync/rename/dir-fsync faults and crash points between
files; modes0700/0600, symlink rejection; no secret sentinels in output. Compare every
C provider/net serialization **byte-for-byte against the corrected agentcfg.py** for
every catalogue/model/profile, custom host, escaping and missing WiFi proxy. Also
load emitted provider files with the actual `llm_provider_load()` and net files with
`netcfg_load()`; injected socket counters prove invalid config/missing key/unselected-route
policy uses zero cloud dials. Existing GD32 allow-list tests remain mandatory.

| Milestone | Deliverable and concrete acceptance |
|---|---|
| **U1 — on-board renderer foundation, host only** | **Implemented 2026-10-19.** C99 HOME/LS state/rendering to caller RGB565 panels; fixed ASCII/CJK/icons and visible replacement; focus/selection/draft count, bounded host/port/preferences editors, network double Apply and discard confirmation, fake int64 knob clock, fb20 e-stop face/state only, injected fake Test. Shared catalogue + stale generator check (glm-coding endpoints/models), Python legacy API/positions retained, directory fsync; WiFi route semantics unchanged. C writer: configurable canonical root, 0700/0600, no-follow exclusive temps, full writes/fsync/rename/dir-fsync, process lock/commit marker, symlink validation and injected faults. Byte conformance for every preset/model/profile plus custom hosts; actual llm_provider_load/netcfg_load acceptance; 13 scenes / 286 exact P6 goldens (lossless gzip), explicit make ui-golden only. make ui-test / onboard-ui-test offline with ASan/UBSan; optional static armhf compile when toolchain exists. See kbd/UI.md for contracts/font sources/licenses and validation counts. **Originally deferred to U2, now completed:** C-backend simulator adapter and cases/onboard.json; see the U2 row and kbd/UI.md. Existing sim regressions retained. Main-loop/ownership and ctl safety are implemented by U2 below; full PTT and physical status remain U4/U7; consumer marker coordination, explicit D1′ mode selection, capability gating and real device Test are U3. No device integration or acceptance claimed. |
| **U2 — loop integration and ownership** | **Implemented host-only 2026-10-19.** Default replacement --run boots C HOME with internal safety/allowed=0; stock-KD/release selection unchanged. Route ids map to ctl 0/1/2/>=3 without renumbering. One compositor gates host JPEG/tile publication by route/epoch, preserves reserved fb20/fb16/footer, rejects agent grants on on-board pages, poisons old partial/complete receive work, and requires fresh full frames. Optional strict cmd101 hello/heartbeat/page negotiation, monotonic accepted generation, 3-s/5-s hysteresis and legacy frame liveness/getInfo re-entry; no attach navigation or cached recovery. Bounded nonblocking ack output, background JPEG decode with shared canvas, incremental/rate-capped panel rendering and echo-wait safety service; confirmation shown only after successful writer completion. Fake-fd/clock integration: 599 ASan/UBSan checks, same checks TSan-clean; 1,130 U1 checks / 286 unchanged goldens / 170 exact config files; 35 simulator tests (24 prior + nine C cases + two adapter checks); 2,324 ctl checks; 92 Python regressions. Requested full make test reaches 3,999 core checks: 3,985 pass / 14 environment failures, exactly reproduced from pristine HEAD; no full-suite pass claimed. See kbd/UI.md for commands, agent timeout retry, limits and counts. C-backed CLI/browser replay and onboard.json complete the U1 deferred adapter. Production durable Apply/Test remain visibly unavailable until U3; full pause/voice remains U4/U6. No hardware or network actions; **no device acceptance claimed**. |
| **U3 — device-side config/test service** | Shared commit lock/marker and consumer snapshot reload, root-bound write-only key/config web service, selected direct/proxy route enforcement in netcfg/net consumers, test worker using net.c/llm.c; injected offline tests prove per-error classes, 10 s deadline/cancel, zero unselected-route or proxy-to-direct fallback dials, coherent crash recovery, key redaction and no agent-loop block. Later authorized device test must use the selected profile; Mac absent with explicitly selected USB WiFi direct or proxy must be possible. |
| **U4 — L5, reserved PTT and e-stop** | Render allow/confirm/pause/state page; implement/document pause gate + events/errors as explicit CTL protocol extension; reserve fb16/fb20 and voice target now, ASR may still be unavailable. Fake-clock tests prove held PTT renews same lease while actions stop, ordinary/L1 PTT revokes, TTL/disable/stop wins, >=300 ms confirmation, cold-start K2 capability handling. No remote enable or key override. |
| **U5 — optional Mac fast paths** | USB-address-only authenticated screenshot/AX endpoints with permissions/errors/limits, per-display/window geometry; agent fast-path->VNC->UVC chooser integrated with feature/uvc dependency. Offline fakes prove ordering, demotion/promotion, stale-coordinate rejection, no-helper operation and no AirPlay. Mac runtime permissions/actual capture remain a later authorized acceptance step. |
| **U6 — board voice/ASR** | Board-mic worker, <=30 s PCM capture, configured proxied ASR <=15 s, transcript becomes task revision; no keys on keyboard/config; fake audio/ASR tests cover silence, failure, manual pause, stop/TTL/client loss during recording and no stale resume. Preserve L1 Right Option. Real mic capability awaits U7. |
| **U7 — on-device acceptance and release** | Only in a separately authorized device run: static armhf build/release checks, physical 21-panel/glyph/layout review; cold-boot Mac-app-absent home/LS/key web setup via ordinary USB host, USB WiFi+proxy test/task; connected/flapping Mac pages and screenshots/AX/VNC/UVC lock/crash fallback; real board mic/ASR, pause vs revoke, e-stop <=100 ms and first-hold K2 at1 s; measured RSS/CPU/SPI/input budgets; A/B probation/rollback, OTA and 24 h no-helper soak. Do not default-enable until these pass. |

U1/U2 are pulled forward from KD plan K5; K0–K5 and agent A0–A9 numbering stays
unchanged. U3 builds on A3/A4; U4 on A1/A5; U5 on A2 and feature/uvc; U6 on the
board audio/ASR capability; U7 joins A8/A9 and KD release acceptance. The task loop
(A5), web service and UVC capture are dependencies, not renderer-foundation scope.
