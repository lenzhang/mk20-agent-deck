• All five diffs are read. Now I need the actual sources to verify the previous findings and hunt for new issues — particularly the `o->pressed`/key-up lifecycle, `release_key`, `ctl_screen_reset`, and K2 release detection.

• Sources are all present. Let me verify the key safety paths in the actual code.

• Sources for ctl.c, k2estop.c and onboard.c verified. Now the remaining pieces: kbd.c (input path, host queue, safety tick), mk20_kbd.c (main loop), ctl.h/ctlscr.c (estops counter, screen reset), and gd32 (release-event reliability).

• kbd.c and ctl.h check out. Now the main loop and GD32 decode path (for the lost key-up question).

• All core sources verified. A few final cross-checks: production callers of the blocking test APIs, K2 slot constant, and that the new test suite is actually wired into the runner.

• One red flag: `round4_regressions` isn't referenced in test_kbd.c. Let me verify how the test rounds are wired.

• Tests are wired via `onboard_test.c:259`. Let me anchor exact source line numbers in devstat.c for citations.

• VERDICT: NO-GO

  **上轮 8 项复核**（均已对照源码与 r4 测试验证）

  1. FIXED。挂起闸门齐备：`suspended()`/`fb20_host()` 含 `!suspend`(onboard.c:63-65),`stop()` 置 `suspend+input_at`(:355)；宿主尾支前吞 down(:537)、旋钮转动（:511)、short_knob(:509);up 仍送达按下时属主（:520-525)，不卡键。ux_suspend(tests_onboard_r4.inc:479-511）覆盖 socket/K2/fb20 三种急停后的吞键与恢复。
  2. FIXED。tick 检测 agent 0→1 且 fb20 为宿主属主按住：先 `release_key` 补 up 再 `stop("fb20_held")`(onboard.c:435-441);ux_fb20_race(:513-530）验证 up 先行、随后急停、fb20 重绘为急停面。
  3. FIXED。文件 I/O 全在 worker(devstat.c:375 起，探针在解锁区执行）；主循环仅 trylock 快照（devstat_take :432,onboard.c:713-717);devstat_kick 同 trylock(:451);devstat_free 最多等 ~1 s 后 orphan+detach(:415-430)，无 UAF。e2e blocked_status_worker 证明 5 s/探针时急停/all-up 延迟与关机上界不变。
  4. FIXED。`stop_agent` 粘滞（ctl.c:198)、re-allow 清除（:219);`stop_notice` 经 footer_lines 常驻各页（onboard.c:78,ui.c),L5 fb11 确认（onboard.c:568)，新急停重新武装（:413);ux_notice 覆盖。
  5. FIXED。占位页 fb20 印"K2 长按急停"(onboard.c:625)，未 live 时惰性（:550);ux_minors(a)。
  6. FIXED。legacy_probe(:69）允许 HOME 解码（:294、:722),sample() ONLINE 分支做唯一一次 auto-L1(:243-246)，任意物理输入撤防（:499);ux_auto_l1 含 legacy 路径。
  7. FIXED。grace 内吞 K1/K2 短按（:480);boundary 仅在 grace 窗外刷新 switch_at(:141-143)，链式切换封顶 2 s;ux_minors(b)。
  8. FIXED。限流戳在发送前记录（:474);ux_minors(c)。

  **新发现**

  - MAJOR-1(b 活锁）：挂起可能永不解除。`o->pressed`(onboard.c:519）与 `o->knob_down`(:505-507）仅由 GD32 事件清除，stop()/boundary()/onboard_loss() 均不清；GD32 边沿上报、无状态重同步，校验失败即丢帧（gd32.c:69)。key-up 或 K1 释放帧丢失（或 GD32 复位）→ held(:443）恒真 → 转发永不恢复；期间宿主页键全被吞（:537)、K1 长按导航被禁（:454)，用户困在 L1/L4 只能重启进程。失败方向虽偏安全，但无恢复路径。修复（有界兜底）:`if (o->suspend && now - o->input_at >= 5000) { o->pressed = 0; o->knob_down[0] = o->knob_down[1] = 0; }`——按住不产生事件故不刷新 input_at，慌乱连按会刷新；被清位键的 down 从未转发，其 up 对 owner 0 为空操作。补测试：丢 up 后 5 s 恢复。
  - MINOR-2(c):devstat_start 失败仅置 ds=NULL(onboard.c:197)，状态区永远显示默认值且无 stale 提示；建议失败时显式标记状态不可用。

  已查无问题：锁序（o->mu 与 d->mu 不嵌套）、stale 快照隐藏配对码、阻塞 API(sync/take_wait/run/status_wait）仅测试调用、safe 模式 trylock 取快照；内存 +~17.6 KB,CPU 可忽略。修复 MAJOR-1 后可改 GO。

  已完整阅读：1-onboard.c.diff、2-devstat.diff、3-ctl-kbd.diff、4-ui.diff、5-tests.diff（全部到底），并核对 deck/kbd 下 onboard.c、kbd.c、ctl.c/h、devstat.c/h、mk20_kbd.c、gd32.c、k2estop.c/h、onboard_test.c。

