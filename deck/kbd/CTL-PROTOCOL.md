# mk20-kbd local control protocol (v1)

Opt in with `mk20-kbd --ctl /run/mk20/kbd.sock`. No socket, new HID report format,
or K2 safety behavior is enabled by default. mk20-kbd remains the sole HID writer.

## Transport

AF_UNIX stream, mode 0600. Linux authenticates peers using SO_PEERCRED: same effective
uid as mk20-kbd or uid 0; macOS host builds use getpeereid with the same rule.
At most two clients; there is no socket operation to enable agent access. Both clients
may query; leases and agent pages remain tied to the requesting connection.

One JSON object per line, including LF at most 1024 bytes (1023 bytes of JSON).
Objects have unique top-level keys and primitive values; no nested request objects.
Strings support JSON escapes and UTF-8; text fields reject control characters and
invalid UTF-8. Integer fields accept at most nine digits, without fractions or exponents.
Malformed/duplicate-key JSON returns `bad_request`; an overlong incoming line drops
the client. Split writes and multiple lines per write work. Requests need `op`;
`id` is an optional nonnegative integer echoed in replies (omitted id appears as -1 in
successful replies and is absent from errors). Notifications and replies can interleave.
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
lease and release keys/pointer buttons. E-stop performs synchronous all-up with a
50 ms write budget, latches stopped, and sets allowed=0. It cancels confirmation,
clears the screen grant/binds, and returns to the system page. Only a keyboard-side
explicit allow clears the latch; reconnecting or acquiring cannot clear it.
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
poll by both lease and confirmation deadlines.

Requester-only event: `{"ev":"confirm.result","cid":42,"ok":false,"reason":"timeout"}`.
Reasons: `confirmed` (ok=true), `cancel`, `timeout`, `estop`, `disabled`, `client_gone`.
Disconnect cancels the pending request; the departed peer cannot receive its result.
Status `confirm`: `pending`/`shown` booleans, `cid`, `deadline_ms` (monotonic), `ignored`
(cumulative count), `risk`. Summary is cached for the renderer, not duplicated in status.

`ui.agent`: required `state` in `idle|observing|planning|acting|awaiting_confirm|done|stopped|error`,
`input:"hid"|"vnc"`, `summary` (0..120 Unicode characters), integer `progress` 0..100.
Needs allowed (therefore also no latched e-stop), no lease. Errors `disabled`, `stopped`,
`bad_args`. Cached `ui` is returned by status; default idle/hid/empty/0. Changed values
set a dirty bit consumed/cleared by keyboard-only `ctl_ui_dirty(c)`. No rendering yet.

## Screens (D8 rule engine; A7 renders)

All `screen.*` require allowed and no e-stop, no input lease. Errors `disabled`, `stopped`.
Page ids: 0 system, 1 reserved agent control (system owner), 2 user; created pages have
monotonically increasing ids starting at 3, owner agent and a specific client connection.
Maximum eight agent pages. Disconnection retains pages, removes binds and client ownership,
and returns a displayed departed page to system; a reused client slot cannot inherit it.

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

The entire control page and the fixed e-stop key (row 3,col 4 / fb20) are reserved.
Draw/text/bind on reserved targets, or deleting the control page, returns `reserved`,
even with a grant. Pressing that key on a control/agent page invokes e-stop. A7 must
render its fixed visual. System/user writes/binds require keyboard-only
`ctl_screen_grant(c, ttl_ms, now)` (1..120000 ms); no socket grant operation. Grants expire
at the exact deadline and clear on disable/e-stop/restart. Agent pages survive disable/
e-stop but all binds are removed and current page becomes system. Builtin page show/
delete restrictions still apply during a grant.

Accepted draw and text share a sliding 1000 ms window: at most 10 writes per framebuffer
and 30 globally across clients/pages. Entries exactly 1000 ms old expire. Rejected writes
do not consume quota. Excess: `rate_limited`, no queue.

Keyboard-only `ctl_key_event(c,row,col,pressed,now)` emits
`{"ev":"key","page":3,"row":0,"col":0,"down":true}` only to the owner of the
currently shown agent page and only for bound keys. Both down and up are forwarded;
system/user/control pages and inactive-page bindings emit no key events.

## Flags, release switches, and keyboard-only work

`--ctl PATH`, development-only `--ctl-allow`, `--hid-scroll`, `--k2-estop` are opt-in.
Release config switches `FEATURE_AGENT_CTL`, `FEATURE_HID_SCROLL`, `FEATURE_K2_ESTOP`
default to 0; 0/1 environment overrides `MK20_AGENT_CTL`, `MK20_HID_SCROLL`, `MK20_K2_ESTOP`
apply after config. `FEATURE_KBD`/`MK20_KBD` still select the replacement daemon.
mk20d passes `--ctl /run/mk20/kbd.sock`, `--hid-scroll`, `--k2-estop` only when enabled;
release never passes `--ctl-allow`. The resolved FEATURE_HID_SCROLL is exported as
MK20_HID_SCROLL and persisted in gadget.env for manual gadget restarts, so descriptor
selection and daemon report format use the same release value. Default descriptor
bytes and launch arguments are unchanged. Reconfigure the gadget and daemon together
when changing the scroll switch.

K2 slot 103 only: once releases are known, held >=1000 ms triggers `k2_hold` from the
poll timer while still down. Starts in press-only mode until the first observed release;
three presses in a sliding <=1500 ms interval trigger `k2_triple` instead. Initial hold
on unmeasured firmware cannot be inferred safely from a single press. Rotation slots
104/105 never contribute. The pure state machine never writes to GD32.

Allow/disallow, confirm shown/key, screen grant and rendering are keyboard-side only.
A7 still supplies the allow/confirm UI and pixel renderer; there is no socket allow,
grant or confirm-result injection. No `hid.health` event is implemented yet; use status.
