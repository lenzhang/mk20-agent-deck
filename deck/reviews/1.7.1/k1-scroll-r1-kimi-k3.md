# Kimi K3 — K1 scroll r1 (…ece7072), 2026-10-25 17:37 — GO (2 findings + 3 MINORs → fixed in a8d02b4)

• VERDICT: GO

  整体设计扎实：事件只在循环步内合并一次发送、adapter 失败有 toast/静默期、helper 只编译本地源码并 ad-hoc 签名、测试覆盖真实 tmux server 与真实编译。以下问题均不阻断合入，建议随修：

  1. **加速判定用的是处理时间而非事件时间** — `daemon.py:283-285` `_apply` 丢弃了 `InputEvent.t`（enqueue 时已带），`controller.py:687` 用 `self.clock()`。慢 loop step（后台轮询可达数秒）后排队的慢速 detents 被一次性处理，`t - sc.last ≈ 0` 全部误判"快转"，直接冲到 15 行/格。修：`_apply` 把 `ev.t` 传进 `on_knob(..., t=ev.t)`，`on_knob`/`_k1_rotate` 优先用事件时间。

  2. **失败/会话消失路径会把 tmux 窗格留在 copy mode** — `controller.py:834-836`：pending 发送失败时直接 `self.scrolling = None`，未走 `_end_scroll`/`scroll_release`；`controller.py:563-565` 会话消失时 `_end_scroll` 因取不到 Session 对象直接 return False，不发 `cancel`。后果：蓝条消失、2 分钟兜底不再生效，窗格无限期停在 copy mode，打字被吃掉。修：`_Scroll` 里存 pane id，`sc.active` 时失败/消失路径仍尝试一次 pane 定向 `send-keys -X cancel`（构造最小 Session 即可）。

  次要项：
  - `scrollhelper.py:57` `up_to_date` 只信 `.sha256` 戳，不校验二进制本体；binary 被替换且戳保留时会跳过重编译。低风险，可顺手对 binary 也做一次 sha256。
  - `tools/macos/mk20deck-scroll.m:196` `--at` 用 `sscanf("%lf,%lf")`，`"0.5,0.5junk"` 也能通过；建议校验结尾 `'\0'`。
  - PRD §2.3 注「K1 按下不再置前」与 §4/Desktop 实现（press 先 `_focus` 再滚）措辞冲突，建议在 §2.3 补半句"Desktop 为滚动需先置前"。

  核查结论（无问题项）：(a) 选项模式/拿起/PTT/切模式/重连的 K1 语义各自独立、互不占先，`drop_gestures` 正确只清未发队列；(b) pane id 走 argv 无注入面，`-e` 语义与真实 tmux 测试一致，超时 3s；(c) disclaim 子进程、TCC 只归 helper、`responsible: parent` 有告警、安装目录 0700 + 原子替换；(d) helper 缺失/未授权只提示一次并静默 20s；(e) L4/选项/拿起下 K1 按下语义保留，alert 横幅优先级合理（GD32 卡住时按键本就全死）；(f) 测试含真 tmux、真 clang、脚本化 setup，仿真度足够。

