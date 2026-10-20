# mk20-kbd local control protocol (v1)

Opt in with `mk20-kbd --run --ctl /run/mk20/kbd.sock`. The socket and scroll report format remain opt-in. U2 replacement `--run` boots
on-board HOME with allowed=0 and enables fb20/K2 safety locally by default. mk20-kbd remains the sole HID writer.
mk20d creates `/run/mk20`; for manual runs, create the socket directory first.

## Transport

AF_UNIX stream, mode 0600. Linux authenticates peers using SO_PEERCRED: same effective
uid as mk20-kbd or uid 0; macOS host builds use getpeereid with the same rule.
At most two clients; there is no socket operation to enable agent access. Both clients
have equal permissions after the uid check; leases and agent pages remain tied to the requesting connection.

One JSON object per line, including LF at most 1024 bytes (1023 bytes of JSON).
Objects have at most 24 unique top-level keys and primitive values; no nested request objects.
Strings support JSON escapes and UTF-8; text fields reject control characters and
invalid UTF-8. Integer fields accept at most nine digits, without fractions or exponents.
Malformed/duplicate-key JSON returns `bad_request`; an overlong incoming line drops
the client. Split writes and multiple lines per write work. Requests need `op`;
`id` is an optional nonnegative integer echoed in replies (omitted id appears as -1 in
successful replies and is absent from errors). A supplied negative or invalid `id`
returns `bad_request` without an id. Notifications and replies can interleave.
A non-reading client is dropped on any incomplete nonblocking send; no output queue.

Success: `{"ok":true,"id":7,...}`. Error: `{"ok":false,"id":7,"err":"bad_args"}`.
Events have `ev` instead of `id`. Unknown operations return `unknown_op`.
A third client receives `too_many_clients` and is closed.

## Status and input lease

| op | Fields / success |
| --- | --- |
| `hello`, `status` | No fields. `v:1`, `state`, `allowed`, `estopped`, `leased`, `scroll`, `hid_open`, `ttl_left_ms`, `confirm`, `ui`. Boolean-like legacy flags are 0/1. |
| `lease.acquire` | Optional `owner` (31 UTF-8 bytes; default `agent`), `ttl_ms` (default 3000, 200..10000). Returns `token`, `ttl_ms`. |
| `lease.renew` | `token`, optional `ttl_ms` with same limits. Returns `ttl_ms`. |
| `lease.release` | `token`. Revokes lease and releases keyboard/pointer buttons. |
| `act` | `token`, strictly increasing positive `seq`, `kind`, fields below. Returns accepted `seq`. |
| `estop` | No fields. Always accepted, even while disabled. Returns `state:"stopped"`. |

State is `stopped`, `disabled`, `leased`, or `idle`. Expired leases are processed before
requests. Acquisition requires allowed and no e-stop latch; one holder only. Renewal,
release and act require that connection's current token. Acquisition errors:
`stopped`, `disabled`, `busy`, `bad_ttl`, `bad_args` (owner). Token operations: `bad_token`, `stopped`,
`no_lease`; renewal also `bad_ttl`; act also `bad_seq`.

| act kind | Fields / errors |
| --- | --- |
| `key` | `usage` 4..101 or modifier 224..231, integer `down` (zero up, nonzero down). `bad_args`, `bad_usage`, `rollover`. |
| `ptr` | `buttons` 0..7, `x`,`y` 0..32767. `bad_args`, `out_of_range`. |
| `scroll` | At least one of `wheel`,`pan`, each -127..127, default 0. Requires scroll descriptor. `bad_args`, `out_of_range`, `unsupported`. |
| `allup` | No additional fields; releases all keyboard keys and pointer buttons. |

Missing/unknown kind: `bad_kind`; actions without a HID instance: `no_hid`.
HID transport is bounded/best effort; accepted input does not promise host application delivery.

Expiry, client loss, release, human physical key press, disable, and e-stop revoke the
lease, purge queued pointer/scroll actions, and release keys/pointer buttons at the
last delivered pointer position. A release is retained even if desired buttons were
already zero but reports were queued or last delivered buttons were down. E-stop performs synchronous all-up with a
50 ms write budget, latches stopped, and sets allowed=0. It cancels confirmation,
clears the screen grant/binds, and returns to the system page. Only a keyboard-side
explicit allow clears the latch; reconnecting or acquiring cannot clear it.
U2 replacement `--run` processes physical safety before host forwarding, including
without a control socket. Host writes never wait for ttyGS0: a bounded 16 KiB buffer
retains partial output, with space reserved for 64 empty cmd100 acks. Overflow drops
ordinary replies, counted as host_tx_errors; EAGAIN is counted as host_tx_timeouts.
Ack means handled, not displayed. The loop checks fresh safety deadlines between
host frames, after poll, before queued HID, and after each render slice. JPEG decode
runs in a worker and cannot block the control poll loop. Existing knob initialization
services safety while waiting for echoes, without changing any outbound GD32 bytes.
Every socket request retains its fresh pre/post-dispatch deadline checks, including
concatenated requests, multiple clients, and bounded all-up waits. The legacy
non-compositor C test path retains its 200 ms host timeout; it is not the shipped
`--run` default. See UI.md for host handshake, liveness and route-generation rules.
With writable HID, stop/preemption must release within 100 ms.
Events: `{"ev":"lease_revoked","reason":"expired"}` (holder only; other reasons:
`released`, `human`, `disabled`, `client_gone`, `estop`) and
`{"ev":"estop","reason":"socket"}` (broadcast; other sources `page_key`, `k2_hold`, `k2_triple`).

## Confirmation and agent UI

`confirm.request`: `cid` (nonnegative integer correlation id), `summary` (0..120 Unicode
characters), `risk:"low"|"high"`, optional `timeout_ms` (default 30000, 1000..120000).
Needs allowed and no e-stop; no lease. Returns ok immediately. One pending confirmation
across all clients; a second gets `busy`. Invalid fields: `bad_args`; gating:
`disabled`/`stopped`. Deadline starts at request acceptance, not at display time.

The renderer calls `ctl_confirm_shown(c, now)` when actually drawn; redraws do not reset
the guard. Keyboard-only `ctl_confirm_key(c, ok, now)` ignores/counts OK before display
or less than 300 ms after display; exactly 300 ms is accepted. Cancel rejects immediately.
At the deadline timeout wins. `ctl_tick` expires requests, and `ctl_timeout_ms` bounds
poll by both lease and confirmation deadlines. All monotonic millisecond timestamps
and deadlines use signed 64-bit storage, including on 32-bit armhf.

Requester-only event: `{"ev":"confirm.result","cid":42,"ok":false,"reason":"timeout"}`.
Reasons: `confirmed` (ok=true), `cancel`, `timeout`, `estop`, `disabled`, `client_gone`.
Disconnect cancels the pending request; the departed peer cannot receive its result.
Status `confirm`: `pending`/`shown` booleans, `cid`, `deadline_ms` (monotonic), `ignored`
(cumulative count), `risk`. Summary is cached for the renderer, not duplicated in status.

`ui.agent`: required `state` in `idle|observing|planning|acting|awaiting_confirm|done|stopped|error`,
`input:"hid"|"vnc"`, `summary` (0..120 Unicode characters), integer `progress` 0..100.
Needs allowed (therefore also no latched e-stop), no lease. Errors `disabled`, `stopped`,
`bad_args`. Cached `ui` is returned by status; default idle/hid/empty/0. Changed values
set a dirty bit consumed/cleared by keyboard-only `ctl_ui_dirty(c)`. U2 renders the
L5 scaffold; pause/voice protocol extensions remain U4/U6.

## Screens (D8 rule engine; A7 renders)

All `screen.*` require allowed and no e-stop, no input lease. Errors `disabled`, `stopped`.
Page ids: 0 system, 1 reserved agent control (system owner), 2 user; created pages have
monotonically increasing ids starting at 3, owner agent and a specific client connection.
Maximum eight agent pages. Disconnection frees all pages owned by that connection
and returns a displayed departed page to system; reconnecting can allocate the freed
slots but cannot inherit the departed page ids. Disable/e-stop keep pages as described below.

| op | Fields / success |
| --- | --- |
| `screen.layout` | `current_page`, `control_page:1`, `estop_key:{row:3,col:4}`, `screen_fields`, `screens`, `page_fields`, `pages`. |
| `screen.page.create` | `name` (1..32 Unicode characters). Returns `page_id`; full table returns `busy`. |
| `screen.page.delete` | `page_id`. Deletes own agent page; if shown, switches to system. Builtin system/user pages cannot be deleted. |
| `screen.page.show` | `page_id`. Only own agent pages or the reserved control page. |
| `screen.draw` | `page_id`, `fb`, `x`,`y`,`w`,`h`, `fmt:"rgb565"|"jpeg"`, `bytes` (1..65536). Returns `rendered:false`. |
| `screen.text` | `page_id`, `fb`, `text` (0..120 Unicode characters), `size` (1..128), `fg`,`bg` (RGB565 integers 0..65535). Returns `rendered:false`. |
| `screen.bind` | `page_id`, zero-based `row` 0..3, `col` 0..4, `action` (1..120 Unicode characters). Caches action; no execution. |

Compact layout arrays keep replies inside 1024 bytes: each `screens` row follows
`screen_fields:["fb","w","h","bus","row","col"]`; each `pages` row follows
`page_fields:["page_id","owner"]`. fb1..20 are 128x128 keys, row-major 4x5;
fb21 is 428x142, row/col null. Bus 0: fb1..9 and fb21. Bus 1: fb10..20.

Draw rectangles must fit the framebuffer, with nonnegative origin and positive dimensions.
RGB565 `bytes` must equal w*h*2; JPEG declares its encoded size. There is no pixel data
in this JSON skeleton. A later binary framing will carry pixel data: CTL_LINE_MAX=1024
cannot carry a 64 KiB image. Text/draw validation and rate accounting succeed without
rendering or queueing. Invalid fields/geometry/fmt/size return `bad_args`, missing page
`not_found`, other-client pages `forbidden`.

The entire system/control pages (0/1), fixed e-stop face/bind (row3,col4 / fb20)
on all pages, and PTT face/bind (row3,col0 / fb16) on agent pages are reserved.
Draw/text/bind on reserved targets returns `reserved`, even with a temporary grant.
fb21 draw rectangles intersecting y112..141 are reserved. fb21 text implicitly
addresses only the y0..111 body viewport; future rendering must clip there. Permitted
text/draw validation currently returns rendered:false without pixel mutation. Pressing fb20 invokes e-stop on every page while the keyboard agent is allowed or holds a lease (always on HOME/LS/L5/agent pages); with the agent disabled, fb20 on the Mac host pages L1/L4 is forwarded to the host as its mode key (2026-10-20). K2 hold/triple always stops. System/control visuals and the
footer are composed locally; agent `screen.draw`/`screen.text` still return
`rendered:false` and never supply safety visuals.

User-page (2) writes/binds require keyboard-only `ctl_screen_grant(c, ttl_ms, now)`
(1..120000 ms); no socket grant operation. Grants cannot override HOME/LS/L5 or the
reserved faces/footer. Grants expire at the exact deadline and clear on disable/
e-stop/restart. Agent pages survive disable/e-stop but all binds are removed and
current page becomes system. Builtin show/delete restrictions still apply during a
grant. `screen.layout` numbering/array fields are unchanged: UI routes are distinct
from ctl page ids (HOME/LS/offline=0, L5=1, host L1/L4=2, agent>=3).

Accepted draw and text share a sliding 1000 ms window: at most 10 writes per framebuffer
and 30 globally across clients/pages. Entries exactly 1000 ms old expire. Rejected writes
do not consume quota. Excess: `rate_limited`, no queue.

Keyboard-only `ctl_key_event(c,row,col,pressed,now)` emits
`{"ev":"key","page":3,"row":0,"col":0,"down":true}` only to the owner of the
currently shown agent page and only for bound keys. U2 records the down's page and
connection generation. Leaving that page (including agent page 3 -> 4) completes
outstanding presses with up events to their original owner; the later physical up
is consumed. A disconnected/replaced connection never inherits an old release.
System/user/control pages and inactive-page bindings emit no new downs.
Leaving/re-entering L5 also resets confirmation presentation eligibility and its
300 ms guard; the new presentation must finish successfully before Confirm works.

## Flags, release switches, and keyboard-only work

`--ctl PATH`, development-only `--ctl-allow`, `--hid-scroll`, and `--k2-release` are opt-in.
U2 makes local safety intrinsic to replacement --run; `--k2-estop` remains accepted
for compatibility. This does not change FEATURE_KBD selection or stock-KD coexistence.
Release config switches `FEATURE_AGENT_CTL`, `FEATURE_HID_SCROLL`, `FEATURE_K2_ESTOP`, `FEATURE_K2_RELEASE`
default to 0; 0/1 environment overrides `MK20_AGENT_CTL`, `MK20_HID_SCROLL`, `MK20_K2_ESTOP`, `MK20_K2_RELEASE`
apply after config. `FEATURE_KBD`/`MK20_KBD` still select the replacement daemon.
mk20d passes `--ctl /run/mk20/kbd.sock`, `--hid-scroll`, `--k2-estop`, `--k2-release` only when enabled;
release never passes `--ctl-allow`. The resolved FEATURE_HID_SCROLL is exported as
MK20_HID_SCROLL and persisted in gadget.env for manual gadget restarts, so descriptor
selection and daemon report format use the same release value. Boot passes all four
resolved switches into the fresh supervisor shell as MK20_* values; restarts retain
these values even if mk20.conf changes after boot. Default descriptor
bytes and launch arguments are unchanged. Reconfigure the gadget and daemon together
when changing the scroll switch.

K2 slot 103 only in replacement --run: `--k2-release` means firmware is known to report
releases, so the first held press >=1000 ms triggers `k2_hold` from the poll timer
while still down. Without `--k2-release`, start in press-only mode until the first
observed release; three presses in a sliding <=1500 ms interval trigger `k2_triple`
instead. Initial hold on unmeasured firmware cannot be inferred from a single press.
The release-capability flag selects hold detection; K2 safety is intrinsic to U2. Rotation slots
104/105 never contribute. The pure state machine never writes to GD32.

Allow/disallow, confirm shown/key, screen grant and rendering are keyboard-side only.
U2 supplies physical allow/confirm presentation; U4 adds full pause/voice behavior; there is no socket allow,
grant or confirm-result injection. No `hid.health` event is implemented yet; use status.
