# Kimi K3 — fw 1.6.1-rc1 (8155e84..2f42a22), 2026-10-23 21:55 — GO

• VERDICT: GO

  审查了 delta.diff 全文及 bridge/kbd/onboard 源码，两层自愈设计与事故根因（5.4.61 pty 输入路径卡死）精确对应，未发现 BLOCKER/MAJOR。

  **(a) 桥接正确性**:`wpend` 处理部分写/EINTR/EAGAIN,`prog` 仅在真实写入成功时刷新——慢读者（1.2 s 暂停）有测试覆盖不会误触；真卡死由 poll 超时兜底，不会漏。pending 满时停止读客户端（TCP 背压），无静默丢弃。客户端替换时 `ci=-1` 防止用旧下标（ttygs_tcp_bridge.c:150),fd 关闭路径均置 -1，无泄漏。master 的 POLLERR/POLLHUP 有 usleep(10 ms) 防忙轮（152/157/162 行）。

  **(b) 数据安全**：两处均为 TCIFLUSH（仅输入=host→device),device→host 方向无任何 flush;e-stop/HID 全在本地。丢弃上限 = 内核未投递字节 + ≤4 KiB pending，且发生时会话已死（SUSPECT 3 s/掉线 5 s，见 onboard.c:528-530，键已在 3 s 释放）。

  **(c) kbd 兜底**:`kbd_host_silence` 位于 safety tick/GD32/ctl/HID 之后（mk20_kbd.c:246)，每 5 s 至多一次非阻塞 ioctl,readable 即跳过——不会冲掉可投递数据；部分帧靠 magic+CRC+preamble 重同步。

  **(d) 重连风暴有界**:kbd 缺席时每个 Mac 连接 ≈"填满 64 KiB + 2 s stall"后被关，单主机、几秒一轮，桥本身存活；相比 1.6.0 的全局挂死是严格改进。

  **(e) soak**：心跳 1.2 s 使 json_in 必然增长（e2e 实证）;reboot/kbd 重启/pid 变化→unknown；显示休眠不休进程、系统休眠则 TCP 断开归 idle，误报面小。

  **MINOR:**
  1. 替换客户端时旧客户端的 pending 字节未清零，stall 会误关新客户端（ttygs_tcp_bridge.c:148-151 vs 173-181)。修复：替换时 `plen=poff=0`（旧会话字节本就会被 preamble 重同步覆盖）。
  2. 持续性内核卡死 + Mac 不断重连时 stall 每 ~2 s 一行日志，bridge.log(tmpfs）无上限。建议仿 kbd 每 streak 只记一行；soak 已记录 bridgelog 大小可作旁证。
  3. hello 字节恰落在"poll 判不可读"与 tcflush 之间的微秒窗口会被丢弃；Mac 3 帧无 ack 会重连重 hello，秒级自愈，可接受。
  4. soak 对"采样间隔内自愈的 stall"不记 stalled，仅靠 host_flushes 差值可见；建议 summary 增加 host_flushes 增长告警。

  **测试缺口**：桥接 tap 在 stall 期间的行为、master POLLERR 防忙轮路径未测（难以触发，防护已在）；验收时按 RELEASE.md 在真机确认 bridge.log 无 `pty input stalled`、`host_flushes` 保持 0。

