# Kimi K3 — GD32 DFU notice r2 (3b158eb..cef750b), 2026-10-25 — GO (all 4 MINORs fixed)

• VERDICT: GO

  四项 MINOR 全部 FIXED：

  1. hub 过期：FIXED — `gd32watch.py:196` 按 wall time 保留 10 min（`HUB_MEMORY_S=600`，gd32watch.py:40），键含 locationID+VID/PID，换 hub 型号不误判，有时钟回拨防护（`0.0 <= now-t`）。
  2. paused 陈旧：FIXED — `daemon.py:564` 抽出 `_sync_gd32()`，paused 分支 `daemon.py:659` 同步。
  3. 线程 join：FIXED — `cli.py:81` `finally` 先 `stop.set()` 再 `join(TIMEOUT_S+1)`，supervisor 抛异常也覆盖（有测试）。
  4. parent=-1：FIXED — `gd32watch.py:118` 加 `n.parent >= 0`，杜绝 `nodes[-1]` 误配，测试断言有力。

  新发现问题（均不阻塞）：

  - **时钟/睡眠语义正确但需知悉**：wall time 在 Mac 睡眠中继续走，睡 >10 min 唤醒后 hub 记忆即失效——注释（gd32watch.py:40）表明是刻意取舍，合理。
  - **过期与仍在场的 Maple 竞态**：真卡死 >10 min 且 T113 全程缺席时，hub 过期会把横幅误撤（Maple 变 other）。已有区分日志（gd32watch.py:217），属设计取舍；如在意可把 `hub_memory_s` 在 sighting 确认期间冻结。建议不改。
  - **join 阻塞退出**：最坏 ~6 s（ioreg 卡满 5 s 超时），线程本为 daemon，有上界，可接受。

  测试新增过期/回拨/异型号 hub/paused 同步/异常 join 用例，质量高。可合入。

