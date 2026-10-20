# MK20 Agent 增强固件 — 进展文档

> 每有关键进展即更新。时间均为新加坡时间（SGT）。最后更新：2026-10-20 09:55 SGT

## 1. 当前状态

| 工作线 | 分支 | 提交 | 状态 |
|---|---|---|---|
| hidfix（HID 模块修复） | feature/hidfix | 8e645d5（过滤修复）/ 7db8f6e（最新） | 第 9 次上机进行中（09:50 开跑） |
| K4 rc2（替换原厂 KD，fw 1.3.0） | feature/kd-replace-k4 | 6f2788b | 测试全过，未安装；等 hidfix 通过 |
| agent（A8c） | feature/agent | 4d07ddd | 已过评审；不在 rc2，进下一个 RC；默认 FEATURE_AGENT=0 |
| 板载 UI | feature/onboard-ui | 7bc959f | U1–U2 完成（待核实，推断），U3–U7 待做 |
| UVC 采集卡 | feature/uvc | 02f1d41 | UHC07P = MS2130，USB 2.0 下仅 MJPEG；待上机 |
| 设备 | — | — | 当前 fw 1.2.1，slot A |

rc2 固件包：Mac `~/worktrees/k4-rc2/mk20-fw-1.3.0.tar`，sha256 `49284d191afadab6c26644d30a2e870a46b06dc92a9b5c73bad0a4e9019e4f4f`。

## 2. 关键进展日志（新的在上）

- **10-20 09:50** hidfix 第 9 次开跑（codex、Kimi 复审均 GO），过滤修复 8e645d5：只在重新绑定时放过相邻的 sunxi_udc -108 报错对，且 3 秒内 UDC/usb0 必须恢复。
- **10-20** 编译测试主机迁到 iMac build-imac.local（colima/Debian 容器，见 `docs/box-archive-imac:docs/BUILD-HOST-IMAC.md`）；助手电脑上的副本已删除；完整副本在 dev-mbp.lan `~/mk20-full`；未推改动在 `wip/box-*` 分支，归档在 `docs/box-archive-imac`。
- **10-20** 第 6–8 次未过，均只因测试判定：dmesg 环形缓冲回卷（16 KiB）、Slab 总量噪声、重新绑定时良性的 sunxi_udc 报错对。HID 模块本身无崩溃、无告警。
- **10-20** 修复：每次绑定新建带引用计数的设备对象，解绑时删除并释放；QEMU（KASAN/kmemleak）100 轮干净、无泄漏。
- **10-20** 第 5 次找到根因 H4：`usb_f_hidfix` 每次绑定重复注册同一设备对象 → 第二次解绑时重复删除 → Oops。原厂 sunxi_udc 无需打二进制补丁（可行性见 hidfix/PATCH-FEASIBILITY.md）。
- **10-20** K4 rc2 完成：6f2788b，715 项固件测试 0 失败。
- **10-20** agent A8c 推送 4d07ddd。

## 3. 接下来要做

- [ ] 第 9 次结果（自动链路：`~/worktrees/hidfix-run9-chain.log`）→ 结果写入 hidfix/reviews/run9/。不跑第 10 次
- [ ] USB 麦克风测试：Mac 音响 `say` 播放 + ASR 比对（阻塞：第 9 次占用键盘）
- [ ] 安装 K4 rc2（阻塞：hidfix 通过）。风险：rescue-hardreset.sh 须先放到键盘上；glm-coding 需单独的 key 文件
- [ ] UVC 采集卡上机测试（UHC07P）
- [ ] 仅充电器供电、Mac 重启测试
- [ ] 键盘上的 agent 测试：MCP 注册、急停、校准、GLM 实时循环
- [ ] iMac 完整 make test 结果补入文档
- [ ] 板载 U3–U7（维护页面放进 `mk20-agent --web`）
- [ ] agent A9；遗留 minor
- [ ] TF 卡恢复演练
- [ ] hardreset.sh 路径：仓库 hidfix/ref/firmware/rescue/hardreset.sh；设备上 /data/mk20/rescue/ 或 /mnt/SDCARD/mk20/（已定位，Mac 链路待接入）
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

- Gitea：ssh://git@192.168.88.6:2222/leo/mk20-agent-deck.git
- 完整副本：dev-mbp.lan `~/mk20-full/mk20-agent-deck`，交接 `~/mk20-full/HANDOFF.md`，归档 `~/mk20-backup/box-archive/`
- hidfix：feature/hidfix 的 hidfix/RESUME.md、hidfix/PATCH-FEASIBILITY.md、hidfix/reviews/
- 迁出资料：`docs/box-archive-imac:docs/box-archive/`（QEMU 环境、评审、续接说明）
- 编译主机：iMac build-imac.local，`docs/box-archive-imac:docs/BUILD-HOST-IMAC.md`；仓库交接文档 `docs/box-archive-imac:docs/HANDOFF.md`
- rc2 包：Mac `~/worktrees/k4-rc2/`
