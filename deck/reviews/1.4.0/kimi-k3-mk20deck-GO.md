• I've read all 5 diffs completely. Now let me verify a few details in the actual sources (read-only) — the waiter wake-up mechanism, id allocation wrap, and `P.parse` ack detection.

• One last check — `offline_frame` error handling (now called from the paused path and at `run()` start):

• All diffs read; key details verified against sources. One last line-number check for precise citations:

• VERDICT: GO

  **BLOCKER**：无。

  **MAJOR**
  - `deck/mk20deck/link.py:253`：心跳下一拍从 **ack 到达时刻**起算（`time.monotonic()`），设备侧采样间隔 = 周期 1.2 s + 完整 RTT。设备规则为间隔 >1.5×周期（即 1.5 s）就重新计数，RTT 余量只有 0.3 s；推图繁忙时设备会反复重启 ONLINE 计数，而主机侧无感知（restart 不触发 reject/re-hello）。修复：改为 `nxt = sent + self.ui_beat_s` —— `call()` 阻塞到 ack 返回后才继续，已天然保证间隔 ≥ 周期，不会落入 <1 s 不计数区间；注释所说风险不成立。

  **MINOR**
  - `deck/tools/hooks/hooks-install.sh:36`：`gen > hooks.json` 非原子写，中途崩溃留残文件且 uninstall 因 sha 不匹配拒卸。修复：写临时文件 + `mv`。
  - `deck/tools/hooks/claude-hooks-install.sh:14`（hooks-install.sh:15 同）：无 homebrew python3 且 `command -v python3` 为空时 PY 为空，报错晦涩。修复：显式检查并退出。
  - `deck/mk20deck/daemon.py:340`：`sleep_frame_s`/`_last_sleep_frame` 是类属性，靠赋值时实例遮蔽才正确，建议移入 `__init__`/字段。
  - `deck/mk20deck/cli.py:73`：sim-serve 传 `bg=None`，后台轮询与前台 refresh 共享 adapter 实例，`TmuxAdapter.hooked/unhooked` 存在竞争（仅模拟器路径）。
  - `deck/mk20deck/adapters/tmux.py:134`：`ps` 失败时 `parents=None` 降级为仅按 pid 存活匹配，pid 复用可能误绑窗格（已有文档说明，方向安全）。
  - 固有代价：Claude 每次 PreToolUse 冷启动一个 python 进程（`-I -S` 已缓解，约 50–100 ms/次）。

  **各优先级确认安全**
  1. Claude hook：不打印 stdout、`os._exit(0)` 恒成立、不输出任何 decision JSON → 不可能改权限结果或崩溃 Claude；stdin 16 MiB 截断 + drain；记录仅白名单字段（无 prompt/tool 内容）；`safe_session_id` 去路径穿越，tmp 用 `O_EXCL|O_NOFOLLOW`，rename 不跟随符号链接，目录 0700 + uid/非链接校验；prune 限 256 条/24 h，tmp 60 s 清扫 → 有界；并发写 last-writer-wins 不损坏。安装器：全部文件先解析、任一畸形即整体拒绝；逐文件 `.bak-mk20-<ts>`（copy2 保权限）后才写；mkstemp+chmod+os.replace 原子、保留原 mode、跟随符号链接而非替换；MARKER 剥离保证幂等、不动他人 hook。
  2. link.py 锁：id 分配与 pending 注册同持 `_lock`，`_wlock` 仅包写且不嵌套 → 无死锁、无 id 冲突；`ids.next()` 回绕到 1，永不等于事件 id 0xFFFFFFFF；设备事件在 waiter 弹出前 return，不会吞 ack；`_wake_waiters` 用 `put_nowait`；close() 先 stop+唤醒再关 transport、beat join 2 s、线程皆 daemon → 无关机悬挂；stop 后注册的 waiter `timeout=0` 立即返回。
  3. ui.openSettings：事件必须**精确等于** `{"method":"ui.openSettings"}`（多字段即丢弃）；URL 只来自配置，`parse_web_url` 限 http/https + IP 字面量/localhost、禁凭据与控制字符；`open` 走 argv 列表无 shell、5 s 超时、独立线程、3 s 限流 → 设备无法让 Mac 打开任意 URL 或执行命令。
  4. tmux 适配器：全部 list-argv 无 shell，窗格标题/路径仅作数据解析 → 无注入；记录绑定要求 socket realpath 一致 + 窗格存在 + agent pid 存活且为 `pane_pid` 后代，回收的 pane/pid 均被拒；12 h 最大年龄、running 900 s 过期转 unknown。
  5. 休眠/重连：连接时已在睡则先 paused；paused 循环仍监视 link.connected，断线抛 ConnectionError 交给 supervisor 重连；每 10 s 重推休眠帧（设备在 L1/L4 之外丢帧），`offline_frame` 推送异常已吞。
  6. 资源：心跳 1.2 s、后台轮询 10 s 且仅在存在 hook 记录时才跑 `ps`；日志 8 MiB 单文件轮换；记录/队列均有界。

  已完整阅读的 diff：1-link-transport.diff、2-daemon-config-controller.diff、3-adapters-state.diff、4-hooks.diff、5-render-cli.diff（全部从头到尾读完，关键行与 ./deck 源码核对）。

