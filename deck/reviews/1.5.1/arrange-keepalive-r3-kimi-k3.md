# Kimi K3 — arrange keep-alive cap r3 (4dc6f89), 2026-10-22 11:27 — GO

  VERDICT: GO

  **原 MAJOR（macOS 503 被 RST 吞掉）：FIXED** — `webui.py:689-696` `shutdown_request` 统一 SHUT_WR 后交 lingerer；503 路径 `webui.py:747` 同走此路。10 条 R7 新测试（8 种 4xx 带 40 KiB 未读字节、cap 处 503 收 `code:"busy"`、flood 下有界且不堵 accept）本机全过，ECONNRESET 消除。

  **原 MINOR（README 过度承诺）：FIXED** — `README.md:11-14` 已加 keep-alive 关闭竞态例外，并如实描述半关闭+drain 语义。

  新排查（逐项过：生命周期/fd 归属/有界性/SHUT_RD/keep-alive/slowloris/ENOTCONN/平台差），无 BLOCKER/MAJOR，两条 MINOR:

  **MINOR 1 — linger 线程异常退出后不自愈**:`webui.py:621-661` `_run` 若被未捕获异常（recv 抛非 OSError 之类）杀死，`self._thread` 永不为 None(`webui.py:601`)，不会重启；此后 linger 退化为「立即 close」,macOS 上 RST 回归且无声。修复：`_run` 最外层 try/finally 里 `with self._lock: self._thread = None`，或循环体 `except Exception: continue`。

  **MINOR 2 — `stop()` 不 join、快照 fd 有复用窗口**:`webui.py:663-670` 关 socket 后，`_run` 快照里的旧 fd int 可能被新连接复用，`p.register(fd)`(`webui.py:634`）会短暂 poll 别人的 fd——实际无害（recv 走 socket 对象，已关则 OSError 兜底关闭），但 stop 后 join 一下即可根除；顺手把 `self._thread` 清空。

  其余确认无问题：fd 单一归属（finish 只关 wfile/rfile，对象 close 幂等，`stop` 与 `_run` 双 close 安全）;64 上限在锁内检查无竞态；poll(50)/ev.wait(0.5) 无忙等；`_expire` 改 SHUT_RD(`webui.py:862-869`）后写侧保留、进行中的响应能发出，linger 侧读侧已关立即 EOF 秒关；被驱逐连接走同一路径无泄漏；ENOTCONN/ECONNRESET 全部被 catch;macOS 无 `select.poll` 时 select 回退正确；fd/内存/CPU 均有界（64 × 0.5 s × 16 KiB)。

