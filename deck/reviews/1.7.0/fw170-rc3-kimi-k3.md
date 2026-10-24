# Kimi K3 — fw 1.7.0-rc3 delta (a5cc8c1..8c4b279), 2026-10-25 00:40 — GO

• VERDICT: GO

  **flap 记录正确性** — flapmon.sh:46 `echo > .t && mv -f` 原子写；flock(fd 8）保证单实例单写者。关键对称性成立：gadget.sh:613 仅在新 monitor 启动前清记录，而新 monitor 的窗口同为空（flapmon.sh:29 `times=""`)——记录与 flapmon 内存窗口同步重置，不存在"记录空了但 flapmon 仍计数"的错位，故 re-arm 的 1–2 drops 不可能在窗口被清空后叠出 FLAP_DROPS≥4，无回滚风险。旧 monitor 在 stopfile(gadget.sh:524）后 ≤0.3s 退出，且一旦进入 FLAPPING 分支永不回到 poll 循环，不可能在 613 行 rm 之后再补写；re-arm 自身不重清记录，其 drops 由在跑的 flapmon 如实写入 → 65s 内再次 re-arm 被拒，语义与 rc2 一致但时钟正确。记录损坏时 case 守卫 fail-open(gadget.sh:358)，可接受（写者仅 root 的 flapmon 且原子）。

  **adb push 打开文件检测** — gadget.sh:369-370 遍历 `pidof adbd` 的 `/proc/PID/fd/*` readlink；readlink/pidof 均在设备可用清单（tests/fixtures/device-commands.txt:250,263)。adbd sync 服务于整个 push 期间持有目标 fd（首个 DATA 包 open、DONE 才 close)，测试用 `exec 3>>` 替身经真实 /proc 验证。MINOR：两个固有空窗——(a) adb 连接建立到 adbd open() 之间（毫秒级，rc2 的 mtime 窗口同样有起始空窗）;(b) 多文件 push 的文件间 close/open 间隙。后果仅是 push 被切断、inbox 留残文件（rc3 起不再阻塞，loader 校验 sha 拒残包），可重试，方向安全。

  **跨启动比较复查** — 无残留：gadget.sh 已零 `date` 调用；loader deadline 同 boot 计算同 boot 轮询（loader.sh:167-173),`deadline-reset-<v>` 仅存在性；watcher/hardreset 的 uptime 纯日志；kbd/agent 状态 age 走墙钟但负值=stale(mk20d:129-131,150-153,1.3.1 起）;kbd 崩溃窗有 `n>=$1` 守卫（mk20d:141);ecm.state 在 tmpfs,ecm_sane(342-346）两个调用点（389,428）全覆盖，ecm_load 白名单仅数字（333)。

  **回归** — 拒绝顺序/文案与测试一致；test_flapmon.py 不在本快照，仅按 diff 审（start() 已注入 MK20_FLAP_LASTDROP,run_fm 不可见，以 RELEASE.md 测试声明为准）。sha 声明未复算（按要求未跑套件）。上机验收项照旧：rc=0、`REARM OK`、记录缺失或陈旧。

