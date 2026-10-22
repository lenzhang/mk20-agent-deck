# mk20deck — MK20 Agent Deck P0 (PRD v1.2)

Code lives in `deck/` so it does not collide with the old untracked skeleton
(`src/`, root `pyproject.toml`) on the Mac. PRD §14 (v1.3 VNC/AirPlay addendum)
is **not** implemented (not approved).

## Layout
| module | what |
|---|---|
| `protocol.py` | Protocol A codec (`A1A55A5E`, crc32 of size & data), stream parser with resync, method allow-list (no `keyboardInput`) |
| `transport.py` / `link.py` | pyserial transport (CDC `1d6b:0104`, alt `1234:5678`), `MockDevice`, handshake (1 MiB `'0'` preamble + `getInfo`, then `ui.hello`; on mk20-kbd fw >= 1.3 a `ui.heartbeat` thread keeps the host ONLINE on every device page, see `kbd/UI.md`; fw 1.5.1 `ui.hidKeys` option-mode digit keys), SHOW_JPG stop-and-wait using the device ack as display ACK, latency stats |
| `layout.py` | canvas/rect model; default guess 640×656 (sub 428×142 @106,0; keys 128² @ (c·128, 144+r·128)); replaced by `getInfo` |
| `adapters/codex_desktop.py` | read-only Codex Desktop source: `state_5.sqlite` (`mode=ro`), rollout tail, `.codex-global-state.json` unread, `codex://` deep links |
| `adapters/tmux.py`, `adapters/herdr.py` | tmux (generation identity; state from Claude/codex hook records, else unknown) / herdr stub |
| `agentstate.py` | read side of the hook state records (`agent-state/*.json`), pane/process-tree matching |
| `slots.py`, `controller.py` | slot manager (15 sticky slots, strict-higher replace, pins, per-mode memory, 8-slot state migration), gestures, roles L1/L4 (see Key layout) |
| `rules.py`, `webui.py`, `webauth.py` | slot rules (固定规则, `slot-rules.json`), the local 会话安排 web page on 127.0.0.1:8781 and its one-time-link / session authorization (see 会话安排网页) |
| `render/` | key & sub-screen renderer, ≤8 fps global cap, single attention animation |
| `daemon.py`, `cli.py` | daemon loop, heartbeat, offline frame on exit, CLI; device events (exact payloads only, `protocol.DEVICE_UI_EVENTS`): `ui.openSettings` (网页设置 key) opens `[deck] web_url` (Mac config only, IP/localhost, max once per 3 s); `ui.openArrange` (fw >= 1.6.0, keyboard LS 会话安排 key) opens the 会话安排 page through a one-time link file (max once per 3 s; page off → toast) |
| `sim.py` | headless screenshots + interactive web sim |
| `supervisor.py` | find port → open → handshake → run; reconnect on unplug / ACK loss / wake; busy-port hint (never kills) |
| `hw/devtest.py` | on-device validation harness (`devtest`), markdown + JSON report |
| `hw/via.py` | VIA raw-HID client: read-only backup, diff-only verified restore, mock keyboard |
| `hw/gd32dfu.py` | GD32 DFU (`1eaf:0003`) re-flash wrapper, dry-run by default |
| `hw/usbenum.py`, `hw/power.py` | read-only USB enumeration; sleep/wake (clock jump + `CGDisplayIsAsleep`) |

## Key layout (L1 / L4, PRD 1.5 — [docs/PRD-1.5-L1-LAYOUT.md](docs/PRD-1.5-L1-LAYOUT.md))
Document coordinates: col 1..5 left→right, row 1..4 top→bottom; logical key `fb = (row-1)*5 + col`.

| row | col 1 | col 2 | col 3 | col 4 | col 5 |
|---|---|---|---|---|---|
| L1 row 1 | S1 | S2 | S3 | S4 | S5 |
| L1 row 2 | S6 | S7 | S8 | S9 | S10 |
| L1 row 3 | S11 | S12 | S13 | S14 | S15 |
| **L4 row 1** | DESK | HERD | TMUX | ＋新会话 | 回 L1 |
| L4 rows 2–3 | S6..S15, same keys as on L1 | | | | |
| row 4 (L1 = L4) | PTT (fb16) | ⌫ 删除 (fb17) | ⏎ 发送 / 可以 (fb18) | ✗ 不行 Esc (fb19) | MODE (fb20) |

- **Session slot (S1..S15)**: single tap = **select and focus** (window / tmux pane to the front, so dictation and
  the device keys land there); the selection happens on press, the focus on release (< 600 ms). No double-tap
  gesture any more. If the mode cannot focus (e.g. tmux without `terminal_app`) a tap only selects and the
  sub-screen says why. Empty slot: "空槽"; lost pin: pick it up and press it again to release it.
- **Pick up and place (2026-10-22)**: long press ≥600 ms = **pick the slot up** ("移动中": pink dashed border +
  「移动中」 chip on the key, sub-screen strip 「移动『<title>』：点目标键放下 · 点自己=切换钉住 · K1 取消」); the
  release does nothing else. The next slot press (L1 S1..S15 or L4 S6..S15) places it, on press and never
  focusing: another slot → move there and **pin** it (an occupant swaps into the source slot and keeps its own
  pin flag; an empty target leaves the source empty for the normal fill rules); the same slot → toggle the pin
  (this is now the only way to pin/unpin). Cancel, nothing changes, 「已取消移动」: K1 press, 8 s without any
  key/knob event, a page/mode switch (K2, MODE short/long, any L4 row 1 key — the key still does its job),
  the source session vanishing, reconnect / wake. Device-side keys (PTT/DELETE/SEND/NO) are ignored. Saved to
  `state.json` at once; placements are pinned, so stickiness/overflow replacement never move them.
- **fb16..fb19 are keyboard-side USB HID** to the Mac's frontmost window: PTT = Right Option (hold), 删除 =
  Backspace (hold repeats), 发送/可以 = Return, 不行 = Escape (also interrupts Claude/codex). mk20deck only draws
  these faces and ignores their key events (the firmware does not forward them; stray ones are dropped). A PTT
  press that is observed only drives the sea-green overlay.
- **MODE (fb20)**: short press cycles Desktop → herdr → tmux (decided on release); long press ≥600 ms opens L4.
  The firmware forwards fb20 on L1/L4 while the keyboard agent is inactive (otherwise it is the e-stop).
- **L4** (K2 short press toggles L1↔L4): DESK/HERD/TMUX switch mode and return to L1, ＋新会话 = new session in
  the current mode (cwd of the confirmed selection), 回 L1. S1..S5 are not on L4 (use L1 or K1).
- **K1**: rotate = move the selection in "needs me" order, press = focus the selection (while a slot is picked
  up: cancel the move), hold = L4. **K2**: rotate
  scrolls the sub-screen summary, short = L1↔L4, hold = L1.
- Slots: 15 per mode, sticky, strict-higher overflow replacement, pins/selection protected, `+N` overflow on the
  sub-screen. State files from the 8-slot layout (no `n_slots` field) migrate by physical key: A1..A4 → S1..S4,
  A5..A8 → S6..S9.
- Screenshots: `python -m mk20deck sim-shots --scenario layout --out <dir>` (pick up / place: `--scenario move`).

## 会话安排网页
哪个会话在哪个键，用网页安排（不用写配置文件）。mk20deck 守护进程自己提供这个页面：
**http://127.0.0.1:8781/**——**只能用 `python -m mk20deck arrange` 打开**：它通过守护进程的本机授权通道拿一个
一次性链接（60 秒内有效、只能用一次）交给默认浏览器（`--print` 只打印这个链接）。直接打开地址只会看到
“请在 Mac 上运行 mk20deck arrange 打开（安全起见）”。授权后浏览器保持 30 天（守护进程重启也不用重新授权）。
端口在 `config.toml` 的 `[web] arrange_port`（默认 8781，`0` = 关闭）；端口被占用时只记一条警告，守护进程照常运行、
没有网页。键盘没接时页面照样能用（显示 “键盘未连接”，打开页面期间每 2 秒刷新当前模式）。

- **页面**：三个模式各一个标签（Codex 桌面 / herdr / tmux），键盘当前模式标 “键盘当前”。3×5 网格和键盘 L1 前三排
  一样（S1–S15），每个键显示标题、工具、状态（与键盘同色同字：待审批/待输入/出错/完成未读/运行/思考/空闲/未知/空槽）、
  钉住和规则标记；每 2 秒刷新。非当前模式的网格来自后台轮询（约 10 秒），是 “切过去后键盘会显示的样子”。
- **点一个键 → 右侧面板**：列出该模式现有的会话（标题、工具、目录名或 tmux 窗口、状态、在哪个键）。
  “放到这里” = 键盘的拿起/放下：移过去并钉住，目标键已有会话则互换（不在键上的会话：原来钉住的占用者挪到第一个空键并保持钉住，
  没钉住的交回自动填充）；“取消钉住”；“清空此键”（交回自动填充，没有别的会话等着上键时可能又放回原会话）。
- **固定规则**（每个模式每个键最多一条）：匹配方式 **目录**（会话目录等于它或在它下面；“用作规则” 取所选会话的 worktree
  根目录 / cwd——完整路径只在 Mac 进程里，网页只显示目录名；也可输入 `/…`、`~/…`）、**tmux 窗口名**（tmux 窗口名或会话名，
  完全一致，含 `* ? [` 时按通配符）、**标题包含**（不区分大小写，适合 Codex 桌面线程标题）、**工具**（ccai / codex / kimi）。
  可启用/停用、替换、删除。
- **规则语义**：每次轮询先应用规则——优先级 **规则 > 手动钉住 > 自动填充**。匹配的会话放在规则的键上并钉住；
  没有匹配会话时这个键显示 “等待：<规则>”（键盘上 “等待：<值>”），并且保留，自动填充不会用它。
  一个会话匹配多条规则 → 放在编号最小的键；多个会话匹配同一条规则 → 最近活动的那个占这个键，其余照常自动填充
  （完全并列时保持现状，不来回跳）。规则挤走的手动钉住挪到规则会话原来的键（互换）或第一个空键，钉住保留。
  **在键盘上（或网页上）手动改动规则键——移入、移出、切换钉住、取消钉住、清空——会停用那条规则**，页面写明原因和时间，
  例如 “已停用：键盘把 S1 移到此键（10-22 14:03）”；重新启用即可恢复。停用/删除规则时，会话留在原键，变成普通钉住。
- **存储**：`~/.config/mk20-agent-deck/slot-rules.json`（遵循 `MK20DECK_HOME`），`version: 1`，原子写入
  （临时文件 + fsync + rename）、权限 0600。文件损坏 → 规则全部忽略，页面顶部和键盘副屏都提示，绝不崩溃；
  下一次保存前把坏文件改名为 `slot-rules.json.bad-<时间>`，不会悄悄覆盖。规则在网页关闭（端口 0）时照样生效。
- **线程与上限**：HTTP 服务（标准库 `ThreadingHTTPServer`）从不直接改控制器 / 槽位：每个读写都排队到
  deck 主循环线程执行（键盘连着时在 `Daemon.step` 里，没连时在 `Supervisor` 的等待里），改动后立刻强制出一帧，
  键盘上还会弹 “网页：…” 提示。主循环忙（例如正在握手）超过 3 秒 → 返回 “请稍后重试”，该请求作废、之后也不会执行。
  同时最多 8 个连接（再多立即 503）；HTTP/1.1 keep-alive，几个标签页每 2 秒轮询各复用一个连接，空闲 4 秒关闭；
  每个请求从第一个字节起最多 5 秒（含读请求头/请求体，慢速逐字节发送也会被切断）；排队的改动最多 32 个（满了立即 503）；
  唤醒主循环会合并，主循环每轮最多执行 8 个，键盘帧不会被网页拖慢。页面遇到 “忙”（`busy`，请求还没执行）时安静地
  退避重试；“可能已生效”（`maybe`，改动已开始执行但超时）不会自动重试。
- **非当前模式的改动**：网页显示的和改动用的是同一批后台会话数据——改动前先把该模式的槽位按这批数据对齐，
  请求里带上页面看到的会话（`expect`），对不上就返回 409 “已经变了，请看最新的页面”；该模式读不到会话
  （例如 herdr 未安装、读取失败、还没轮询到）时拒绝修改，绝不在空列表上改。
- **授权**（`webauth.py`）：一次性令牌只从 `$MK20DECK_HOME/run/arrange.sock` 发放（目录 0700、socket 0600，系统报告
  对端 uid 时再核对）——需要文件系统权限，只会发 HTTP 请求的本机程序（包括沙盒 App）拿不到。`/auth?t=…` 兑换后设
  `SameSite=Strict; HttpOnly` 会话 cookie（30 天，每次打开页面换新 id，旧 id 短暂宽限），每个会话有单独的 CSRF 令牌。
  令牌**从不出现在任何进程参数里**（macOS 上别的用户能用 `ps` 看到参数）：守护进程把链接写进运行目录（0700）里的
  0600 文件（macOS 上是 `open-*.webloc`，交给默认浏览器；其他系统是带 meta refresh 的 `open-*.html`），
  `mk20deck arrange` 只把文件路径交给 `open`；令牌兑换或过期（60 秒）后、网页停止时、以及启动时（上次遗留的）删除这些文件。
  `--print` 只把链接打印到当前终端；日志里链接只记到 `/auth?` 为止。
  会话只以 id 的哈希保存在 `run/arrange-sessions.json`（0600），最多 8 个。
- **从键盘打开**（固件 1.6.0）：L4 上短按 K1 进入键盘自己的设置页（LS），按 **会话安排** 键（左下第二个，fb17）。
  键盘只发 `{"method":"ui.openArrange"}`（没有参数、没有链接；Mac 在线时才发，每 3 秒最多一次），mk20deck 在进程内用
  `ArrangeServer.mint_file()` 生成同样的一次性链接文件，把**文件路径**交给 `open`（令牌只在那个 0600 文件里，不进参数、
  不进日志、不走数据线），每 3 秒最多打开一次。网页关闭（`arrange_port = 0`）、没启动（端口被占用）或生成失败时
  不打开，键盘上弹提示（约 5 秒，回到 L1/L4 也看得到）。
- **安全**：只绑定 127.0.0.1；`Host` 必须正好是 `127.0.0.1:<端口>` 或 `localhost:<端口>`（防 DNS rebinding）；
  无任何 CORS 头，`OPTIONS`/`DELETE`/`PATCH`/`HEAD` 一律 405；改动只能 `POST`/`PUT` JSON（`Content-Type:
  application/json`，`Content-Length` ≤ 8 KiB，不接受分块），`Origin` 必须等于 `http://<Host>`，`Sec-Fetch-Site`
  （若有）必须 `same-origin`，要有效的会话 cookie，并且 `X-MK20-CSRF` 请求头与该会话的 CSRF 令牌恒定时间比较一致；
  读状态 `GET /api/state` 也一样。CSP `default-src 'none'`，内联样式/脚本用每次响应的 nonce，无外部资源；
  `frame-ancestors 'none'`、`nosniff`、`no-referrer`、`no-store`。接口只给标题、工具、状态、tmux 窗口名和目录**名**，
  不给完整路径、摘要或提示词；Codex 桌面线程没有标题、只有提示词预览时，网页只显示线程 id 前 8 位
  （键盘上照旧，键盘副屏本来就显示预览）。规则文件写失败时改动整个不生效并返回错误（500），不会显示 “已保存”。
- **接口**（都要会话 cookie + CSRF 令牌）：`GET /api/state`；`POST /api/move {mode, slot, key}`、`POST /api/unpin {mode, slot}`、
  `POST /api/clear {mode, slot}`（三者都可带 `expect`：页面看到的那个键上的会话 key，或 null）；`POST /api/rules {mode, slot, kind, value | session, enabled}`（建立/替换该键的规则），
  `PUT /api/rules/<id> {enabled?, kind?, value? | session?}`，`POST /api/rules/<id>/delete`。`slot` 是 0–14（S1–S15）。
- 开发：`python -m mk20deck sim-serve --arrange-port 8782` 对着模拟数据开这个页面（规则写到临时文件；启动时打印
  `mk20deck arrange --socket <临时 socket>` 命令和一个一次性链接）。

## Dev
```bash
python3 -m venv .venv && .venv/bin/pip install -e . pytest
.venv/bin/python -m pytest -q                 # 95 tests, Linux-safe
.venv/bin/python -m mk20deck sim-shots --out ../artifacts
.venv/bin/python -m mk20deck sim-serve        # http://127.0.0.1:8765 (--source real on the Mac)
python3 -m mk20deck probe-codex [--json]      # read-only Codex Desktop snapshot (Mac)
python3 -m mk20deck probe-tmux [--json]       # read-only tmux panes + hook-derived states (Mac)
python3 -m mk20deck arrange [--print]         # open the 会话安排 page (one-time link over the daemon's 0600 socket)
.venv/bin/python -m mk20deck sim-shots --scenario attention --out ../artifacts/attention
.venv/bin/python -m mk20deck [--config ~/.config/mk20-agent-deck/config.toml] run [--port /dev/cu.usbmodemXXX]
.venv/bin/python -m mk20deck launchd-plist > ~/Library/LaunchAgents/com.leo.mk20-agent-deck.plist
```
Config: copy `config.example.toml` → `~/.config/mk20-agent-deck/config.toml`
(`MK20DECK_HOME` overrides the dir). `max_fps` is clamped to 8.

Golden frames: `tools/golden_gen.cpp` reproduces the vendor `packet_cmd_ack`
with the vendor `CRC32.cpp`; output is `tests/fixtures/golden_frames.txt`.

## Codex hook probe (optional, reversible)
`tools/hooks/hooks-install.sh` writes `~/.codex/hooks.json` (refuses if one exists;
`--dry-run` prints it); the hook logs metadata only (no prompt/tool input) to
`~/.config/mk20-agent-deck/hooks.log` (rotated at 8 MiB), writes the deck state record
(see below) and never returns a decision.
`tools/hooks/hooks-uninstall.sh` removes it if unchanged. Codex requires hooks to
be trusted (App: Settings → Hooks) before they run in Desktop sessions.

## 需要我的 agent（hooks）
tmux 模式靠 agent 自己的 hooks 知道「哪个 agent 在等我」。hook 脚本
`tools/hooks/mk20-agent-state-hook.py`（只用 stdlib、<100 ms、不联网、不输出、永远 exit 0，
不替你做任何审批决定）把事件写成 `~/.config/mk20-agent-deck/agent-state/<tool>-<session>.json`
（目录 0700、文件 0600、tmp+rename；`MK20DECK_HOME` 可改目录），只含
`tool, session_id, tmux_pane, tmux_socket, cwd, state, ts, ppid`，等待编号选择时（选项模式，2026-10-22）另加其一：
`options`（Claude AskUserQuestion 第一个问题的选项标签，≤5 个 × 24 字，去掉控制 / 格式字符）或
`prompt_kind: "permission"`（PermissionRequest / `permission_prompt`，codex PermissionRequest）；会话下一次写记录
（PostToolUse / UserPromptSubmit / Stop …）即消失。**不存** prompt、问题正文、其他工具参数、
通知正文、transcript 路径；超过 24 h 的记录自动清理，最多 256 个。改了 hook 后需重跑安装器（已装的是副本）。

| 事件（Claude Code，[hooks 文档](https://code.claude.com/docs/en/hooks)） | 状态 |
|---|---|
| `UserPromptSubmit` / `PreToolUse` / `PostToolUse` / `PostToolUseFailure` / `ElicitationResult` | running |
| `PermissionRequest`（立即）、`Notification` `permission_prompt`（约 6 s 后）、`PreToolUse` `ExitPlanMode` | needs_approval |
| `Notification` `idle_prompt`（完成后约 60 s 未输入；已是 done 时不重复提醒）/ `elicitation_dialog` / `elicitation_url_dialog` / `agent_needs_input`，`Elicitation`，`PreToolUse` `AskUserQuestion` | needs_input |
| `Stop` | done → 键面「完成未读」，聚焦后本地清除 |
| `StopFailure`（API 错误） | error |
| `SessionStart` | idle（`compact` 不改状态） |
| `SessionEnd` | removed |

codex CLI 0.163（无 Notification 事件）：`UserPromptSubmit`/`PreToolUse`/`PostToolUse` → running，
`PreToolUse` `request_user_input` → needs_input，`PermissionRequest` → needs_approval，`Stop` → done，
`Interrupt` → idle，`SessionEnd` → removed。kimi 没有 hooks：窗格按 `pane_current_command=kimi` 列出，状态 unknown。

安装（集成方执行；不要在没看 diff 的情况下改真实配置）：
```bash
# Claude Code（ccai 启动时会把 settings-clean/-glm 拷成 settings.json，所以三个都要装）
tools/hooks/claude-hooks-install.sh --dry-run ~/.claude/settings.json ~/.claude/settings-clean.json ~/.claude/settings-glm.json
tools/hooks/claude-hooks-install.sh           ~/.claude/settings.json ~/.claude/settings-clean.json ~/.claude/settings-glm.json
tools/hooks/claude-hooks-uninstall.sh [--dry-run] <同样的文件>     # 只删 mk20 条目
# codex CLI（旧的 mk20 探针要先卸再装；装后需在 codex 里重新信任 hooks）
tools/hooks/hooks-uninstall.sh && tools/hooks/hooks-install.sh
python3 -m mk20deck probe-tmux        # 只读：列出窗格 id / 命令 / hook 状态（不读屏幕内容）
```
安装器幂等（重复执行显示 unchanged）、保留已有 hooks、改动前备份 `<file>.bak-mk20-<ts>`、
JSON 损坏则整体拒绝且不写任何文件。hook 命令用 `exec python3 -I -S …`，使记录里的 `ppid`
就是 claude 进程本身。

deck 侧（`[tmux]`，见 `config.example.toml`）：窗格按命名（`ag-ccai-*`/`ag-codex-*`/`ag-kimi-*`）、
显式登记、`pane_current_command`（claude/codex/kimi）或**有 hook 记录**发现（ccai 窗格的命令是
`bash` 包装脚本，只能靠 hook 记录关联）。记录须同一 tmux socket、窗格仍在、`ppid` 存活且在该窗格的
进程树内（同窗格里由 Claude 启动的 `codex exec` 不会盖掉 Claude 本身）、未超过 `hook_max_age_s`；
`running` 超过 `running_stale_s` 无事件 → unknown（Esc 中断不触发 Stop）。标题用 worktree 名/cwd 末级。

**选项模式**（`docs/PRD-1.5-L1-LAYOUT.md` §3.2）：在 L1 单击 hook 报告的「待输入 / 待审批」会话（tmux Claude/codex；
桌面 / herdr 不进入）并成功置前 → 第 1 行变成选项键（标签 / 权限固定「是 · 是，且不再问 · 否」/ 数字 1–5），
第 2–3 行清空；固件 1.5.1 起按选项键由键盘直接给前台窗口发一次数字（`ui.hidKeys`，按住也只一个；按住说话时忽略并
提示「先松开按住说话」），Mac 只显示「已选 k」；Esc、K1、点暗键、换页 / 换模式、30 s 无输入、状态改变即返回。

K1：**转 = 按「待审批 > 待输入 > 出错 > 完成未读 > 其余按槽位」顺序移动选中**；**按 = 聚焦选中会话**
（同单击会话键）；长按仍进 L4。effort 不再映射到 K1（各模式本来都「此会话不支持」）。
其他模式每 10 s 后台轮询一次（`[deck] background_poll_s`，不阻塞帧循环），副屏顶部显示
「tmux 2 待处理」，L4 模式键与 L1 模式键带琥珀角标。

## P0 deviations / known limits
- Canvas geometry is a guess until `getInfo`; firmware 0/1-based coords are inferred from getInfo rects (`fw_coord_base = "auto"`).
- Desktop states: `error` from rollout `event_msg:error`; `turn_aborted` → idle;
  running older than 600 s without completion → unknown; rollout compressed to
  `.jsonl.zst` (Codex does this for inactive threads) → idle (or done-unread if in
  the App unread set). All Desktop sessions are
  tagged 来源推定 (source=vscode + originator heuristic; codex-tui also writes vscode).
- No accept/decline/send/stop/skill keys since the 1.5 layout (see Key layout); the adapters keep
  `send()`/`stop()`/`skill()` (unbound). Desktop effort stays unsupported. `[[skills]]` in config.toml
  is still parsed (old files load) but bound to nothing.
- Knob event mapping unverified (`knob_map` in config). PTT path B not implemented (PTT = device-side KC_RALT).
- Sleep: display sleep → "Mac 已休眠" frame + pause; wake (clock jump) → reconnect + re-handshake, gestures dropped. tmux new-session off unless configured. herdr stub. L2/L3 are P1.
- Knob events that arrive as a non-`keyStateChanged` JSON method are recorded by devtest but not yet mapped by the daemon.

## On-device checklist → `devtest`
Stop the daemon first (only one process may own the CDC port). Then on the Mac:
```bash
python3 -m mk20deck usb-list                 # read-only enumeration
python3 -m mk20deck via-backup               # read-only VIA keymap backup (pip install hidapi)
python3 -m mk20deck devtest                  # guided, ~5 min; add --with-sleep for the sleep/wake step
python3 -m mk20deck devtest --auto           # non-interactive subset (USB, port, getInfo, display timing, VIA)
python3 -m mk20deck devtest --headless       # no terminal input: guided on the device screens (see below)
```
`--headless` shows each step on the sub-screen with the target key highlighted (done keys turn green),
waits for "press any key" (`--start-timeout`, default 180 s), then runs the guided steps within
`--overall-timeout` (default 300 s), each `--step-timeout` (20 s) — a step that times out is skipped and
recorded.  Questions become automatic checks: the HID-leak gate watches macOS HID activity
(`CGEventSourceSecondsSinceLastEventType`: keyDown / flagsChanged / systemDefined / scroll, no
permission needed, no characters, no source device) and correlates it (±0.35 s) with MK20 key events;
the visual check and PTT→IME are recorded as `needs_manual_confirmation`.  It sets the backlight
(default 80) because host mode starts dark; `progress.json` in the report dir shows the current step.

### Offline typing vs host mode
Out of the box the MK20 is in *offline* mode (protocol V1): each key types its keycap character
through the GD32 as an ordinary keyboard.  The first answered `getInfo` (daemon, devtest, ScreenKey)
switches it to *host* mode (V2): keys are reported to the host over CDC and stop typing, and the
backlight starts at 0.  **It stays in host mode until the MK20 is rebooted / replugged** — quitting
the daemon does not restore offline typing.  The daemon therefore sends `setBacklight`
(`[deck] backlight`, default 80; `false` to disable) right after every handshake, including after
replug and wake.
Covers PRD §10.1: USB roles (T113 CDC / GD32 VIA / DFU / FEL), CDC port + ScreenKey contention
(read-only lsof/pgrep), getInfo canvas + 0/1-based coords, SHOW_JPG ACK P50/P95 + full-screen fps
(pattern vs noise JPEG, q60/85/95 sizes), wrong-resolution reject, visual check, 20 keys with on-screen
highlight (offset/transposition analysis), knobs (raw JSON, suggested `[knobs]` map), HID-leak question
(P0 hard gate), PTT CDC visibility + hold time + IME, tap/double/long timing vs 600/350 ms,
unplug/replug latency, optional sleep/wake, VIA read-only audit (+ backup copy).
Writes beyond images need `--allow-device-writes` (only `--backlight N` today).
Report: `~/.config/mk20-agent-deck/devtest/<time>/devtest.{md,json}` with a suggested config block.

Backup / recovery (VIA restore, GD32 DFU, T113 TF image): see `RECOVERY.md`.

## Transport: ECM/TCP first, ACM fallback (P2 composite)

With the P2 firmware setup the MK20 boots as **USB ECM (network) + HID (keyboard + absolute pointer) + ADB**,
and KeyboardDevice is reached through an on-device PTY↔TCP bridge instead of the CDC-ACM port.
`find_ports()` therefore returns the first answering bridge address (`tcp://172.30.250.2:17890`, then the 1.0.x
`10.20.0.2`, then link-local `169.254.7.2`, all bound to the ECM interface), then any CDC-ACM
`/dev/cu.*` port (stock / rollback mode). `open_transport()` picks `TcpTransport` or `SerialTransport`.

- Addressing (firmware >= 1.1.0): device `172.30.250.2/24` on `usb0` (plus link-local alias `169.254.7.2/16`),
  Mac `172.30.250.1/24` (firmware 1.0.x: `10.20.0.2` / `10.20.0.1`).
  Fixed MACs (device `02:11:22:33:44:55`, host side `02:11:22:33:44:56`) keep macOS on the same `enN`
  interface and its saved settings. One-time Mac setup (no router, so the default route is untouched):
  `tools/mk20_host_net.py sync` on the p2 branch (sets only the MK20 service, matched by MAC, to the device's
  active subnet with TUN/overlap checks and automatic restore), or by hand
  `networksetup -setmanual "<ECM service, e.g. Serial>" 172.30.250.1 255.255.255.0`.
- Env: `MK20_TCP=host:port` (override) / `MK20_TCP=off` (ACM only); `MK20_ECM_IF=en13` or
  `MK20_ECM_MAC=…` to pick the interface explicitly.
- The bridge serves one client at a time and a new client replaces the old one (so a probe or `mk20ctl`
  over TCP kicks the daemon; it reconnects).

### VPN / proxy TUN compatibility (any TUN, not only mihomo)

TUN-mode proxies (mihomo/Clash, Surge, sing-box, some VPNs) install broad routes on a `utun` interface.
The deck daemon is TUN-proof by itself:

1. It finds the ECM interface by its fixed MAC and **binds its socket to it** (`IP_BOUND_IF`, macOS's
   `SO_BINDTODEVICE`), so packets to the MK20 leave via the ECM link even if a TUN owns the route.
2. On connect it runs a **self-check** (`route -n get <device-ip>`): if the route goes via `utun*` it logs a
   warning with the bypass to add, e.g. mihomo/Clash `tun.route-exclude-address: [172.30.250.0/24]`,
   Surge `tun-excluded-routes = 172.30.250.0/24` (+ `skip-proxy`), sing-box `route_exclude_address`,
   WireGuard/Tailscale: keep the subnet out of AllowedIPs. It **never modifies proxy configs**.
   Other tools (ping, ssh, `adb connect`) are not bound and do need that bypass.
3. Most TUNs already exclude RFC1918 ranges (this Mac's mihomo excludes `172.16.0.0/12` and `10.0.0.0/8`, so
   no change was needed). If `172.30.250.0/24` collides with a corporate network, the device also answers on link-local
   `169.254.7.2` (bind to the ECM interface; macOS otherwise routes 169.254/16 via the primary interface).
