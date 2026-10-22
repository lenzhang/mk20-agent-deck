# Kimi K3 — arrange keep-alive cap r1 (5e36c55), 2026-10-22 09:34 — NO-GO

• VERDICT: NO-GO

  旧 MINOR 已修：年龄/请求数上限能回收长期占槽；未见死锁、fd 复用误关、已执行 POST 丢响应或线程泄漏，accept 的 1 秒等待有界。

  **MAJOR — 已到达请求仍可能被当作 idle 驱逐**：`deck/mk20deck/webui.py:683-690` 先 `peek()`，再 `_set_idle(False)`；接受线程可在两者之间经 `deck/mk20deck/webui.py:584-591` 标记并 `shutdown`。真实 socket 已复现：完整 POST 到达后直接 EOF，未执行也无响应；部分请求同样受影响。修复：改用 `select` 等待首字节，可读时先在锁内转 busy 再读；驱逐方也须在锁内复核无可读数据，并补首字节/POST 竞态测试。

  完整 pytest 因缺少 `pytest` 和 `test_arrange_web.py` 未运行。

