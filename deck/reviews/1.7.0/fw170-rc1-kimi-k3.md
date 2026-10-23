# Kimi K3 — fw 1.7.0-rc1 delta re-review (feature/fw-1.6.2, head b26203a), 2026-10-24 03:19 — GO

All 1.6.2-rc1 items FIXED; the MAJOR/MINORs below are fixed in 1.7.0-rc2. Record from the verdict on.

• VERDICT: GO（附验收前置：先在真机确认 `ping -c 1 -W 1` 可用）

  旧项核验：
  - BLOCKER **FIXED**：bind 后等 `/sys/class/net/usb0` 出现并重做三条 ifconfig+校验（gadget.sh:419-421、370-378），bridge 经自身 loop 重启并双地址验证（379-389），web 经 `web.rebind` 重绑（377、mk20d:786-790），`MK20_LL_IP` 已入 gadget.env 与 GENV 白名单（mk20d:595-603、gadget.sh:40）；恢复/gate 第二次探测均要求 ICMP 应答或新 bridge 客户端，不再认 RX 增长（mk20d:292-311、391-400、451-455）；测试用假内核重建裸 usb0，旧断言已反转。
  - MAJOR1 **FIXED**：rc=4 等满 90 s 与无计数均 WARN 不判（mk20d:423、433-446）。
  - MAJOR2 **FIXED**：FAT 旗下 gate rc=7→WARN、watchdog 不重启（mk20d:443-445、357）。
  - MINOR **FIXED**：gate 重臂让键盘且含旋钮（mk20d:290、433-437）；`ECM_TICK_S` 严格整数+1 s 兜底（270、278、718）；拒绝面扩到 adbd 任意子进程+push 收件箱（gadget.sh:357-363）；keep_until 截断已在 UI.md 声明接受。

  新问题：
  - **MAJOR**：非 fancy 路径 `ping HOST` 永不自行退出，而 1.7.0 把它包进子 shell 循环，`kill $!` 杀的是子 shell（mk20d:298-301、309）→ 每个 in-flight ping 成孤儿常驻（1.6.2 直接后台 ping，无此问题）。长 episode 中每 15 s 一轮验证泄漏至多 4 个 ping，可累积耗尽进程/内存，应答还抬高 RX。修：非 fancy 时 `( exec ping "$h" )` 直接后台；验收清单加一条 `ping -c 1 -W 1 127.0.0.1`（fixture 看不出 FEATURE_FANCY_PING）。
  - MINOR：`ra_push_recent` 只看 `/data/mk20|/tmp/mk20/incoming`（gadget.sh:361)，loader 实际走 `$tb` 参数与 `releases/.staging.tar/.incoming-*`（loader.sh:225-251)，快照内无宿主安装脚本可核对 push 落点——验收时确认一致，否则 push 保护为空。
  - MINOR：`ra_has_cfg`/`ra_listeners` 强制 usb0:1 别名与双监听（gadget.sh:366-369)，而 setup_main 对别名失败仅 WARN（574-575)：无别名运行的机器一次 re-arm 即 rc=2→重启（有界：ecm-reboots≥2 即停，hardreset.sh:38,45 会 sync 落盘）。建议别名缺失沿用 WARN 语义。
  - MINOR（已文档化）：adbd 永久子进程→永远 rc=4，升级需 3 次实际 re-arm 才触发（mk20d:361-363)，故永不重启，仅 WARN。可接受。

  重点问题结论：`ecm-reboots` 有界正确——升级仅当计数<1（mk20d:348)、≥2 停 re-arm(360)、probation 不重启（347)、证明链路才清零；Mac 屏 ICMP 且无 mk20deck 最多跨启动重启一次，不会循环。RECOVERY 手动路径安全（job.sh 先 mv 再跑、detached 逃脱 300 s kill_tree、push 检查不豁免）。无 unbind-不-bind 路径：point-of-no-return 后只有 rc0/2，rc2→cmd-reboot。锁序无 ABBA；web 重绑 gated=1 不计崩溃。

