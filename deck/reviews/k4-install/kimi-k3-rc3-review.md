• Diff 已读完。现在审查 mk20d 本体、各设备脚本用到的命令、conftest 与 shims。

• 现在做设备脚本命令审计（与 device-commands.txt 对比），并检查 share/rescue 脚本内容。

• 正则太噪。改用“局部函数+变量名剔除”的方法重新审计。

• 再确认两点：测试如何调用脚本（是否绕过 PATH 用绝对路径/shebang），以及设备脚本是否用绝对路径调命令。

• 查一下测试 mock helper 的调用方式（PATH 收窄后是否会影响）和负 age 测试。

• VERDICT: GO

  **(1) mk20d 替换的正确性 — 通过。** BusyBox 1.27.2 `date` 支持 `-r FILE`（coreutils/date.c 的 `--reference`），`ls -ld` 第 2 字段即链接数，均成立。边缘情形全部 fail-safe：
  - 文件缺失/悬空符号链接：`date -r` 失败→stdout 空→`num()` 归 0→age=当前时间→判 stale（mk20d:90-91）；KST 缺失先走 `[ -f ]` 的 missing 分支。方向安全。
  - 符号链接：`date -r` 与 `stat -c %Y` 同样 follow，语义无回归。
  - 含空格文件名：两处均正确引用 `"$1"`；`ls -ld` 的文件名在第 9 列之后，不影响 `$2`。
  - 负 age：AST（mk20d:70）与 KST（mk20d:91）都判 stale，修复了无 RTC+/run 持久化的正确性问题。
  - nlinks：文件消失→空串≠"1"→hook 被拒（mk20d:135），安全方向。

  **(2) 设备命令审计 — 通过。** mk20d、share/*.sh、rescue/*.sh 中无残留 `stat -c`/`timeout`/`od`/`realpath` 调用（仅注释和 `/proc/PID/stat` 文件读取）。其余外部命令（sha256sum、mktemp、flock、readlink、killall、netstat、df 等）均在 device-commands.txt 内。

  **(3) 测试桩 — 主漏洞已堵，留一个小口。** PATH 收窄为 `SHIMS:bb`，顶层经 `[BB,"sh",script]` 调用不经宿主 shell，shims/stat 以 127 精确模拟设备行为。MINOR：`conftest.py:30-32` 的 `elif shutil.which(a)` 仍是静默宿主回退——设备有但 busybox 构建没有的命令（如 curl；若 bb 构建缺 `sh` applet 名，mk20d 内嵌的 `sh …` 调用会落到宿主 dash）会用宿主语义跑测试，正是 rc2 事故的机制。修复：把允许回退的命令写成显式白名单（如 `{"curl"}`），其余命中即 fail 或至少记录到测试报告。

  **(4) 工具逻辑 — 通过。** `mk20_accept.py:199-206` keyboard_check 双向 fail-closed：无 `kbd` 键（1.2.x）走 kd=1；`enabled is True` 时要求 kd=0+running+mode=normal，缺字段→False。`mk20_soak.py` 的 `kbd_pid` 模式 `bin/mk20-kbd --run` 与 mk20d:516 的启动命令行匹配，重启计数 `kbd_pid or kd_pid` 正确；健康取不到时 keyboard_ok=False→ok=False，安全。

  **(5) build-image — MINOR。** `Dockerfile:1` `debian:trixie` 未钉 digest，`Dockerfile:10` uv 安装脚本无版本/校验和直接管道执行——复现性与供应链风险，修复：钉 `@sha256:` 与 uv 版本+sha256。`build-busybox127.sh` 钉了 tarball sha256（好），sed 补丁若失配会在编译期显式报错，`mktemp -d` 残留仅容器内，可接受。

  **MINOR 附记：** `mk20-agent-child.sh:9` 用了 `renice`，不在 device-commands.txt（设备应无此命令）→ 设备上走 WARN 分支，已被守卫且 agent 默认关，无害；若确认设备有 renice 请补进 fixture，否则删掉该尝试。

