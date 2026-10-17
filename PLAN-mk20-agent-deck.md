# MK20 Agent Deck — 实现规划（PLAN）

| 字段 | 内容 |
|------|------|
| 文档版本 | 0.2（2026-10-17 SGT：PTT 改右 Option 透传、动画管线、每任务标注是否需设备） |
| 日期 | 2026-10-17 SGT |
| 对应 PRD | `PRD-mk20-agent-deck.md` |
| 原则 | 文档先行；**设备未插入亦可推进 M0（代码+模拟）**；不刷机、不改官方固件为默认路径 |

---

## 1. 架构总览

```
┌──────────────────────────────────────────────────────────┐
│ 私人 Mac                                                  │
│                                                          │
│  ccai (Claude Code) ──hooks──┐                           │
│  codex CLI ──hooks/notify───┼──▶ Session Hub (状态库)    │
│  (可选 kimi/grok 适配器) ───┘         │                  │
│                                       ▼                  │
│                              Action Router               │
│                         (accept/reject/send/PTT/text)    │
│                                       │                  │
│         ┌─────────────────────────────┼─────────────┐    │
│         ▼                             ▼             ▼    │
│   Layout / Layer               Key Renderer      Dial    │
│   (YAML 键位)                  (Pillow→JPEG)     Mapper  │
│         │                             │                  │
│         └─────────────▶ Transport ◀───┘                  │
│                    (CDC-ACM 协议 A)                       │
│                           │                              │
│                    HID 旁路（可选 F13–F24）                 │
└───────────────────────────┼──────────────────────────────┘
                            │ USB
                            ▼
┌──────────────────────────────────────────────────────────┐
│ MK20 B                                                   │
│  GL852G Hub ─┬─ GD32 (QMK HID)  按键/旋钮 → 电脑         │
│              └─ T113 (Linux)    JPEG/JSON ←→ 电脑        │
│                   SPI → 20×GC9107 + NV3007 副屏          │
│                   UART ↔ GD32；codec 麦/喇叭             │
└──────────────────────────────────────────────────────────┘
```

**推荐默认路径（Host-driven）**：Mac 守护进程持有全部业务逻辑；只把 JPEG/JSON 推到 T113；按键优先听 `keyStateChanged`，必要时叠加 HID。

**可选远期路径（On-device）**：在 T113 上替换/旁路 ScreenKey 的 `KeyboardDevice`，本地组帧——复杂度高，留待 P2。

---

## 2. 方案对比与推荐

| 选项 | 做法 | 优点 | 缺点 | 建议 |
|------|------|------|------|------|
| **A. Host JPEG 推流** | 用协议 `SHOW_JPG` + JSON；复用 Demo 帧格式 | 不改固件；箱内已有 Demo 源码 | 带宽/刷新率有限；需拼整幅或按键裁剪策略 | **M0–M2 采用** |
| **B. 改 T113 主题引擎** | overlay/`lunch.sh` 跑自研进程 | 延迟更低、可本地动画 | 要嵌入式调试；设备插入后才能深做 | M3+ 可选 |
| **C. 改 GD32 QMK** | 自定义 HID report / VIA | 旋钮/层更干净 | 官方 QMK 工程未公开；DFU 风险 | **默认不做** |
| **按键：CDC `keyStateChanged`** | 听 JSON `col,row,pressed` | 不需 Input Monitoring | Demo 未证明编码器；依赖 T113 转发 | **主路径** |
| **按键：HID F13–F24** | GD32 报空闲键码，Mac 用 `CGEvent`/快捷键听 | 实现快、与 QMK 兼容 | 需权限；可能与其他工具冲突 | **M0 备份 / 并行验证** |

**推荐组合**：A + CDC 主路径 + HID 备份探针；业务全在 Mac；Python 3.12+ 或 Rust 二选一（见 §6）。

---

## 3. 模块划分

| 模块 | 职责 | 建议接口 |
|------|------|----------|
| `transport` | 打开 CDC 串口、组帧/拆帧、CRC、重连 | `send_jpg(bytes)` `send_json(obj)` `on_event(cb)` |
| `renderer` | 按布局将 Agent 状态绘成 键区 JPEG + 副屏 JPEG | `render(snapshot) -> Image` |
| `session_hub` | 聚合多工具会话状态 | `upsert(session)` `list()` `select(id)` |
| `adapter_ccai` | 安装/接收 Claude hooks | HTTP localhost 或 Unix socket |
| `adapter_codex` | hooks + 可选扫 sessions 目录 | 同上；`notify` 脚本转发 |
| `actions` | accept/reject/send/interrupt/inject | 先钩子代答，再 TTY |
| `ptt` | PTT 键动画 + 回落方案（合成右 Option） | 主路径在 GD32 `KC_RALT` |
| `anim` | 状态→精灵帧、帧调度器（脏键、fps 预算、优先级） | `tick(now) -> dirty_frames` |
| `avatars` | 每工具吉祥物 × 状态表情（程序绘制占位 → 后续美术） | `avatar(tool, state, frame)` |
| `config` | 键位 YAML、槽位、技能文案 | `~/.config/mk20-agent-deck/` |
| `sim` | 无设备：假 Transport + 键事件 CLI | M0 必备 |

协议常量（来自 `/workspace/mk20-research/oss/OpenSourceLicenseDemo_V1.0/`）：

- 帧头 `A1A55A5E`
- `SHOW_JPG=100`，`JSON=101`
- 主动事件 `keyStateChanged`：`col`,`row`,`pressed`

---

## 4. 里程碑

图例：**免设备** = 无 MK20 也能完成并验收；**需设备** = 必须插上 MK20；**部分** = 代码免设备，最终确认需设备。

### M0 — 协议尖刺 + 模拟器（全部免设备，2026-10-17 已开工）

| 任务 ID | 内容 | 设备 | 体量 | 验收（codex） |
|---------|------|------|------|---------------|
| M0.1 | 协议 A 编解码（帧构造/解析、CRC 同 Demo）、JSON 命令、文件分块传输（`saveToFile`/`setFileCRC`） | 免设备 | 小 | pytest 往返/CRC/粘包/分块 |
| M0.2 | Transport 抽象：串口（按 VID:PID `1d6b:0104` 自动发现）+ Fake/Loopback | 免设备 | 小 | 单测；无设备时清晰报错 |
| M0.3 | 设备模拟器：渲染 20 键 + 副屏 + 2 旋钮为 PNG/HTML，接受相同帧，点击生成 `keyStateChanged` | 免设备 | 中 | 截图 + 单测 |
| M0.4 | 渲染器：128×128 键图 = 状态色 + 简单动画（spinner / pulse / shake）+ 占位头像 | 免设备 | 中 | 每状态出 PNG |
| M0.5 | CLI：`deck send-image --key N file`、`deck sim`、`deck demo-states` | 免设备（`send-image` 真机部分需设备） | 小 | 命令可跑 |
| M0.6 | `docs/HARDWARE-TEST.md` 实机检查清单 | 免设备 | 小 | 清单完整 |
| M0.7 | 实机：端口发现、`getInfo`、单键图片、`keyStateChanged`、旋钮事件、右 Option、fps 实测 | **需设备** | 中 | 照片 + 日志 |

### M1 — 状态板

| 任务 | 内容 | 设备 | 验收 |
|------|------|------|------|
| M1.1 | `session_hub` + 状态枚举（含 needs_approval / ptt_listening） | 免设备 | 单测 |
| M1.2 | `adapter_ccai`：Claude hooks（SessionStart / UserPromptSubmit / PreToolUse / PermissionRequest / Notification / Stop / StopFailure）→ hub | 免设备（需真 ccai 会话） | 跑一次 ccai，状态变化可见于模拟器 |
| M1.3 | `adapter_codex`：hooks（需 `/hooks` 信任）+ `notify` → hub | 免设备 | 同上 |
| M1.4 | tmux 绑定：会话 ↔ `tmux` pane（命名 `ag-<tool>-<worktree>`） | 免设备 | 双击能切 pane |
| M1.5 | 8 槽 L1 布局 + 动画调度器（脏键、≤8fps） | 部分 | 模拟器正确；实机 fps 达标 |
| M1.6 | 吉祥物 v1（ccai、codex 两套 × 8 状态） | 免设备 | 截图评审 |

### M2 — 动作与旋钮

| 任务 | 内容 | 设备 | 验收 |
|------|------|------|------|
| M2.1 | 接受/拒绝：钩子代答（PermissionRequest 挂起等键，超时交回终端提示） | 免设备（模拟器按键） | ccai/codex 一键允许/拒绝 |
| M2.2 | 发送/停止/技能注入：`tmux send-keys` | 免设备 | 选中会话收到文本/Enter/Esc |
| M2.3 | 旋钮 → effort（Claude `effort`、Codex `model_reasoning_effort`）+ 副屏 | 部分（旋钮事件需设备） | 档位显示正确；生效时机写明 |
| M2.4 | HID F13–F24 备份通道（守护进程监听） | 部分 | 模拟注入 + 实机 |

### M3 — PTT（右 Option 透传）

| 任务 | 内容 | 设备 | 验收 |
|------|------|------|------|
| M3.1 | 通过 VIA 协议把 PTT 物理键改为 `KC_RALT`（脚本化，可回滚，先备份键位） | **需设备** | 按住键 = 输入法开始听写，松开结束 |
| M3.2 | 回落：守护进程收 `keyStateChanged` → 合成右 Option down/up（keycode 61）+ 防卡键 | 部分（逻辑免设备，体验需设备） | 输入法能被合成事件触发〔未确认〕 |
| M3.3 | PTT 键面波形动画 + 副屏「语音输入中」 | 部分 | 按住期间动画 |
| ~~M3.x 自研 ASR~~ | 移出范围，P2 可选 | — | — |

### M4 — 打磨、分层、设备端动画

| 任务 | 内容 | 设备 | 验收 |
|------|------|------|------|
| M4.1 | L2/L3 页 + 旋钮切页 | 部分 | |
| M4.2 | 注意力：闪烁 + `playAudio` + macOS 通知 | 部分 | |
| M4.3 | **设备端动画缓存调研**：上传 GIF / `frame_%d.png` 主题，寻找远程切页途径 | **需设备** | 结论文档；可行则切换为缓存模式 |
| M4.4 | 背光/休眠策略 | 部分 | |
| M4.5 | launchd、README、键位 YAML | 免设备 | |

### 4.1 动画渲染管线（对应 PRD §13）

```
SessionHub 状态变化 ─▶ AnimScheduler(每键状态机, 帧索引, fps)
                          │  每 tick 仅产出脏键
                          ▼
            SpriteCache(tool, state, frame) ← 首次程序绘制并缓存 PNG
                          │
                          ▼
            Compositor：整画布合成（若设备只收整幅）或单键 JPEG
                          │  预算：全局 ≤8fps，单帧 ≤60KB，停等 ack
                          ▼
                 Transport.send_jpg()  /  Simulator
```
远期：SpriteCache 改为上传到设备（`saveToFile`），运行时只发切换指令（M4.3 验证后）。

## 5. 任务派工约定

- **实现**：`ccai`（Claude Code）按任务 ID 开 worktree。  
- **验收**：`codex` 独立跑测、读日志、对照本 PLAN 验收表。  
- 每任务结尾更新 `CHANGELOG.md` 一行。  
- 不在任务中要求刷 GD32/T113，除非用户另开里程碑。

---

## 6. 技术栈建议

| 层 | 建议 | 理由 |
|----|------|------|
| 语言 | **Python 3.12+** | 最快对齐 Qt Demo 逻辑；Pillow 绘屏；pyserial；钩子脚本同语言 |
| 备选 | Rust（`serialport` + `image`） | 若后期要常驻低占用再迁核心 transport |
| 配置 | YAML（键位）+ JSON Schema | 易手改 |
| IPC | Unix domain socket 或 `127.0.0.1:7362` HTTP | hooks `curl` 友好 |
| 打包 | `uv` / `pipx` | 与私人 Mac 现有习惯对齐时可再定 |
| 测试 | `pytest` + 模拟 Transport | M0 无硬件 |

**不选 Electron/ScreenKey 二开**：开源包实质是固件镜像+小 Demo，不适合当产品底座。

---

## 7. 仓库建议

| 项 | 值 |
|----|-----|
| 本地路径 | `/Users/dev/workspace/mk20-agent-deck` |
| 远端 | Gitea `ssh://git@192.168.88.6:2222/leo/mk20-agent-deck.git`（用户已确认建仓） |
| 分支 | `main` 稳；功能 `feature/*` |
| 箱内镜像文档 | `/workspace/mk20-agent-deck/`（本 PLAN/PRD） |
| 相关调研只读 | `/workspace/mk20-research/`（不进本仓亦可 submodule/文档链接） |

建议目录：

```
mk20-agent-deck/
  README.md
  PRD-mk20-agent-deck.md
  PLAN-mk20-agent-deck.md
  pyproject.toml
  src/mk20_agent_deck/
    transport/
    renderer/
    hub/
    adapters/
    actions/
    sim/
  hooks/ccai/
  hooks/codex/
  configs/layouts/default.yaml
  tests/
```

---

## 8. 风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| ChatGPT 私有集成无法复用 | 无法 1:1 行为 | 已接受；对齐体验而非协议 |
| 批准/发送依赖 TTY 焦点 | 动作键失灵 | 优先钩子代答；tmux 命名约定；失败时副屏报错 |
| `notify` 不含 approval | Codex 等待态漏报 | 用 `PermissionRequest` hook + 扫 TUI 事件；文档标明 |
| 编码器不上报 CDC | 旋钮难做 | HID 旋钮码或消费者音量键临时映射；实机抓包 |
| JPEG 整屏刷新带宽 | 动画卡 | 脏矩形/降频；状态变化才推 |
| USB Hub 复合设备枚举 | 认错串口 | `getInfo` 握手；VID/PID 过滤 |
| Karabiner 等抢键 | HID 备份失效 | 主路径走 CDC |
| 多 worktree 会话发现不准 | 槽位错乱 | 显式「固定槽」+ 手动钉扎 |
| VIA 改键不可用 / 合成右 Option 不被输入法识别 | PTT 失效 | 实机先验证 VIA；回落 B；最坏刷 GD32（需用户另批） |
| 设备只支持整画布 JPEG 且 fps 低 | 动画卡顿 | 脏键 + 降帧 + 优先级；M4.3 设备端缓存 |
| 合成修饰键「卡住」 | 输入异常 | 超时强制 key-up；USB 断开时释放 |

---

## 9. 已确认 / 开放依赖

已确认：tmux、ccai+codex、8 Agent 键、保留 HID 备份、建 Gitea 仓、PTT=右 Option。仍开放：effort 语义映射、头像画风、VIA 改键可行性（需设备）。

---

## 10. 近期下一步（用户插上 MK20 之后）

1. 枚举 `/dev/cu.*`，对 `1d6b:0104` CDC 做 `getInfo`。  
2. 跑 M0.5 单键 JPEG。  
3. 记录一串 `keyStateChanged` 与旋钮是否产生事件。  
4. 再开 M1.2 hooks 接线。

在插上之前：执行 **M0.1–M0.6**（已于 2026-10-17 交由 ccai 实现）。插上后按 `docs/HARDWARE-TEST.md` 做 M0.7。
