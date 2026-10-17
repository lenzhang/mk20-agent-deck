# MK20 Agent Deck — 产品需求文档（PRD）

| 字段 | 内容 |
|------|------|
| 文档版本 | 0.2（2026-10-17 SGT 按用户反馈更新：PTT=右 Option 透传、8 Agent 键、动画/头像规范、tmux、首期 ccai+codex） |
| 日期 | 2026-10-17 SGT |
| 状态 | 草案（仅文档，不依赖实体机） |
| 目标硬件 | Waveshare MK20 B（黑色） |
| 参考产品 | OpenAI × Work Louder **Codex Micro** |
| 宿主环境 | 私人 Mac（`ccai` / `codex` / kimi / grok；worktree 于 `/Users/dev/worktrees/`） |

---

## 1. 背景与动机

Codex Micro（约 $230，限量）是 ChatGPT 桌面端的实体控制台：6 个 RGB Agent Key 显示多会话状态，Command Key 做接受/拒绝/按住说话等，摇杆触发技能，旋钮调推理强度。它**深度绑定 ChatGPT 桌面 App 私有集成**，无法直接驱动本机的 `ccai`（Claude Code）、`codex` CLI、kimi、grok。

用户已购 **微雪 MK20 B**：20 个 0.85″ 128×128 LCD 键帽屏 + 2.8″ 副屏 + 双旋钮 + 麦/喇叭。显示能力强于 Codex Micro（文字+图标，而非仅灯色），但没有摇杆/RGB 底光/蓝牙三通道，也无法接入 ChatGPT 私有 API。

**目标**：在 MK20 上复刻 Codex Micro 的「多 Agent 状态一眼可见 + 高频操作不离手」体验，对接用户真实工作流（`ccai` / `codex` 等），不依赖 ChatGPT 桌面或 ScreenKey 上位机。

---

## 2. 目标与非目标

### 2.1 目标（Goals）

1. **状态面板**：**8 个** Agent 键（用户已确认），键帽屏用「颜色 + 动画图案 + 工具头像表情」表达状态。
2. **命令键**：接受 / 拒绝 / 发送 / 新会话 / 按住说话（PTT）等，映射到当前选中会话。
3. **技能键**：用一组键代替摇杆四向（审 PR、调试、重构等可配置技能）。
4. **旋钮**：调 reasoning / effort 档位；第二旋钮切层或滚动摘要。
5. **副屏**：当前选中 Agent 的摘要、effort、等待原因。
6. **分层**：至少 2–3 页布局（Agents / Skills+Commands / Ops），旋钮按下或专用键切页。
7. **纯 Mac 守护进程 + USB**：不依赖 ScreenKey；协议基于已还原的 PC↔T113 帧。

### 2.2 非目标（Non-goals）

- 不接入 / 不逆向 ChatGPT 桌面 ↔ Codex Micro 私有协议。
- 第一期不刷改 GD32 QMK、不改 T113 系统镜像（可后续可选）。
- 不做蓝牙三通道、不做 RGB 底光复刻。
- 不把 MK20 做成通用 Stream Deck 替代品（家居/媒体为 P2）。
- 本期文档阶段**不要求**设备插上；不写刷机/发版步骤为必须项。

---

## 3. Codex Micro 功能清单（调研合成）

来源优先级：ChatGPT Learn 官方文档 > Work Louder 设置页 / 官网 > OpenAI Supply > The Verge / Ars / 评测与社区。未在官方表中写死的标 **〔未确认〕**。

| 功能 | 行为摘要 | 来源 | MK20 映射 | 优先级 | 可行性 |
|------|----------|------|-----------|--------|--------|
| **Agent Keys ×6** | 跟随会话；灯色=状态；单击切会话（默认不置前）；350ms 内双击切会话并置前 ChatGPT；可选「单击即置前」 | [learn.chatgpt.com](https://learn.chatgpt.com/docs/features/codex-micro)；[worklouder setup](https://worklouder.cc/openai-micro-setup) | 键帽屏显示色块+短名+状态字；单击选中；双击聚焦对应终端/窗 | P0 | 高（显示更强）；聚焦需自研桥 |
| **跟随模式** | 最近 6 个更新会话 / Pinned 前 6 / Priority（待输入>未读>活跃优先）/ Custom（会话、快捷键、技能） | Learn | 配置：最近 worktree 会话、固定槽位、Priority 排序、Custom | P0 | 中（需会话发现） |
| **状态色** | 白 Idle；蓝 Thinking；绿 Complete（有未读）；琥珀 Requires input；红 Error；灭=未分配；**选中键脉冲** | Learn 状态表；Marketing 另提 running/waiting〔部分与表重叠〕 | LCD 底色+图标；选中描边/脉冲动画（JPEG 刷新） | P0 | 高 |
| **「Running」独立色** | Marketing：「thinking, running, waiting, or done」 | OpenAI Supply / Verge | 建议映射：Thinking=蓝；Running=青/蓝脉冲〔产品自定，非官方分色〕 | P1 | 高（自定） |
| **Command：Fast mode** | 开/关 Fast | Learn 默认布局 | 一键切 effort/fast 等价配置 | P1 | 中（工具相关） |
| **Command：Approve** | 批准当前请求 | Learn；Work Louder | 向选中会话发 accept/allow | P0 | 中–低（见集成节） |
| **Command：Decline** | 拒绝 | 同上 | deny/reject | P0 | 中–低 |
| **Command：Continue in new chat** | 当前对话开新分支/新聊 | Learn | 新 `ccai`/`codex` 会话或 fork | P1 | 中 |
| **Command：Push-to-talk** | 按住说话，松手结束；双击保持录制；用**电脑麦克风**（设备无麦） | Learn | PTT 键 = 按住右 Option，交给用户输入法听写（§5.5）；不自研 ASR | P0 | 高（QMK `KC_RALT`） |
| **Command：Send** | 发送 composer 内容 | Learn | 向选中终端发回车/提交 | P0 | 中（焦点依赖） |
| **录音灯效** | 录制时海绿色光绕键盘转；处理时白光移动；就绪固态白；按 Codex 键发送 | Learn | 副屏/键环动画 + 选中键高亮〔近似〕 | P2 | 中 |
| **Voice Chat 模式** | Mic 键可改为 Voice Chat / 开关麦；长按结束；可拆独立双键 | Learn | P2 | 低优先 | 中 |
| **摇杆四向** | 推满触发方向动作；默认可为 Plan / 前进 / 侧栏 / 后退；可映射任意桌面命令或已启用 skill。营销文案强调「审 PR、调试、重构」〔与 Learn 默认表不一致，以 Learn 默认为准，营销为可配置例〕 | Learn；Work Louder 营销 | **无摇杆** → 4 个技能键 或 旋钮 2 四向模拟 | P0（技能） | 高 |
| **旋钮：Composer navigation** | 转=浏览 composer 控件；按=选中；菜单打开时拨右侧 Agent Key 亮红=取消 | Learn | 旋钮 1 默认改为 **Reasoning**；Composer 导航对 CLI 价值低 | P2 | 低（CLI） |
| **旋钮：Reasoning only** | 调 reasoning effort；按打开滑块/高级 | Learn；Supply「Set the Brainpower」 | 旋钮 1：**effort**（low/medium/high/xhigh…） | P0 | 中（写入配置/发命令） |
| **旋钮：Conversation scrolling** | 滚动活动聊天；按跳到最新 | Learn | 旋钮 2 或副屏滚动 | P1 | 中 |
| **旋钮：Custom** | 左/右/短按/长按可映射；长按默认可开设置（非 Custom 时） | Learn | 长按旋钮打开 Mac 设置 UI / 打日志 | P1 | 高 |
| **触控条** | 底左：长按 3s 进 BLE；点选通道 1/2/3；第 4 下有线白光；5s 无操作退出；也用于 Input 切层 | Learn；Work Louder setup | **无触控条** → 专用「层」键 + LED/副屏层指示 | P1 | 高 |
| **蓝牙 3 通道 / USB-C** | 三 BLE 主机 + 有线；有线插入在 BLE 下会充电〔setup 与 Learn 对「是否自动切有线」表述略异，以 Learn「充电但不切有线」为准，setup Note 写会切有线——**〔冲突，未最终确认〕**〕 | Learn；setup | MK20 仅 USB（Hub）；无 BLE。Wi‑Fi 靠 2.4G 网卡，与本项目无关 | — | N/A |
| **照明** | 亮度；自动灭屏 30s–1h（默认约 3min）；状态变化或操作唤醒；电池可显示 | Learn | `setBacklight` JSON；无电池（外供电） | P1 | 高 |
| **Input 6 层** | ChatGPT 占 layer 1；Input 可配另 5 层；AppSense 随前台 App 切层；触控切层 | Learn；Input docs / 社区 CLI | 守护进程内 3–6 页；可选随前台 App 切页 | P1 | 高 |
| **macOS Input Monitoring** | ChatGPT 需权限读键；Karabiner / Logitech Options+ 可能干扰 | Learn；Work Louder | 若走 HID F 键：需 Accessibility/Input Monitoring；若走 CDC `keyStateChanged`：**可不依赖**该权限 | P0 设计约束 | — |
| **Settings > Codex Micro** | Agent/Command/摇杆/旋钮/灯/语音；长按旋钮打开 | Learn | 本机 `~/.config/mk20-agent-deck/` + 简易 UI 或 YAML | P1 | 高 |
| **硬件规格** | 13 机械轴、触控、1 编码器、平面摇杆；RGB；BT/USB；Mac/Win；$230；含 32 图标键帽 | Supply；Work Louder；Verge；Ars | MK20：20 LCD + 副屏 + 2 编码器 + 麦喇叭；无摇杆/BT | — | — |
| **兼容 Creator Micro 2** | ChatGPT 亦支持 CM2 | Learn | 无关 | — | — |
| **可靠性问题** | 休眠后 Codex 层失灵、BLE 重配对、固件刷错砖机等 | Reddit / Work Louder feedback；CNET / HN 评价两极 | USB 有线优先；守护进程自愈重连 | P1 | 设计规避 |

### 3.1 状态色规范（对齐官方 + MK20 扩展）

| 状态 ID | Codex Micro 灯色（官方） | 含义 | MK20 键面建议 |
|---------|--------------------------|------|---------------|
| `unassigned` | Off | 未分配 | 深灰底 +「—」 |
| `idle` | White | 空闲 | 白/浅灰底 + 名 |
| `thinking` | Blue | 模型在工作 | 蓝底 + 「思考」/spinner |
| `running` | 〔营销提及，官方表无独立项〕 | 工具执行中 | 青蓝脉冲（自定） |
| `needs_input` | Amber | 要批准/回答 | 琥珀 + 「待你」 |
| `done_unread` | Green | 完成且有未读 | 绿 + 「完成」 |
| `error` | Red | 出错 | 红 + 「错误」 |
| `selected` | 同色脉冲 | 当前选中 | 粗白边 + 脉冲帧 |

官方 Learn 表无单独 “running”；实现上可将 `thinking` 与工具执行中的 `running` 合并为蓝，或拆青——**产品决定，标为自定**。

---

## 4. 用户故事

1. 作为并行开多个 `ccai`/`codex` worktree 的开发者，我希望一眼看出谁在等我批准、谁已跑完，而不切窗口。
2. 作为正在看浏览器的人，我希望按下 MK20 的「接受」即可批准当前高亮 Agent 的权限请求。
3. 作为常用语音输入的人，我希望按住 PTT 说话，松手后文本进入选中会话的输入框。
4. 作为调模型强度的人，我希望转旋钮即可在 low↔xhigh 间切换，并在副屏看到当前档。
5. 作为偶尔运维 voice-gateway 的人，我希望第二页键直接打开 smoke / SSH / Gitea（P2）。

---

## 5. 功能需求详述

### 5.1 Agent 状态键（P0）

- 固定 **8 个** Agent 键（上两排，见 §6 布局）；首期只接 `ccai` + `codex`（kimi/grok 头像预留，P2）。
- 每个槽位绑定一个 `AgentSession`：`id`、`tool`（`ccai`|`codex`|`kimi`|`grok`）、`cwd`/`worktree`、`status`、`title`、`last_message_preview`、`needs_attention_at`。
- 单击：选中该会话（更新副屏与命令键上下文）。
- 双击（≤350ms，对齐 Codex Micro）：选中并切到对应 **tmux** 窗口/pane（`tmux select-window/-pane`，用户已确认终端栈为 tmux）。
- 状态来源见 §7；刷新目标：状态变化 <500ms 内反映到键面（尽力）；全量重绘可降频。

### 5.2 命令键（P0/P1）

| 键 | 默认动作 | 优先级 | 备注 |
|----|----------|--------|------|
| 接受 | 对选中会话批准当前 Permission/Approval | P0 | ccai：`PermissionRequest` 决策或向 TTY 发快捷键；codex：approval 通道——**需验证** |
| 拒绝 | 拒绝 | P0 | 同上 |
| 发送 | 提交当前输入 | P0 | 多数情况发 Enter 到聚焦会话 |
| 新会话 | 在同 worktree 或默认目录开新 `ccai`/`codex` | P1 | |
| PTT | **按住期间保持按下「右 Option（Right Alt）」，松开即释放**——触发用户现有输入法的语音听写 | P0 | 首选 GD32 QMK `KC_RALT`；守护进程合成事件为备份（见 §5.5） |
| 停止/打断 | Esc / Interrupt | P1 | |
| Fast/Effort 显示 | 只读或点击循环 | P1 | 与旋钮联动 |

### 5.3 技能键（替代摇杆，P0）

默认四技能（可配置，**非**照搬 Learn 默认导航，而取营销向「工作流」更贴 CLI）：

1. 审 PR  
2. 调试失败测试  
3. 重构当前文件  
4. 写/更新文档  

触发：向选中会话注入预设 prompt 或 slash/skill 命令。具体字符串用户可配。

### 5.4 旋钮（P0/P1）

- **旋钮 1（转）**：reasoning / effort 档位循环；副屏显示。  
- **旋钮 1（按）**：打开/确认 effort 面板；长按打开设置。  
- **旋钮 2（转）**：滚动副屏摘要或切 Agent 槽。  
- **旋钮 2（按）**：切布局页（Agents ↔ Commands/Skills ↔ Ops）。

编码器事件：一期可用 HID 消费者键/宏；理想路径为 `keyStateChanged` 扩展或单独 JSON——**编码器是否出现在 `keyStateChanged` 的 col/row 中尚未在 Demo 中证实〔未确认〕**。

### 5.5 按住说话（PTT，P0）——右 Option 透传

**用户需求（2026-10-17 确认）**：用户输入法的语音输入由**按住右 Alt / macOS 右 Option**触发。MK20 的 PTT 键只需「按下＝按住右 Option，松开＝释放」，**不做自研 ASR**（自研 ASR 降为远期可选）。

| 方案 | 做法 | 优点 | 缺点 |
|------|------|------|------|
| **A. GD32 QMK 键码 `KC_RALT`（推荐）** | 在 VIA/QMK 键位里把 PTT 物理键设为 `KC_RALT` | 真实 HID 修饰键，按住语义天然正确；**不依赖守护进程**，Mac 上无需辅助功能权限；延迟最低 | 需要改 GD32 键位：优先用 VIA 协议（固件为 `…mk20_plus_via.bin`，大概率支持 VIA 动态改键〔未实机确认〕），不重刷固件；若必须重刷则属高风险 |
| B. 守护进程合成事件 | 监听 `keyStateChanged` 按下/松开 → `CGEventCreateKeyboardEvent` 发右 Option（keycode 61）down/up | 不动固件 | 需辅助功能/输入监控权限；某些输入法可能只认真实 HID 或区分合成事件〔未确认〕；守护进程挂了就失效；长按期间若事件丢失会「卡键」 |
| C. 守护进程 HID 虚拟设备 | 用 Karabiner VirtualHIDDevice 等 | 更像真键 | 依赖第三方驱动，与用户现有键盘工具可能冲突 |

**推荐**：A 为主（P0），B 为回落（守护进程检测到 A 未生效时可开启，默认关）。防卡键：B 方案在失去 USB/超时 30s 时强制发 key-up。

**键面表现**：静止时显示麦克风图标；按住期间守护进程通过 `keyStateChanged(pressed=true)` 得知（A 方案下 GD32 仍上报 T113，T113 仍发 `keyStateChanged`——**需实机确认按键同时走 HID 与 CDC 两路**），在该键播放「聆听波形」动画、副屏显示「语音输入中…」；松开恢复。

### 5.5.1 远期可选：自研 ASR
保留 Whisper/云 ASR + 注入会话的设计为 P2，不进 M3。

### 5.6 分层（P1）

至少 3 页，旋钮 2 按下切换；副屏显示 `L1 Agents` / `L2 Act` / `L3 Ops`。

### 5.7 副屏（P0）

428×142：选中 Agent 名、tool、status、effort、一行 preview、等待原因。

### 5.8 注意力提示（P1）

`needs_input` / `error`：键面闪烁 + 可选 `playAudio` 短音（协议已有）+ macOS 通知。

---

## 6. 键位布局（ASCII）

MK20 为约 **5×4** 键区 + 上方/侧方 **副屏** + **两枚旋钮**（具体丝印位置以实机为准；下列为逻辑布局）。

### 6.1 L1 — Agents（默认，8 Agent 键）

```
┌──────────────────────────────────────────────────────────────┐
│ 副屏 428×142：[大头像] 选中 Agent 名 | 工具 | 状态 | effort | 摘要 │
├──────────┬──────────┬──────────┬──────────┬──────────┐  (K1) │
│ A1 Agent │ A2 Agent │ A3 Agent │ A4 Agent │  接受 ✓  │  effort
│ 头像+动画│          │          │          │          │  转/按 │
├──────────┼──────────┼──────────┼──────────┼──────────┤       │
│ A5 Agent │ A6 Agent │ A7 Agent │ A8 Agent │  拒绝 ✗  │  (K2) │
├──────────┼──────────┼──────────┼──────────┼──────────┤  切页/ │
│ 技能·审PR│ 技能·调试│ 技能·重构│ 技能·文档│  发送 ⏎  │  滚动  │
├──────────┼──────────┼──────────┼──────────┼──────────┤       │
│ 🎙 PTT   │ 停止 Esc │ 新会话   │ 聚焦tmux │ 层 L1/2/3│       │
│(右Option)│          │          │          │          │       │
└──────────┴──────────┴──────────┴──────────┴──────────┘
```
说明：左 4 列 × 上 2 行 = 8 Agent 键；右列为最高频「接受 / 拒绝 / 发送」；底行 PTT 放左下（拇指/食指易按）。具体物理旋钮位置以实机为准。

### 6.2 L2 — 扩展命令 / 工具

```
│ 模型切换 │ Plan模式 │ Worktree │ Diff/审  │ 通知开闭 │
│ 静音     │ 背光     │ 刷新状态 │ 设置     │ 回 L1    │
```

### 6.3 L3 — Ops（P2，可选）

```
│ NAS smoke │ Gitea    │ StopWatch│ 串口     │ …        │
```

---

## 7. 与本机 Agent 的集成设计

**约束**：不能使用 ChatGPT 桌面的 Codex Micro API。

### 7.1 Claude Code / `ccai`（已核实文档）

来源：[Hooks reference](https://code.claude.com/docs/en/hooks)。

| 信号 | 用途 | 映射状态 |
|------|------|----------|
| `SessionStart` / `SessionEnd` | 注册/注销槽位 | idle ↔ unassigned |
| `UserPromptSubmit` | 用户已发送 | → thinking |
| `PreToolUse` / `PostToolUse` | 工具进行中 | → running（自定） |
| `PermissionRequest` | 要权限 | → needs_input；命令键可 JSON `decision.behavior` allow/deny |
| `Notification`（`permission_prompt` / `agent_needs_input` / `agent_completed`） | 提醒（有的有延迟） | 补充注意力 |
| `Stop` | 回合结束 | → done_unread / idle |
| `StopFailure` | API 错误 | → error |

钩子脚本将 JSON 摘要 POST/写 Unix socket 到本机守护进程。`PermissionRequest` **可用钩子代答**（官方支持），因此「接受/拒绝」对 ccai **相对可行**。

向会话注入文本/发送：仍依赖终端焦点、tmux `send-keys`、或 Claude 的未公开控制通道——**TTY 注入标为不确定**。

### 7.2 Codex CLI（已核实文档）

来源：[Codex Hooks](https://learn.chatgpt.com/docs/hooks)；[config notify](https://learn.chatgpt.com/docs/config-file/config-reference)；社区对 `notify` 的说明。

| 机制 | 用途 | 注意 |
|------|------|------|
| `hooks`：`SessionStart`/`Stop`/`PermissionRequest`/`PreToolUse`/… | 状态与批准 | 非托管钩子需 `/hooks` **信任**；`PermissionRequest` 可返回 allow/deny |
| `notify = ["program", …]`（仅用户级 `~/.codex/config.toml`） | 回合结束外部通知 | 文档/社区称目前主要 `agent-turn-complete`；**批准等待勿只靠 notify** |
| `[tui] notifications = ["agent-turn-complete","approval-requested",…]` | 终端通知 | 需终端支持 OSC；守护进程不易直接消费 |
| Session rollout JSONL | `~/.codex/sessions/YYYY/MM/DD/rollout-…jsonl` | 可扫目录做发现；格式非稳定契约 |
| `model_reasoning_effort` | effort 档 | 旋钮可改用户配置或发会话内命令——**运行中热更新是否立即生效未确认** |

### 7.3 动作投递（Accept / Reject / Text）——不确定项

候选（需实机+终端栈验证）：

1. **钩子代答**（ccai/codex `PermissionRequest`）——批准类首选。  
2. **tmux `send-keys`** —— 若会话在已命名 pane。  
3. **AppleScript / Accessibility** 向 Ghostty 等发键——需权限，脆弱。  
4. **CLI 官方「远程控制」/app-server** —— 若用户启用，优先查当前版本文档；**未在本 PRD 中假定可用**。

---

## 8. 非功能需求（NFR）

| 项 | 要求 |
|----|------|
| 平台 | macOS（私人 Mac）；守护进程 launchd 常驻可选 |
| 延迟 | 状态→键面 P95 < 500ms（JPEG 推流受限时允许 1s） |
| 可靠 | USB 拔插自动重连；串口粘包按 Demo CRC 帧解析 |
| 安全 | 不把密钥写入键面；钩子脚本最小权限；不访问 ChatGPT 私有 API |
| 可测 | M0 提供无设备模拟器（假串口/录包回放） |
| 文档语言 | 中文为主；代码标识符英文 |

---

## 9. 验收标准（产品级）

1. 文档：本 PRD + PLAN 经用户审阅可进入 M0。  
2. M1：≥2 个模拟/真实会话状态能在不同键上区分 idle/thinking/needs_input/done/error。  
3. M2：接受/拒绝至少对 **一种** 工具（优先 ccai）在 needs_input 时生效。  
4. 旋钮改 effort 后副屏档位与配置一致（允许需新开回合才生效，但需文档写明）。  
5. 不依赖 ScreenKey 进程。  
6. 拔掉 MK20 时守护进程不崩溃，插回后恢复。

---

## 10. 已确认决策（2026-10-17）

- 终端栈：**tmux**；首期工具：**ccai + codex**；Agent 键：**8**；保留 **HID F13–F24 备份**；建 Gitea 仓 **leo/mk20-agent-deck**；PTT = **右 Option 按住透传**，不做自研 ASR。

## 10.1 仍开放的问题

- **effort 语义**：对齐 Claude `effort` 还是 Codex `model_reasoning_effort`，还是两套旋钮映射？  
- **编码器事件路径**：实机插入后优先抓 UART/HID 还是改 QMK（后者非目标）？  
- **tmux 会话命名约定**：建议 `ag-<tool>-<worktree>`，用户确认。  
- **头像风格**：像素风 / 扁平矢量 / 手绘？是否需要用户提供参考图。  
- **右 Option 改键方式**：VIA 动态改键是否可用（需实机）；不可用时是否接受刷 GD32。

---

## 11. 参考链接

- https://learn.chatgpt.com/docs/features/codex-micro  
- https://openai.com/supply/co-lab/work-louder/  
- https://worklouder.cc/codex-micro  
- https://worklouder.cc/openai-micro-setup  
- https://www.theverge.com/ai-artificial-intelligence/965901/openai-hardware-codex-micro-launch  
- https://arstechnica.com/ai/2026/07/openais-first-branded-hardware-is-a-light-up-keyboard/  
- https://code.claude.com/docs/en/hooks  
- https://learn.chatgpt.com/docs/hooks  
- 箱内调研：`/workspace/mk20-research/`（协议 Demo：`oss/OpenSourceLicenseDemo_V1.0/mainwindow.cpp`）

---

## 13. 键面动画与头像规范（新增）

### 13.1 设计原则
每个键是 128×128 LCD，状态表达 = **颜色（底色/描边）+ 动画图案 + 工具头像表情**，三者冗余，色弱也能分辨。

### 13.2 头像（Mascot）
- 每个工具一个固定吉祥物，跨状态只换表情/动作：`ccai`（Claude Code，暖橙色调小机器人〔风格待定〕）、`codex`（黑白云朵/终端方块角色）、`kimi`、`grok`（P2 预留）。
- 自绘原创形象，**不使用各公司商标原图**。
- 键面布局：上 80px 头像 + 动画区，下 40px 文字（会话短名 ≤6 字 / 状态字），外圈 4px 状态色描边。
- 副屏：左侧 128×128 大头像（同表情的放大版），右侧文字信息。

### 13.3 状态 → 颜色 / 动画 / 头像表情

| 状态 | 颜色 | 图案动画 | 头像表情/动作 | 帧数×fps | 循环 |
|------|------|----------|---------------|----------|------|
| idle | 灰白，亮度 40% | 慢呼吸（亮度 40↔60%） | 闭眼/打盹 | 8 帧 @ 2fps | 循环 |
| thinking | 蓝 | 环绕旋转的 3 点 / spinner | 托腮思考、眼珠转 | 8 帧 @ 6fps | 循环 |
| running（工具执行） | 青蓝 | 底部流动斜纹进度条 | 敲键盘/挥锤 | 8 帧 @ 6fps | 循环 |
| needs_input（等你回复） | 琥珀 | 外圈脉冲 | 挥手 | 6 帧 @ 4fps | 循环 |
| needs_approval（等批准） | 琥珀 + 红角标 | 「✋/!」徽章闪烁 | 举牌 | 4 帧 @ 4fps | 循环 |
| done_unread | 绿 | ✓ 弹出缩放（一次）→ 柔光呼吸 | 竖大拇指 → 微笑 | 6 帧 @ 8fps 一次 + 4 帧 @ 2fps | 先一次后循环 |
| error | 红 | 左右抖动 + 故障条纹 glitch | 晕眩 ×_× | 6 帧 @ 8fps 一次 + 静帧 | 一次后静止 |
| unassigned | 深灰 | 无 | 无（显示「—」） | 1 帧 | 静止 |
| selected（叠加态） | 白色粗描边 | 描边亮度脉冲 | — | 叠加层 | 循环 |
| ptt_listening（PTT 键） | 海绿 | 声波柱状跳动 | 麦克风图标 | 8 帧 @ 8fps | 按住期间 |

needs_input 与 needs_approval 区分：Claude `PermissionRequest` / Codex `PermissionRequest` → needs_approval；`Notification(agent_needs_input / idle_prompt)` 或 Stop 后带问题 → needs_input。

### 13.4 帧预算与链路估算

- **链路**：T113 以 USB gadget CDC-ACM 出现（`1d6b:0104`），走 USB 2.0 高速 bulk，**115200 只是名义波特率**，对 USB CDC 不限速（Demo 里 `setBaudRate(115200)` 不影响实际吞吐）。经验上 Linux gadget CDC 实际吞吐约 5–20 MB/s〔未实测〕。
- **真正瓶颈**更可能在 T113 端：JPEG 解码（双核 A7 1.2GHz）+ 共享 SPI 刷屏。全部 20 键 + 副屏一次全刷 ≈ 20×128×128×2 B + 428×142×2 B ≈ **776 KB 像素**；SPI 最高约 80 MHz（≈10 MB/s 理论），实际可能 3–6 MB/s → **全刷约 4–8 fps 上限**〔估算，待实机〕。
- **Demo 行为**：`SHOW_JPG` 发的是**整个画布**（deviceWidth×deviceHeight 截图）的一张 JPEG，设备回 ack（`frame N update finish`）后再发下一帧——即**停等流控**。是否支持只刷某个键的子区域**未证实**。
- **预算策略**：
  1. 只有动画中的键才产生新帧；静止键不刷。
  2. 每键 4–8 fps，全局合帧 ≤ 8 fps；超预算时按优先级降帧（needs_approval > error > thinking > running > idle）。
  3. 小调色板（≤16 色扁平画风），JPEG 质量 70–80，单键约 3–6 KB。
  4. 若整画布 JPEG 是唯一路径：合成整幅（约 640×512 级别〔实际画布尺寸以 `getInfo` 的 deviceWidth/Height 为准〕）后发一张，目标单帧 ≤ 60 KB。

### 13.5 渲染管线：实时推流 vs 设备端缓存

| 方案 | 说明 | 带宽 | 现状证据 |
|------|------|------|----------|
| **L. 实时推流（M0–M1 默认）** | Mac 端 Pillow 渲染每帧 → `SHOW_JPG` | 高 | Demo 已证 `SHOW_JPG` 可用 |
| **C. 设备缓存精灵图（目标）** | 用 `saveToFile`+`setFileCRC` 一次性上传 GIF / `frame_%d.png` 序列，之后只发「切换」命令 | 极低 | 固件字符串中有 `QMovie`（`.gif`）、`VideoPlayer`（`.mp4`）、`FrameAnimator`（`frame_%1.png`、`frameDelays`）、`ThemeFile::switchPage`——**说明主题引擎支持动图/视频/帧动画背景**；但 JSON 远程方法表只列出 `getInfo/setBacklight/setVolume/saveToFile/setFileCRC/deleteFiles/getFilesBySuffix/playAudio/stopAudio/keyboardInput`，**没有「切页/切动画」远程命令**〔需实机验证〕 |

**推荐**：先 L 跑通；并行在实机上验证 C 的可行路径：
1. 上传含 GIF 键背景的主题文件（格式需从 ScreenKey 主题包反推〔未确认〕），每个「状态组合」做一页；
2. 找能远程触发 `switchPage` 的途径（例如主题内按键动作或未列出的方法）；
3. 若无远程切页，则保持 L，并用「静态底图 + 小动画区」降低带宽。

### 13.6 需实机验证清单（动画相关）
- `getInfo` 返回的 deviceWidth/Height（画布尺寸）和 `SHOW_JPG` 是否必须整幅。
- 实测 `SHOW_JPG` 往返 fps（不同 JPEG 大小）。
- 主题内 GIF/MP4 键背景是否真的逐键播放、是否支持多键同时动画。
- 是否存在远程切页/切主题的方法。

---

## 12. MK20 协议摘要（实现约束，来自已有调研）

- PC↔T113：帧头 `A1 A5 5A 5E`，字段 id/cmd/size/size_crc/payload/data_crc。  
- `CMD_VALUE_SHOW_JPG = 100`：整帧 JPEG 刷键区/布局（Demo 对控件截图下发）。  
- `CMD_VALUE_JSON = 101`：`getInfo`、`saveToFile`、`playAudio`、`setVolume`、`setBacklight`、`keyboardInput` 等。  
- 设备主动：`method=keyStateChanged`，参数 `col` / `row` / `pressed`（Demo 已证）。  
- T113↔GD32：UART VIA 风格；GD32 USB HID `4250:426f`；T113 gadget `1d6b:0104`。  
- 不在本期要求改固件。
