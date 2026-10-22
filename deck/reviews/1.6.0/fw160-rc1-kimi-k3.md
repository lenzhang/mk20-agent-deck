# Kimi K3 — fw 1.6.0-rc1 (61cca87..78d7042), 2026-10-22 13:03 — NO-GO

The first review attempt of rc1 timed out before writing a verdict (its progress notes were forwarded as six items and fixed in rc2); this is the full re-review.

• VERDICT: NO-GO

  **MAJOR 1 — PTT（owner 2）没有 10 秒丢-up 兜底，same-page 后 RAlt 可无限期卡住。** 记录属实。10 s cut 只管 owner≥5（`deck/kbd/onboard.c:539`），PTT 按下时连 `hid_at` 都不记（`:667-671`）。same-page `ui.page` 保留 owner 2 后，若 GD32 丢失其 up，主机 ONLINE（心跳使 SUSPECT 永不触发，`:495`）、无真实切页/急停时 RAlt 一直压着；1.6.0 前同页请求的 full boundary 还能顺带解救。修复：PTT 按下时记 `o->hid_at[fb-1]=now`，cut 条件改为 `owner==2 || owner>=5`。

  **MAJOR 2 — 急停暂停期的重复 down 清掉 owner，后续 up 丢失（Mac 侧卡键）。** 记录属实。`suspended` 分支把仍持有键的 owner 1–4 清零（`onboard.c:661`）；之后的物理 up 走 `release_key` owner 0 什么都不发（`:642-647`、`:156-177`），host key/转发键（owner 6→1）的 up 就此丢失。新加的"重复 down=同一按"（`:701`）在暂停期够不到（`:661` 在前）。修复：`:661` 删除清 owner（新 down 的 owner 本是 0，清了才有害），让重复 down 落到 `:701`。

  **MINOR 1 — L4 回 L1（owner 4）的 up 在暂停期导航并发 K2 tap。** 属实。up 路径无 suspend 检查（`:645`），`onboard_route` 在 ONLINE 下 `host_tap(103)`（`:218-220`），Mac 端翻页（`daemon.py:278-280`）——违背"急停后输入不到达 Mac"。修复：`:645` 加 `!suspended(o)`。

  **MINOR 2 — 代际围栏：tty 侧封死，Mac 管道侧只排序不围栏。** 属实。固件侧 hello→`onboard_loss`→boundary 的接收屏障 + 陈旧拒绝 + ONLINE 门槛（`onboard.c:192-195,371`）使 hello 前的字节不可能被接受；但 Mac 侧 hello 前决策、阻塞在 `_gen_lock` 上的调用会在 hello 后取新号发出并被接受（`link.py:317-337`），测试只覆盖了"hello 等在途请求"这一方向（`4-mac-tests.diff:168`）。修复：调用方带 `ui_epoch`，锁内失配即弃。

  **MINOR 3 — tap-before-hello 无重复计数/漏翻，但 PROBING 切页语义怪。** 部分修正记录：`daemon.py:367` 的 rx 闸 + `:386-393` 先排空事件再播种，使 tap 不双计、不漏（固件 `:351` tap 先于应答）；旧 Mac+新固件只是早 ~2 s 收到 tap，等价；新 Mac+旧固件回退 1.5.1。确认的问题：PROBING 期设备 K2 在 ONLINE 时 tap，Mac 跟随后又以 ui.page 把设备拉回，用户那次 K2 净效果为零（终态仍一致）。建议改 PROBING 期 tap 只更新 belief 不动 controller，或文档化。

  **MINOR 4 — openArrange 管道安全，但一次性令牌进 DEBUG 日志。** 属实。固定 payload 白名单（`protocol.py:359-365`）、双侧 3 s 限流（`onboard.c:580-586`、`daemon.py:105-110`）、Mac 只 `open` 本地 0600 链接文件（`webui.py:970-972`），token 不过 cable、不进 argv。但 `webui.py:616-617` 的 `log_message` 把含 `/auth?t=<token>` 的请求行写进 DEBUG 日志时 token 可能仍有效（60 s 窗、浏览器未打开时），违背 `daemon.py` "never in argv / logs"。修复：日志脱敏查询串。

  **核实无问题项：** 急停延迟/优先级不变（`stop` `:440-454` 未改，fb20 判定 `:651` 在新闸 `:701` 之前）；same-page 不会让输入作用于旧标签（grace 吞新按 `:706`，HID 键需 fresh `:663`）；SUSPECT/急停/hidg0/退出释放路径未回归；新旧固件交叉兼容成立。

  **测试缺口：** MAJOR 1/2、MINOR 1/4 均无回归用例（`2-diff:518-521` 只测"急停前按下的 up 送达"）；代际仅测单一方向。两个 MAJOR 修复都很小，建议随 rc2 一并补用例。
