# MK20 Agent 增强固件 — 进展文档

> 每有关键进展即更新。时间均为新加坡时间（SGT）。最后更新：2026-10-20 12:16 SGT

## 1. 当前状态

| 工作线 | 分支 | 提交 | 状态 |
|---|---|---|---|
| hidfix（HID 模块修复） | feature/hidfix | 35b577a 测试逻辑评审 hidfix/TEST-REVIEW.md | 第 11 次暂停；先按评审一次修完测试工具，再试运行 → 完整跑 |
| K4 rc2（替换原厂 KD，fw 1.3.0） | feature/kd-replace-k4 | 6f2788b | 测试全过，未安装；等 hidfix 通过 |
| agent（A8c） | feature/agent | 4d07ddd | 已过评审；不在 rc2，进下一个 RC；默认 FEATURE_AGENT=0 |
| 板载 UI | feature/onboard-ui | 7bc959f | U1–U2 完成（待核实，推断），U3–U7 待做 |
| UVC 采集卡 | feature/uvc | 02f1d41 | UHC07P = MS2130，USB 2.0 下仅 MJPEG；待上机 |
| 设备 | — | — | 当前 fw 1.2.1，slot A |

rc2 固件包：Mac `~/worktrees/k4-rc2/mk20-fw-1.3.0.tar`，sha256 `49284d191afadab6c26644d30a2e870a46b06dc92a9b5c73bad0a4e9019e4f4f`。

## 2. 关键进展日志（新的在上）

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

- [ ] hidfix：等 codex 模型恢复后只补跑第 3 轮 codex 复审（Kimi 已 GO），两边都 GO 后跑 dry11-2 → 第 11 次（需你点头：本轮规定已用完）；另查 usb-storage 每约 23 秒复位、开机 alloc_fd 告警
- [ ] USB 麦克风测试：Mac 音响 `say` 播放 + ASR 比对（阻塞：第 9 次占用键盘）
- [ ] 安装 K4 rc2（阻塞：hidfix 通过）。风险：rescue-hardreset.sh 须先放到键盘上；glm-coding 需单独的 key 文件
- [ ] UVC 采集卡上机测试（UHC07P）
- [ ] 仅充电器供电、Mac 重启测试
- [ ] 键盘上的 agent 测试：MCP 注册、急停、校准、GLM 实时循环
- [ ] iMac 完整 make test 结果补入文档
- [ ] 板载 U3–U7（维护页面放进 `mk20-agent --web`）
- [ ] agent A9；遗留 minor
- [ ] TF 卡恢复演练
- [ ] 如用户需要，删除助手电脑密钥配置里的 MK20_VNC_PASSWORD

负责人：代码由 codex/ccai 写，Kimi 交叉评审，助手负责编排与验收；装固件/刷写需用户同意。

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
