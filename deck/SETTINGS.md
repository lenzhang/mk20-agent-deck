# MK20 设置在哪里改

一句话：**用户设置只在键盘网页里改**；Mac 端行为在 `config.toml`，其中哪个会话在哪个键用 Mac 本机的
**会话安排网页**（http://127.0.0.1:8781/）；其余都是开发者/救援入口。

## 1. 用户设置（唯一入口）：键盘网页

- 地址：**http://172.30.250.2:8780** —— 在 Mac 上用浏览器打开（走 USB 网卡 usb0，不经过互联网）。
  改过 `ECM_SUBNET` 时是该子网的 `.2:8780`；键盘上的设置页会显示实际地址。
- 键盘按键只显示状态；按 **“网页设置”** 键，Mac 上的 mk20deck 会自动打开这个网址。
- 第一次打开：点“在键盘上显示配对码”，把键盘屏幕上的 6 位数字填进网页。配对码 5 分钟有效、
  每个码最多输错 5 次；10 分钟内配对失败满 10 次（包括没有显示配对码时提交的、已过期的码）会锁定配对 10 分钟，
  页面会提示多少秒后再试。键盘无法显示配对码时页面会直接报错，不会假装已开始。
- 最多同时 **2 个浏览器**保持配对。第三个浏览器会提示“已有 2 个已配对的浏览器，请等待过期或在已配对页面退出”：
  在已配对的页面点“退出配对（释放名额）”，或等 30 分钟无操作自动过期。配对只存在设备内存里
  （30 分钟无操作、12 小时或设备重启后需要重新配对）。
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
- `[[skills]]`：旧版四个技能键的标签和提示词。1.5 键位（[docs/PRD-1.5-L1-LAYOUT.md](docs/PRD-1.5-L1-LAYOUT.md)）已去掉技能键，此段照常读入但不绑定任何键，只为旧配置兼容。
- `[knobs]`：旋钮事件映射。
- `web_url`：“网页设置”键要打开的地址（默认就是上面的设备网页）。
- `[web] arrange_port`：会话安排网页的端口（默认 8781，`0` = 关闭），见下。

### 会话安排（哪个会话在哪个键）：Mac 本机网页

- 地址：**http://127.0.0.1:8781/** —— mk20deck 守护进程自己提供，只监听本机；`python -m mk20deck arrange` 直接打开。
  键盘没接时也能用。端口被占用时只记一条警告，键盘照常工作。
- 能做：看三个模式的 S1–S15（和键盘同样的颜色、状态字）；把某个会话 “放到这里”（移动并钉住，已有会话则互换）、
  取消钉住、清空此键；设 **固定规则**：按目录 / tmux 窗口名 / 标题包含 / 工具，把匹配的会话固定在某个键上，
  没有匹配会话时这个键显示 “等待：…” 并保留。优先级：规则 > 手动钉住 > 自动填充。
- 在键盘上把会话移入/移出规则键（或切换钉住）会停用那条规则，网页上写明原因；需要时重新启用。
- 规则存在 `~/.config/mk20-agent-deck/slot-rules.json`（0600，只由网页写；文件损坏时规则全部忽略并在网页和键盘副屏提示）。
  键位记忆照旧在 `state.json`。细节（规则语义、安全）见 [README.md](README.md)「会话安排网页」。

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
