• Now let me read the full `onboard.c` to verify the fix in context.

• VERDICT: GO

  **MAJOR-1 已修复**：onboard.c:447-451 在挂起中加入 5 s 无任何键/旋钮事件的有界兜底，清零 `pressed`/`knob_down` 并对卡住的 K1 补 `ui_knob UI_UP`；同 tick 内 :452-453 的 held 即变假、静默已超 1 s，挂起随之解除。`input_at` 在每个物理事件刷新（onboard.c:509），慌乱连按顺延、长按不产生事件故不刷新——正是上轮建议的方案。ux_suspend_lost_up(tests_onboard_r4.inc:97-113) 验证：丢 up 后 1 s 仍挂起、5 s 恢复转发、 stale K1 未触发长按 HOME、迟到 up 被吞。

  **新安全性核查：无新问题**。
  - 卡键/RAlt：挂起中吞下的 down owner=0(onboard.c:546)，其迟到 up 经 release_key(:114) 对 owner 0 为空操作；RAlt/PTT 分支在 swallow 之后（:551-555)，挂起中无法新取 RAlt；被清位键的真 up 仍送达按下时属主。
  - 宿主泄漏：down(:546)、旋钮（:520)、short_knob(:518）的挂起闸门未动；解除要求 5 s 静默，严于原"全抬起+1 s"规则，未放宽。
  - 急停路径：stop()、estops 检测、fb20 兜底均未改；K2 急停由 k2estop.c 独立计时，清 `knob_down[1]` 不影响，且此刻已 estopped。
  - K1/K2 长按：长按判定 1 s(:461）远早于 5 s 兜底，挂起中导航本被 :463 闸门阻断；兜底的 UI_UP 与正常释放同路径（:517)。
  - e2e:sock_ready(e2e_run.py:30-39）等 chmod 0600 终态后再连，0600 断言保留（:68)，属加固而非削弱。

  遗留（非本次引入）:GD32 真丢 up 时宿主侧该键仍视为按住，需用户再点按一次自愈——这是丢帧固有问题，delta 前的行为是整机卡死，现为可自愈。

