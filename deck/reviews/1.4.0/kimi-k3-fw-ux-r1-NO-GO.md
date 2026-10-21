• 我需要读取 diff 的剩余部分（ui.c / ui.h / ui_labels.inc / kbd.c 等改动），用 line_offset=1417 继续。

• 上次调用未带 line_offset 参数导致重读了开头；这次显式指定 line_offset=1417 读取 diff 剩余部分（ui.c/ui.h/kbd.c/ctl.c 及 r4 测试）。

• 我需要 diff 的 1417 行之后的内容（ui.c、ui.h、kbd.c 等改动），这次显式传 line_offset 参数。

• 我需要读取 diff 第 1417 行之后的部分，这次明确传入 line_offset 参数。

• VERDICT: NO-GO

  说明：Read 分页故障，diff 仅读到第 1416 行（ui.c、kbd.c、ctl.c、tests_onboard_r4.inc 未覆盖），已据此标注"待验证"。

  BLOCKER

  1. e-stop 停留 L1/L4 后，宿主按键转发无 estopped 闸门。旧不变式是"e-stop 必离页"，转发代码在其下编写；本次 stop() 在宿主路由仅 release_mac+banner(onboard.c:334–347 新行），不再 boundary/导航，且 diff 全文未新增任何 estopped 检查。e-stop 后 agent_active()=0,fb20 立即变回宿主 mode 键，fresh/ONLINE 不变时按下即转发给 Mac——停机锁存期间设备仍向宿主发键。修复：onboard_input 宿主转发尾支（down 与 up）加 `if (k->ctl && k->ctl->estopped) return 1;`；补 r4 用例。
  2. 租约授予与 fb20 按住的竞态。fb20_host() 只在 key-down 求值（onboard.c:493–495)；按下转发给 Mac 后 socket 租约生效，本次按下永远不 e-stop，换面还要等 tick 的 agent_was 重绘（onboard.c:409–410)。修复：tick 检测 active 0→1 时若 fb20 宿主属主按住，先向宿主补 up 再立即 stop()（宁可误停），并同步重绘 fb20 保留面。

  MAJOR

  3. devstat 每 2s 读 /data(UDISK ext4）的 net/provider.json 并对 secrets lstat;O_NONBLOCK 对普通文件无效，UDISK 卡死将 D 态挂住主循环——该进程独占 e-stop/all-up，停机延迟无上限（devstat.c:46–66、296–310)。修复：探针移入工作线程+快照邮箱，或对 /data 探针设超时降级。
  4. 停止横幅 10s 自动消失（STOP_BANNER_MS,onboard.c:408)，而 estopped 仍锁存；live L1/L4 上宿主恢复独占 142 行，无任何"已停机"指示，唯一 e-stop 只剩 K2 长按。修复：estopped 期间 ui_footer_attention 恒真，常驻一行"急停中"。

  MINOR

  5. SUSPECT/未 live 且 agent 未激活时 fb20 完全惰性（onboard.c:518),e-stop 仅靠 K2；占位页 fb20 面应改印"K2 长按急停"。
  6. auto-L1 触发点仅在 sample() 心跳分支（onboard.c:233–239);legacy_probe 允许 HOME 解码 JPEG(onboard.c:65、281)，若 onboard_valid_frame 直接置 ONLINE 则永不 auto-L1，与 UI.md 宣称不符——需测试或删除。
  7. 宽限期不吞 K2 短按 L1↔L4(knob 分支在 grace 检查之前，onboard.c:467–472 vs 519)，反复切换会刷新 switch_at 延长吞键；建议 grace 内同样吞旋钮短按。
  8. open_settings 限流戳仅发送成功才记录（onboard.c:448)，背压时可每按重试；payload 固定无 URL，符合要求（e)，可接受。

  已确认无问题（b/f):devstat 全程 O_NOFOLLOW+S_ISREG+0600/uid,RF_SIZED 复读一致性、strlen==n 拒 NUL；密钥仅 lstat 且目录 0700 非符号链接；JSON 递归≤8、path[100]/key[32] 有界、dup/escape 即无效；pair 文件 0600/64B/600s 格式严格；每次调用至多一个探针。内存约 +17.6KB,CPU 可忽略。grace 吞键对 down/up 对称（owner=0，抬起为空操作），不会卡 RAlt/PTT(c)。footer attention 涵盖 SUSPECT/PTT/incompatible/确认/配对码（d)，除 #4 外不隐安全态。

  待验证（影响 #1/#4 定级）：读取 diff 1417 行后的 ui.c(ui_footer_attention 是否含 stopped)、kbd.c（转发/释放尾支）、ctl.c(st.estops)、r4 测试；修复 #1–#4 后再复审。

