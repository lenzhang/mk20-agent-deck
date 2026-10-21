• The diff and the mk20d script are read. Now I'll check the supporting files (mk20lib, conftest, test imports) and enumerate every `/run/mk20` reader/writer in the repo.

• Now reading the runtime dependencies: `mk20lib.sh` (sourced by mk20d), `mk20_soak.py` (full, to verify the parsing changes in context), and the mk20-kbd status writer.

• mk20lib and the soak changes check out. Now the mk20-kbd C side: how kbd.status is written, whether it creates `/run/mk20`, and the kbd.sock bind behavior (matters for the "hidden live socket" guard).

• VERDICT: GO

  核查记录（rc2.diff 全文 + mk20d、mk20lib.sh、mk20_soak.py 实读；测试侧静态核对）：

  1) BusyBox 1.27.2：新增命令 awk、`cd`/`pwd -P`、cut、mkdir、mount、head、pgrep 均在 device-commands.txt 内或为 ash 内建；未用 mountpoint/stat/timeout/realpath（以 `pwd -P` 代替 realpath，正确）。`local`、`${fs%% *}`/`${fs#* }`、`$(...)` 内嵌套引号均合 POSIX/ash；`mount -t tmpfs -o size=…,mode=0755,nosuid,nodev,noexec` 语法 1.27.2 支持。

  2) 顺序/幂等：run_mount 位于 boot 入口最前（mk20d:349），早于 gadget 等待、kd_svc stop 及 kbd/router/web 监督器启动（mk20d:389/407/413）；composite/fallback/safe/rollback 共用同一 `boot)` 路径，无绕过。重入：已是 tmpfs（含 /run 本身 tmpfs，最长前缀命中）→跳过；异种 fs 精确挂于同点→拒；`kbd_running||agent_running`→拒，活 kbd.sock 不会被藏；不会叠挂。回退 rc1 走老路径无影响。

  3) 失败处理：run_mount 所有分支 return 0、无 sleep；run_report 只出 INFO/WARN；confirm 仅看 selftest 的 FAIL（mk20d:438），WARN 只致 degraded，不堵 confirm/boot，符合设计。

  4) 前缀匹配：最长前缀 + 同长后者胜（栈式取顶层）正确；`/run/mk20` 不会误配 `/run/mk20x`；`m=="/"` 有兜底。

  5) soak：`_LEVEL` 零宽 split 对空格/换行两种 detail 拼接均正确，`WARNING`/`FAILED` 不误切；8×160 截断、`problem_key` 折叠 `\d+s→Ns`、summary 去重排序与测试一致。

  MINOR:
  - mk20d:34 `fs_at` 未反转义 /proc/mounts 的 `\040` 八进制转义：含空格等特殊字符的挂载点会漏配，`already has a mount` 守卫失效可能叠挂。/run/mk20 无此字符，实际不触发；修：awk 内 `gsub(/\\040/," ",m)`，或注释声明仅限此固定路径。
  - mk20d:50 pgrep→mount 间有毫秒级 TOCTOU：backoff 中的监督器恰在此窗口拉起 mk20-kbd，刚 bind 的 kbd.sock 会被藏入新 tmpfs（agent ctl 断连至 mk20-kbd 下次重启）。可选加固：对 `$TM/kbd.lock`、`$TM/agent.lock` 做 `flock -n` 探测，锁被持有即拒挂。
  - mk20d:53 `echo "$out"`：BusyBox ash 的 echo 默认解释反斜杠转义，mount 报错含 `\` 时日志变形（仅美观）。修：`printf '%s' "$out" | head -n 1 | cut -c1-120`。

