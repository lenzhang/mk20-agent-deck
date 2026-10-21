• I'll verify each claim against the sources. Let me first map the files.

• Now let me trace B2's critical path — whether mk20deck ever sends getInfo/ui.hello/ui.heartbeat after connect — plus the adapter claims.

• Now the remaining sources: codex_desktop adapter, the onboard test, onboard.h enums, and PRD sections.

• Let me read the PRD key sections and remaining bits.

• Almost done. Final checks: codex_desktop capabilities (I11/I4), UI.md naming (I11), supervisor reconnect (B2 nuance), k2estop (B3).

• One last cross-check: ONBOARD-UI-PLAN for fb16/L5-auto-switch (I9), and GRILL-DECISIONS for anything contradicting.

• XCHECK: 11 confirmed/3 partly/0 refuted

  **B1 证实**。herdr.py:22 `"blocked": (State.UNKNOWN,…)`；model.py:25–38 unknown(1)<idle(2)、不在 ATTENTION_STATES；codex_desktop.py:302–305 等待批准时最后事件仍是 task_started→「进行中（粗）」，超出 running_window_s=600(:115）后→「运行信号过期」unknown；tmux.py:96 全部 `state=State.UNKNOWN`、:90 非 ccai/codex（含 kimi）被滤掉。

  **B2 证实**（标注 inference 但链条完整）。精确路径：onboard.c:238 `!host_route||handshake_required||SUSPECT` 丢帧且不推进 `seen`；:296 `now-seen>=5000`→onboard_loss→handshake_required=1、OFFLINE；此后 ui.heartbeat(:219）与 ui.page(:224）均被拒，只有 getInfo(:204–209 的 OFFLINE 分支）或 ui.hello(:211–216）能复位。mk20deck 全库无 ui.hello/ui.heartbeat（grep 命中的 "heartbeat" 只是 daemon.py:140 每 2s 推一帧 JPEG）；getInfo 仅在 supervisor 握手时发（link.py:155；supervisor.py:69–116 只在拔插/唤醒/连断时重握手）。致命点：kbd.c:443 对被丢的 SHOW_JPG 照常 ACK，link.py:176–180 的 3 次失 ACK 重连永不触发→停留 HOME/LS/L5 >5s（含急停跳 HOME 后）L1 永久卡在「Reconnect required」(onboard.c:453)。测试确只覆盖 lost→getInfo(onboard_test.c:160–161)。

  **B3 证实**。onboard.c:386 `if (fb==20) { stop(…); }` 全页生效、:291 跳 HOME；fb20=PRD §6.2 行4列5「模式」键；PRD:767 R-CU-3「不与 L1 已批准键冲突」、AGENT-BUILD-PLAN:29 D4「agent 页固定急停+长按 K2」。注：ONBOARD-UI-PLAN:180/221 反而明文规定 fb20 全页急停——文档间冲突。

  **I4 证实**。controller.py:546–589 attention 只做高亮，无「下一待处理」键；K1 旋转=set_effort(:304–312)，而 effort 三模式全不支持（model.py:113、codex_desktop.py:150、herdr.py:46、tmux.py:59）。

  **I5 证实**。PRD:282「PTT 始终跟随 Mac 当前输入焦点」与设备选中脱节；footer onboard.c:477；:297–298 3s 无新帧→SUSPECT+release_mac(:71–78)；mk20deck 心跳帧 2.0s(config.py:47)→余量 1s，一次 ack 超时（1s）即可截断长听写。

  **I6 证实**。daemon.py:132–134→controller.refresh(:201 `mode or self.mode`)，只轮询当前模式。

  **I7 证实**。slots.py:91 须严格更高优先级才替换；tmux 全 unknown(1）永不替换；DONE_UNREAD(4)>RUNNING(3)，完成线程挤掉运行中线程。

  **I8 证实**。onboard.c:476–484 设备自绘 fb21 y112–141、:520 主机帧只发 112px；subscreen.py:60–68 把 AC11「状态受限」/+N 待处理画在 y=120 被覆盖；:478 L1 同样显示「K1: settings (L4)」，与 L1 实际 K1=effort 提示（:351）不符。

  **I9 部分**。自动切 L5(:315–317)、fb1/fb11/fb12 改义（:414–415)、boundary 释放按住中的 Mac PTT(:99–116→:88，违 PRD:284「页面切换不打断 PTT」）均属实；但「fb16→板载麦 PTT」代码不存在——L5 分支无 fb16 动作，标签为「PTT: ASR unavailable」(:460)，仅 ONBOARD-UI-PLAN:222 的规划。

  **I10 证实**。D14 统一 600ms(PRD:575/251) vs 板载 1000ms(onboard.c:322/369/565)；controller.py:29 实现 PRD 语义，但旋钮按下被设备本地消化、只转发合成短 tap(:351)，长按永远到不了 controller；宽限期 fb5/6/7/18→导航（:430–434）属实。

  **I11 证实**。停止键三模式全禁（codex_desktop.py:148 等）；fb20/K2 急停走 ctl_estop 只断板载 agent 租约，不碰 Mac 侧 ccai/codex；「停止」(controller.py:566) 与「STOPPED/E-STOP」同名不同域。

  **S12 部分**。聚焦才清绿属实（controller.py:495–497)，但这是 PRD:241 明文规定。**S13 部分**。tmux 新建带 cwd/命名（tmux.py:125–135)、kimi 不匹配（:33–39）属实；但「boot to L1」与 ONBOARD-UI-PLAN:248「Boot→Home」既定决策相反。**S14 部分**。PRD 现为 v1.3(:714)，纯流程建议。

  **评审遗漏**（多 agent 视角）：
  1. **模式键已死**：fb20 被 onboard.c:386 拦截，PRD §6.7.1 的模式短按循环（controller.py:393–395）从物理键盘永远无法触发；ONBOARD-UI-PLAN:207 称模式移到 K2 短按，但 K2 短按在设备（:349）和 controller(:302) 都是 L1↔L4 切页——没有任何物理途径切换 Desktop/herdr/tmux，多 agent 监督的主导航失效。
  2. **K1 长按去 HOME** 而非 PRD §6.4(:259) 的 L4 设置（onboard.c:324–327)；controller.py:327–330 的 K1-long→L4 是死代码。
  3. **主机侧零设备健康遥测**：丢帧也 ACK(kbd.c:443)→frames_sent 全绿、副屏绿点（subscreen.py:29)，设备却显示「Reconnect required」；daemon.py:214–224 的离线帧在 HOME 同样被 :238 丢弃，「守护进程已退出」不可见。
  4. tmux/herdr 永远产生不了 attention 态（tmux.py:96、herdr.py:22–23)，§6.6 注意力机制实际只对 Desktop 生效。

  **我的 Top-3**：
  1. 修活锁（B2)：mk20deck 落实 ui.hello+ui.heartbeat（或周期 getInfo)；设备允许任意路由有效帧推进 seen；ACK 区分「已显示/已丢」并上报设备健康。
  2. 还回模式键+统一手势：急停收缩到 L5/agent 页（K2 长按/三连已在 k2estop.c)，长按阈值统一 600ms，旋钮 down/up 透传给单一手势 owner（顺带修 K1-long 路由）。
  3. 「需要我」信号+跨模式轮询：ccai/codex hooks→needs_approval、herdr blocked 保留语义，K1 旋转遍历 attention 队列、按下聚焦；副屏内容让出 y112–142。

