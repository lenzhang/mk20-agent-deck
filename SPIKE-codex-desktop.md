# SPIKE：Codex Desktop 状态/动作可行性（PRD v1.2 §6.8.4 / §10.1）

| 字段 | 内容 |
|------|------|
| 日期 | 2026-10-17（CDT 上午） |
| 方法 | 盒子上克隆 `openai/codex`（commit `ce0323f`, 2026-10-16 23:02 CDT）读 `codex-rs/app-server*`、`hooks`、`cli`；公开文档（learn.chatgpt.com Commands/Hooks）；私人 Mac **只读**盘点（`ps`/`lsof`/`plutil`/`sqlite3 -readonly`/`grep`），**未**启动任何 codex/模型 CLI，**未**向任何 socket 发请求，未改配置 |
| 被测 | `/Applications/ChatGPT.app`，bundle `com.openai.codex`，26.1007.30912 (build 20156)；内置 codex-cli `0.162.0-alpha.17.2`；Homebrew `codex` 0.162.1 |
| 结论一句话 | **P0 可做到：可靠线程列表/标题/项目/effort（S4 只读 SQLite）+ 粗状态 running/idle/done-unread（rollout 尾部 + readState）+ deep link 聚焦/新会话预填**；**waiting-approval、error 实时、审批代答只能走 P1 hooks**；**桌面正在用的 app-server 外部不可接入**（stdio 私有子进程），S2 降级为「独立实例读同一存储」。 |

## 1. 关键事实（证据）

1. **桌面 app-server 是 Electron 主进程的 stdio 子进程**：`ChatGPT(552) → codex -c features.code_mode_host=true app-server --analytics-default-enabled …`（pid 969）。`lsof` 显示 fd 0/1/2 为与 552 互连的 unix socketpair，**无 TCP LISTEN、无 unix 监听 socket**。→ 外部进程无法挂到桌面实例的 JSON-RPC 上（无 `thread/status/changed` 实时推送）。
2. **`~/.codex/app-server-control/app-server-control.sock` 是悬空符号链接**（→ `/private/tmp/codex-daemon-501/…` 不存在）；`app-server-daemon/` 日志止于 10-14；当前桌面**不使用**托管 daemon。daemon/`app-server proxy --sock` 是 SSH/远程控制用途，不是桌面实例。→ PRD「未文档化 socket 写入不采用」维持；且读也接不上。
3. **`~/.codex/ipc/ipc.sock`** 由 Electron 主进程监听（多条连接），协议未公开、源码不在 codex-rs → 不采用（同 S5）。
4. **持久化存储（只读可读，WAL 模式）**：
   - `state_5.sqlite.threads`：`id`(=threadId=rollout 文件名 UUID)、`title`/`name`/`preview`、`cwd`、`project_id`、`source`、`originator`、`model`、**`reasoning_effort`**、`is_pinned`、`archived`、`recency_at_ms`、`rollout_path`。本机 111 线程（未归档 102）。桌面线程 `source=vscode`，`originator` ∈ {`Codex Desktop`, `codex_work_desktop`, 空, `codex-tui`…} —— **originator 不唯一**，`codex-tui` 也写 `source=vscode`，来源区分需组合判断（见 §4 风险）。
   - `thread_history_1.sqlite.thread_turns.status` ∈ {completed, failed, interrupted, inProgress}；**有 5 条陈旧 inProgress（7 月–10 月崩溃残留）**→ 不能单独当「thinking」。
   - rollout `sessions/YYYY/MM/DD/rollout-…-<threadId>.jsonl`：`event_msg` 子类型含 `task_started` / `task_complete`(带 `last_agent_message`)/`turn_aborted`/`thread_settings_applied`/`item_completed`/`token_count`，`turn_context.effort`。**近 9 个 rollout 中 0 个含 approval request 事件** → 待批准状态不落盘。单文件可达 100MB+，只能读尾部。
   - `.codex-global-state.json` → `electron-thread-read-state-v1.unreadByIdentity.<id>.durable:[threadId…]`：桌面自己的未读列表（16 个，其中抽查的 3 个**不在本地 threads 表**→ 远程/云线程也会进来，需与本地表求交）。
5. **app-server 协议（源码 v2）**：`ThreadStatus = notLoaded | idle | systemError | active{activeFlags:[waitingOnApproval|waitingOnUserInput]}`；`thread/status/changed`、`thread/list`(+experimental `readStates`)、`thread/read.readState`、`thread/readState/changed`、`turn/interrupt`、`thread/settings/update`、`item/*/requestApproval`。**这些正是我们要的精确信号，但只对连接到“持有该线程的那个进程”的客户端有效**；独立实例里桌面线程只会是 `notLoaded`。
6. **hooks（源码 + 官方文档）**：事件 SessionStart/UserPromptSubmit/PreToolUse/PermissionRequest/PostToolUse/Stop/SubagentStop/PreCompact/SessionEnd；来源 `~/.codex/hooks.json` 或 config.toml `[hooks]`；**新/改动的 hook 需在 `/hooks`（或桌面 设置→Hooks）信任后才执行**。hooks 在 core 内执行，桌面线程跑在同一 core 上 → **理论上会触发（P1 待实测）**。PermissionRequest 输入含 `session_id`、`turn_id`、**`transcript_path`(=rollout 路径，内含 threadId，session_meta 含 originator)**、`tool_name`、`tool_input`；输出可 allow / deny / **不决定（交回原生审批）**，多 hook 时 deny 优先。→ session_id↔threadId 有确定映射路径。注意：hook 运行期间原生审批 UI 不会出现（阻塞式），PRD 的 ≤60s 挂起+交还设计成立但需实测。本机**无 hooks.json**。
7. **deep link（官方文档 + asar 字符串）**：`codex://threads/<uuid>`、`codex://threads/new?prompt=&path=&originUrl=`、`codex://new?…`、`codex://settings…`、`codex://skills`、`codex://automations`；Info.plist `CFBundleURLSchemes=[codex,http,https]`。asar 中确认 `codex://threads/` 10 处引用。
8. AppleScript：仅 Chromium/Standard Suite（v1.1 结论不变）。`notify` 仍指向 Computer Use 客户端 `turn-ended`。桌面 app-server 经 127.0.0.1:7890 出网（代理，与本 spike 无关，未改）。

## 2. 信号 × 来源 × 可行性

| 信号 | 来源 | 可行性 | 证据 / 说明 |
|------|------|--------|-------------|
| 线程列表 | S4 `state_5.sqlite.threads`（只读，`archived=0`，按 `recency_at_ms`） | **works** | 字段齐全；只读 `mode=ro` 打开不加锁写；schema 随版本变 → 启动时校验列存在，否则降级 |
| 线程列表（协议） | S2 独立 `codex app-server` → `thread/list` | **degraded（未实测）** | 读同一存储，等价于 S4 但有协议兼容层；需在 Mac 起一个 codex 进程（本次按规则未起）；状态恒 `notLoaded` |
| 线程标题 | S4 `name`(用户命名) > `title` > `preview` | **works** | |
| 项目/cwd | S4 `cwd`/`project_id` + global-state `local-projects` | **works** | |
| 来源=Desktop | S4 `source=vscode` ∧ `originator∈{Codex Desktop, codex_work_desktop, 空}` | **degraded** | `codex-tui` 也有 `source=vscode`；需白名单 originator + 排除 tui/exec，仍属启发式 → 标记「来源推定」 |
| idle | rollout 尾部最后事件为 `task_complete`/`turn_aborted` | **works（粗）** | |
| thinking/running | rollout 尾部 `task_started` 无对应 complete ∧ 文件 mtime 近（≤N 分钟）∧ 桌面进程存活 | **degraded** | 崩溃残留会假阳性（thread_turns 已见 5 条），必须加 mtime/进程守卫，超时转 unknown |
| waiting-approval | S1 PermissionRequest hook | **P1 only** | 不落盘、桌面 app-server 不可接入；P0 不可能 → 显示 running/unknown |
| waiting-user-input | S1（无专用事件）/ 协议 flag 不可达 | **not possible (P0)** | 仅 `waitingOnUserInput` 协议 flag，接不上 |
| done-unread | global-state `unreadByIdentity…durable` ∩ 本地 threads；或 S2 `readStates` | **works（近似）** | 是桌面自己的未读集合，延迟=桌面写盘频率（盘点时文件 mtime 为当分钟，延迟待量化）；非稳定接口 |
| error | rollout `turn_aborted` / thread_turns `failed` | **degraded** | 能区分中断/失败的已结束 turn；实时 systemError 不可得 |
| 聚焦线程 | `open codex://threads/<id>` | **works（官方文档）**；多窗口/冷启动待 GUI 实测 | |
| 新会话/技能预填 | `codex://threads/new?prompt=&path=` | **works（官方文档）**；长度上限待测 | 只预填不发送 |
| approve / decline | S1 PermissionRequest hook allow/deny/不决定 | **P1 only** | 需 hooks 信任 + 桌面触发实测 + 交还原生 UI 语义实测 |
| 停止当前 turn | 协议 `turn/interrupt` 不可达；AX 停止按钮 | **degraded（需 AX, P0 可选）** | 无 AX 授权则禁用 |
| 读取每线程 effort | S4 `threads.reasoning_effort` / rollout `turn_context.effort` | **works** | |
| 设置每线程 effort | 协议 `thread/settings/update` 不可达；AX 模型菜单 | **degraded → P0 禁用「此会话不支持」** | 不写全局 `model_reasoning_effort` |
| 新结果事件（完成） | S3 `notify` 链式包装 / Stop hook | **P1** | notify 被 Computer Use 占用，需包装 |

## 3. 推荐 P0 实现路径（Desktop adapter）

1. **列表/身份**：只读 SQLite（`file:…?mode=ro`，短事务，2–5s 轮询或 FSEvents 监听 `state_5.sqlite-wal`）。身份 = threadId（与 rollout 文件名、deep link 同一 UUID）。启动时做 schema 列探测 + App 版本记录，失败 → 「状态受限：存储格式变化」。
2. **粗状态**：对 8 个槽位线程（不是全部）只读 rollout **尾部 64KB**，解析最后的 `task_started/task_complete/turn_aborted`；结合 mtime + `pgrep -f ChatGPT.app` → running / idle / unknown。
3. **未读**：global-state `electron-thread-read-state-v1` 中 durable 列表 ∩ 本地线程 → done-unread；只读，绝不写回；聚焦后只清本地提醒。
4. **聚焦/新会话**：`open "codex://threads/<id>"` / `codex://threads/new?path=&prompt=`（URL 编码，超长失败不截断）。
5. **其余**：approve/decline/waiting-approval 置灰「P1 / 请在 App 内处理」；effort 只读显示；停止仅在 AX 授权且控件可确认时启用。
6. 不起 app-server、不碰 ipc.sock / control.sock、不注入 JS。

**P1**：`~/.codex/hooks.json`（PermissionRequest + Stop + UserPromptSubmit + SessionStart）→ 本地 daemon；用 `transcript_path` 解析 threadId + originator 做来源判定；首次需用户在桌面 设置→Hooks 信任。

## 4. PRD 条目确认 / 降级

| PRD 项 | 结论 |
|--------|------|
| §6.8.1 S2「app-server thread/list」 | **降级**：桌面实例不可接入（stdio 私有）；只剩独立实例/直读存储，状态 notLoaded。建议 PRD 改述为「S2 仅独立实例，等价 S4」 |
| §6.8.1 S4 只读兜底 | **升级为 P0 主来源**（列表/标题/effort/未读）；仍属非稳定接口，按版本核验 |
| §6.8.1 S1 hooks | **确认为 P1 唯一精确/审批路径**；字段含 transcript_path → session_id↔threadId 映射可确定（待桌面触发实测） |
| §6.8.1 S5/S6、ipc.sock、control.sock | 维持不采用；control.sock 当前悬空 |
| §6.8.2 deep link 聚焦/新会话 | **确认（官方文档）**；多窗口/冷启动/已读副作用仍需 GUI 实测 |
| §6.8.2 停止 / effort 写入 | **P0 降级**：协议不可达，仅 AX；无 AX → 禁用 |
| §3.1 waiting-approval 在 Desktop P0 | **不可得**，P0 显示 running/unknown（与 §6.8.3 一致） |
| 来源区分 Desktop vs CLI | **降级为启发式**（originator 白名单），需在 UI 标注 |

## 5. 仍需 Mac GUI 实测（不需 MK20、不需模型）

- deep link：同/异项目、多窗口、App 未运行冷启动；打开后 `unreadByIdentity` 是否变化。
- global-state 写盘延迟（新结果出现→文件更新）。
- rollout 尾部在 turn 进行中的写入节奏（判 running 的 mtime 阈值）。
- （需用户点头）P1：放一个只记日志、不做决定的 hooks.json，在桌面跑一次需审批的操作，验证触发与交还——这一步会产生一次真实模型 turn，需用户批准。
