# MK20 板载 Agent 能力：建设计划（AGENT-BUILD-PLAN v0.2，2026-10-19）

> 依据：PRD v1.3 §14（2026-10-18 01:18 SGT 批准）+ 后续决定（网络双 profile、provider 可配置默认国内、
> 密钥落 `/data/mk20/secrets/<provider>.key`、mk20-kbd 取代 KD、OTA 可用时不回原厂）。
> 分支：`feature/agent`（自 `feature/kd-replace` @ 19a0057）。v0.1 只是计划；v0.2 写入用户 2026-10-19 的决定（§0.1），A0/A1 开始实现。没有碰设备。
> 本文的约定：A0..A9 = 阶段；**[D]** = 需要真机；**[U]** = 需要用户在场或需要用户决定；**[M]** = 需要 Mac（只读或 Mac 侧小工具）。

---

## 0. 结论先行

- **现状**：设计已批准（PRD §14），底座大部分就绪：ECM 网络、带绝对坐标指针的 HID、A/B 发布与 selftest、mk20-kbd、设置页、
  net/provider 配置格式、VNC 认证探针（已在真机 `AUTH OK`）。**agent 本体（VNC 抓帧、模型客户端、动作执行、MCP、
  急停、密钥页）一行代码都还没有。**
- **架构**：新增一个独立的静态 C 程序 `mk20-agent`（armhf，mbedTLS + zlib + 小 JPEG 编码器 + JSON），由 `mk20d agent` 监管；
  **HID 仍然只由 mk20-kbd 持有**，agent 经 `/run/mk20/kbd.sock` 申请“输入租约”下发动作；**急停在 mk20-kbd 进程内处理**
  （不依赖 agent 存活、不依赖网络）。
- **顺序**：A0 契约/仿真 → A1 kbd 控制口+急停 → A2 VNC 抓帧 → A3 HTTPS/代理 → A4 模型适配 → A5 agent loop+安全 →
  A6 MCP → A7 键面 UI + 密钥页 → A8 发布集成 → A9 真机验收与 soak。A0–A6 基本都能在 box 上用假 VNC/假模型完成。

### 0.1 用户决定（2026-10-19，取代 §9 中对应的待决项）

| # | 决定 | 对计划的影响 |
|---|---|---|
| D1 代理接入（**已被 D1′ 取代**） | **Mac 上不需要任何“键盘驱动”。**板子直接连 Mac 的代理端口：若 mihomo `allow-lan` 在 USB 链路（172.30.250.1）上可达就直连该端口；否则在 Mac 上加一个极小的 launchd TCP 转发，**只绑 USB 接口 172.30.250.1**，转发到 `127.0.0.1:7890`。**代理配置永不修改。**设备侧连通性检查等用户回家再做 | §3.1、A3、§10 |
| D2 出口 | **键盘程序里不做出口 IP 证明。**出口选择由网关/代理负责（mihomo 规则）。A3 删除出口证明，`/run/mk20/egress.json` 取消 | §3.1、A3、A8 health、§6.1 |
| D3 模型 | **只做一个 OpenAI 兼容 `chat/completions` 适配器**（视觉用 `image_url` base64 data URI，支持 tool calls）。provider 只是配置（`base_url`、`model`、key 文件）：Qwen（默认）、GLM、Kimi、OpenAI | §3.5、A4 |
| D4 急停 | agent 页固定急停键 + 任意页长按 K2 1 s（按原计划） | §3.4 |
| D5 聊天/任务入口 | 新增 **HTTP API + 极简网页**（只在 usb0 上服务）：提交任务、查看进度/步骤/截图、暂停、停止。在键盘上打字太累，所以任务从网页/API 下达；**语音任务后续**经 PTT + 现有语音网关 ASR。agent 输入中文用**剪贴板粘贴（Cmd+V）**，剪贴板写入机制待定（见 §3.3） | §3.3、§3.8、A7 |
| D6 MCP | MCP **可以提交任务，但不能启用 agent**；总开关只在键盘上 | §3.7 |
| D7 密钥 | key 通过板载配置网页录入，存 `/data/mk20/secrets/<provider>.key`（root 0600） | §3.8 |
| **D1′ 网络出口（修订，2026-10-19）** | 出口是**用户在板载配置网页上选的 profile**：① “Mac 代理”（填 host:port，走 HTTP CONNECT）；② “USB WiFi dongle”（直连，或再配一个代理）。**可达性由用户保证，键盘只按所选 profile 走。**计划里不再设计转发；launchd 转发只作为可选备注（§3.1） | §3.1、A3、A7 配置页 |
| **D5′ 语音（两条路）** | (a) 给 Mac 侧 agent / Mac 输入法：键盘把自己暴露成 **USB 麦克风**（composite 里加 UAC1/UAC2），macOS 听写/输入法直接用它当输入源；(b) 给键盘 agent：本地录键盘麦克风，发给配置页里配置的 **ASR 模型**。可行性见 §3.9（**UAC 方向有硬件风险**） | §3.9、A1b、A7 |
| **D8 屏幕开放给 agent** | 21 块屏（fb1–20 键屏 + fb21 副屏）经 kbd 控制 API 开放：读当前页/布局、往键屏或副屏画图/写字、创建 agent 自有页面、在这些页面上绑定按键动作。**保留且不可覆盖**：急停键和 agent 控制页。agent 写屏限速，且只能写 agent 自有页面，除非用户授权 | §3.4b、A1、A7 |
| D6 | 再次确认：MCP 可提交任务，不能启用 agent | §3.7 |
| 7（截图提示/遮挡） | 同意默认副屏提示“正在上传画面”，遮挡区域可配置 | §3.2 |

---

## 1. 现有资产清单（Inventory）

| 资产 | 路径 / 提交 | 状态 / 说明 |
|---|---|---|
| PRD §14 硬件级 computer-use（R-CU-1..5、§14.5 验收 8 条、§14.6 待验证） | `PRD-mk20-agent-deck.md` L724–，v1.3 批准记录 L714（59ec906） | 已批准。§14.2 写的是“T113 执行端、模型在云端”。本计划把 loop 放到 T113，并加 MCP 接口（按后续决定） |
| Spike 报告（gadget、EP 预算、VNC、GD32） | `deck/NOTES-computer-use-spike.md`（86547b1） | 结论已被后续工作推进：stock 内核没有 ECM、EP 不够用 → 已靠 `usb_f_ecm.ko` 和 ep3→int 内核补丁解决（`NOTES-stock-kernel-patch.md`） |
| USB 复合 gadget = ECM + HID + ADB | `deck/firmware/release/share/gadget.sh`（19a0057），`lib/usb_f_ecm.ko` | 量产中（fw 1.1.x–1.2.1）。设备 172.30.250.2/24，Mac 172.30.250.1，回落 10.20.0.x / 169.254.7.x |
| HID report 描述符 | gadget.sh L390：report 1 = 键盘（8 修饰位 + 6 键，usage 0..0x65），report 2 = **绝对指针**（3 键，X/Y 0..32767），report_length 9 | **缺**滚轮/横向滚轮、Consumer（媒体键）。scroll 动作需要改描述符 → 要上真机 |
| HID 写入端（含绝对指针） | `deck/kbd/hid.c` `hid_pointer_abs()`（8fdf10d 引入，9b08871 stale fd parking） | 已实现、有测试，**还没有调用方**。hidg0 rebind（约 13 s 或约 74 s）用 parking 处理；mk20-kbd 死掉后要等下次 rebind 或重启才能恢复（kdr-r1 #3 未解决） |
| mk20-kbd（KD 替代） | `deck/kbd/*`（19a0057），`KD-REPLACE-PLAN.md` | 持有 ttyS1/ttyGS0/fb1–21/hidg0。有 `--status /run/mk20/kbd.status`，**没有本地控制 socket**，也没有本地页面/组件（cmd201 widget 是计划中的） |
| 板载 router（1.2.x） | `deck/router/mk20_router.c`，`release/bin/mk20-router` | PTT 右 Alt；未来并入 mk20-kbd |
| VNC 认证探针 | `deck/m2-scripts/vnc/vnc_authcheck.c` + `des.c`（ffc7114） | RFB 3.8 + VNC Auth（type 2），从文件读口令，不打印。真机已验证 `AUTH OK`（1.0.4，见 `NOTES-crash-20261018-and-tf-backup.md` L313）。可作为 rfb 模块的起点 |
| VNC 口令 | 设备 `/data/mk20/secrets/vnc.pass`（root 0600）；box env `MK20_VNC_PASSWORD` | 已就位。`deck/tools/backup-exclude.txt` 已把 `/data/mk20/secrets` 排除在备份之外 |
| 设置页（LS） | `deck/mk20deck/settings.py`、`render/settings.py`、`controller.py`；`sim/SETTINGS.md`；cases `deck/mk20sim/cases/settings.json`（6f570bd） | 已定稿：网络 / 模型商 / 模型 / 连接测试 / 设备信息。**页面由 Mac 端 Python 守护进程渲染**（T113 上没有 Python） |
| net/provider 配置写入 | `deck/mk20deck/agentcfg.py` + `tests/test_agentcfg.py`（6f3a097） | 格式已定（见 §6.2）：原子写，目录 0700、文件 0600，密钥只按路径引用。**缺口：`AgentConfigApplier` 默认写本机 `/data/mk20/agent`，但它跑在 Mac 上，没有任何通道把配置送到 T113**（Mac 上大概率报“写配置失败”）。连接测试也是从 Mac 发起的，不代表设备侧的连通性 |
| 发布 / 守护进程 | `release/sbin/mk20d`、`share/mk20lib.sh`（FEATURE_* 开关、FAT 标志 `mk20-no-*`、`MK20_*` 环境变量）、`RELEASE.md`、`tools/mk20-install.sh`、`tools/mk20-build-bins.sh` | A/B + probation/selftest/confirm、health.json、maint 日志轮转。agent 照 router/kbd 的模式接入 |
| box 交叉编译 | `/usr/bin/arm-linux-gnueabihf-gcc` 14.2 | 可用。glibc 2.23 的设备上一律 `-static`（mk20-kbd 已经这么做） |
| 仿真器 | `deck/mk20sim/*`（harness、JSON cases、web 测试面板、延迟注入） | 可扩展 agent 页面和急停 case |
| 没有的东西 | — | VNC 帧解码、缩放/JPEG 编码、HTTPS/TLS 客户端、模型适配、动作 schema、仲裁/急停、MCP、密钥录入页、时间同步、出口校验 |

---

## 2. 目标架构（T113 进程划分）

```
                      ┌──────────── Mac ────────────┐
  cloud model ◀─HTTPS─┤ mihomo :7890 (只读，不改配置)  │   Screen Sharing :5900 (VNC)
                      │ mk20deck (Python): 设置页渲染、  │        ▲
                      │  配置下发、launchd 转发(可选)   │        │ RFB over ECM
                      └──────▲────────────▲───────────┘        │
                 HTTP CONNECT│            │HTTP(配置/密钥/MCP)   │
 ═════════ USB ECM 172.30.250.0/24 ═══════════════════════════════
 T113 ┌──────────────────────── mk20-agent (新，static C) ─────────────────────────┐
      │ cfg  rfb(VNC client+ZRLE→流式缩放)  img(JPEG)  net(TCP/CONNECT/mbedTLS)       │
      │ llm(OpenAI 兼容适配器×1)  loop(观察→规划→执行)  policy(风险/预算/确认)         │
      │ mcp(Streamable HTTP, :8765@usb0)  web(密钥页 :8780@usb0)  log(脱敏)          │
      └──────────────┬───────────────────────────────────────────────────────────┘
                     │ /run/mk20/kbd.sock (unix, 0600, 帧化 JSON-lines 或 TLV)
      ┌──────────────▼──────── mk20-kbd (已有，扩展) ────────────────────────────────┐
      │ input lease + 仲裁 + 急停(进程内) → hid.c → /dev/hidg0 (kbd + abs ptr)        │
      │ 键面/副屏：agent 状态组件、agent 页、确认页 (≥300 ms 才接受确认)               │
      └──────────────────────────────────────────────────────────────────────────────┘
      mk20d: `mk20d agent` 监管 (flock, backoff), health.json "agent", selftest INFO
```

**为什么这样拆：**
1. **hidg0 只能有一个持有者。**有了 Tina 上 `cdev_init` 的怪癖和 stale fd parking，两个进程各开一份 hidg0 必出事。
   所以 agent 不碰 hidg0，只向 mk20-kbd 发动作。这样“禁止双路输入”（R-CU-2）也就自然成立。
2. **急停必须独立于 agent。**急停键事件本来就在 mk20-kbd 里（GD32 → ttyS1）。kbd 在进程内撤销租约、发 all-up、拒绝后续动作，
   然后再通知 agent。即使 agent 卡死、断网或正在 TLS 握手，也能做到 ≤100 ms 生效。
3. **mk20-agent 崩了不能影响按键、屏幕和 OTA。**它是独立进程，崩了由 mk20d 重启；`oom_score_adj=500`，内存不够时先杀它。
4. MCP 和 loop 放在同一个进程里，共享同一个租约、预算和确认逻辑，避免出现两套安全规则。
   密钥页也放进同一个二进制（`--web` 线程），体积小，省一个进程的 RSS。

**实现语言/依赖（全部 static，vendored 到 `deck/agent/vendor/`，许可证要记录）：**
- TLS：**mbedTLS 3.6 LTS**（推荐）。不选设备上的 libssl 1.1：动态链接 glibc 2.23 的 sysroot 很麻烦，1.1 已经 EOL，
  还会和固件里的库版本绑死。mbedTLS 静态体积约 300–400 KB，单连接 RAM 约 60–120 KB。
  CA：随发布包带 `share/ca-bundle.pem`（Mozilla 精简版，或只带所选 provider 需要的根证书），走 MANIFEST 校验。
- zlib（ZRLE 需要）：zlib 1.3 或 miniz。JPEG 编码：`stb_image_write.h`（已有 stb 家族），或 TooJpeg（更快、内存更小）。
- JSON：cJSON（MIT）或 jsmn；请求体用自写的流式 writer 拼接，避免整份 DOM。
- HTTP/1.1 客户端和服务端自写：只需要 Content-Length、chunked 和 SSE 读取；服务端只服务 usb0，单线程 poll，连接数上限 4。

---

## 3. 关键设计

### 3.1 网络与代理

- **Profile `mac_proxy`（默认）**：所有模型流量都走 `HTTP CONNECT <host>:443` 到 `net.json.proxy.url`（`http://172.30.250.1:<port>`，
  回落顺序 10.20.0.1 → 169.254.7.1）。分流交给 Mac 上 mihomo 的规则：国内 provider 走 DIRECT 规则，国外 provider 走 AI 组。
  设备侧**绝不直连**：`net.json` 缺失或解析失败时一律不发请求（fail-closed）。
- **出口选择归网关/代理（D2）**：设备侧不做出口 IP 证明、不读 mihomo API。国外 provider 走 112 US 出口这件事由 Mac 上
  mihomo 的规则保证（AI 组），**任何情况下都不改代理配置**。设备只保证“所有模型流量只经代理端口，绝不直连”。
- **网络出口 profile（D1′，取代下面的 D1 条目）**：配置网页上二选一：`mac_proxy{host,port}`（HTTP CONNECT），或
  `usb_wifi{mode: direct | proxy, proxy:{host,port}}`。`net.json` 按 profile 写；**可达性由用户保证**，设备不做出口探测，
  只在“连接测试”时报告错误类别。`usb_wifi + direct` 是用户的显式选择，因此允许直连（与旧的“foreign 不许 direct”规则相比是放宽，
  只在用户在配置页选了这个 profile 时生效）。没有 `net.json` 或解析失败 → 不发请求（fail-closed 不变）。
- （可选备注，非计划项）**代理接入（D1，旧）**：Mac 上不装驱动。若 mihomo `allow-lan` 且在 172.30.250.1 可达，`net.json.proxy.url` 直接指向该端口；
  否则在 Mac 上加一个极小的 **launchd TCP 转发**（plist + 几十行的转发程序），**只绑 172.30.250.1**，转发
  `172.30.250.1:17891 → 127.0.0.1:7890`。不改 mihomo 配置。哪种方式可行，等用户回家时在设备上实测 [D][M]。
- **Profile `board_wifi`（后期）**：Wi-Fi dongle + 板载代理。国内直连，国外必须经板载代理，否则 `blocked`，与 agentcfg 的现有规则一致。
  放在 A10 之后，本计划不展开。
- **时间同步（TLS 证书校验依赖正确时钟）**：T113 有没有可靠 RTC 待确认。方案：mk20d 或 agent 启动时，向 Mac 侧
  （`mk20deck` 新增 `GET /time`，或直接读代理返回的 HTTP `Date` 头）取时间，偏差超过 120 s 就 `settimeofday`，并记录日志。
  时钟没校准就**不发 TLS 请求**（不能为了绕过时钟问题而跳过证书校验）。

### 3.2 屏幕采集（VNC → 模型输入）

- 从 `vnc_authcheck.c` 演进成 `rfb.c`：RFB 3.8、VNC Auth（type 2）。口令从 `vnc.pass` 读到栈上，用完立刻 `memset`。
  连接只发往 ECM 网段的 Mac 地址（硬编码白名单），不允许经代理或 Wi-Fi。
- SetPixelFormat：32bpp true colour。编码优先级：ZRLE（16）> Hextile（5）> Raw（0）；加上伪编码 Cursor（-239）和 DesktopSize（-223）。
- **按需抓帧**：每次观察发一个 `FramebufferUpdateRequest(incremental=0)`，不持续推流。抓完保持连接空闲，
  idle 30 s 后断开（Screen Sharing 菜单栏会提示“有人在观察”）。
- **内存要点：不保存全分辨率帧。**Retina 屏 2560×1664×4 B 约 17 MB，超出预算。解码每个矩形时直接按行累加到目标缩放缓冲：
  长边 1280 → 1280×832 RGB888 约 3.2 MB，外加每行累加器约 15 KB。ZRLE 解码按 64×64 tile 流式进行，峰值很小。
- 输出：JPEG q≈70（约 120–250 KB），再 base64（约 ×1.33），作为 `image_url` 的 data URI。同时记下 `scale_x/scale_y`、
  framebuffer 尺寸以及光标位置（如果服务端发了光标）。
- 隐私：开始上传前副屏显示“正在上传画面”（R-CU-4）。可配置遮挡区域：在缩放缓冲上直接涂黑，配置写在 `agent.json`。
- 多显示器/Retina：一期只支持**主显示器**。A2 时实测 Screen Sharing 的 framebuffer 是只含主屏还是拼接的整个桌面，再定坐标映射。

### 3.3 动作 schema 与执行（HID 主，VNC 备）

统一的内部动作（模型 tool call 和 MCP 工具都翻译成这一套）：

| action | 参数 | HID 实现（mk20-kbd） | VNC 备用 |
|---|---|---|---|
| `move` | x,y（截图坐标） | report 2 绝对坐标 | PointerEvent |
| `click` / `double_click` / `right_click` | x,y,button | move → 按下 → 50 ms → 抬起；双击间隔 80 ms | PointerEvent |
| `drag` | x1,y1,x2,y2 | 按下 → 分步移动（≤20 步）→ 抬起 | PointerEvent |
| `scroll` | x,y,dy,dx | **需要给描述符加 Wheel/AC Pan**（A1 改 gadget.sh 描述符 + report_length）；没加之前走 VNC 按钮 4/5 | 按钮 4/5/6/7 |
| `type` | text（≤256 字符/次） | US 布局 ASCII → keycode 序列，每键 8–15 ms | KeyEvent keysym |
| `key` | chord，如 `cmd+space` | 修饰位 + 键码，抬起后全部释放 | KeyEvent |
| `wait` | ms（≤5000） | — | — |
| `screenshot` | — | （观察，不是动作） | — |

- **坐标映射**：截图坐标 → 主屏逻辑坐标 → HID 的 0..32767。macOS 把绝对指针映射到主显示器。A5 加一个
  `calibrate` 步骤：依次移到四角和中心，通过 VNC 光标伪编码或截图找光标，拟合仿射映射，存进 `/data/mk20/agent/calib.json`。
  验收误差 ≤2 逻辑像素（§14.5-3）。
- **非 ASCII 文本**（中文，D5）：HID 打不出来，改用**剪贴板粘贴**：先把文本放进 Mac 剪贴板，再经 HID 发 `cmd+v`。
  只规划 VNC 剪贴板路线（见 A2），不加 Mac helper。经典 ClientCutText 仅 Latin-1；中文需测 Extended Clipboard。
  一期 `type` 仍只接受 ASCII，非 ASCII 走新动作 `paste{text}`，协议支持未确认时返回 `unsupported`；不读回用户剪贴板。
- **单通道仲裁**：mk20-kbd 维护 `active_input ∈ {hid, vnc}`。VNC 输入由 agent 自己发，但必须先向 kbd 拿到 `input=vnc` 租约，
  kbd 也会对 HID 做 all-up。切换规则照 R-CU-2：连续 2 次失败 → 冻结 → all-up → 切到 VNC；连续 3 次健康 → 在空闲点切回。
  同一个动作只在一个通道发一次，**不会到另一个通道重试**。
- **锁屏**：VNC 帧里检测到登录窗口（启发式规则加模型判断）时，只允许 HID，并且拒绝一切 `type`（不输密码）。

### 3.4 mk20-kbd 控制口 `/run/mk20/kbd.sock`（A1）

- unix stream，0600，同 uid / root 校验。当前协议为每行一个 JSON（含换行 ≤1024 B；像素二进制 framing 留给 A7）。同时最多 2 个客户端；通过同 uid / root 校验后权限相同，租约和 agent 页分别绑定连接（当前没有只读第二客户端角色）。
- 请求：`lease.acquire{owner,ttl_ms}` → token；`lease.renew`；`lease.release`；`act{token,seq,op,...}`；
  `ui.agent{state,input,summary,progress}`；`confirm.request{id,cid,summary,risk,timeout_ms}` → 异步返回 `confirm.result{cid,ok,reason}`。
- 事件推送：`estop`、`confirm.result`、`hid.health`、`key{...}`（只在 agent 页激活时推送）。
- **租约规则**：TTL 默认 3 s，必须续约，超时就 all-up 并撤销租约。急停、按键进入 agent 关闭状态、主机 PTT 按下时也会撤销
  （人手优先，用户一碰键盘 agent 就让开）。
- **急停键**：放在 agent 页（L5）的固定键位，**另外**任意页长按 K2 1 s 也算急停（具体键位见 §9 决策 4）。
  触发后：撤销租约 → all-up（键盘和指针按钮）→ 状态写成 `stopped` → 推送给 agent → agent 取消正在进行的 HTTP 请求。
  恢复必须显式重新启用（按 agent 页的“允许”键）。
- 确认页：显示摘要，展示满 ≥300 ms 后才接受“确认”键；“取消”键和超时（默认 30 s）都按拒绝处理。

### 3.4b 屏幕 API（D8，A1 定协议 / A7 实现渲染）

在 kbd.sock 上增加 `screen.*`（同一 uid 检查与 2 客户端上限；**不需要输入租约，但需要 agent 已被键盘“允许”**）：
- `screen.layout` → 21 块屏的编号、尺寸（键屏 128×128，副屏 428×142）、总线、当前页 id、每页属主（`system` / `agent` / `user`）。
- `screen.page.create{name}` → `page_id`（属主 agent，上限 8 页）；`screen.page.delete`、`screen.page.show{page_id}`（只能切到 agent 自有页或 agent 控制页）。
- `screen.draw{page_id, fb, x, y, w, h, fmt: rgb565|jpeg, bytes}`（当前只校验元数据并返回 rendered:false，像素 binary framing / 渲染在 A7）、`screen.text{page_id, fb, text, size, fg, bg}`（文字由 kbd 内置字体渲染）。
- `screen.bind{page_id, key(row,col), action}`：agent 页上的按键按下时，kbd 推送事件 `{"ev":"key","page":..,"key":..}` 给 agent（只在该页显示时）。
- **保留、不可覆盖**：急停键位（agent 页固定键）和 agent 控制页（允许/禁止、急停、确认/取消）。对它们的 draw/bind/delete 一律 `reserved`。
  K2 长按急停在任何页面都生效。
- **权限范围**：默认只能写 agent 自有页；写 system/user 页需要用户在键盘上授权（`grant{scope: "all_pages", ttl}`，键面确认，上电失效）。
- **限速**：每屏 ≤10 帧/s，全体 ≤30 次写/s，单条 ≤64 KB（base64 前）；超限 `rate_limited`，不排队。写屏不得让按键→HID 延迟 P95 恶化 >5 ms。
- 急停或撤销“允许”时：agent 自有页保留但切回 system 页，agent 的 bind 全部失效。
- 测试（A1 协议单测 + A7 渲染/sim cases）：保留页拒绝、限速、授权过期、未允许时拒绝、页切换后 key 事件只发给属主。

### 3.5 模型客户端与 provider 适配

- **只有一个适配器（D3）**：OpenAI 兼容的 `POST {base_url}/chat/completions`，带 `tools`（function calling）和
  `image_url`（base64 data URI）。provider 只是配置：`base_url`、`model`、`api_key_file`，外加少量可选旋钮
  （`coord_mode`: `pixel` | `norm1000`，`max_image_px`，`supports_tool_choice`），不写 per-provider 代码。
- 预置 provider：**Qwen（DashScope 兼容模式，默认）**、GLM、Kimi、OpenAI。Anthropic 原生 computer-use 不做。
  **具体型号要等 A4 用真 key 实测后再定**（视觉 + tool call 都得可用），本文不写死。
- 请求预算：每步只带**当前一张截图**，历史截图换成文字摘要（“上一步点击了…，结果…”）；system prompt 和 tool 定义每次都带上。
  连接超时 10 s，整个请求 60 s，响应体上限 256 KB。429/5xx 指数退避，最多 2 次；其余错误直接失败。
- **密钥**：每次请求前读 `/data/mk20/secrets/<provider>.key`（0600），放进 `Authorization` 头，发完 `memset`。
  文件权限不是 0600 或属主不是 root 就拒用。密钥绝不写进 `provider.json`、日志、MCP 响应或 health.json。

### 3.6 Agent loop 与安全规则

状态机：`disabled`（上电默认）→ `idle` → `observing` → `planning` → `acting` ↔ `awaiting_confirm` → `done | stopped | error`。

- **总开关**：`FEATURE_AGENT`（发布级，默认 0）+ FAT 标志 `mk20-no-agent` + `MK20_AGENT=0`。运行级的“agent 允许”**每次上电都是关闭的**，
  只能在键面 agent 页手动打开（MCP 不能远程打开；见 §9 决策 6）。
- **预算**：每个任务最多 30 步（可配，上限 100），时长 10 min，模型调用 40 次，输入字符 256/次；
  单个动作执行超时 2 s；连续 3 步截图哈希不变就判定“无进展”，暂停并请示用户。
- **高风险动作必须确认**：本地规则加模型自报 `risk` 字段，二者**取并集**。命中下面任一条就必须按确认键：
  - `key` 命中危险组合黑名单（可配）：cmd+q、cmd+w、cmd+delete、cmd+opt+esc、ctrl+cmd+q、cmd+shift+delete、return/enter（在发送类界面）等。
  - `type` 内容含换行加终端关键字，或含 `sudo`、`rm`、`curl|sh` 等。
  - 点击目标的 OCR 或模型描述含“删除 / 发送 / 付款 / 购买 / 提交 / 确认支付 / Delete / Send / Pay / Submit”。
  - 任务首个动作，以及切换到新 App 时（可配）。
- **永不做**：输入密码、解锁锁屏、修改系统安全或隐私设置、操作 Screen Sharing / mihomo / 代理设置（窗口标题黑名单），
  命中直接拒绝，**不提供确认通道**。
- **日志**：`/data/mk20/logs/agent.log`，maint 负责轮转，上限 1 MB × 3。只记步骤号、动作类型、坐标、模型 id、延迟、token 用量。
  `type` 的文本只记长度和哈希，截图默认不落盘（调试开关打开时写 tmpfs，上限 5 张）。统一过一遍脱敏函数：
  `Authorization`、`sk-…`、`vnc.pass` 内容、任何 secrets 文件内容都不会出现在日志里。CI 用测试 grep 守住（§14.5-8）。

### 3.7 MCP 接口（A6）

- **Streamable HTTP**（MCP 2025-06-18 规范）：`POST /mcp` 走 JSON-RPC，按需 SSE。**只绑 usb0 的 172.30.250.2:8765**（`SO_BINDTODEVICE`），
  不监听 Wi-Fi，也不监听通配地址。
- 认证：`Authorization: Bearer <token>`，token 存在 `/data/mk20/secrets/mcp.token`（0600，首次启动随机生成）。
  Mac 侧通过 adb 或密钥页的配对流程取得 token（不打印到日志）。
- 工具：`status`、`screenshot`（返回 JPEG image content 和缩放信息）、`click`、`double_click`、`move`、`drag`、`scroll`、`type_text`、
  `key`、`estop`、`run_task{goal}`、`task_status`、`cancel_task`。
- **MCP 不能启用 agent（D6）**：没有任何 MCP 工具能打开“agent 允许”；总开关只在键盘上。MCP 可以 `run_task` 提交任务
  （进入与网页相同的任务队列），在未允许时返回 `disabled`。
- **MCP 的动作和板载 loop 走同一个租约、预算、风险规则和确认流程**。外部 Claude/Codex 通过 MCP 驱动时，T113 只负责执行和安全闸，
  不调用模型。急停对 MCP 同样生效；急停后 MCP 一律返回 `stopped`，直到在键面重新允许。

### 3.8 键面 UI 与配置/密钥通道（A7）

- mk20-kbd 本地组件（cmd201 widget 或内置渲染）：副屏右半区显示 agent 状态行（状态、通道、最近动作、步数/预算）。
  另有 **agent 页 L5**：允许/禁止、急停、确认/取消、当前任务、provider 和代理连通状态。
  Mac 端设置页（Python）新增“智能体”入口和状态镜像。
- **配置下发**：设置页“应用”后，`AgentConfigApplier` 改成通过 HTTP `PUT http://172.30.250.2:8780/config/{net,provider}`
  （带 token）推给设备，由设备侧原子写 `/data/mk20/agent/*.json`（0700/0600）。设备侧做 schema 校验：拒绝未知字段，
  拒绝 base_url 不在 provider 表里的写入，**也拒绝 `routes.foreign=direct`**。现在“写本机 /data”的路径保留给仿真和测试用。
  连接测试也改成调用设备的 `POST /test`，从设备经代理实测，返回延迟和错误类别。
- **密钥录入页**：设备侧 `GET /` 返回一个 ~10 KB 的静态 HTML 表单，表单 `POST /secret/{provider}`：
  - **配对码**：副屏显示 6 位一次性码，5 分钟有效，用来防同机其他进程或 CSRF 偷写。
  - 只写不读：响应只回“已设置 · 长度 N”，**没有任何读出接口**。
  - 写入方式：`O_CREAT|O_EXCL` 临时文件 0600 → fsync → rename → fsync 目录。
  - 支持删除；同一个通道也用来设 `vnc.pass`。
  - SETTINGS.md 里的占位地址 `http://172.30.250.1:8780` 改为设备地址 `172.30.250.2:8780`。
- **聊天/任务入口（D5）**：同一个 `--web` 服务（只绑 usb0 172.30.250.2:8780，配对后带 token/cookie）提供：
  - API：`POST /api/tasks{goal}` → id；`GET /api/tasks/{id}`（状态、步骤列表、每步动作摘要）；`GET /api/tasks/{id}/steps/{n}/shot.jpg`
    （最近 N 张缩略截图，只在 tmpfs 内存环形缓冲，上限 5 张，不落盘）；`POST /api/tasks/{id}/pause|resume|stop`；`GET /api/events`（SSE 进度）。
  - 极简网页 `GET /chat`：一个输入框 + 进度列表 + 截图 + 暂停/停止按钮（~15 KB 静态 HTML/JS，无外部依赖）。
  - 规则：agent 未在键盘上“允许”时，任务可排队但不执行（显示“等待键盘允许”）；网页的“停止”走与急停相同的 kbd 撤销路径；
    网页/API 不能打开总开关。同一时刻只跑一个任务。
  - 语音任务（后续）：PTT 录音 → 现有语音网关 ASR → 文本作为 `goal` 提交到同一 API。

---

### 3.9 语音（D5′）与 UAC 可行性（box 上只读调研，2026-10-19，未碰设备）

**(b) 键盘 agent 本地录音 → 配置的 ASR 模型**：可行性高。
- 内核 `config-221`（`/workspace/kpatch/live-221/config-221`，即量产 5.4.61 内核配置）：`CONFIG_SND=y`、`CONFIG_SND_PCM=y`、
  `CONFIG_SND_SUNXI_SOC=y`、`CONFIG_SND_SUNXI_SOC_SUN8IW20_CODEC=y`（T113 片内 codec，ADC/麦克风输入）、`SIMPLE_CARD`、`DAUDIO`；
  `SND_SOC_DMIC`/`SUNXI_SOC_DMIC` 未开（若麦克风是 PDM 数字麦则不可用，需真机确认麦克风接在 codec 的模拟 MIC 输入上）。
- stock KD 的 `ConfigManager::updateUSBtype()` 会 `killall arecord/aplay`，说明 stock rootfs 有 `arecord`、且曾经录过音。
- 待真机确认 [D]：`/proc/asound/cards`、`arecord -l`、增益/mixer 名称；PTT 按下录音、松开结束，16 kHz mono PCM → ASR（OpenAI 兼容
  `/audio/transcriptions` 或 provider 自定义，配置项 `asr{base_url, model, key_file}`）。

**(a) 键盘当 USB 麦克风给 Mac**：**有硬件风险，可能不可行。**
- 内核里 `CONFIG_USB_F_UAC1=y`、`CONFIG_USB_CONFIGFS_F_UAC1=y`、`CONFIG_USB_U_AUDIO=y`（内建）；**`F_UAC2` 和 `UAC1_LEGACY` 未开**（UAC2 要换内核）。
- 但 stock `/data/setusbconfig`（`/workspace/mk20-research/fw/rootfs/bin/setusbconfig` L128–140）的 uac1 模式写着
  **“lack of ISO(IN) endpoint, disable PC capture”** 并设 `p_chmask=0`——即 sunxi UDC 上**没有可用的 ISO IN 端点**，
  恰好就是“设备→主机”的麦克风方向。加上 composite 已经为 ECM+HID+ADB 打过 ep3→int 内核补丁，EP 预算本来就紧。
- 结论：UAC 麦克风要么需要再改 UDC 端点分配（内核补丁、要真机验证，风险同 ep3 补丁），要么放弃 UAC，改用
  **Mac 侧小助手**（经 ECM 收 PCM，注入虚拟音频设备，如 BlackHole 类驱动）——但那就回到“Mac 上要装东西”。
- 下一步（box，只读）：读 sunxi UDC 驱动的端点表（`kallsyms`/源码里的 `ep_fifo`/ISO 能力）确认 ISO IN 是否真的没有、能否把某个
  bulk/int 端点改成 ISO；在此之前 (a) 标为 **待定，需要用户在“内核补丁”和“Mac 助手”之间取舍**。

## 4. 资源预算（T113：2×A7，约 57 MB 可用）

| 项 | 常驻 | 峰值 |
|---|---|---|
| mk20-agent 代码 + 静态库 | ~2 MB | |
| 缩放帧缓冲 1280×832×3 | 3.2 MB（抓帧时才分配，用完释放） | 3.2 MB |
| ZRLE zlib 窗口 + tile | 0.3 MB | 0.5 MB |
| JPEG 输出 + base64 | | 0.6 MB |
| 请求体（prompt + 1 图 + 历史摘要） | | 1.2 MB |
| TLS（1 连接）+ HTTP 缓冲 | 0.2 MB | 0.5 MB |
| MCP/web 连接（≤4） | 0.1 MB | 0.4 MB |
| **合计目标** | **≤8 MB RSS** | **≤16 MB**（硬上限 24 MB：`setrlimit RLIMIT_AS`，超了就单步失败，进程不崩） |

- CPU：一次抓帧加缩放加 JPEG 估计 300–600 ms 单核突发（A2 实测）。agent 以 `nice 10` 运行，mk20-kbd 的按键线程优先级更高。
  抓帧期间如果 mk20-kbd 的按键到 HID 延迟 P95 超过 +5 ms，就降低 JPEG 分辨率或分片让出 CPU（§14 R-CU-5：显示优先）。
- 存储：二进制约 1.5 MB 进 release，`/data` 2.4 GB 富余。

---

## 5. 分阶段计划

每个阶段的标准动作都一样：box 上单测和 e2e 通过、codex 复审 GO（走 112 代理前置检查）、更新 RELEASE.md，然后才进下一阶段。

### A0 契约与仿真底座（box，约 0.5–1 天）
- 建 `deck/agent/` 骨架：Makefile 同时出 host 和 armhf static 两种构建；vendor mbedTLS、zlib、cJSON、JPEG 编码器，记录许可证。
- 写 `deck/agent/PROTOCOL.md`：定义 kbd.sock 消息、动作 schema、MCP 工具 schema、`agent.json` 的预算和风险配置。
- **假环境**（`deck/agent/tests/fakes/`，Python）：
  - `fake_vnc.py`：RFB 3.8 服务端，type-2 认证，支持 Raw/ZRLE，内容是一张合成的“桌面”PNG，可以记录 PointerEvent/KeyEvent。
  - `fake_model.py`：OpenAI 兼容 HTTP 服务，按剧本返回 tool call，并记录请求，用来断言请求里没有密钥。
  - `fake_proxy.py`：HTTP CONNECT 代理，可注入失败、延迟和“直连尝试”检测。
  - `fake_kbd.py`：kbd.sock 服务端，或直接在 e2e 里跑真 mk20-kbd + FIFO 当 hidg0（已有现成模式）。
- 验收：在 box 上 `make -C deck/agent test` 跑通空骨架，假服务器全部起得来。

### A1 mk20-kbd 控制口、租约、急停、描述符（box 为主 + [D] 1 次）
- kbd：实现 `kbd.sock`、租约（TTL 和撤销）、`act` 映射到 `hid_*`、急停（进程内）、`confirm.request` 和 ≥300 ms 规则、`ui.agent` 状态缓存。
- gadget.sh：指针 report 2 增加 Wheel（0x38）和 AC Pan（0x0238），report_length 仍为 9（指针 1+7）。同步更新 `hid.h` 的 `HID_PTR_LEN` 和测试。
- 测试：租约过期会 all-up、急停 ≤100 ms（e2e 里计时）、急停后 act 被拒、PTT 按下抢占、确认 <300 ms 无效、hidg0 rebind 期间动作排队或失败但不 spin。
- **[D]**：装到真机（kbd 本来就要真机验证），在 Mac 上 `ioreg` 看描述符，并验证滚轮。这一步属于 **gadget 变更**，按惯例走 probation，自检不过就回滚。
- 验收：box 测试全过；真机能滚动；kbd 原有验收不退化。

- **A1 状态（2026-10-19，本 worktree）**：租约/act/急停/人手抢占已实现；补齐 confirm（shown 后 ≥300 ms、超时/断连/禁用取消）、ui.agent 缓存/dirty、D8 screen 权限/保留键/滑动限速/页属主事件。
  发布开关 FEATURE_AGENT_CTL / FEATURE_HID_SCROLL / FEATURE_K2_ESTOP / FEATURE_K2_RELEASE（均默认 0）已接入，release 不传 --ctl-allow；boot 把解析值通过 MK20_* 传入新 supervisor shell，与 gadget.env / scroll 描述符一致，启动后改配置不影响重启参数。
  K2 默认先用 1500 ms 内三击，观察到 release 后启用 1 s hold；已测出 release 能力的固件可设 FEATURE_K2_RELEASE=1 / --k2-release，使冷启动首次 hold 就能由 poll 定时触发；真机 press/release 尚待测。
  撤销租约/急停先丢弃未发送的 pointer/scroll，再发 neutral；断连释放连接的 agent 页（禁用/急停保留页）；单调毫秒时基和期限统一为 64 位，覆盖 armhf 长 uptime。
  b46c562 的完整 Linux suite 已全绿：24 sim，kbd 3829 checks ×2（ASan/UBSan、TSan），real-binary e2e（含 stalled-host 急停），45 agent，58 tools，546 pytest；shellcheck 通过。
  R3 修复：每条 socket 请求（同批次及跨客户端）验证前和 dispatch 后均用新单调时间跑 ctl/K2 safety tick，覆盖 allup 等有界等待；补充 fake-clock socket/HID 背压回归。本轮改动仍需 Linux suite / 静态 armhf 复验；真机描述符、滚轮、K2 press/release、probation/rollback 验收及 release 二进制重建/source-match/MANIFEST 前置条件仍未完成。详见 kbd/CTL-PROTOCOL.md。
  kimi r3 follow-up：gd32io.c knob-init 的 32 位毫秒时基已改 int64_t；K2 timer-only e2e 的 [0.95,1.10] s 窗口仍无法区分 K2 timeout bound 与 100 ms HID base tick，后续用关闭 HID 的 250 ms base tick 或 poll-timeout 单测加强区分。
- **A1b 屏幕 API（D8）**：协议/规则引擎已实现，draw/text 返回 rendered:false；A7 实现字体/像素 binary framing 和渲染、固定急停键视觉、物理允许/确认 UI。

### A2 VNC 抓帧（box + [D] 测量）
- `rfb.c`（ZRLE/Hextile/Raw + Cursor/DesktopSize）、`scale.c`（流式按行累加）、`img.c`（JPEG 编码）；先做 CLI `mk20-agent --snap out.jpg`。
- box：对 fake_vnc 做逐像素比对（容差内），并加 RSS 上限测试（`/usr/bin/time -v`，或 qemu-arm 跑 armhf 二进制）。
- **[D][M]**：真机对 Mac Screen Sharing 实测 P95 延迟、峰值 RSS、CPU，以及 Retina/多屏的 framebuffer 形态。只读操作，不需要用户在场，但 Mac 必须开着且已登录。
- 验收：§14.5-2（P95 ≤800 ms、峰值 ≤16 MB），数据记录在 `NOTES-agent-a2.md`。
- **A2 状态（2026-10-19，本 worktree）**：host RFB 3.3/3.7/3.8、DES/None、ZRLE/Hextile/Raw、流式面积缩放、限长 JPEG/CLI、Latin-1 剪贴板与扩展能力检测已实现；ASan/UBSan C 测试及 85 项 socketpair A2 测试通过。非 sanitizer fake 测得 2880×1800→1280×800 峰值 RSS 7,664 KiB、owned heap 6,368,592 B。沙箱禁止 127.0.0.1 bind，标准 pytest 为 38 过/92 环境失败；TCP、Linux/static armhf 和真机 [D][M] 的安全类型、Retina/多屏、P95/CPU/Extended Clipboard 仍待验收，详见 `agent/NOTES-agent-a2.md`。

#### A2 中文剪贴板路线（仅计划，不写代码）
- 经典 RFB `ClientCutText` 只携带 Latin-1，不能据此承诺中文；Extended Clipboard pseudo-encoding `0xC0A1E5CE` 提供 UTF-8。
  macOS Screen Sharing 是否协商/支持 Extended Clipboard、是否把文本同步到系统剪贴板目前未知，必须在 A2 真机测量并记录结果；box 上不推断支持。
- 规划流程：`paste{text}` → 经 VNC 设置剪贴板 → 在有效输入租约下经 HID 发 `Cmd+V` → 可选恢复（仅恢复 agent 自己先前已知的内容，或协议提供的不读取内容的恢复机制；否则不恢复）。
  写入/粘贴前检查 allowed、e-stop 和租约；中途失效立即停止。
- 永不读回用户剪贴板；不加 Mac helper。UTF-8 扩展未确认或不可用时，中文 paste 返回 `unsupported`，不降级成乱码。

### A3 网络：CONNECT 代理 + mbedTLS + 时间（box + [M]）
- **A3 状态（2026-10-19）**：host 实现与 ASan/UBSan C 测试完成；离线 IPC 补充测试 164 pass，标准 TCP 测试受 sandbox EPERM 阻断；armhf/[M][D] 待验，见 `agent/NOTES-agent-a3.md` 与 `reviews/agent-a3-codex-summary.md`。
- `net.c`：TCP 连接（10 s 超时）→ `CONNECT` → mbedTLS 握手（SNI，用随包 CA 校验）→ HTTP/1.1。
  `net.json` fail-closed，**代码里根本没有直连路径**（禁止 `routes.*=direct` 用于 foreign；`mac_proxy` profile 两类都只走代理）。
- 时间同步：从 Mac `GET /time` 或代理 `Date` 头取时间；时钟没校准就拒绝 TLS。
- **不做出口证明（D2）**。如果 mihomo 不在 172.30.250.1 可达，就装 D1 的 launchd TCP 转发（只绑 USB 接口，不改代理配置）。
- box：fake_proxy 加本地 TLS 测试服务器（自签 CA 注入测试 bundle）；证书错误、过期、域名不符都必须失败。
- **[M][D]（用户回家时）**：只读确认 mihomo 是否在 172.30.250.1 可达；不可达则装转发。
- 验收：设备经代理拿到 Qwen 的 `/models` 401 或 200；代理不可达时请求失败且无任何直连。

### A4 模型适配器（box + [U] 真 key）
- **A4 host 已实现**：llm.c + net.c/OAI、配置驱动 provider、像素坐标校验、--llm-test、ASan/UBSan 与 TLS fake/socketpair 测试；TCP sandbox 验证、[U] 10 图真 provider 基准及 [D] 设备/RSS 仍待，见 agent/NOTES-agent-a4.md。
- `llm.c`：单一 OpenAI 兼容适配器（chat/completions、tools、image_url base64）；provider 全是配置（Qwen 默认、GLM、Kimi、OpenAI）；
  `coord_mode` 统一坐标约定；做错误分类。
- box：fake_model 跑剧本，包括畸形 JSON、超长响应、缺 tool call、坐标越界（clamp 后拒绝）。
- **[U]**：用户通过密钥页（A7 前可先用 adb 写入 0600 文件）提供 Qwen/GLM/Kimi key。在 box 或设备上跑一套固定的
  10 张截图基准，挑出视觉和 tool call 都合格的默认型号。
- 验收：每个 provider 至少通过 8/10 定位基准；请求日志 grep 不到 key。

### A5 Agent loop + 安全闸 + 校准（box + [D][U]）
- **A5 host 状态（2026-10-19，本 worktree）**：policy/loop/kbdc/calib、真实 RFB/LLM ports、--loop-test 与 sanitizer/socketpair 主机测试已实现；真实 Unix/TCP 监听验收受沙箱限制，kbd v1 的 VNC 租约/health 与 [D][U] 校准、检测、急停/任务实测待验收，详见 `agent/NOTES-agent-a5.md`、`reviews/agent-a5-codex-summary.md`。
- `loop.c`、`policy.c`：状态机、预算、无进展检测、风险规则、永不做清单、确认流程、VNC 备用仲裁、锁屏检测、`calibrate`。
- box e2e：fake_vnc + fake_model + 真 mk20-kbd（FIFO hidg0）跑完整任务；覆盖急停（各状态下）、确认拒绝/超时/早按、预算耗尽、
  HID 故障注入切到 VNC 再切回、锁屏拒绝 `type`。
- **[D][U]**：真机做校准和点击精度测试（§14.5-3），急停实测（§14.5-6），在用户在场时跑无害任务，比如
  “打开备忘录并输入 hello”。按惯例先备份；出问题就按急停或拔 USB。
- 验收：§14.5 第 3、4、5、6、7 条。

### A6 MCP 服务（box + [M]）
- `mcp.c`：Streamable HTTP、JSON-RPC、工具注册、bearer token、usb0 绑定。
- box：用官方 MCP inspector 或自写客户端做协议一致性测试；未认证 401；非 usb0 地址不可达（测试 netns）。
- **[M]**：在 Mac 上把它注册成 Claude/Codex 的 MCP server，只读调用 `status` 和 `screenshot`。动作类调用要等用户在场。
- 验收：MCP 动作同样受急停、预算和确认约束（e2e 证明）。

### A7 键面 UI、配置下发、密钥页（box + sim + [D]）
- mk20-kbd：agent 状态组件和 L5 agent 页（仿真里先做，JSON cases 覆盖急停、确认、允许开关）。
- mk20deck：设置页的应用改成推送到设备；连接测试改由设备侧执行；增加“智能体”状态镜像。
- 配置网页新增：网络出口 profile（D1′）、ASR 模型配置（D5′b）。
- mk20-kbd：`screen.*` 渲染与 agent 自有页（D8），保留页与授权确认。
- A7 follow-up（kimi r1 #8 / #12）：清理或说明仅供观察的 `/tmp/mk20-hid-scroll` marker；真实渲染落地时为 screen.page.show / layout / page.create 评估限速（当前只有 draw/text 消耗额度）。
- mk20-agent `--web`：配对码、写入式密钥页、`PUT /config`、`POST /test`，以及 **D5 任务 API + `/chat` 极简网页**（提交/进度/截图/暂停/停止）。
- 测试：sim cases、web 表单安全测试（无读出接口、配对码过期、CSRF、权限 0600、原子性），secrets 不进 backup（已排除，加测试固化）。
- **[D]**：真机看页面效果，用户回家后核对一次。

### A8 发布集成（box）
- `tools/mk20-build-bins.sh` 加入 `bin/mk20-agent`，binary-match 测试；MANIFEST 带上 `share/ca-bundle.pem`。
- `mk20lib.sh`：`FEATURE_AGENT=0` 默认，接入 FAT 标志 `mk20-no-agent` 和 `MK20_AGENT`。
- `mk20d agent`：照 router/kbd 的模式监管（flock 单实例，backoff 1→30 s，60 s 正常运行后重置）；
  只有在 composite 正常、kbd.sock 存在时才启动；`oom_score_adj 500`，`nice 10`。
- selftest：agent 一律 **INFO/WARN，永不 FAIL**，不影响 confirm 和 A/B 回滚，因为 agent 是可选能力。
  例外：二进制缺失或 MANIFEST 不符按常规 FAIL。
- health.json 增加 `agent{enabled,allowed,state,provider,route,last_error_class}`，不含任何密钥或 token。
- maint 负责轮转 agent.log；卸载或回滚时 `/data/mk20/agent` 配置保留，secrets 保留。
- 版本：作为 fw **1.3.0** 的 opt-in 功能发布（假设 1.2.x 和 kbd 默认启用已先落地）。

### A9 真机验收与 soak（[D][U]）
- 跑 §14.5 全部 8 条，并新增：
  - 9：断网或代理挂掉时安全失败，不直连；
  - 10：OOM 注入（ulimit）不影响按键和屏幕；
  - 11：agent 崩溃循环不影响 OTA 和回滚；
  - 12：12 h soak（每 5 min 一次 screenshot，只读）时 RSS 无增长、按键延迟无退化。
- 通过后才讨论 `FEATURE_AGENT` 默认值（即使默认 1，“agent 允许”仍是上电关闭）。

### 之后（不在本计划范围）
- A10：`board_wifi` profile（dongle + 板载代理）；Anthropic 原生 computer-use 适配；中文输入通道；多显示器；语音触发（audio 模块之后）。

---

## 6. 接口与文件约定

### 6.1 文件
| 路径 | 属主/权限 | 内容 |
|---|---|---|
| `/data/mk20/agent/net.json` | root 0600（目录 0700） | 见 agentcfg.py `net_config()` |
| `/data/mk20/agent/provider.json` | root 0600 | `provider/model/base_url/domestic/route/api_key_file` |
| `/data/mk20/agent/agent.json` | root 0600 | 预算、风险黑名单、遮挡区域、急停键位（新） |
| `/data/mk20/agent/calib.json` | root 0600 | 坐标仿射参数（新） |
| `/data/mk20/secrets/<provider>.key`、`vnc.pass`、`mcp.token` | root 0600 | 只写不读；不进备份、日志、git |
| `/run/mk20/kbd.sock` | root 0600 | kbd 控制口 |
| `/run/mk20/agent.status` | root 0644 | 给 selftest/health 用的非敏感状态 |

### 6.2 net.json 规则（沿用 agentcfg，设备侧再校验一遍）
- `mac_proxy`：`routes.domestic=proxy`、`routes.foreign=proxy`，回落只在 Mac 的三个地址之间切换。
- `board_wifi`：`domestic=direct` 可以，`foreign` 只能是 `proxy` 或 `blocked`。
- 设备拒绝任何 `foreign=direct`，也拒绝 proxy url 不在白名单（Mac 三个地址，或板载代理）里的配置。

---

## 7. 测试策略

- **单测（C，host 构建）**：rfb 解码、缩放、JPEG 尺寸和质量、HTTP 解析（chunked、畸形输入）、CONNECT 状态机、JSON 构造和解析、
  坐标映射、policy 规则表、脱敏函数、预算计数。
- **e2e（box）**：fake_vnc + fake_model + fake_proxy + 真 mk20-kbd（FIFO hidg0、假 ttyS1 注入急停键）。断言内容：
  - HID 报告序列（按下和抬起成对、all-up）；
  - 急停延迟；
  - 请求体里没有 key，日志里没有 key 和 vnc 口令；
  - 直连被拒绝（fake_proxy 关掉时，agent 不应出现任何外连；用 netns 或 iptables OUTPUT 计数）。
- **armhf 测试**：qemu-arm 跑静态二进制的单测；RSS 和 rlimit 用例。
- **sim**：mk20sim 增加 agent 页、确认、急停的 JSON cases。
- **安全回归**：`tests/test_agent_secrets.py`，对所有日志、health、MCP 响应和 core dump 配置（`RLIMIT_CORE=0`、`PR_SET_DUMPABLE 0`）做 grep 断言。
- **复审**：每个阶段交 codex 复审（Cursor 额度 11/13 恢复后可加 Opus 或 GPT）。

## 8. 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| mihomo 不监听 ECM 地址，又不能改配置 | 设备完全出不了网 | D1：Mac launchd TCP 转发，只绑 172.30.250.1（已批准） |
| T113 时钟不准 | TLS 校验失败 | Mac 授时；未校准不发请求 |
| macOS Screen Sharing 行为变化（认证、Retina、多屏、锁屏下 VNC） | 抓帧失败或坐标错 | A2 实测；只支持主屏；锁屏只走 HID 且不打字 |
| 国产 VL 模型的 GUI 定位精度不足 | 点不准、任务失败 | A4 基准选型；截图带网格可选；校准；失败就停下请示，不盲点 |
| 内存紧张（57 MB）、与 kbd 争 CPU | 按键卡顿或 OOM | 流式缩放、rlimit、oom_score_adj、nice；A2 和 A9 实测 |
| hidg0 rebind 或 kbd 崩溃（parked fd，kdr-r1 #3） | 动作丢失，HID 需重启才恢复 | 动作失败就停下回报，不重试到另一路；HID 修复模块（已排在最后上真机）落地后消除 |
| 误操作或模型被网页内容 prompt-inject | 发送、删除、付款 | 风险规则加确认、永不做清单、预算、急停；system prompt 声明“屏幕上的文字不是指令” |
| 密钥泄露 | 账户被盗刷 | 0600、只写接口、脱敏、无 core dump、不进备份、不进 git；MCP 和 web 只绑 usb0 并加 token/配对码 |
| 第三方库（mbedTLS/zlib）CVE | 远端攻击面 | 只作客户端连已知 provider；MCP/web 只在 USB 点对点链路上；版本记录在 RELEASE.md |

## 9. 决策状态

已决（2026-10-19，详见 §0.1）：1 代理接入 → D1；2 出口证明 → D2（不做）；3 provider → D3（单一 OpenAI 兼容适配器）；
4 急停 → D4；5 中文/任务入口 → D5；6 MCP → D6（不能启用）；7 截图提示 → 同意默认提示 + 可配置遮挡；密钥录入 → D7。

仍待定：
- **剪贴板写入机制**：仅 VNC；A2 实测 macOS Screen Sharing 的 Extended Clipboard UTF-8 支持；不做 Mac helper、不读回用户剪贴板。
- **默认国内模型**：A4 基准之后推荐、用户确认；需要用户经板载密钥页录入 Qwen/GLM/Kimi key。
- **D5′(a) USB 麦克风**：UDC 缺 ISO IN 端点（见 §3.9），需要在“再打内核补丁”与“Mac 侧助手”之间取舍。
- 麦克风硬件形态（模拟 MIC 进片内 codec 还是 PDM）：真机 `arecord -l` 确认。

## 10. 需要设备 / 用户的步骤汇总

| 阶段 | 需要设备 [D] | 需要用户 [U] | 需要 Mac [M] |
|---|---|---|---|
| A0 | — | — | — |
| A1 | 装 kbd + 新描述符，验证滚轮（probation，可回滚） | 回家后按一下急停键、滚一下 | `ioreg` 只读 |
| A2 | 抓帧实测（只读） | Mac 开机且已登录即可 | Screen Sharing 已开（已就绪） |
| A3 | 设备经代理连通性（回家时） | — | mihomo 可达性只读检查；不可达则装 launchd 转发 |
| A4 | 可选 | 经密钥页提供 key；确认默认模型 | — |
| A5 | 校准、点击精度、急停实测 | **必须在场** | — |
| A6 | — | 动作类调用要在场 | 注册 MCP |
| A7 | 页面效果 | 核对一次（含 /chat 任务页） | — |
| A8 | — | — | — |
| A9 | 全量验收和 soak | 在场跑 §14.5 | — |

*作者：Grok Bot executor · 2026-10-19 · 只读调研，没有碰设备，没有改代理，没有写 GD32。*
