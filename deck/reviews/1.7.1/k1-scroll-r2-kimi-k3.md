# Kimi K3 — K1 scroll r2 (ece7072..a8d02b4), 2026-10-25 — GO (NaN guard → c702ff7)

• VERDICT: GO

  逐项核对：

  1. **加速用处理时间 → FIXED**。`daemon.py:283-286` 两处 `on_knob(..., t=ev.t)`；`controller.py:678` 收 `t` 参数、`:688` 优先事件时间；测试 `test_acceleration_uses_event_time_not_the_late_loop` + daemon 级 `test_daemon_judges_queued_detents_by_event_time` 均覆盖。

  2. **失败/消失残留 copy mode → FIXED**。失败路径 `controller.py:855` 补 `_scroll_release(sc, s)`；消失路径 `:884-894` `_scroll_release` 用 `_Scroll` 存的 `native+generation`（`:263-264`、`:788` 处写入）构造最小 Session 发一次 cancel。`test_failed_scroll_still_puts_the_tmux_pane_back_live`、消失用例断言最小 Session 的 sid 三元组，覆盖到位。

  3. MINOR 三项 → 全 FIXED：`scrollhelper.py:143-152` `up_to_date` 重哈希二进制、`:161` 戳带双 digest（`test_scroll_setup_rebuilds_a_corrupted_binary` 验证）；`mk20deck-scroll.m:195-199` strtod 双端点校验；PRD §2.3 已补 Desktop 例外半句。

  新问题（均 MINOR，不阻断）：

  - **strtod 收 NaN** — `mk20deck-scroll.m:196-199`：`"nan,0.5"` 解析成功且 `fx<0||fx>1` 对 NaN 均为假，校验被绕过。修：加 `isnan(fx)||isnan(fy)` 或 `fx==fx` 判断。
  - **旧戳迁移代价** — 旧格式戳无空格，`partition(" ")` 得空 `bin_d` → 判过期重编译。逻辑正确，但重编译换新 code hash，**现有安装的辅助功能授权会失效一次**，用户需重新授权。建议 release note 提一句。
  - **最小 Session 依赖 adapter 校验** — diff 未含 tmux adapter，`stale generation 被拒`仅靠注释与 MockAdapter（不做校验）背书。请确认真实 adapter 的 `scroll_release` 确实按 generation 拒绝，否则该修复在真机上可能是空操作。
  - 事件时间假设 `ev.t` 与 `controller.clock` 同源（单调整），跨重连若更换时钟源会破坏加速判定；当前构造路径下成立，留意即可。

