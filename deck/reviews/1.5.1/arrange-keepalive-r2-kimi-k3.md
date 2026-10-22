# Kimi K3 — arrange keep-alive cap r2 (8454b90), 2026-10-22 10:18 — NO-GO

  VERDICT: NO-GO

  **原 MAJOR:FIXED** —— `webui.py:709-718` 先 poll 等首字节（不消费）、在 `_conns_lock` 内置 busy 再读；`_evict_oldest_idle`(`webui.py:604-618`）在同一把锁内 0 超时 poll 复核、shutdown 后才放锁。两条新竞态测试（POST/部分请求不被驱逐、503 留给新客）逻辑成立；`_buffered()`(`webui.py:736-745`）正确处理管道化，无走私面（不转发，逐条过鉴权）。

  **新 MAJOR —— 503 拒绝路径关连接前不读请求，macOS 上 RST 毁掉 503**:`webui.py:626-632` 非阻塞 `send` 后 `shutdown_request` 直接 close，接收缓冲里未读的请求字节触发 RST，客户端收不到 503（页面拿不到 `code:"busy"`，改动类请求不再静默重试）。该路径虽是旧代码，但本次新增的两条 R6 测试和旧 B2 测试 `test_connection_cap_answers_503_beyond_the_limit` 在本机确定性 ECONNRESET（3 failed)；交付即红。修复：发完 503 后 `shutdown(SHUT_WR)` 并有界 drain（如 ≤64KiB/0.2s，参照 `_discard`/R4 修法；无界 recv 循环会被持续发包的客户端拖住）。我在 /tmp 副本打此补丁后 50 passed。

  **MINOR —— 残留窗口（可接受）**：驱逐 poll 与 shutdown 之间到达的字节会随连接关闭丢失、请求未执行——这是标准 keep-alive close race，无法根除，仅在有新客抢满槽时的微秒级窗口；改动不会"已执行却丢响应"。但 `README.md:10` "已开始到达的请求一定会被执行并回应" 过度承诺，建议改为"除keep-alive 关闭竞态外"。

  其余排查：EOF/POLLHUP 无忙等（peek 到 b"" 即关）；驱逐只 shutdown 不 close,fd 由属主线程在 `finish()` 先 `unregister`（同锁）后关闭，无 EBADF/fd 复用；线程/槽位有 `finally` 释放，无泄漏；驱逐按年龄跳过有字节的候选，公平性可。

