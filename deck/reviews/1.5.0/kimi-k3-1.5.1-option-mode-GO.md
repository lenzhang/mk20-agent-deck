• Diff 1、2 已完整读完。继续读 diff 3(Mac 控制器）。

• 四个 diff 已全部读完。现在核对源码中 diff 依赖的上下文（门控条件、jstr 边界、boundary 调用点等）。

• 固件侧上下文已确认（数字键分支在 `ONLINE && fresh && !suspended && !keycode` 门内，fb20 急停在前）。还需核对三处：jstr 的 NULL/0 安全性、物理按键是否先撤销 lease、tmux 适配器 hooked 标志重置。

• `ctl_json_count` 的 `jstr(NULL,0)` 安全（out 为 NULL 时纯扫描）。还需确认物理按键是否先撤销 agent lease（文档声称数字键视为人类输入）。

• 控制器已通读。最后核对 daemon 主循环中 tick/同步的时序与重连路径。

• 全部代码核对完毕。以下为评审结论。

  VERDICT: GO

  已完整通读：`diffs/1-kbd-onboard.diff`、`diffs/2-kbd-other.diff`、`diffs/3-deck.diff`、`diffs/4-hook.diff`（逐行，无跳过），并对照源码 `deck/kbd/onboard.c`、`kbd.c`、`ctl.c`、`deck/mk20deck/{controller,daemon,link,protocol}.py`、`adapters/tmux.py`、`tools/hooks/mk20-agent-state-hook.py` 验证上下文。

  **(a) 主机能否越权打字 / 过期映射 / 重放**
  - 只能打数字 1–9:`parse_hid_keys`(onboard.c:300-313）键名严格 "1".."15"、值严格 "1".."9"、去重、≤9 条、`found!=total` 拒绝杂键；usage 固定 0x1E–0x26，主机无法注入其他键码。
  - 页面门控双层：接受时要求 ONLINE+L1+非 suspend(onboard.c:342)，按下时还要求 fresh 且 keycode==0 且 PTT 位优先（onboard.c:624-638)。清除面完整：boundary() 覆盖路由切换/ui.page/K1 长按/ctl 翻页/hello/loss(onboard.c:181,208,273,358,493),SUSPECT(:473)、急停 stop()(:424)、30 s 超时（:514，且 onboard_timeout:924 保证唤醒）。
  - 防重放：hid_generation 严格递增、跨 hello/loss 保留（:340);tty 滞留帧按 stale 拒绝（kbd.c:408)；急停帧先到则 safety_tick 先清映射再拒非空请求（kbd.c:464 + onboard.c:342)。嵌套解析器有界：单层、每层 ≤24 唯一键、32 字节键缓冲、1023 字节总行；`jstr(NULL,0)` 纯扫描不写，`ctl_json_count` 先校验后计数，无越界（ctl.c:30-64,128-140)。
  - 唯一例外设计合理：`{}` 清除在 suspend 时也接受（安全方向）。

  **(b) 卡键路径**：数字键按下即 down+up(onboard.c:645),owner 7 抑制重复 down(:617),10 s 守护覆盖 owner≥5(:515);fb17–19 owner 6 释放时先 HID up 再 host up(:159),release_mac 在急停/SUSPECT 时发 HID up 并降级为 owner 1(:152);PTT 按住时数字键完全忽略（owner 1，仅转发 ignored 标记）。无新增卡键面。

  **(c) 急停**:fb20 判定在所有新分支之前（:615),K2/ socket 急停路径未动；`stop()` 仅增 20 字节 memset;`ctl_human_input` 仍在按键最早点撤销 lease(kbd.c:262)。延迟不受影响。

  **(d) Mac 侧**：仅在 `_focus()` 返回 ok(tmux: switch-client+select-window/select-pane+`open -a` 全成功）且 hooked needs_* 且 page==L1 时进入（controller.py:616,725)；退出路径齐全（状态变化/Esc/K1/空白键/翻页/切模式/重连 drop_gestures/30 s/3 s)；标记事件只做反馈，即使选项模式刚结束也不触发选择/置前（:411-414);classify_json 白名单校验 hid/ignored(protocol.py:746-749)；旧固件走 supports()→"legacy" 提示，不发请求。

  **(e) Hook**：仅存 options(≤5×24 字符，控制/bidi/零宽字符剥离）与 prompt_kind="permission"；不存问题文本/tool_input/消息；`_carry` 重校验旧记录；仍是无输出、exit 0、stdin 限量排空。

  MINOR:
  1. `daemon.py:247-253`：重连后 hello 完成前的窗口期，`supports()` 为 False 会把提示错报为"固件不支持"（应报"未确认/pending")。修法：epoch 重置分支里若 `not ui_negotiated` 且 want 非空，`set_option_hid("pending")` 后 return。
  2. `link.py:296-298` + `protocol.py:717`:`ui_generation` 越过 999999999 后 `P.ui_hid_keys` 抛 ValueError,`_sync_hid_keys` 未捕获会击穿 step()。实际不可达（约 1e9 次请求），建议加 try/except 归因为 "rejected" 防御。

  测试面已有覆盖：固件 `tests/tests_onboard_r6.inc`（畸形/嵌套/重复键/stale/代际耗尽）,deck 侧 devtest/sim option 场景。无 BLOCKER/MAJOR。

