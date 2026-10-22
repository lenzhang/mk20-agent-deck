# Kimi K3 — page sync r2 (5901c2a..3913eb7), 2026-10-22 08:33 — GO

• VERDICT: GO

  **前次逐项复核**

  - BLOCKER（stale/K2 交错持久错页）→ **FIXED**。拒绝/超时即 `_page_dev=None`（daemon.py:353），下次变页发绝对页；ack 在读线程派发时盖 rx 戳（link.py:164-178），tap 仅当 `rx>_page_dev_rx` 才翻猜测（daemon.py:308-314）；固件 ack 前强制先冲刷滞留 tap（kbd.c:163-170）、边界前入队字节判 stale（kbd.c:408）。test_a/b/c + SIGSTOP e2e 覆盖，手工推演双 tap 在途、tap+ack+reject 交叉、HOME 往返均收敛。
  - MAJOR（同页 boundary 释放按住 PTT/HID）→ **PARTLY**。同页请求现只在猜测未知窗口发出（daemon.py:280-292）；但"拒绝→未知→重发同页"且物理键按住时，onboard.c:358→182 仍会释放。已明确留作固件后续，可接受。
  - MAJOR（re-hello 不唤醒）→ **FIXED**。hello 与第 3 心跳 `_ui_changed`→`_wake`（link.py:273,325-326；daemon.py:529-530）；真实 `_run` 测试覆盖。即便丢唤醒，帧心跳 ≤2s 自愈。
  - MINOR（代际耗尽）→ **FIXED**。`_next_generation` 封顶、单次告警、双请求停用（link.py:284-297,344-348）。

  **新发现问题（均 MINOR，无 BLOCKER/MAJOR）**

  - link.py:287 告警称"键盘进程重启即恢复"，但 `ui_generation=max(...)`（link.py:265）只增不减，同连接重 hello 并不恢复，只有新连接（新 DeviceLink）才行。修复：改告警文案，或 hello 回报世代明显变小时允许重置（需 fencing 旧请求）。
  - ONLINE 门按固定 3 拍估算（link.py:325），设备实际要求"≥1s 间隔的 3 个样本且跨度 ≥2s"（onboard.c:280-284）；heartbeat_ms<~834 时第 3 拍早于 ONLINE，会多一次被拒再退避重试（≤2s 自愈）。现固件 heartbeat_ms 硬编码 1000（onboard.c:334），不触发。修复：按 hello 回报的 heartbeat_ms 推算拍数，或容忍首拒。
  - 显示休眠时暂停路径丢弃队列中的 K2 tap（daemon.py:555-556），猜测不变；唤醒走 WakeReconnect 新 epoch 即未知化自愈，仅休眠窗口内滞留。建议注释说明。

  测试真实性：SIGSTOP 用 `host_rx_barrier` 真实复现"请求先于边界入队"（kbd.c:290-296），且若 GD32/主机服务顺序假设失效，断言 `pages_rejected+1` 会让测试显式失败而非空过；legacy 退避/时序测试保留。未发现猜测可永久错误的路径。

