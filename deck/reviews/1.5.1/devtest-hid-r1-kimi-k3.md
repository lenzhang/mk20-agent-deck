# Kimi K3 — devtest HID-leak gate r1 (d696de6), 2026-10-22 06:36 — NO-GO

• VERDICT: NO-GO
  - BLOCKER `deck/mk20deck/hw/devtest.py:780`：±0.35s“其他键”还被限制在当前步骤内；上一非允许键的 HID 落到下一允许步骤即被豁免，最小复现为 `pass`。修复：全局检查邻近事件，先归因泄漏再豁免；补跨步骤、延迟采样用例。
  - BLOCKER `deck/mk20deck/hw/devtest.py:813,820-824`：PTT 窗口丢弃所有 HID 类型，错类型或同期泄漏可通过；底排错类型单独发生又仅 `warn`。修复：PTT 仅豁免可归因的 `flagsChanged`，引导步骤内错类型明确判 `fail`。
  - MAJOR `deck/mk20deck/hw/devtest.py:374`、`deck/kbd/onboard.c:627`：hello 不等于 fw≥1.5，也不等于 ONLINE+fresh 的 L1/L4；旧固件底排会被错误豁免，1.5.x 非 live 页会误缺键。修复：校验版本及实际 live，未确认不得按 live 判定。
  - MAJOR `deck/mk20deck/hw/devtest.py:854`：live PTT 合法地无 CDC，完整 1.5.x 自检仍必 `warn`、退出失败。修复：接受预期 `flagsChanged`。
  - MINOR `deck/tests/test_reconnect_devtest.py:620`：错类型与另一必失败泄漏混合，且未覆盖上述边界、非 live、完整 PTT；应拆分并补隔离用例。

