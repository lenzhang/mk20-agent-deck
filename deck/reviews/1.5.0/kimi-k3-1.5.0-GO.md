• 4 个 diff 已通读。现在对照 `./deck` 源码核查释放路径、e-stop、lease 抢占与迁移逻辑。

• 源码与 diff 一致（工作区已是新版）。继续核查 hid.c 报告语义、hidg0 替换/退出全抬、ctl lease 抢占，以及 Mac 侧 slots/controller 细节。

• `selected` 存的是会话 key 字符串而非槽位索引，迁移无需变换。继续核查 controller.py 的长按/单击与 capabilities 语义。

• 现在做冒烟验证：PRD 文档存在性、Python 侧迁移/tap 逻辑、固件测试套件。

• 快照缺 `providers.json`（与本次 diff 无关）。用桩模块绕过继续冒烟。

• Mac 侧全部通过。最后构建并运行固件 onboard 测试套件（含新增 r5）。

• VERDICT: GO

  已完整通读：`diffs/1-kbd-onboard.diff`、`diffs/2-kbd-other.diff`、`diffs/3-deck-controller-slots.diff`、`diffs/4-deck-other.diff`，并对照 `./deck` 下源码（onboard.c、kbd.c、hid.c、ctl.c、mk20_kbd.c、slots.py、controller.py、tests_onboard_r5.inc、PRD-1.5-L1-LAYOUT.md）逐条核实。

  (a) 不会有 HID 键滞留 Mac。按下即记 owner=5+usage+时间（onboard.c:593-598）；抬起在 onboard.c:563-568 先于一切分支释放。急停 fb20/K2/socket 均经 stop()→release_mac()（onboard.c:374-385,139-143，已含 owner 5）；页边界（K2 短按、ui.page、K1 长按、ctl 换页）与 ui.hello/断线都走 boundary()→release_key 逐个抬起；SUSPECT 在 onboard.c:431 直接 release_mac；hidg0 替换由 reattach_keep 先全抬再补发仍按住态（hid.c:96-102）；stop 文件/SIGUSR1/退出均 hid_release_all（hid.c:108-110, mk20_kbd.c:264）；10 秒切断在 onboard.c:472-475 且 onboard_timeout:861 保证唤醒。PTT 组合：hid.c 修饰字节与 6 键槽独立，互抬不误（r5 测试 l1_ptt_combo 覆盖）。重复 down 被吞（onboard.c:574)，up 路径在其前，无死锁。

  (b) 不会在非 live L1/L4 或未抢占租约时发 HID。门槛为 host_route+ONLINE+fresh(onboard.c:584),grace 窗、SUSPECT、PROBING、离线占位页、HOME/LS/L5/AGENT 页均不产生 HID（r5 的 l1_inert_pages/states 逐项断言）。物理按下先在 kbd.c:262 经 ctl_human_input 撤租约+全抬，再发 HID down(l1_agent_preempt 验证顺序）。

  (c) 急停不受影响：fb20 判定仍在 owner==5 检查之前（onboard.c:572 vs 574),stop() 路径仅多一轮 20 元循环，r5 mode 0 断言 <50ms。

  (d) 一致：设备 L4_BACK_FB=5(onboard.c:12,587)= Mac L4_TOP 第 5 列 BACK_L1(controller.py:59,89);fb14=行3列4，两侧均为 S14(role_at 索引 13，实测通过）。

  (e) Mac 侧实测：单击 S7=选中+置前、长按 S8=仅钉扎、DELETE/OK/NO 事件被忽略零动作、抬起归属按下时角色（on_key 用 p.role)；旧 8 槽状态按物理位迁移（A5→S6…选中/钉扎/排除保留，重复与畸形条目不崩）,[[skills]] 畸形仅告警。

  MINOR:
  - deck/mk20deck/controller.py:495：新会话键 PTT 按住时提示"禁止置前"，应为"禁止新建"。改文案即可。
  - deck/kbd/onboard.c:472 的 10s 切断依赖 tick 调度，重负载下可能略超 10s；可接受，如需严格可在切断条件里按 hid_at 提前一拍。

  验证限制：本快照缺 deck/kbd/ui_font.c 与 mk20deck/providers.json/PIL，固件测试与 sim-shots 未能实际运行；但 r5 测试（255 行）覆盖上述全部释放/门槛路径，已逐行与实现核对一致。

