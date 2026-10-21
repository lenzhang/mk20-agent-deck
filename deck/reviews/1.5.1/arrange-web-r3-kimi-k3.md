# Kimi K3 — arrange web delta re-review r3 (d82cd58), 2026-10-22 06:41 — GO

• Now let me read the actual source files to verify the diff and hunt for new issues.

• Now let me check daemon.py and cli.py in full, and verify the Python timeout mechanics plus run the test suite.

• Sources verified. Now run the test suite to confirm the fix and tests are green.

• VERDICT: GO

  **旧项复核**
  - MAJOR(argv 带 token)→FIXED：cli.py:101 改用 `request_link_file`，cli.py:120 只把文件路径交给 `open`；daemon 侧 `mint_file` 用 mkstemp(O_EXCL) 在 0700 目录建 0600 文件（webauth.py:162-168），token 只在文件内容里。冒烟实测：路径/stdout 均无 token，`.webloc` 内容含完整 URL。
  - MINOR（失败日志带 token)→FIXED:daemon.py:32-34 `_shown` 截到 `/auth?`，三处日志（daemon.py:41,46,49）全走它。
  - MINOR(8 上限+HTTP/1.0 短连 503)→FIXED:HTTP/1.1 keep-alive(webui.py:610）实测同连 3 请求复用、203 响应无 `Connection: close`；页面 busy 静默退避（webui.py:1241-1243)。

  **新面检查**
  - 链接文件：0600/0700/O_EXCL 无创建竞态；兑换即删（webauth.py:133-135)、过期 ~5 次/秒 prune(webauth.py:147 + webui.py:994)、stop/启动清理（webui.py:1006,996）均实测通过。`clear_links` 只在抢到 socket 后跑（webui.py:995)，另一活 daemon 因 bind 冲突根本走不到清理；残留边角仅当两个 daemon 共用 run 目录但 socket 名不同（需手动 --socket)，理论级。`open` 请求与 mint 同权（uid 校验 webauth.py:388-392)，应答只有文件路径+page，不含 token(webauth.py:404-405);CLI 校验路径必须在 run 目录且 `open-` 前缀（webauth.py:469-472)。
  - HTTP/1.1：首字节后 5s 硬 deadline、trickle 被斩（实测）;4s 空闲关闭（实测，Python 3.15 `StreamRequestHandler.setup` 应用 self.timeout);Content-Length 恰好一个、≤8KiB、拒 chunked(webui.py:774-787)，无走私面；`_fail` 强制关连（webui.py:717）防错位；上限实测第 9 连仍立返 503 busy。
  - maybe/busy:started 超时→"maybe"（webui.py:809-810)，页面只重试 "busy" 且 ≤3 次（webui.py:1257-1259)。

  **新发现（MINOR)**:keep-alive 使占槽更省——本机进程每 <4s 发一次 favicon 请求即可无限期占满 8 槽（旧码 5s 强制回收）。同属本机 DoS 能力，建议后续加每连接累计时长上限。不阻塞发布。

