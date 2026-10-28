# MK20 Agent 增强固件 — 进展文档

> 每有关键进展即更新。时间均为新加坡时间（SGT）。最后更新：2026-10-28 14:44 SGT

## 1. 当前状态

| 工作线 | 分支 | 提交 | 状态 |
|---|---|---|---|
| hidfix（HID 模块修复） | feature/hidfix | 5452175（证据 4eb0bf4） | 第 11 次通过（20:50）：T4 50/50 + T7×3，解绑 57 次，0 Oops；codex 第 3 轮复审由用户豁免（Kimi GO + 离线 58/58） |
| K4 / 1.7.x | release/1.7.1 | v1.7.1-rc1（2b194f1） | **v1.6.1 生产版**（10-24 23:37，24 h soak PASS）；**1.7.1-rc1 已装机 good**（10-25 01:21），真机软重连测试 PASS；**1.7.1 soak 结论未知**（10-25 01:23 起，在 dev-mbp 上跑，数据在已离线的 dev-mbp `soak-20261025-012324.*`；Gitea 无结论、无 v1.7.1 tag） |
| agent（A8c） | feature/agent | 4d07ddd | 已过评审；不在 rc2，进下一个 RC；默认 FEATURE_AGENT=0 |
| 板载 UI | feature/onboard-ui | 7bc959f | U1–U2 完成（待核实，推断），U3–U7 待做 |
| UVC 采集卡 | feature/uvc | 02f1d41 | UHC07P = MS2130，USB 2.0 下仅 MJPEG；待上机 |
| 设备 | — | — | **10-27 起接在 iMac（dev-imac）**；fw 1.7.1 good，B 槽（gen 28），1.7.0 在 A 槽；health ok、自检全 PASS；mk20-kbd normal、21/21 屏、旋钮 6/6；**FAT 剩余仅约 1.2 MB**；10:23 经 cmd-reboot 安全重启验证 OK；iMac 侧 ECM 网卡仍未起（macOS AppleUserECM 驱动自身崩溃，见日志） |
| iMac 主机 | chore/imac-tooling | deecd26 | adb 需 platform-tools **r35.0.2**（37.0.1 遇空序列号设备崩溃）；`~/mk20-backup/mkadb.sh`（仅 MK20）、`~/worktrees/route-check.sh`；**ECM 网卡未起**（macOS 15.8 Intel 的 AppleUserECM dext 在 init() 断言崩溃）→ mk20deck / 桥接 / 网页 / soak 暂不可用；route-check 改为按出口 203.0.113.10 判定（Hy2 或 SS 均可，非 Hy2 告警）；mihomo 运行时 AI→AI-Auto 固定 Hy2（未写配置） |
| 10-25 后新分支 | feature/gd32-dfu-notice、feature/k1-scroll、mac/1.7.1-gd32-notice、mac/1.7.1-k1-scroll、feature/gd32-default-keymap | cef750b / c702ff7 / 59a8334 / b21fbda / a581757 | GD32 DFU 提示、K1 滚动会话历史（均 Kimi K3 GO，未部署）；GD32 默认键位 KC_NO 补丁仅分析，**未刷写**（刷 GD32 仍禁止，需用户明确决定） |
| PRD | main | c582581 | **PRD v1.5 已批准**（10-25，用户）：生产版保留 ADB；§17 补到 1.7.1 |

rc2 固件包：Mac `~/worktrees/k4-rc2/mk20-fw-1.3.0.tar`，sha256 `49284d191afadab6c26644d30a2e870a46b06dc92a9b5c73bad0a4e9019e4f4f`。

## 2. 关键进展日志（新的在上）

- **10-28 14:44** **fw 1.8.0-rc1 24 h soak soak-20261027-144438 结束：PASS**（288 样本；判定与数据 feature/ncm-gadget 8fb7041 deck/reviews/1.8.0/soak-20261027-144438/VERDICT.md，自动生成，未打 tag）
- **10-27 14:47** **iMac 部署 mk20deck + 启动 fw 1.8.0-rc1 24 h soak**。① 部署：`~/mk20-deploy` = feature/ncm-gadget 4eb998f（Mac 侧同 release/1.7.1；**未并入** mac/1.7.1-gd32-notice、mac/1.7.1-k1-scroll，留待 soak 后），venv uv python3.13；config `~/.config/mk20-agent-deck/config.toml`（来自 dev-mbp 归档，Ghostty→Terminal、tmux=/usr/local/bin/tmux；mk20deck 无需 VNC 密码）；LaunchAgent com.leo.mk20-agent-deck running；Claude hooks 三个 settings*.json 追加合并、Codex `~/.codex/hooks.json` 新建（需在 Codex App 设置→Hooks 信任）；备份 `~/mk20-backup/pre-deploy-20261027/`。**阻塞：mk20deck 连不上桥接**——macOS 15 本地网络隐私拦截 uv 的 python3.13（connect 172.30.250.2:17890 → EHOSTUNREACH 65；nc、系统 python3 均通），需用户在 系统设置→隐私与安全性→本地网络 允许该 python，supervisor 会自动重连。② soak：**开始 10-27 14:44:38 SGT（01:44:38 CDT），预计结束 10-28 14:44 SGT（10-28 01:44 CDT）**，`soak-20261027-144438`，launchd com.leo.mk20-soak-180（caffeinate -i -s），每 3 h 推部分数据（com.leo.mk20-soak-180-push），结束自动 VERDICT.md + 本文件一行；首个样本 ok；部分数据已推 feature/ncm-gadget 798c317，工具副本 bfdbfb6。桥接未连期间 host_link=idle/unknown。查看方法见 HANDOFF。
- **10-27 14:31** **NCM 1.8.0-rc1 设备验证 PASS**：双向同时 20 MiB TCP（设备→Mac 7.0 s ≈ 24 Mbit/s；Mac→设备 20971520 B 全收），usb0 tx_dropped/rx_errors 0，dmesg 无新行，UDC 一直 configured；Mac en2 MTU 仍 1500（改需 sudo，未做）：TCP 不受影响，Mac→设备 ICMP > 462 B 丢（待用户 sudo networksetup -setMTU en2 462）；1.8.0 由 probation gate 自动 confirm（未手动）；证据 4eb998f；相机 after 图 14:29
- **10-27 14:27** **NCM 1.8.0-rc1 安装成功并自动 confirm**：slot A 1.8.0 good（selftest PASS + 'PASS ecm host traffic (usb0 rx 42 -> 43)'，真实 Mac 流量，uptime 92 s）；iMac en2 = AppleUSBNCM（MAC 02:11:22:33:44:56），AppleUserECM 未再接管；host_net sync 无需 sudo → 172.30.250.1/24，ping .2 0% loss，:17890/:8780 通；证据 feature/ncm-gadget 666e69c。双向大流量测试进行中
- **10-27 14:21** **NCM 1.8.0-rc1 安装开始**（feature/ncm-gadget f957d74）：全量 deck 测试 1431 passed/2 skipped（2aa7668，新 docker 镜像含 procps；imac-tooling 47dde2c）；tar sha 294e1544…ecc5；设备 1.7.1 good slot B → mk20-install.sh --reboot --wait（watcher cmd-reboot，probation 600 s 自动回滚）
- **10-27 13:27** **NCM 上机 · 加载测试 PASS**：基线 fw 1.7.1 good（B 槽 gen 28），iMac 侧 usb0 仍无链路（ECM dext）。`usb_f_ncm.ko`（6699be5f…）推到 /data/mk20/incoming，insmod rc 0 / rmmod rc 0，dmesg 无新错误，UDC 保持 configured。证据 feature/ncm-gadget 3a09aff `deck/reviews/1.8.0/device-ncm-20261027/`。下一步：打 1.8.0-rc1 包装到待定槽。
- **10-27 13:20** **iMac ECM → CDC-NCM（1.8.0 候选，未上机）**：分支 `feature/ncm-gadget`（基于 release/1.7.1，提交 012bd19 WIP + a06c081）。`usb_f_ncm.ko` 按 5.4.61 #221 构建（vermagic `5.4.61 SMP preempt mod_unload ARMv7 p2v8`，256388 B，sha256 6699be5f…c436，未定义符号全在设备 ksyms 内）；f_ncm 补丁让每个 NTB ≤512 B（OUT 512 / IN 上限 508 / wMaxSegmentSize 476 → NCM MTU 462，TX 守卫精确、含 CRC 模式）。gadget.sh 优先 NCM，ko 缺失/被拒/建函数失败自动回退 ECM；`/mnt/SDCARD/mk20-no-ncm`、`FEATURE_NCM=0`、`MK20_NCM=0` 强制 ECM；re-arm/看门狗兼容两种函数。离线：deck tests 1430 passed / 2 skipped（BusyBox 1.27，含 procps）+ shellcheck + tools 83 passed；GLM 评审 2 个阻断（守卫多算 5 B 丢满 MTU 帧；切换时旧实例占 usb0）已修，复审 APPROVE×2。**未上机**，上机测试计划见报告。
- **10-27 10:40** **dev-mbp 资料归档到 Gitea**（用户将关机；dev-mbp 不在家里局域网，经 home-dev-vm 跳板推送）。**1.7.1-rc1 soak 未完成/无结论**：3 次运行均被外部事件打断——012324（12.7 h 干净后 14:08 外部断电+GD32 DFU）、164758（键盘被拔 17:05–22:31）、224813（干净 10.0 h：121 样本全 ok、重启/回滚/链路卡死 0、SUnreclaim 9.7 kB/h 趋势 flat，08:53 键盘移走）；未见固件故障，需在 iMac 重跑 24 h。归档分支 `archive/dev-mbp-20261027`（孤儿分支：mk20-backup 脚本/证据/验收/soak、mk20-deploy 文档、mk20-build-local docker/日志、~/.config/mk20-agent-deck、LaunchAgent、worktrees 评审输出；固件 release tar、live-221 原厂 dump、tf 启动镜像、tina54 内核源码 tar；超大文件见 MANIFEST-large.txt；已扫描无密钥）。新推分支：docs/prd-1.5-approved、integ/1.5.1-b/c/d、k4-install-wt、release/1.3.2、mac/1.7.1-gd32-notice(快进)、agent-a1-codex、hidfix-dev、hidfix-r10、hidfix-r11、hidfix-r11b、k4、onboard-ui、onboard-ui-k4doc、onboard-ui-u2、uacmic-dev、uacmic-mod、uacmic-review；未提交改动 → wip/dev-mbp-20261027/*（10 个）。~/mk20-full 已同步 origin/main。
- **10-27 10:28** iMac 跟进：① 代理（用户决定：看出口 IP，Hy2 优先、SS 备份）：iMac mihomo 其实有 US-Exit-Hy2（在 Fallback 组 AI-Auto 里，Selector AI 不能直选）→ 经 API 运行时改 `AI-Auto` 固定 Hy2、`AI`→AI-Auto（未改配置文件、无 sudo；备份 ~/mk20-backup/mihomo/proxies-before-20261027-1023.json；回退 `PUT AI US-Exit-SS` + `DELETE AI-Auto`）；route-check 新规则 PASS：`AI > AI-Auto > US-Exit-Hy2`，TUN 与 :7890 出口均 203.0.113.10，常规连通正常。② 安全重启：10:22:53 `cmd-reboot` → 10:23:18 USB 断开 → 10:24:22 adb 回来；loader current=B good=1.7.1，health ok、自检 PASS。③ **ECM 仍不通，根因在 macOS**：每次枚举 Apple 自带 DriverKit 驱动 com.apple.DriverKit.AppleUserECM 在 `AppleUserECM::init()`→`IOUserNetworkEthernet::init()`→`IOService::init()` 断言 SIGABRT（崩溃报告 09:49、10:24 两份），尚未接触设备即崩 → 控制接口退回内核 AppleUSBECMControl、数据接口被 AppleUserECMData 占住但无配对 → 无 enX。设备内核 5.4.61 只内建 RNDIS，ECM 来自自编 usb_f_ecm.ko。建议：gadget 改 CDC-NCM（自编 usb_f_ncm.ko，macOS 由内核 AppleUSBNCM 认领、无 dext 竞争）或临时用 adb forward（需桥接加听 127.0.0.1）；未做任何 sudo/kext 改动。工具提交 chore/imac-tooling deecd26。
- **10-27 10:10** **设备迁到 iMac**（dev-mbp.lan 离线；用户 10-27 把键盘插到 iMac）。只读核实：fw 1.7.1 good（B 槽，A=1.7.0），loader `current=B gen 28 good=1.7.1 pending=`，health ok、自检全 PASS，运行 12 min（迁移时断电重启）；loader.log 显示 1.7.1 确认后又启动 5 次（无 RTC，无法判断是否在 soak 窗口内）。**1.7.1-rc1 soak 结论未知**：soak 在 dev-mbp 主机侧运行，数据在 dev-mbp，Gitea 无结论/无 v1.7.1 tag。iMac 工具（chore/imac-tooling 8867fb9）：adb 37.0.1 遇空 USB 序列号的 MK20 被系统 EXC_GUARD 杀掉、libusb 后端跳过该设备 → 用 r35.0.2（transport_id 选设备）；新写 `~/mk20-backup/mkadb.sh`（原版只在 dev-mbp）与 `~/worktrees/route-check.sh`。**iMac 上 ECM 网卡不出现**：控制接口被内核 AppleUSBECMControl 认领、数据接口被 DriverKit AppleUserECMData 认领且未注册 → 无 enX，mk20deck/桥接/网页/soak 受阻（需用户拔插重试或驱动层处理）。route-check：iMac mihomo 的 AI 组只有 US-Exit-SS（Shadowsocks），出口 203.0.113.10 US，按 Hy2 规则判 FAIL，待用户决定；决定前不跑 codex/kimi。10-25 后另一线程的工作：GD32 DFU 提示、K1 滚动（Kimi GO，未部署）、GD32 默认键位补丁（仅分析未刷写）；PRD v1.5 已批准（生产版保留 ADB）。设备 FAT 剩余约 1.2 MB 需留意。dev-mbp `~/mk20-full` 未同步（离线）。
- **10-25 01:30** **v1.6.1 生产版**（24 h soak PASS：288 样本全为 0，链路卡死/自愈 0，内存持平）；**1.7.1-rc1 已装机 good**。1.7.x：USB 网络接收看门狗（Mac 在、网卡通、收包 60 s 不涨且探测证实 → 软重连并恢复 MTU/IP/别名与桥接/网页，Mac 真回应才算恢复，每小时 ≤3 次后最多一次硬复位）+ 试用期必须看到 Mac 真实流量才确认。Kimi K3：1.6.2-rc1 NO-GO（软重连后网卡没 IP 还会被误判恢复）→ 1.7.0-rc1/rc2/rc3 GO。真机：1.7.0-rc2 的软重连被拒（读到上次开机的断线记录，重启后约 20 小时内看门狗失效）→ rc3 修复，因设备已装 1.7.0、以 1.7.1-rc1 发布；1.7.1 上真机软重连测试 PASS（rc=0，网卡保留，Mac 约 7 秒重新握手）。make test 1407 全绿。
- **10-23 23:20** **1.6.0 soak FAIL → 1.6.1-rc2 已装机**。20:01 主机链路卡死 51 分钟：设备内核（5.4.61）的虚拟串口输入通路停滞，桥接程序阻塞在写入上、不再接新连接，Mac 重连全部超时；soak 工具没察觉（不查链路）。手动 tcflush 恢复后定位根因。1.6.1：桥接不再阻塞（停滞 2 s 自动清空并断开让 Mac 重连）、mk20-kbd 静默 5 s 兜底清空、soak 增加链路存活与自愈统计、Mac 端断连期间每 10 分钟记日志；Kimi K3 rc1 GO、rc2 增量 GO；make test 1283 / mac-test 817 全绿。装机时第二个事故：重启后 USB 网络 host→device 方向不通（loader 照样确认 good），watcher 硬复位后恢复，重新验收全 PASS；列入 1.6.2（ECM 接收看门狗、确认前要求真实网络流量）。另：10-23 00:48 巡检的 661 字节 adb 请求触发 adbd EOVERFLOW 导致 USB 重新枚举 → 规则 adb 请求 <512 字节，soak/验收工具加 480 字节保护。
- **10-23 05:40** **v1.5.1 生产版**：1.5.1 24 h soak PASS（288 样本，失败/重启/kbd 重启/复合丢失/回滚全为 0，ping 0 丢包，内存与内核不可回收内存持平；00:48 一次 USB 重新枚举是巡检的 661 字节 adb 请求触发 adbd EOVERFLOW 所致、设备与 Mac 自行恢复，之后定规则 adb 请求 <512 字节）。tag v1.5.1 = release/1.5.1 f8de052（固件 tar 3d7c1470…e41b 可从 tag 复现）。**1.6.0-rc4 已装机 good**（05:26，验收全 PASS；旧 Mac 端与新固件兼容已实测），Mac 端随后部署 release/1.6.0（hello 带 page=L1）。1.6.0：同页 ui.page 不松按住的键（PTT 120 s/10 min、保持键 10 s/60 s、丢 up 后锁存）、hello 报告当前页并重置序号、LS 页「会话安排」键、急停暂停保留按键归属（顺带修 1.5.1 两个边角 bug）、Mac 侧 epoch 围栏；Kimi K3 rc1 NO-GO → rc2/rc3/rc4 GO；make test 1272 / mac-test 815 全绿。10-22 白天：会话安排网页连接上限与 macOS 优雅关闭（Kimi 三轮）、macOS 原生测试门槛 make mac-test。用户离开数天（10-22 10:30 起），待用户事项见 HANDOFF Open items。
- **10-22 09:00** Mac 端两次更新（固件不变，仍 1.5.1-rc1）：① 07:10 **会话安排网页上线**（http://127.0.0.1:8781，终端运行 `mk20deck arrange` 打开，授权 30 天；Kimi K3 r2 NO-GO（一次性链接经 `open` 参数可被 ps 看到）→ 链接改写进 0700 目录里的 0600 .webloc、HTTP/1.1 keep-alive、拒绝测试去竞态 → r3 GO；集成 make test 1160 通过）。② 08:56 **键盘与 Mac 页面同步**：代码审查发现长按模式键只切了 Mac 页面，键盘仍在 L1，之后 K2 反向、键 5 被当成回 L1、K1 打开旧 Mac 设置页；改为 Mac 每次换页都发绝对 `ui.page`（固件早已支持），未确认即重发，K1/K2 转发一律当单击；Kimi r1 NO-GO（在途请求被 K2 判过期后永久错页，已用真实固件复现）→ 修复 → r2 GO；真实固件端到端测试覆盖交错。③ devtest HID 泄漏判定按 1.5.x 规则（PTT + 底排 ⌫/⏎/Esc + 授予的数字键）重写，Kimi r1 NO-GO → r2 GO。集成 make test 1218 通过（tests 1218 / onboard 12957 / kbd 4191×2 / agent 398 / ui 41396）。PRD v1.5 草案在 docs/prd-1.5（待用户批准）。soak 3.7 h 全部合格。固件后续（下一 rc）：同页 ui.page 不释放按住的键、hello 回报当前页、generation 用尽可重置。
- **10-22 05:04** **1.5.1-rc1 已装机 good（05:01）**：选项模式（点等待输入的会话→上三排清空、首排显示选项，按数字直接选；固件经 ui.hidKeys 发瞬时数字键，按住说话时忽略），Kimi K3 GO（2 个 MINOR 已修），make test 全绿（tests 1000 / onboard 12957 / kbd 4191×2）；Claude hook 脚本已更新（只记选项短标签）。此前：10-21 23:00 用户照片证明物理屏顺序错（fb1–4 是最右一列）→ 1.4.3 修复，用户确认；10-21 23:20 用户定新布局（15 会话 + 底排 说话/删除/发送=可以/Esc/模式）→ 1.5.0（01:31）；空闲会话不再闪；长按拿起-放下移动钉扎已部署。会话安排网页（127.0.0.1:8781）已做完但 Kimi NO-GO，修复中，未部署。1.5.1 soak 05:02 开始。
- **10-21 21:31** **1.4.2-rc3 已装机 good（21:29，slot A；1.4.1 在 slot B 作回退），验收全部 PASS**。起因：20:47 巡检见 1.4.1 上 MemAvailable 约 -0.75 MB/h，查到 /tmp/adbd.log（tmpfs=内存）2.5 h 涨到 2.4 MB——调试版 adbd 每条 adb 命令都记日志且以 `>` 打开、从不截断（主要由测试的 adb 轮询触发）；Slab 与我们进程 RSS 平稳，非泄漏。修复：adbd 改追加写 + mk20d maint 与其他 /tmp 日志一样截断（Kimi K3 GO）。另修 K3 测试的两处时序竞态（固定 150 ms 窗口、写线程占用面板期间 d=0 的快照），24×ASan 负载 + TSan 全过。make test 全绿。1.4.1 soak 3.2 h 全部合格（无 WARN）。1.4.2 的 24 h soak 21:30 开始，10-22 21:47 自动判定。
- **10-21 18:19** **1.4.1-rc2 已装机 good（18:17，slot B；1.4.0-rc1 在 slot A 作回退），验收全部 PASS**（含 Mac 屏幕睡眠时开机自动进 L1、/run/mk20 为 tmpfs、网页全套）。起因：16:47 巡检发现 1.4.0 soak 16:00 有 1 个 degraded 样本（自检 WARN，未能归因），排查时发现 /run 在 TF 卡 overlay 上、mk20-kbd 主循环每秒往 TF 写状态文件（磨损 + TF 卡顿会拖住急停/HID 主循环 + 重启后残留）→ rc2 开机挂 512 KiB tmpfs（Kimi K3 GO，MINOR 已修：检查监管锁防 TOCTOU）；soak 现在会记录 WARN 原文。另修一个 ASan 下偶发的 K3 定时测试。make test 全绿（tests 877 / agent 398 / onboard 11348 / kbd 4019×2 / ui 41396）。1.4.0 soak 收尾：82 样本（6.8 h），仅 16:00 一次 degraded，0 重启/崩溃/复合设备丢失。1.4.1 的 24 h soak 18:18 开始，10-22 18:37 自动判定；每 2 h 巡检。
- **10-21 11:54** mk20deck Kimi K3 评审 GO（hooks 不影响 Claude 权限/不存内容、设备无法让 Mac 打开任意 URL、tmux 无注入）；唯一 MAJOR（心跳按 ack 计时导致设备侧间隔=周期+RTT）已改为按实际写出时间计时，另修 hooks 安装原子写/python3 检查（release/1.4.0 a0d6537，已重新部署）。真机：上一连接 1421 个心跳全部接受、0 拒绝、0 超时；Mac 屏幕睡眠时设备停在 L1 显示「Mac 已休眠」（jpg_ok 持续增长、jpg_bad 0）。四条线（rc3 / 界面 / 网页 / mk20deck）交叉评审全部 GO。剩余生产门槛：1.4.0 24 h soak（10-22 11:24 到期，已设到期自动判定）+ 用户亲手验证 fb20 模式键 / fb19 打开网页 / L1 PTT / K1 跳「需要我」。会话内每 2 h 自动巡检 soak 与 Mac 服务。
- **10-21 11:27** **1.4.0-rc1 已装机并确认 good**（11:15，slot A，1.3.1 保留回退；tar sha256 b3071a20…1e04，tag v1.4.0-rc1）。验收 12/12 + 1.4 专项：网页默认运行、错误 Host 403、键盘配对码 0600 + 配对成功 + 配对后删除、无 CSRF/跨源写 403、维护操作走通、退出后 401；**开机自动进 L1 实机通过**（无任何按键，jpg_ok>0）。装机时发现并修复 mk20deck 两个问题：Mac 屏幕睡眠时设备重启后不重连、暂停时未重发「Mac 已休眠」画面（ebe8814 及后续，已重新部署）。Kimi K3：界面 r1/r2 NO-GO → 修复（急停后暂停转发 + 5 s 兜底重同步、fb20 租约竞态、状态探针移到工作线程、急停常驻提示、安全模式显示配对码）→ r3 GO；网页 GO（5 个 MINOR 已修）。make test 全绿（tests 863 / agent 398 / onboard 11348 / kbd 4018×2 / ui 41396）。1.4.0 上 24 h soak 11:24 开始。交接文档已重写（HANDOFF.md）。
- **10-21 10:19** Mac 端 mk20deck 已部署为 launchd 常驻服务（~/mk20-deploy，固定在 feature/deck-attention 7babf2b；配置 ~/.config/mk20-agent-deck/config.toml：terminal_app=Ghostty、tmux 新会话=ccai sg）：经 usb0 桥连接，ui.hello + 心跳被 1.3.1 接受，已识别真实 Codex Desktop 线程。**在 1.3.1 上进主界面：HOME 按「会话 L1」**（1.4.0 起开机自动进）。Claude hooks（「哪个 agent 需要我」）已装入 ~/.claude/settings-glm.json（ccai 所有模式的源头）与 settings.json，备份 *.bak-mk20-20261021-095159；新会话生效。崩溃循环验收 PASS（3 次 SIGKILL → 安全模式 reason=crash-loop，自检只 WARN、HID 可用 → exit-safe-mode 钩子恢复；tools/mk20-crashloop-gate.sh，72d24a9）。1.3.1 soak 11.5 h：0 重启、0 真实崩溃（5 次为验收诱发）、0 复合设备丢失、ping 0 丢包、mk20-kbd RSS 2.3 MB 不涨；soak 报的 126 次 kd-restart 是 soak 工具 pgrep 自匹配 bug（716f5d4 已修）。Kimi K3：rc3 GO；网页单一入口 GO（5 个 MINOR 修复中）；界面修订 NO-GO（e-stop 后转发、租约竞态、devstat 在安全主循环做 I/O、急停指示）→ 修复中。release/1.4.0（网页 + mk20deck）make test 全绿（tests 861 / agent 396）。PRD v1.4 草案 §15（待批准）。
- **10-20 22:56** 1.3.1 上机后续：① 全量测试在本机（dev-mbp，colima + mk20-build:trixie，BusyBox 1.27.2，测试 PATH 只含设备上真有的命令）**715 通过 / 0 失败**；先前 46 个失败全是镜像缺 procps（pkill），构建镜像与 BusyBox 1.27.2 配方已入库 deck/tools/build-image（e9fb269）。② soak 改为 K4 规则（原先要求 kd=1，K4 上每个样本都会判失败；37b455d），24 h soak 已在 1.3.1 上开跑（22:31，~/mk20-backup/soak/soak-20261020-223110.*），首样本 ok。③ 「usb-storage 每 23 s 复位」：1-1 实际是 Realtek 无线网卡（0bda:b711），1.3.x 默认不启动 mk20-router，开机 20 min 复位 0 次——当前版本不再出现；router 本身问题保留。④ 交互评审（Claude 子代理）+ Kimi K3 交叉验证（codex 满载不可用）：11 证实 / 3 部分 / 0 驳回；另发现物理键盘无法切换 Desktop/herdr/tmux 模式。⑤ 设置入口盘点：1.3.1 上普通用户没有可用的设置通道（LS「应用」恒失败、网页默认关且配对坏），HOME 20 格中 8 格数据设备已有却显示 unknown。⑥ 用户决定：键盘网页 http://172.30.250.2:8780 为唯一设置入口，按键只显示；「网页设置」键按下由 Mac 端自动打开网页（不做二维码：网页只在 USB 链路上可达）。进行中（各自 worktree）：feature/deck-ui-hello（mk20deck ui.hello/心跳 + ui.openSettings）、feature/fw-ux（HOME 真实状态、开机 Mac 在线进 L1、右下角键还给「模式」、只读设置页、配对码显示）、feature/web-settings（网页默认开启、配对修复、维护页、单一 provider 目录、deck/SETTINGS.md）。合并后出 1.4.0，过 Kimi 交叉评审 + 上机验收 + 崩溃循环 + 24 h soak。
- **10-20 22:12** K4 rc3（1.3.1）安装通过：loader confirmed good，验收 12/12（K4 规则 kd=0 + mk20-kbd normal），KD 未运行，21/21 屏、旋钮 6/6、0 内核告警；1.2.1 保留为回退。此前唯一的 FAIL 是判定脚本把 post-kbd.txt 里前一段 kbd.status JSON 当 state.json 解析（已修，重判 PASS）。证据 deck/reviews/k4-install/k4-rc3-install-20261020-220751/
- **10-20 22:10** K4 rc3（1.3.1）：安装/验收未过：FAIL 1 K4: maint state.json normal, 0 restarts（证据 deck/reviews/k4-install/k4-rc3-install-20261020-220751/）
- **10-20 22:08** K4 rc3（1.3.1）：安装前验收 1.2.1 通过（12 项），槽位已备份；开始安装 + 重启（watcher cmd-reboot）
- **10-20 22:07** K4 rc3（1.3.1）：开始安装（sha bdd51ce3…）；先做安装前验收和槽位备份
- **10-20 21:47** K4 rc2 结论更正：rc2（1.3.0）**没有装成功**，键盘已自动回滚到 1.2.1（loader：`last_rollback=1.3.0->1.2.1 not confirmed after 1 attempt(s)`，进程是原厂 KeyboardDevice）。21:20 的状态行列的是失败项名称，被误读成了通过。1.3.0 本身能启动（mk20-kbd 运行，21/21 屏，旋钮 6/6，0 内核告警），但自检 `FAIL kbd status stale (38525s)` → 试用期不确认 → 600 s 后 loader 硬复位 → 回滚。根因：设备 BusyBox 1.27.2 没有 `stat` applet，`stat -c %Y` 为空 → 年龄 = 当前时间；离线测试退到主机 GNU stat 所以没发现。同因还有维护请求的硬链接检查 `stat -c %h` 在设备上会拒绝所有请求。rc3（1.3.1）修复：`date -r` / `ls -ld` 替代，负年龄判过期（`/run` 跨重启保留、无 RTC）；测试只暴露设备上真有的命令（fixtures/device-commands.txt，从设备采集），并用 busybox 1.27.2 跑。另修：验收工具 K4 下按 kd=0 + mk20-kbd 判断；里程碑脚本崩溃（macOS bash 3.2 在 `$VAR` 后紧跟中文时吞字节）。证据 deck/reviews/k4-install/k4-install-20261020-210750/
- **10-20 21:07** K4 rc2：安装前验收 1.2.1 通过（12 项），槽位已备份；开始安装 + 重启（watcher cmd-reboot）
- **10-20 21:07** K4 rc2：开始安装（sha 已核对 49284d19…）；先做安装前验收和槽位备份
- **10-20 21:04** 文档修正：表头时间和状态表改为每次里程碑自动更新；~/mk20-full 根目录的 PROGRESS.md 是散放副本（不在 git 里），之前同步只更新了里面的仓库 mk20-agent-deck/，现在每次都从 origin 复制并核对 sha（见 ~/mk20-full/SYNC.txt）。K4 rc2 开始安装
- **10-20 20:58** hidfix 第 11 次：第 11 次通过（完整规格，20:42–20:50）：T1–T7 全过；T4 对照 20 轮 + 真实 50/50 轮，T7 3 次，解绑/绑定 57 次（=周期数+7），引用计数/UAF 门槛无告警；不可回收内存斜率 实测 1.34、对照 2.32、差 −0.98 kB/轮（上限 4），总增 100 kB（上限 1024）；sunxi 报错对豁免 16 次（上限 29，都在 3 秒内恢复），新开设备重试 0 次；0 Oops，0 条 hidfix/hidg 行；唯一的 WARN 是切换时原厂 f_hid 的 refcount（见 REFCOUNT-RCA；因为只打一次，之后的引用计数告警看不到）；键盘复位后恢复到 1.2.1。dry11-2 也通过（8 次解绑）。codex 第 3 轮复审已由用户豁免（Kimi GO + 离线 58/58）。证据 4eb0bf4
- **10-20 20:50** hidfix 第 11 次：run11 结束 exit=0：# hidfix run11 — PASS
- **10-20 20:42** hidfix 第 11 次：run11 开始（profile=full，HEAD 8ab7c44）
- **10-20 20:42** hidfix 第 11 次：dry11-2 结束 exit=0：# hidfix dry11-2 — PASS
- **10-20 20:38** hidfix 第 11 次：dry11-2 开始（profile=dry，HEAD f1e469e）
- **10-20 20:38** hidfix 第 11 次：codex review3 waived by user 20:36, Kimi GO + 58/58 offline（最后一次 codex：gpt-6.1-sol、effort high，仍无结论，满载报错 2 次）；开始 dry11-2
- **10-20 20:35** hidfix 第 11 次：补记 20:26：codex 第 3 轮复审第 2 次（gpt-6-luna）立即报满载。诊断：今天 15:17 以后所有 codex exec 都报满载（CLI 0.162.1 和 app 0.163，high/xhigh，fast→priority，显式代理和 TUN 都试过，模型 gpt-6.1-sol/luna/astra/sol），13:27–14:59 同样设置正常；本机今天没有交互会话记录（最近一次是 10-19 22:37），所以是服务端满载，不是调用参数问题
- **10-20 20:33** hidfix 第 11 次：codex 第 3 轮复审第 3 次（gpt-6.1-sol，设置与交互一致：app 0.163、xhigh、TUN）仍报满载，停止（不算 NO-GO）
- **10-20 20:17** hidfix 第 11 次：codex 第 3 轮复审第 1 次（gpt-6.1-sol，20:14）审到一半报模型满载，没有结论（重记：上一条进度提交把 PROGRESS 清空了，已恢复）
- **10-20 16:10** hidfix 第 11 次仍停在第 3 轮评审：codex 又试了 3 次（15:48 gpt-6.1-sol、15:59 gpt-6-astra（审到一半中断）、16:09 gpt-6-sol），全部报模型满载，没有结论（不算 NO-GO，也没有换别的评审）。Kimi 已 GO，离线全套 58 项 PASS（fix3 5775714）。dry11-2 和第 11 次都没跑，设备没动。两项分析结论：① refcount 下溢告警来自原厂内置 f_hid：开机重绑时 cdev_init 把 mk20-router 持有的 /dev/hidg0 引用计数清零，之后在切换步骤（hidg_unbind）或 router 退出（cdev_put）时下溢；不是 usb_f_hidfix。因为用的是 WARN_ONCE，同一次开机里之后的引用计数告警不会再打印（文档里给了 clear_warn_once 的建议，规格冻结所以没改）。② 第 5–10 次和 dry11-1 的全部报错清单里，只有第 5 次的 Oops 属于我们（已在 17081b2 修复）；其余是原厂噪声或原厂问题（sunxi_udc 稳压器计数下溢、-108 竞态、开机 alloc_fd 告警）；另外每约 23 秒一次的 usb-storage 复位由 mk20-router 用户态引起。详见 hidfix/REFCOUNT-RCA.md、hidfix/LOG-ERRORS-RCA.md。
- **10-20 15:40** hidfix 第 11 次第 3 轮（最后一轮）停在评审：fix3（5775714）修好 benign 判定（dry11-1 失败原因：良性 `ERR: Operation not supported` 被算进 sunxi 竞态上限），离线全套 58 项 PASS；Kimi 复审 GO，codex 两次都报模型满载（15:17、15:38），没有给出结论，按「两边都 GO」规则未跑 dry11-2 和第 11 次。只读分析：`refcount_t underflow` 告警来自原厂内置 f_hid（每次开机重绑时 cdev_init 把 mk20-router 持有的 /dev/hidg0 引用计数清零），不是 usb_f_hidfix；WARN_ONCE 会让同一次开机里之后的引用计数告警不再打印。第 5–10 次和 dry11-1 的全部报错清单里，只有第 5 次的 Oops 属于我们（已在 17081b2 修复）；另有每约 23 秒一次的 usb-storage 复位，随 mk20-router 停止而消失，需要排查。详见 hidfix/REFCOUNT-RCA.md、hidfix/LOG-ERRORS-RCA.md（feature/hidfix 67c1214）。
- **10-20 12:15** hidfix 第 11 次暂停，先审测试逻辑（hidfix/TEST-REVIEW.md）：第 6–10 次全是测试工具的问题，累计 84 次带设备文件的真实重绑，模块诊断 0 条。另外找到必然失败的缺陷：主机端身份校验对 kmsg 文件必然报错（离线复现），所以就算设备全过也会判失败；时间预算估高约一倍；错误关键词不分大小写会误报（debug、WiFi Warning）。建议：codex 一轮修完，设备上先跑约 3 分钟的试运行，再完整跑一次。
- **10-20 11:33** hidfix 第 10 次未过：T4 跑到 31/50 时测试总时长预算不够，主动收尾（剩 506 s，需要 511 s），不是模块出错。共解绑/绑定 33 次，T7 未到；第 9 次的 ESHUTDOWN 没再出现（重试 0 次）；sunxi 报错对豁免 2 次（3 秒内恢复）；不可回收内存斜率 对照 +0.13、实测 −1.04 kB/轮（样本不全，未认证）；usb_f_hidfix 相关 Oops/告警 0；kmsg 连续；Mac 两次都识别到 HID；键盘自行恢复。harness 1f62c71，结果与 RCA 3245bbc。
- **10-20 09:55** hidfix 第 9 次未过：T4 第 3 轮重新绑定后立即写 HID 报告返回 ESHUTDOWN（主机还没配好）。共解绑/绑定 5 次（T4 2/50），T7 未到；sunxi 报错对豁免 1 次（3 秒内恢复）；未见 usb_f_hidfix 崩溃/告警，kmsg 无缺口；键盘自行恢复（1.2.1，adb/usb0 正常），未用 hardreset。结果与 RCA：hidfix/reviews/run9/，e6996ca。
- **10-20 09:50** hidfix 第 9 次开跑（codex、Kimi 复审均 GO），过滤修复 8e645d5：只在重新绑定时放过相邻的 sunxi_udc -108 报错对，且 3 秒内 UDC/usb0 必须恢复。
- **10-20** 编译测试主机迁到 iMac build-imac.local（colima/Debian 容器，见 `docs/box-archive-imac:docs/BUILD-HOST-IMAC.md`）；助手电脑上的副本已删除；完整副本在 dev-mbp.lan `~/mk20-full`；未推改动在 `wip/box-*` 分支，归档在 `docs/box-archive-imac`。
- **10-20** 第 6–8 次未过，均只因测试判定：dmesg 环形缓冲回卷（16 KiB）、Slab 总量噪声、重新绑定时良性的 sunxi_udc 报错对。HID 模块本身无崩溃、无告警。
- **10-20** 修复：每次绑定新建带引用计数的设备对象，解绑时删除并释放；QEMU（KASAN/kmemleak）100 轮干净、无泄漏。
- **10-20** 第 5 次找到根因 H4：`usb_f_hidfix` 每次绑定重复注册同一设备对象 → 第二次解绑时重复删除 → Oops。原厂 sunxi_udc 无需打二进制补丁（可行性见 hidfix/PATCH-FEASIBILITY.md）。
- **10-20** K4 rc2 完成：6f2788b，715 项固件测试 0 失败。
- **10-20** agent A8c 推送 4d07ddd。

## 3. 接下来要做

- [ ] **iMac ECM**：网卡不出现（驱动匹配问题）；先请用户拔插一次 MK20，仍不行再评估驱动层方案（需 sudo/用户决定）；通后 `mk20_host_net.py sync`
- [ ] **route-check 节点**：用户决定 iMac 上 US-Exit-SS（SS）是否可代替 US-Exit-Hy2（`WANT_NODE=US-Exit-SS`）
- [ ] **1.8.0-rc1 24 h soak 进行中**（10-27 14:44 → 10-28 14:44 SGT，自动判定）
- [ ] **1.7.1 soak 结论**：dev-mbp 上线后读 `soak-20261025-012324.summary.json`；否则 ECM 通后在 iMac 重跑 24 h → tag v1.7.1
- [x] iMac 部署 mk20deck（10-27 14:47，feature/ncm-gadget 4eb998f）— [ ] **用户：本地网络权限允许 ~/.local/bin/python3.13（uv cpython-3.13.15）**；[ ] 用户：Codex App 信任 hooks
- [ ] iMac 构建/测试环境：colima mk20、bb127、make test / make mac-test、安装链脚本（目前只在 dev-mbp）
- [ ] 合并/部署 gd32-dfu-notice、k1-scroll；GD32 默认键位补丁需用户明确决定（禁止刷写规则不变）
- [ ] 设备 FAT 剩余约 1.2 MB：查明占用并清理方案（先只读）
- [ ] dev-mbp 上线后同步 `~/mk20-full`
- [x] usb 1-1 周期复位：1-1 是 Realtek 无线网卡；1.3.x 不启动 mk20-router 后不再出现（10-20 22:30 核实）。router 若再启用需修
- [ ] hidfix：第 11 次已过；可选：补 codex 复审、在测试里加 clear_warn_once
- [ ] USB 麦克风测试：Mac 音响 `say` 播放 + ASR 比对（阻塞：第 9 次占用键盘）
- [ ] 安装 K4：rc3 已完成；剩手动按键/旋钮/屏幕检查、强制失败回滚、24 h soak
- [ ] UVC 采集卡上机测试（UHC07P）
- [ ] 仅充电器供电、Mac 重启测试
- [ ] 键盘上的 agent 测试：MCP 注册、急停、校准、GLM 实时循环
- [ ] iMac 完整 make test 结果补入文档
- [ ] 板载 U3–U7（维护页面放进 `mk20-agent --web`）
- [ ] agent A9；遗留 minor
- [ ] TF 卡恢复演练
- [ ] 如用户需要，删除助手电脑密钥配置里的 MK20_VNC_PASSWORD

负责人：代码由 codex/ccai 写，Kimi 交叉评审，助手负责编排与验收。10-20 21:30 起 codex 额度用完，由 Claude Code 接手修复与安装；用户授权装 RC/上机验证不必再问（刷写 GD32 等硬规则不变）。构建/测试可在 dev-mbp 本机做（colima + mk20-build:trixie 镜像，busybox 1.27.2 在 ~/mk20-build-local/tools/bb127）。

## 4. 规则与约束

- 运行 codex/kimi 前必须 `~/worktrees/route-check.sh`：AI=US-Exit-Hy2，出口 203.0.113.10；失败不跑，绝不直连。
- ccai：少用 sg（新加坡出口）；同一时间只跑一个 ccai 会话；不动用户自己开的会话。
- 不碰 ESP32 `/dev/cu.usbmodem1111101` 和 CD 手表 adb 设备；adb 用 `~/mk20-backup/mkadb.sh`。
- GD32：禁止 `0x19`、`02 AA E2 78`，禁止刷写；只允许 `05 00 r r 00 00`（r=100..105）。
- 不用 plain `reboot`，用 cmd-reboot 或 hardreset.sh；改 USB 的步骤不在 adb 前台跑。
- 密码/API key 不打印、不记录、不提交（设备 /data/mk20/secrets/，权限 0600）。
- 只启动 mk20-kbd，不启动原厂 KD；崩溃循环进 mk20-kbd 安全模式；维护入口在 usb0 管理页 172.30.250.2:8780。保留 A/B 回滚。
- 不用工作 Mac；代码不放助手电脑；研发资料及时提交 Gitea 并在 dev-mbp.lan 保留完整副本。
- 每个并行任务独立 worktree。

## 5. 资料位置

- iMac（10-27 起设备主机）：仓库 `~/mk20-build/mk20-agent-deck`（main）；adb r35.0.2 `~/mk20-build/tools/pt-old/r35.0.2/platform-tools/adb`；`~/mk20-backup/mkadb.sh`；`~/worktrees/route-check.sh`；说明 `tools/imac/README.md`（chore/imac-tooling）
- hardreset.sh：仓库 hidfix/ref/firmware/rescue/hardreset.sh；设备上 /data/mk20/rescue/hardreset.sh 或 /mnt/SDCARD/mk20/hardreset.sh（带 .sha256 校验）

- Gitea：ssh://git@192.168.88.6:2222/leo/mk20-agent-deck.git
- 完整副本：dev-mbp.lan `~/mk20-full/mk20-agent-deck`，交接 `~/mk20-full/HANDOFF.md`，归档 `~/mk20-backup/box-archive/`
- hidfix：feature/hidfix 的 hidfix/RESUME.md、hidfix/PATCH-FEASIBILITY.md、hidfix/reviews/
- 迁出资料：`docs/box-archive-imac:docs/box-archive/`（QEMU 环境、评审、续接说明）
- 编译主机：iMac build-imac.local，`docs/box-archive-imac:docs/BUILD-HOST-IMAC.md`；仓库交接文档 `docs/box-archive-imac:docs/HANDOFF.md`
- rc2 包：Mac `~/worktrees/k4-rc2/`
