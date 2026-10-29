# 参与开发 / Contributing

欢迎协作。这是一个**会碰真实硬件**的项目：请先读完下面的「硬件安全规则」再动设备。

## 1. 开发环境

**Mac（主平台）**
- macOS 14/15。mk20deck 跑在**系统自带 `/usr/bin/python3`（3.9）**上：macOS 15 的「本地网络」隐私会静默拦截
  未签名的 Python（uv / pyenv / Homebrew 下载的解释器），连不上键盘也不弹授权。代码必须兼容 Python 3.9
  （`from __future__ import annotations`，3.9/3.10 用 `tomli`）。
  ```sh
  cd deck && /usr/bin/python3 -m venv .venv-sys
  .venv-sys/bin/pip install "Pillow>=10.1" "pyserial>=3.5" "tomli>=1.1" pytest
  ```
- 截图（SNAP）需要「系统设置 → 隐私与安全性 → 屏幕录制」里勾选 python3。
- 配置：`cp deck/config.example.toml ~/.config/mk20-agent-deck/config.toml`；本地环境变量参考 `.env.example`
  （`.env` 已被 git 忽略）。
- adb：MK20 的 USB 序列号为空，platform-tools 37.x 在 macOS 15 上会崩，用 **r35.0.2**，并且只通过
  `tools/imac/mkadb.sh` 访问设备。

**Linux / Docker（全量测试、交叉编译）**
- `sh tools/imac/build-image.sh .` 用 `deck/tools/build-image/Dockerfile` 构建 `mk20-build:trixie`（含 armhf 工具链、
  shellcheck、procps）。BusyBox 1.27.2（设备同版本）用 `deck/tools/build-image/build-busybox127.sh` 构建。

## 2. 测试（提 PR 前必须通过）

```sh
# Linux 全量（C 单测 ASan/UBSan、mk20-kbd e2e、agent、模拟器、shellcheck、pytest）
docker run --rm -v "$PWD":/work/wt -v <busybox 目录>:/bb127:ro -w /work/wt/deck \
  -e MK20_TEST_BUSYBOX=/bb127/busybox mk20-build:trixie make test
# macOS 原生（mk20deck 必须在 Mac 上也通过；设备端 C/BusyBox 测试自动跳过）
make -C deck mac-test
```

已知：macOS 上有 4 个计时敏感用例偶发失败（`test_agent_backoff_and_healthy_reset`、
`test_agent_selftest_never_fails[crashing]`、`test_hook_interrupts_backoff_and_resets_next_delay[*]`），
见 `deck/reviews/1.8.0/mac-test-4eb998f-failure.md`。新增失败不算「已知」。

UI 改动：更新金标帧（`deck/kbd/tests/golden/`）时在 PR 里附前后截图（`python -m mk20deck sim-shots`）。

## 3. 分支与 PR

- `main`：最新可用代码，受保护，只能经 PR 合入（至少 1 人 review + 测试通过）。
- 功能分支：`feature/<主题>`；修复：`fix/<主题>`；杂项：`chore/<主题>`；文档：`docs/<主题>`。
- 发布分支：`release/<x.y.z>`，从 `main` 切出，只接修复；**发布分支不回合到 main**（修复先进 main 再 cherry-pick）。
- 提交信息：`<模块>: <做了什么>`，例如 `mk20deck: K1 scroll acceleration`、`kbd: fix panel order`。
  一个提交做一件事；不要提交生成物（`out/`、固件 tar、`.venv*`）。
- PR 描述写清：改了什么、为什么、怎么测的（Docker / Mac / 真机）、是否改动设备侧行为、回滚方式。
- 改协议、固件安装流程、GD32 相关代码，需要维护者明确批准。

## 4. 发布约定

- 固件版本写在 `deck/firmware/VERSION`；每个 rc 在 `release/<x.y.z>` 上打 `v<x.y.z>-rcN`。
- 生产版：真机验收 + **24 h soak 通过**后，在 `release/<x.y.z>` 上打**附注 tag**：
  `git tag -a v<x.y.z> -m "<摘要>"`，同时在 `deck/firmware/RELEASE.md` 记录 tar 的 sha256、大小、构建提交和证据目录。
- 固件 tar **不进 git**：作为 GitHub Release 附件上传，附 `.sha256`。
- Mac 端（mk20deck）单独发版时同样用附注 tag，并在 `PROGRESS.md` 记录部署与回滚方式。

## 5. ⚠️ 硬件安全规则（强制）

违反可能让键盘变砖或需要拆机救援。PR 中出现违反以下规则的代码会被直接拒绝。

1. **GD32 键盘 MCU**：绝不发送 `0x19` 或 `02 AA E2 78`；绝不刷写 GD32 固件（DFU 工具默认 dry-run，别去掉）。
   唯一允许的写入是旋钮回显 `05 00 r r 00 00`（r = 100..105）。改 GD32 键位需要维护者书面同意。
2. **重启**：设备上绝不执行 `reboot`（会卡死）。只用 `touch /mnt/SDCARD/cmd-reboot && sync` 或 `hardreset.sh`。
   远程能重启的情况下不要让别人去拔插。
3. **adb**：只通过 `mkadb.sh`（只选空序列号的 MK20 USB 传输，拒绝 `reboot/root/connect` 等）；不要碰同一台电脑上的
   其他 adb 设备、串口设备（例如 ESP32 开发板）。一次 shell 命令 < 400 字节。
4. **USB**：改 USB 复合设备的操作脱离前台执行；不要在前台 adb shell 里 `killall adbd`。
5. **刷固件前先备份**（TF 分区、`/data`）；按 `deck/firmware/INSTALL.md` 走 pending → 自检 → confirm 流程，
   不要手动改 loader 指针。
6. **不要启动原厂 ScreenKey / KeyboardDevice** 与 mk20-kbd 抢屏。
7. 真机测试结束后确认键盘回到正常状态（L1 页、mk20deck 已连接）。

## 6. 机密与隐私

- 不提交任何密钥：VNC 密码、LLM API key、OAuth token、摄像头密码、ssh 私钥。设备密钥放 `/data/mk20/secrets/`（0600），
  本机放 `~/.config/...` 或 `.env`。
- 不提交内网地址 / 主机名 / 个人路径（用 `192.0.2.x`、`203.0.113.x`、`~` 等占位），不提交私人设备序列号。
- 不提交大文件和厂商资料（Waveshare 照片、示例代码、原厂固件 / 磁盘镜像）。
- 提交前建议跑：`gitleaks git --log-opts="origin/main..HEAD"`。
