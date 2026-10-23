# Kimi K3 — fw 1.6.1-rc2 delta (2f42a22..7c07ebf), 2026-10-23 22:56 — GO

• VERDICT: GO

  **四个 MINOR 复核**（对照源码，均与 delta 一致）:
  1. **FIXED** — 替换客户端清零旧 pending:`ttygs_tcp_bridge.c:178-181`(drop 计数+日志，`plen=poff=0`);stall 计时随新客户端首字节重置（:201 `prog=mono_ms()`)。测试证 rc1 会误关、rc2 不会。
  2. **FIXED** — 每 streak 只记一行：:74-79 常量（60 s/10 min)、:161-163 截止取 min(pending,streak-end)、:206-222 首行全量/静默/计数/结束行，逻辑无死角（孤立单 stall 不记 over 行，60 s 后新 stall 重新全量，正确）。
  3. **FIXED**（按建议接受并文档化）— `kbd.c:137-140` 注释 + UI.md 竞态段，行为不变。
  4. **FIXED** — stall 文件 `bridge.c:47-55/122/148/214`,`gadget.sh:436`（仅 MK20_REL，先于 :439 mkdir、:441 启动）,soak `mk20_soak.py:68-69/200-225/400-410` 读入并标记 self-healed、汇总计数；stalled 仍压过 self-healed 且 self-healed 保持 ok，无 rc1 回归。

  **新问题排查**：无 BLOCKER/MAJOR。stall 文件 tmp+rename 原子、O_NOFOLLOW、rename 不穿透目标符号链接、退出 unlink 只删自身；启动失败记日志并降级（:148);0644 在单租户 root 设备无碍；tmpfs 满→计数冻结，方向 fail-safe。gadget.sh 随各 release 自带且 `$BR=$MK20_REL/bin/...`(:52-53)，回滚 1.5.1/1.6.0 用其旧脚本旧桥，不会跨版混搭；新 soak 对旧固件读到 None，等同 rc1。adb 载荷 MEM_SH≈175 B，测试断言 ≤400。

  **新 MINOR**:
  1. 运行期 `stall_file()` 失败完全静默（仅启动失败有日志）,tmpfs 满时 soak 计数悄悄冻结——建议首次失败记一行（`ttygs_tcp_bridge.c:214`)。
  2. RELEASE/UI.md 援引 mk20d maint 截断 bridge.log(256→64 KiB,`test_maint_rotates_and_trims`)；本快照无 mk20d 源码及该测试，无法证实。若不存在，重连风暴的 connected/gone 行仍无上限——验收前确认。

