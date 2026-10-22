# Kimi K3 — page sync r1 (5e5a4d1..5901c2a), 2026-10-22 07:58 — NO-GO

• VERDICT: NO-GO

  - **BLOCKER** `deck/mk20deck/daemon.py:300`、`deck/kbd/kbd.c:408`：已复现持续错页：初态均 L1；Mac 发 L4 时设备 K2→L4，使请求 stale；Mac 收 tap 后相对切回 L1，因 `want==_page_acked` 不再发送，设备停 L4。修复：capability 固件的设备 K2/回 L1 tap 无条件调用 `request_page_sync()`；更好是上报绝对 route。补 in-flight ACK 交错测试。
  - **MAJOR** `deck/kbd/onboard.c:178-183,350-359`：同页 `ui.page` 也执行 `boundary()`，释放仍按住的 PTT/HID，需重按恢复，造成断话或停止自动重复。修复：同页重绘/清 map 与输入 owner 清理分离；异页仍释放。补 PTT、e-stop 测试。
  - **MAJOR** `deck/mk20deck/link.py:245-248`、`deck/mk20deck/daemon.py:510-514`：re-hello 只增 epoch，不唤醒已阻塞在 `events.get()` 的 loop，`ui.page` 和全帧可能拖延至旧 timeout。修复：hello 成功后回调投入 `wake`，用真实 `_run` 覆盖长等待。
  - **MINOR** `deck/mk20deck/link.py:244-247,332-339`：generation 达 `999999999` 后持续越界，re-hello 仍取旧最大值，无法恢复。需定义带旧请求 fencing 的重置/回绕协议。

  退避、日志及 legacy 静态未见回归。全套测试未跑（缺 pytest/PIL/providers）；BLOCKER 与耗尽问题已故障注入复现。

