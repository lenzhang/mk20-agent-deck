# On-board UI

`fbN` in this document is the **logical** panel: keys fb1..fb20 row-major in natural orientation
(fb1 top-left, fb5 top-right, fb16 bottom-left, fb20 bottom-right = e-stop), fb21 the sub-screen; the
same numbering as GD32 key events, the host canvas and ctl. It is not the `/dev/fbN` node: the key LCDs
are wired column-major from the top-right (logical fb1 is /dev/fb17, fb20 is /dev/fb4); `kbd/panels.c`
holds the only map (1.4.3-rc4; before that every page was displayed permuted, see kbd/K2-RESULTS.md).

## L1/L4 bottom row: Mac keyboard keys (user decision 2026-10-21; unreleased 1.5.0)

New L1 layout: rows 1–3 (fb1–fb15) are 15 session slots rendered by the Mac (host canvas, no device
change); L4 keeps the same bottom row. For exactly the keys below this replaces the PRD rule "the
non-PTT keys never produce HID". The only fb -> usage map is `HOST_HID_KEYS` in `kbd/onboard.c`.

| Key | GD32 col,row (0-based) | Label | Live L1/L4 (USB HID on /dev/hidg0) | Elsewhere (unchanged) |
|---|---|---|---|---|
| fb16 | 0,3 | PTT | Right Option (modifier 0x40) while held; `--ptt COL,ROW` moves it | L5/agent: board PTT; placeholder: inert |
| fb17 | 1,3 | DELETE | Backspace (usage 0x2A) while held | HOME task state, LS 会话安排 (`ui.openArrange`, 1.6.0), L5 read-only, agent-bindable; placeholder blank |
| fb18 | 2,3 | SEND / OK | Return (0x28) while held | HOME ✓, LS 主页, L1/L4 placeholder 主页 |
| fb19 | 3,3 | NO | Escape (0x29; also interrupts Claude Code / codex) while held | HOME/LS 网页设置 (`ui.openSettings`), placeholder URL |
| fb20 | 4,3 | MODE | Mac mode key (forwarded) unless the keyboard agent is active, then e-stop | e-stop |

- **Gate = the Mac PTT's:** host route L1/L4, host ONLINE with a fresh full frame (hence never in
  the page-switch grace window), not PROBING/SUSPECT/offline, not during the post-e-stop pause,
  GD32 keycode 0. The physical press first takes input back from an agent lease (`ctl_human_input`:
  revoke + all-up), then the usage goes down; its release sends the up. These edges are consumed and
  never forwarded as `keyStateChanged` (with `--no-hid` nothing is sent at all). A `--ptt` position
  wins over the table (that key becomes the PTT; fb16 then is an ordinary host key).
- **Hold:** the key stays down, macOS auto-repeats; mk20-kbd never sends synthetic repeats. A repeated
  GD32 down of a held key is ignored. Press-time ownership (`key_owner` 5 + the recorded usage): the
  up always releases exactly what that press put down. Reports are `hid.c`'s boot keyboard report
  (`hid_key`: modifier byte + 6 key slots): physical keys contribute at most RAlt + these three, and
  releasing one of them (or the PTT) keeps the others down.
- **Releases (never a key stuck down on the Mac):** any page boundary (K2 toggle, host `ui.page` to the other
  page, K1 hold, ctl page change, `ui.hello`, host loss/EOF) sends each up (1.6.0: a same-page `ui.page` only
  redraws and keeps held keys, see "1.6.0 host protocol additions"); SUSPECT and every e-stop
  (fb20/K2/socket: ctl all-up first, nothing re-pressed during the pause) release them; on a hidg0
  node replacement the new node gets all-up then the still-held state (as for a held RAlt), a release
  during the gap leaves only all-up; gadget teardown (stop file/SIGUSR1) and process exit send all-up.
- **Lost key-up guard:** GD32 key reports are edge-triggered with no state resync, so a hold longer than
  10 s (`HID_KEY_HOLD_MS`) is cut: the up is sent, the late physical up does nothing, press again to
  continue. The PTT (long dictation) is not cut at 10 s; since 1.6.0 it ends after 120 s (`HID_PTT_HOLD_MS`,
  same lost-up reasoning: the GD32 cannot be asked for the key state, it has one allowed outbound frame), press
  again to continue; a repeated GD32 down of the held PTT confirms it pressed and restarts the 120 s, but never
  past 10 min after its original press (`HID_PTT_MAX_MS`, rc3). After any of these lost key-up guard cuts the key
  is inert until the GD32 reports its up (`cut_latch`, rc3): a GD32 emitting phantom downs cannot press it again
  right after the cut. Residual: such a storm holds a key at most for one bound (PTT 10 min, ⌫/⏎/Esc/digit 10 s,
  a kept key 60 s) and then nothing until an up arrives; a real user, who releases before pressing again, sees no
  difference (after a lost GD32 up the next press is ignored once, its release re-arms the key).
- Safe mode and the legacy non-onboard loop keep PTT-only HID.

## L1 option mode: host `ui.hidKeys` (user decision 2026-10-22; unreleased, for 1.5.1)

When the user taps a needs_input / needs_approval session, mk20deck shows that agent's numbered choice on row 1
(fb1..fbN, N <= 5; rows 2–3 dark; row 4 unchanged) and asks the keyboard to make those keys type the digit on the
Mac. The tap already brought that session's window to the front, and Claude Code / codex accept number keys in
their option menus.

- **Request** (cmd101): `{"method":"ui.hidKeys","generation":G,"keys":{"1":"1","2":"2","3":"3"}}`. The only
  host method with a nested value: `keys` is one flat object under the same strict parser (unique keys, primitive
  values, <= 24 keys, <= 1023 bytes, nothing nested deeper). Keys are logical fb `"1"`..`"15"` (exact decimal),
  values exactly one of `"1"`..`"9"` (HID usages 0x1E..0x26), at most 9 entries, no digit twice; `{}` clears.
  G is 1..999999999 and strictly greater than the last accepted ui.hidKeys generation (own counter, independent
  of `ui.page`; since 1.6.0 every `ui.hello` starts it at 0 again, before: kept until process restart); a refused
  request consumes nothing.
- **Accepted** (non-empty) only while negotiated (`ui.hello`), ONLINE, on L1 and not in the post-e-stop pause;
  `{}` whenever negotiated and no handshake is pending. A request still queued in the tty at a route boundary is
  refused as stale, like `ui.page`. Reply `{"ack_method":"ui.hidKeys","success":true,"result":{"generation":G,
  "mapped":N}}`, otherwise `success:false` ("unavailable or invalid UI request" / "stale UI request"; firmware
  without the method answers "unsupported"). `ui.hello` now returns `"capabilities":["ui.page","ui.heartbeat",
  "ui.hidKeys"]`, `"generation"` (ui.page) and `"hid_generation"` (last accepted ui.hidKeys); the host continues
  above both.
- **Cleared** on every page boundary (leaving L1: K2, HOME, K1 hold, ctl page; `ui.page` even to L1 (1.6.0: that
  one keeps held keys); `ui.hello`;
  host loss/EOF), on SUSPECT, on every e-stop (fb20/K2/socket), and 30 s (`HID_MAP_MS`) after the last accepted
  request (mk20deck refreshes it every 10 s while the choice is shown). `onboard_hid_keys()` reports the count.
- **Mapped digit** (only on the live L1 page: ONLINE + fresh frame, never grace/PROBING/SUSPECT/offline/e-stop
  pause, keycode 0; a `--ptt` position wins): the fb17–19 gate of `HOST_HID_KEYS` (lease preemption via
  `ctl_human_input` first), but **momentary** (integrator decision 2026-10-22): the press writes the digit down
  and immediately up (two boot reports), so macOS can never auto-repeat it; the physical release sends no HID.
  Ownership is still tracked from the press (`key_owner` 7, pressed at `hid_at`): a repeated GD32 down is the same
  press (no second digit), the 10 s guard ends a press whose up was lost, every boundary/SUSPECT/e-stop releases
  it, so nothing can stay stuck. A held fb17–19 keeps its digit-free slot: the digit is typed around it.
- **Mac PTT held** (Right Option, `key_owner` 2): a mapped digit is ignored — nothing is typed (Option + digit
  would be a symbol such as "¡") — and its press is forwarded as `{"method":"keyStateChanged","parameters":
  {"col":C,"ignored":"ptt","pressed":true,"row":R}}` so mk20deck says "先松开按住说话"; its release is an ordinary
  up. fb17–19 with the PTT are unchanged (combined with RAlt). Unmapped fb1..fb15 stay ordinary host keys.
- **Also forwarded** (unlike fb17–19 normally): a typed digit's press as `{"method":"keyStateChanged","parameters":
  {"col":C,"hid":U,"pressed":true,"row":R}}` with U = the usage the device typed, the release as an ordinary up
  (also after the 10 s guard). While a map is active fb17–19 are forwarded the same way (U = 0x2A/0x28/0x29; they
  stay held keys, `key_owner` 6), so the Mac sees Escape/Return at once. Safe because only a host that itself
  requested the map in the last 30 s receives them, and the mark says what the keyboard did: mk20deck maps a
  marked press to an option role only (toast "已选 k", leave option mode on Escape, "先松开按住说话") and never to a
  slot select/focus/pick-up or any other Mac action, even when its option mode ended a moment earlier. The HID
  path never waits for or depends on the host.

## 1.6.0 host protocol additions (integrator brief 2026-10-22; MINOR bump)

Both directions stay compatible: an older mk20deck ignores the new hello field and the new event; a newer mk20deck
falls back to its 1.5.1 behaviour when the hello carries no `page`. Tests: `kbd/tests/tests_onboard_r7.inc`,
`kbd/tests/e2e_run.py`, `tests/test_page_sync.py` / `test_page_sync_e2e.py` (real binary), `tests/test_ui_open_arrange.py`.

- **Same-page `ui.page` = redraw only.** A `ui.page` naming the route the keyboard already shows clears the
  option-mode map and invalidates content exactly like a boundary (`invalidate()` in `onboard.c`: staged tile,
  restore, queued decode, `epoch++`, `fresh`/`publishing` = 0 so a fresh full frame is required, the receive
  barrier that makes queued frames / `ui.page` / `ui.hidKeys` stale, grace window, full redraw, `host_page`), but
  it does **not** release key owners: a held PTT (RAlt), ⌫ / ⏎ / Esc, an option digit (owner 7: already typed, only
  its host up is due), a forwarded fb17–19 (owner 6), a host key or the Mac mode key (owner 1) and L4 回 L1
  (owner 4) end on their physical release as before. A real route change keeps the full `boundary()`.
  Bounds (rc2, Kimi K3 review): owners ≥ 5 keep their 10 s cut from the press; the PTT keeps its 120 s cap (every
  PTT hold has it); a kept host key / L4 回 L1 press (no bound of its own) ends 10 s after the first same-page
  request (`keep_until`: host up / the pending 回 L1 is cancelled, never run late); a new GD32 down of that key
  confirms it pressed and renews the 10 s, never past 60 s after the first request (`KEEP_MAX_MS`, rc3) — a
  request used to release them at once, and a lost GD32 up (or a phantom-down storm) must not keep them for ever.
  Every path that ends an owner (release, boundary, e-stop, loss, a transport flush without a host) also ends
  that bound (rc4); the cut latch is only ended by the key's up (a physical fact). rc4: a repeated GD32 down of a
  held host key (owner 1) is the same press on every host page — not forwarded again (it used to send another
  `"pressed":true`, unbounded under a phantom storm); its next up releases. A genuine press is one down + one up,
  so the Mac's long-press gestures (600 ms pick-up) are unchanged.
- **Residual, accepted for 1.6.0 (pre-existing; Kimi K3 rc4 review): a held host key with a lost GD32 up.** A host
  key (owner 1: a session / mode key, the Mac mode key fb20, or the host half of a forwarded ⌫/⏎/Esc or digit)
  whose GD32 up is lost, and that no same-page request kept (which would bound it at 10 s / 60 s), has no time
  bound on the keyboard. What the Mac sees: the key stays pressed — its controller press stays open, so the
  press's own long-press gesture fires once (a slot is picked up, "移动中", which the Mac drops after 8 s without
  input; a held MODE goes to L4 once) and nothing more happens; no HID is involved, so nothing is typed or
  auto-repeated on the Mac. What ends it: the key's next physical release (the press in between is swallowed as
  the same press since rc4, its up sends the host up), any real page boundary (K2, `ui.page` to the other page,
  K1 hold, ctl page, `ui.hello`), host loss (including SUSPECT turning into a loss after 5 s), an e-stop (its
  5 s pause resync releases it, rc3), process exit. Why no cap in 1.6.0: the key carries no HID, the Mac's
  gestures are bounded on its side, and how long a host key may legitimately be held is the Mac's decision (a
  cut would end real holds the Mac interprets). Proposal for 1.6.1: a generous owner-1 cap (e.g. 60 s from the
  press, ended with the host up and `cut_latch` like the other guards) once mk20deck confirms no gesture needs
  longer holds. Log-only nit, same review: when a forwarded key (owner 6) or digit (owner 7) is turned into its
  host half (owner 1) by an e-stop / SUSPECT, `press_at` keeps the original press, so a later same-page bound's
  log line names the time since that press (not since the conversion).
  Why it is safe: (1) nothing new is pressed — the page is not live until the fresh frame and the grace window
  swallows new presses, so no input can act on stale labels; (2) every other release path still applies to the
  kept owners: the 10 s lost-up cut (owners ≥ 5, `hid_at` unchanged), the 120 s PTT cap, the 10 s `keep_until`
  bound of a kept host key / L4 回 L1, SUSPECT (`release_mac`), every e-stop
  (`release_mac`, then the post-e-stop pause, which a same-page request neither starts nor ends), host loss /
  `ui.hello` / any real boundary, hidg0 replacement and exit; (3) a repeated GD32 down of a key still owned while
  the page waits for its frame is the same press (`onboard_input`: owner kept, nothing sent) — without this a
  re-sent down would reset the owner and leak RAlt or lose the host's up; (4) the map is cleared, so no digit
  mapping outlives the request (the Mac re-sends it after the accepted page); (5) `tile_generation` also changes
  with the accepted generation, so a tile staged before the request is dropped.
- **`ui.hello` names the page.** The reply gains `"page"`: `"L1"` / `"L4"` when the keyboard's route is a host
  page, `"none"` on HOME / LS / L5 / agent pages (`ui.page` is refused there). Existing fields are unchanged
  (`version`, `heartbeat_ms`, `capabilities`, `generation`, `hid_generation`; `page` comes before the
  generations). The `ui.page` reply already names the accepted page. The K2 tap that tells the host "my host page
  flipped" stays the only relative signal, so the reported page must be where the host's tap mirror starts: a route
  changed while no host was ONLINE (`host_page` != route, e.g. K2 while the Mac slept) is announced with the usual
  K2 tap **before** the hello reply (was: at ONLINE), and `host_page` = route. Route changes after the hello (while
  PROBING) are still tapped at ONLINE. An older host just sees that tap about 2 s earlier, with the same effect.
  mk20deck (rc2) applies a K2 tap newer than its known page belief absolutely — its page becomes the keyboard's
  (the other page than the belief) — so a K2 on the keyboard while PROBING is followed, never pulled back.
- **A fresh `ui.hello` resets both generations.** `generation` (ui.page) and `hid_generation` (ui.hidKeys) are 0
  after every hello and reported as before, so a used-up 999999999 recovers with the next hello. Within a host
  session (hello to hello) both still strictly increase; a host loss alone does not reset them (it requires a new
  hello anyway). Why it is safe: generations only order requests within one session; nothing sent before a hello
  can be accepted after it, independent of its number — earlier bytes on the ordered stream are handled before
  the hello, bytes already received or queued behind it are stale (the hello's loss boundary sets the parser
  barrier and `host_rx_barrier` = FIONREAD, and stale `ui.page` / `ui.hidKeys` are refused, see
  `v7_generation_reset`), and `ui.page` / a non-empty map need ONLINE, i.e. ≥ 3 heartbeats spanning ≥ 2 s after the
  hello. What the reset newly admits is only a request written after the hello with a number ≤ the old counter —
  i.e. the same host continuing from the values the hello reported. mk20deck holds one lock across hello and every
  generation-checked request (allocate, write, ack), so none of its requests numbered from the old counter can
  follow a hello, drops (inside that lock) a request decided under an older hello epoch instead of renumbering it
  (rc2), and takes the hello's values as they are.
- **LS fb17 会话安排 → `{"method":"ui.openArrange"}`.** Modelled on `ui.openSettings`: exactly this payload,
  frame id 0xFFFFFFFF, sent only while ONLINE, at most once per 3 s (own limiter, counted per attempt), toast
  "已请求 Mac 打开会话安排" or, not ONLINE, "Mac 端未运行：无法打开会话安排"; LS stays (no navigation). No token or
  URL crosses the cable: mk20deck mints a one-time link file through its in-process `ArrangeServer` and opens
  that path. On a live L1/L4 fb17 stays the Mac's Backspace; HOME fb17 is unchanged (task state).

## On-device UX revision (2026-10-20, branch feature/fw-ux; supersedes U2 text below where they differ)

Host-validated only (Docker, fake clock/files); **no device acceptance claimed**.

- **HOME status is real, not "unknown".** `devstat.c` probes on **one low-priority worker
  thread** (every 2 s, the pairing code every 1 s, all at once on HOME/LS entry). The
  poll/safety loop never touches the filesystem: it copies the latest immutable snapshot
  from a mutex mailbox with `pthread_mutex_trylock` (never waits; a busy lock just means
  "next loop"). A stuck read only makes the snapshot stale: after 10 s without a completed
  cycle the last values are shown dimmed with "读取超时", the sub-screen says so and the
  pairing code is withdrawn (its expiry cannot be verified). Shutdown waits at most ~1 s for
  the worker; a worker stuck in a read is detached and frees itself when the read returns.
  `MK20_KBD_PROBE_DELAY_MS` (test-only, <=60000) sleeps before every probe. Probes: `MK20_VERSION`
  (mk20d) or `version=` in `$MK20_REL/MANIFEST` (fb4); `MK20_ECM_IP` in
  `/tmp/mk20/gadget.env`, default 172.30.250.2 marked "默认地址" (fb11, fb19, URLs);
  UDC state via configfs `g1/UDC` + `/sys/class/udc/<udc>/state` (fb1);
  `/sys/class/net/wlan0` existence only (fb12 "无网卡"/"未启用"; no WiFi stack is claimed);
  `/data/mk20/agent/{net,provider}.json` (fb9/fb10, default marked "默认") and
  `.apply-in-progress` (fb13 默认配置/已配置/配置无效/应用中); and **only lstat** of
  `/data/mk20/secrets/<selected provider>.key` (fb14: regular, not a symlink, 0600/0400 as
  accepted by `net_read_key()`, our uid, 1..4096 bytes, under a 0700 non-symlink directory).
  Key content is never opened, read or logged. Reads are `O_NOFOLLOW|O_NONBLOCK`, regular
  files only, size-capped (16 KiB config, 64 B pairing, 1 KiB gadget.env), with a strict
  bounded JSON walk (duplicate keys, escapes in captured ids and unknown catalogue ids are
  invalid). `MK20_ROOT` prefixes every probe path (tests); `ui.c` remains I/O-free and
  receives a `ui_status_t` snapshot.
- fb3 智能体 shows 未启用/已禁用/已允许/急停中 from ctl; fb17 the ctl `ui.state` (无任务 when
  idle/disabled); fb2 代理状态 "未测试" (there is no device probe); fb16 语音目标 "Mac 输入法 /
  会话页按住" (on L1/L4 the PTT key sends Right Option; agent ASR is unavailable).
  fb1 distinguishes Mac 已连接 (ONLINE) / Mac 未运行 (USB configured, no host app) /
  USB 未连接 / 正在连接 / Mac 无响应. Values that do not exist show a dim "—".
- Navigation tiles (fb5 设置, fb6 智能体页, fb7 Agent 会话, fb8 模式切换, fb15 设备信息) have
  no value line, only a blue chevron; fb18 主页 shows ✓. Positions are unchanged. Normal
  "not configured" states use plain colours; amber is reserved for invalid config,
  insecure keys, e-stop and confirmation badges. The `NONE` toast sentinel is never drawn.
- Sub-screen: title, one status line, one instruction line (e.g. "Mac 端未运行：在 Mac 上启动
  mk20deck", "Mac 已就绪：按「Agent 会话」进入"), the settings URL, and a two-line device
  footer (knob hint + one Chinese voice line).
- **LS is read-only.** The usb0 admin web `http://<usb0 IP>:8780` is the only place to
  change settings. LS tabs 网络/模型/密钥/连接测试/设备信息 show the same status data; keys and
  knobs only choose what to look at (fb16 返回, fb18 主页, fb19 网页设置, K1 ⟲⟳ tab,
  K2 ⟲⟳ item). There is no draft, Apply, Undo, editor or on-key Test. `uicfg.c`
  (serializer/validator/writer) stays for its conformance tests but is unreachable from keys.
- **fb19 网页设置** shows the URL (large on the sub-screen). On HOME and LS a press sends,
  only while the host is ONLINE and at most once per 3 s (counted from each attempt, also
  when the send fails), exactly one device-initiated
  cmd101 event `{"method":"ui.openSettings"}` (frame id 0xFFFFFFFF like keyStateChanged,
  no URL or payload: the Mac builds the URL itself) and toasts "已在 Mac 上打开设置网页";
  otherwise nothing is sent and the toast says "Mac 端未运行：在 Mac 浏览器打开" + URL.
- **Boot -> L1 once.** If the first ONLINE after process start (ui.hello + heartbeats, or a
  legacy host's full frames, decoded on the boot HOME only for this purpose) happens on
  HOME and no key/knob event has arrived since boot, the route changes to L1 once. Any
  physical input, any later loss/e-stop/reconnect, or ONLINE on another page consumes it.
  Legacy hosts (no ui.hello) qualify only with the normal legacy liveness rule: three
  decoded full frames, consecutive ones 1.0..1.5 s apart, spanning >=2 s. An idle legacy
  host that sends one frame every 2 s (mk20deck `heartbeat_s` default 2.0) never becomes
  ONLINE, so for it the auto-L1 needs ui.hello/ui.heartbeat (tested with both).
- **fb20.** On L1/L4 with the keyboard agent inactive (not allowed, no lease), fb20 is the
  Mac's mode key: its face is host content and presses are forwarded like any host key
  (inert while the host page is not live). With the agent allowed or leased, and on
  HOME/LS/L5/agent pages always, fb20 is the e-stop. K2 hold (or the press-only triple
  fallback) is always the e-stop. While the host page is not live (SUSPECT or offline
  placeholder) and the agent is inactive, fb20 is inert and its face reads "K2 长按急停".
  An e-stop on L1/L4 stays on the page: ctl revokes the lease and sends all-up, a held Mac
  PTT is released (a still-held fb16 cannot re-press it), the ctl page stays 2.
- **Host forwarding pause after an e-stop on L1/L4** (fb20, K2 hold/triple or socket): no
  key or knob event reaches the host, and nothing navigates (K1/K2 short and K1 hold
  included), until every key/knob is up **and** 1 s passed without input. Presses in that
  time are swallowed with their matching ups; presses made before the stop keep their
  press-time owner (the host still gets those ups), also through a repeated GD32 down in the
  pause (1.6.0 rc2: before, that down cleared the owner and the host up was lost — also in
  1.5.1). An L4 回 L1 press made before the stop is cancelled by it and its release is inert in
  the pause (before rc2 it navigated and sent the K2 tap). The 5 s resync (no input for 5 s) also
  releases the press-time owners still held (rc3; pre-existing in 1.5.1): after the stop only
  owner 1 is left (host keys, and the host halves of forwarded keys / digits — the stop released
  the PTT and ⌫/⏎/Esc and cancelled 回 L1), so a host key whose GD32 up was lost gets its host up
  instead of staying pressed on the Mac. fb20 is the e-stop meanwhile (face
  and action). K2 counts as held only once its release capability is known.
- **fb20 held while the agent becomes active.** If `allowed`/a lease appears while fb20 is
  down and owned by the host, the host first gets fb20's up, then the keyboard e-stops
  (reason `fb20_held`, fail safe) and fb20 is redrawn as the e-stop face.
- **E-stop indication.** If the e-stop found the agent allowed or leased (ctl `stop_agent`),
  "智能体已急停 · 到智能体页重新允许" stays in the footer of every page (a live host page
  keeps the device footer for it) until the user re-allows on L5 (fb1) or acknowledges on L5
  (fb11 "知道了" while no confirmation is pending); a new stop of an active agent re-arms it.
  Otherwise L1/L4 show the 10 s stop banner. HOME fb3 keeps "急停中" while latched.
- **Grace window.** After a host-page switch while ONLINE, until a fresh full frame or 2 s,
  every key and K1/K2 short press is swallowed (no navigation, no host event); the
  placeholder hides its navigation labels. Chained switches (e.g. a host `ui.page` during
  the window) never extend it beyond 2 s from the first switch. Offline placeholders keep
  their labelled navigation keys; K1 hold (HOME) stays available.
- **No auto-L5.** A confirmation request never changes the page or releases a held Mac
  PTT. HOME fb6 shows "待确认", HOME/LS show "智能体请求确认 → 进入智能体页", host/agent
  pages get a footer line; the user enters L5. Deadlines/guards are unchanged.
- **Host footer.** On a live L1/L4 the host's full 142-px sub-screen is published (and
  host tiles may touch rows 112..141). The device footer replaces rows 112..141 only for
  attention: pairing code, stop banner, confirmation, SUSPECT, held PTT or GD32 mapping
  warning; afterwards the host rows are restored from the last fresh canvas.
- **Pairing code.** The web process writes `/run/mk20/pair-code` (root, 0600, atomic,
  <=64 bytes, `code=NNNNNN\nexpires_uptime=<s>\n`). mk20-kbd polls it every <=1 s and
  shows "网页配对码 NNNNNN" in the device footer on every page while `expires_uptime` is
  in the future (and at most 600 s ahead of `/proc/uptime`); symlinks, other modes/owners,
  oversize or malformed text are ignored. `MK20_KBD_PAIR_CODE` overrides the path.
- **Safe mode** (static rescue panels) also starts the status worker: the admin line shows
  the usb0 IP from `gadget.env` (fallback 172.30.250.2) and, while valid, "配对码 NNNNNN"
  (fb21: "网页配对码 NNNNNN"), so the web page (including its "exit safe mode") can be paired.
  Panels are redrawn from the snapshot only when it changes; no filesystem I/O on the loop.

Validation (Docker `mk20-build:trixie`, gcc 14): see the report in RELEASE.md "Unreleased".

# U2 host integration (historical; see the revision above)

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
and K1 hold returns HOME (2026-10-20: LS is read-only, no drafts). LS Back
returns to its prior route. Knob short actions happen only on release below 1 s.
K2 long/triple stops, and its release cannot perform a short action. Nonzero GD32
keycodes display an incompatible-mapping warning and never synthesize duplicate HID. E-stop is
available on every route, independent of a Mac or listening control socket (2026-10-20:
via K2 everywhere; via fb20 except on L1/L4 while the keyboard agent is inactive).

The compositor preserves fb20 on every page, fb16 on L5/agent pages, and fb21
rows112..141 (2026-10-20: on live L1/L4, fb20 and the footer belong to the host unless
the agent is active / an attention footer is due). Host JPEGs only copy host-owned faces and the sub-screen body.
Tiles overlapping owned regions return cmd200 `E` without mutation. Entire ctl pages
0/1 are reserved even during a grant; agent fb16/fb20 binds/draws and any fb21 draw
intersecting the footer are reserved. fb21 `screen.text` implicitly addresses only the y0..111 body viewport;
permitted text still returns `rendered:false`, with no pixel mutation. No new
agent pixel transport is invented. Builtin show/delete restrictions are retained.

At a route switch, pending ordinary display work is cancelled without queuing black
frames; fb20 and the existing footer pixels remain intact until replacement renders.
Cancelled in-flight writer snapshots cannot requeue a stale retry. Prior receive bytes are
marked stale, including partial frames and a snapshot of unread fd bytes. Their eventual cmd100 ack retains its id;
cmd200 returns `E`. A decoder result carries its original epoch and cannot publish
on a later route. A fresh full JPEG is required after every host-route switch/loss;
tiles cannot initialize a page. One in-flight writer may finish, then its replacement
follows. Host handshakes/loss never blank or navigate local pages (2026-10-20 exception:
the one-time boot auto-L1).
Press-time routing consumes local releases; leaving Mac PTT sends modifier-up.
Every outstanding host press receives an up before navigation taps. Agent page-id
changes are boundaries even within the AGENT route; releases go to the original
page/connection, with disconnect generations preventing client-slot reuse leaks.
HOME/LS pressed keys retain their labels and show a white top edge until release.
Mac PTT edges (and, since 2026-10-21, the fb17–19 Mac HID key edges) are not also forwarded as host key events
(exception since 1.5.1: while a `ui.hidKeys` map is active, mapped keys and fb17–19 are forwarded marked with `hid`). Short host K1 (on L1; the
Mac deck focuses the selected session) and short K2 toggle are forwarded as complete
short taps only after the release decision. Knob rotation on L1/L4 is forwarded per
detent (press edge only) while ONLINE and fresh; the Mac deck uses K1 rotation to move
the selection in attention order (effort is not adjustable from the device).
Physical host-page navigation synchronizes the known host mode with a K2 tap; L4
Back is decided locally on release. Accepted host ui.page requests do not synthesize
a second navigation tap.

Optional cmd101 **flat JSON** methods (<=1023 JSON bytes, strict duplicate/type checks):

- `{"method":"ui.hello","version":1}` returns version1, heartbeat_ms1000,
  capabilities `ui.page`/`ui.heartbeat` (since 1.5.1 also `ui.hidKeys`), since 1.6.0 `page` (`L1`/`L4`/`none`),
  and the last accepted generation (since 1.5.1 also `hid_generation`; since 1.6.0 both 0: every hello resets them).
- `{"method":"ui.hidKeys","generation":G,"keys":{...}}` (1.5.1): L1 option-mode digit keys, see
  "L1 option mode" above.
- `{"method":"ui.heartbeat"}` at 1 Hz: three consecutive samples spanning >=2 s
  establish ONLINE. Samples closer than 1 s do not advance the count; a gap >1500 ms
  restarts the probing count. At 3 s silence, SUSPECT freezes host input/content and
  adds a footer badge; at 5 s, OFFLINE replaces visible host content with a placeholder.
  EOF/link-down is immediate OFFLINE. A new hello restarts negotiation, never navigation
  (except the one-time boot auto-L1 on the first ONLINE).
- `{"method":"ui.page","page":"L4","generation":7}` is accepted only while
  ONLINE and already in an explicitly entered L1/L4 route. Page is L1 or L4;
  generation is 1..999999999 and strictly increases within a host session (until
  1.5.1 across hello/loss until process restart; since 1.6.0 a hello resets it to 0).
  The reply echoes the accepted page/generation; even same-page acceptance
  invalidates prior content and requires a fresh full frame (since 1.6.0 without
  releasing held keys). Home/LS/L5/agent routes
  cannot be claimed. cmd201 remains reserved/unimplemented; no widget generation
  transport is claimed yet.
- Device -> host event `{"method":"ui.openSettings"}` (2026-10-20): exactly this payload,
  frame id 0xFFFFFFFF, sent only while ONLINE when fb19 网页设置 is pressed on HOME/LS, at
  most once per 3 s. The host opens its own settings URL; the device never sends a URL.
- Device -> host event `{"method":"ui.openArrange"}` (1.6.0): the same rules for LS fb17
  会话安排 (own 3 s limiter); the host opens its 会话安排 page with a one-time link file.

Replies retain the KD `ack_method`/`success`/`result` envelope and frame id. `getInfo`
and backlight work on all routes and grant no ownership. Legacy hosts need no new
methods: valid decoded full frames in an explicitly entered host route establish
liveness (three samples over >=2 s); valid permitted tiles maintain it after the first
full frame. After loss, `getInfo` serves as the legacy fresh handshake. No cached
pre-switch frame is reused, and host recovery never enables/resumes an agent.
Hello/heartbeat requests already queued at physical route entry remain valid: they
carry no page content. Only stale ui.page requests are rejected by the JSON epoch
gate. This avoids swallowing the handshake when HOME-to-L1 entry races ui.hello.

JPEG decoding runs in one worker, reusing the existing host canvas. There is one
compressed job <=1 MiB plus at most one <=1 MiB fresh replacement while an older
epoch is still decoding (no unbounded frame queue), a 1 MiB operational parser payload cap, and
one 121,552-byte local panel scratch. Decode/full-page production are capped at4 Hz;
local redraws coalesce and run one panel per poll boundary. Cmd200 stages into one
121,552-byte slot; same-rectangle updates coalesce, other rectangles return the
existing `E` payload while occupied. Enqueue never wakes a writer. JPEG, tiles and
ordinary local panels share a 250 ms per-panel cooldown and bus byte allowances
(416,464 / 360,448 bytes per 250 ms), charging whole-panel writer cost even for a
small tile. Safety faces/live-host footer updates preempt ordinary production.
A newer accepted full frame or route boundary cancels an unproduced tile.
Budget waits sleep instead of spinning. Safety/ctl deadlines run
before HID/host work and after each render slice. Echo waits during the unchanged
allow-listed knob initialization service the safety/UI loop every <=10 ms; GD32
encode/tx gates and outbound bytes are unchanged. Writers stay asynchronous with
200 ms retry backoff; failure to start them exits instead of blocking on SPI in the
safety loop. A successful confirmation presentation is acknowledged only when all
required pending panels are successfully written and both writers are idle.
Every boundary resets confirmation `shown` and its timestamp. Returning to L5
requires a new completed presentation and a new 300 ms guard.

Host output has a bounded 16 KiB nonblocking buffer; partial writes are retained.
Space for 64 empty cmd100 acks is reserved against all other output, including
cmd200 floods. Another 64 one-byte cmd200 error acks (or empty success acks) are
reserved against ordinary output. Both protocol acks bypass pending key-ups and
navigation taps; key-ups retain ownership until queued and precede later taps and
ordinary output. This preserves the stop-and-wait host's ack under backpressure
without delaying safety. A physically lost link cannot promise delivery;
unbounded noncompliant floods may exhaust the
fixed buffer. Ack means handled, never physically displayed.

The L5 scaffold provides physical allow/disable, guarded confirm/cancel, HOME/LS,
fixed safety/PTT faces and ctl state/summary/risk. (2026-10-20: LS is read-only; settings,
keys and the connection test live on the usb0 web page, so no Apply/Test is reachable.) Full pause/voice behavior remains U4/U6;
PTT/ASR and pause are visibly unavailable, with no transcript or paused wire enum.
The simulator has no Apply/test injection any more (read-only LS). Real status probes, device timing/RSS/SPI budgets and physical acceptance remain U7.

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

Round 1 fixes (macOS, offline): `tests/onboard_test` and
`tests/onboard_test_tsan` pass 2,736 checks, including unread-hello startup, HID
stop/reopen/replacement/backpressure/shutdown, local feedback, press ownership,
confirmation re-entry, intermediate reserved queues and actual cmd200 flood/acks.
The fake-clock page-settling helper advances the new shared 250 ms budget; the
rate tests explicitly hold time fixed and check 249/250 ms boundaries.
`make -C deck onboard-ui-test` passes using `UV_CACHE_DIR=/tmp/mk20-a2-uv-cache`
with the target's UV_OFFLINE=1 (11 sim tests, unchanged U1 conformance/goldens).
`make -C deck/kbd test` stops at 3,999 core checks / 14 environmental failures;
the same 14 assertions were reproduced from base 25b07e1. Linux e2e requires
`/proc`; it was not run on macOS and `tests/e2e_run.py` is unchanged in this round.

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
