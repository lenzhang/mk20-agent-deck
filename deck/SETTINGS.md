# MK20 设置在哪里改

一句话：**用户设置只在键盘网页里改**；Mac 端行为在 `config.toml`；其余都是开发者/救援入口。

## 1. 用户设置（唯一入口）：键盘网页

- 地址：**http://172.30.250.2:8780** —— 在 Mac 上用浏览器打开（走 USB 网卡 usb0，不经过互联网）。
  改过 `ECM_SUBNET` 时是该子网的 `.2:8780`；键盘上的设置页会显示实际地址。
- 键盘按键只显示状态；按 **“网页设置”** 键，Mac 上的 mk20deck 会自动打开这个网址。
- 第一次打开：点“在键盘上显示配对码”，把键盘屏幕上的 6 位数字填进网页。配对码 5 分钟有效、
  每个码最多输错 5 次，连续输错会锁定 10 分钟；配对只存在设备内存里（30 分钟无操作、12 小时或重启后重新配对）。
- 能改：模型商和模型（列表来自固件内置目录 `share/providers.json`）、API 密钥（只写入，不回显）、
  网络线路（经 Mac 代理的端口 / 键盘 Wi-Fi 代理）、从设备测试连接。
- 维护：查看设备状态（版本、A/B 槽、运行时间、内存/存储、自检摘要）、重启键盘程序、退出安全模式、
  切换详细调试日志。每个操作都要确认。
- 不能改：开启 agent（只能开发者打开 `FEATURE_AGENT`，且仍需键盘上实体允许）、重启设备、回滚固件、GD32。
- 服务默认开启（`FEATURE_WEB=1`），由 mk20d 守护、只监听 usb0。接口和安全细节见
  [agent/README.md](agent/README.md)，维护操作的底层约定见 [firmware/MAINT-HOOKS.md](firmware/MAINT-HOOKS.md)。

## 2. Mac 端行为：`~/.config/mk20-agent-deck/config.toml`

mk20deck（Mac 程序）自己的行为，不影响设备：模板是 [config.example.toml](config.example.toml)。

- `[deck]`：默认模式、串口、亮度、帧率等；模式相关段 `[codex_desktop]`、`[tmux]`、`[herdr]`。
- `[[skills]]`：四个技能键的标签和提示词。
- `[knobs]`：旋钮事件映射。
- `web_url`：“网页设置”键要打开的地址（默认就是上面的设备网页）。

用法见 [README.md](README.md)。

## 3. 开发者 / 救援（不是日常设置）

| 入口 | 用途 | 详细 |
|---|---|---|
| `/data/mk20/config/mk20.conf` | 白名单 `KEY=VALUE`：`ECM_SUBNET`、`FEATURE_WEB`、`FEATURE_AGENT`、`FEATURE_AGENT_CTL` 等 | [firmware/INSTALL.md](firmware/INSTALL.md) §4 |
| 环境变量 `MK20_WEB=0`、`MK20_AGENT=0`、`MK20_KBD`、`MK20_AGENT_CTL` … | 开发测试时临时覆盖 | [firmware/INSTALL.md](firmware/INSTALL.md)、`firmware/release/share/mk20lib.sh` |
| FAT 标志文件 `/mnt/SDCARD/mk20-no-web`、`mk20-no-agent`、`mk20-no-composite` …，`cmd-reboot`、`cmd-rollback` | 救援开关 | [firmware/RECOVERY.md](firmware/RECOVERY.md) |
| 维护钩子（adb）：`/tmp/mk20/maint/`、`/data/mk20/state/maint/` | 网页不可用时用 adb 做同样的维护 | [firmware/MAINT-HOOKS.md](firmware/MAINT-HOOKS.md) |
| VIA 键位 / GD32 固件（**危险**） | `mk20deck via-backup`、`gd32-flash` | [RECOVERY.md](RECOVERY.md) |
| 安装 / 升级工具 | `tools/mk20-install.sh`、`tools/mk20-release-build.sh` | [firmware/INSTALL.md](firmware/INSTALL.md)、[firmware/RELEASE.md](firmware/RELEASE.md) |

密钥文件在设备 `/data/mk20/secrets/`（0600），只能经网页写入；备份工具默认排除这个目录。
