# Kimi K3 — fw 1.6.2-rc1 (release/1.6.1..feature/fw-1.6.2, head 046a742), 2026-10-24 01:33 — NO-GO

Renamed to 1.7.0 after this review (a new self-test gate is MINOR); fixes in 1.7.0-rc1. Record from the verdict on.

• VERDICT: NO-GO

  **BLOCKER 1 — re-arm 后 usb0 没有任何 IP/MTU，软重枚举在真机上不可能成功，且会被误判成"已恢复"**
  `gadget.sh:357-386`（rearm_main）只做 unbind/bind，从不重做 `:527-530` 的 `ifconfig usb0 mtu 478 / $ECM_IP / usb0:1 $LL_IP`。UDC unbind 会注销 usb0 netdev，重新 bind 出来的是裸网卡：无 IP、MTU 回 1500（丢失 478 的 sunxi_udc DMA/bounce 规避）。后果：(a) 设备无地址，ping 发不出，bridge 监听 socket 绑在已消失地址/ifindex 上，mk20deck 永远连不回；(b) 更糟——重枚举后 Mac 会为 172.30.250.2 持续发 ARP 请求，这些帧能到设备 → usb0 RX 增长 → gate 第二次 probe 判 PASS（mk20d:395-402）→ **确认一个链路实际已死的 release**（正是本 feature 要防的事故换了形态）；(c) watchdog 同样走 `ecm_episode_end`（mk20d:362）记假 recovery，health 显示 ok。测试还把它固化了：`test_rearm_block_never_changes_configfs_and_never_reboots` 显式断言 rearm 块不含 `ifconfig`。修：bind 成功后等 `/sys/class/net/usb0` 出现，重做 mtu/ECM_IP/LL_IP 三条 ifconfig（GENV 白名单需加 MK20_LL_IP），并验证 bridge 监听存活（必要时重启 bridge）；设备验收清单补 `ifconfig usb0` 检查。

  **MAJOR 1 — gate 把"re-arm 被拒/读数失败"当成链路死**：rc=4 等满 90 s 后 FAIL（mk20d:388-392），`ecm_counts` 为空也 FAIL（:380）。前者需"真 stall+持续忙"才会触发，但一旦触发即回滚好版本；后者应 skip。修：rc=4 超时与无计数都降级为 WARN/skip，交给 loader 600 s 原路径。

  **MAJOR 2 — FAT `mk20-no-ecm-rearm` 语义矛盾**：文档称"只观察"，但 gate 在该标志下遇 stall 仍 FAIL（rc=7 → FAIL，且被测试固化）。设了观察旗标的机器反而更容易回滚。修：该标志下 gate 遇 stall 改 WARN skip。

  **MINOR**
  - 假阳性残余：主机不跑 mk20deck 且两个探测地址都不属于它（未按 INSTALL §5 配置）→ ARP 无人答 → watchdog 对好链路每小时 re-arm 3 次（每次 HID 掉 ~2 s）/gate 回滚。60 s 无 RX 后 ARP 条目必 stale、probe 会触发 ARP，挡住 stealth 防火墙场景；残余风险依赖 §5 前提，已在文档声明，可接受。
  - gate 的 re-arm 不做 typing 延迟（`ecm_rearm_try gate 0`，mk20d:391）；watchdog 的 key_events 检查不含旋钮。
  - `ECM_TICK_S` 校验（`*[!0-9.]*`）放过 `1.2.3`，sleep 失败后 maint 循环忙轮询且每圈写 ecm.state。
  - `ra_adb_session`（gadget.sh:353）只认 shell 子进程：adb push 阶段（尚无 install.lock）可能被 killall adbd 打断，可重试，概率低。
  - kbd 60 s 上限合理（Mac 手势 ≤600 ms）；注意迟到的 same-page 请求 keep_until（press+至多 70 s）会被 press+60 s 截断，测试已固化此取舍。

  其余核查通过：BusyBox 1.27.2 命令均在 fixture 内（ping/flock/mktemp/sed/awk）；无 `set -e`；/proc/net/dev 字段与计数器回绕/重置处理正确；锁随进程退出释放、re-arm 计数只计 rc 0-3、probe ≤8 s 有界；flapmon 日志格式与 `flap_recent` 正则匹配；拒绝面（install/loader 锁、watcher 队列、flap 窗口）齐全。测试覆盖面好，但 re-arm 用全桩抽取块，恰好看不到"重 bind 重建 netdev"这一真实行为——这是 BLOCKER 漏网的原因，须补设备验收。

