# MK20 Agent 增强固件 — 进展文档

> 每有关键进展即更新。时间均为新加坡时间（SGT）。最后更新：2026-10-21 10:19 SGT

## 1. 当前状态

| 工作线 | 分支 | 提交 | 状态 |
|---|---|---|---|
| hidfix（HID 模块修复） | feature/hidfix | 5452175（证据 4eb0bf4） | 第 11 次通过（20:50）：T4 50/50 + T7×3，解绑 57 次，0 Oops；codex 第 3 轮复审由用户豁免（Kimi GO + 离线 58/58） |
| K4 rc（替换原厂 KD） | feature/kd-replace-k4 | 3302eb2 | rc3（1.3.1）已安装并通过验收（22:10）：good=1.3.1；剩 24 h soak、强制回滚、按键/旋钮目检 |
| agent（A8c） | feature/agent | 4d07ddd | 已过评审；不在 rc2，进下一个 RC；默认 FEATURE_AGENT=0 |
| 板载 UI | feature/onboard-ui | 7bc959f | U1–U2 完成（待核实，推断），U3–U7 待做 |
| UVC 采集卡 | feature/uvc | 02f1d41 | UHC07P = MS2130，USB 2.0 下仅 MJPEG；待上机 |
| 设备 | — | — | fw 1.3.1（good，slot B）；1.2.1 为回退；soak 运行中（22:31 起）；Mac 端 mk20deck 常驻（launchd） |

rc2 固件包：Mac `~/worktrees/k4-rc2/mk20-fw-1.3.0.tar`，sha256 `49284d191afadab6c26644d30a2e870a46b06dc92a9b5c73bad0a4e9019e4f4f`。

## 2. 关键进展日志（新的在上）

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

- hardreset.sh：仓库 hidfix/ref/firmware/rescue/hardreset.sh；设备上 /data/mk20/rescue/hardreset.sh 或 /mnt/SDCARD/mk20/hardreset.sh（带 .sha256 校验）

- Gitea：ssh://git@192.168.88.6:2222/leo/mk20-agent-deck.git
- 完整副本：dev-mbp.lan `~/mk20-full/mk20-agent-deck`，交接 `~/mk20-full/HANDOFF.md`，归档 `~/mk20-backup/box-archive/`
- hidfix：feature/hidfix 的 hidfix/RESUME.md、hidfix/PATCH-FEASIBILITY.md、hidfix/reviews/
- 迁出资料：`docs/box-archive-imac:docs/box-archive/`（QEMU 环境、评审、续接说明）
- 编译主机：iMac build-imac.local，`docs/box-archive-imac:docs/BUILD-HOST-IMAC.md`；仓库交接文档 `docs/box-archive-imac:docs/HANDOFF.md`
- rc2 包：Mac `~/worktrees/k4-rc2/`
