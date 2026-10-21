# Kimi K3 — arrange web re-review r2 (eb145f2), 2026-10-22 05:15 — NO-GO (argv token)

• VERDICT: NO-GO

  **旧项复核（全部 FIXED）**
  - B1 匿名领令牌→FIXED：`/` 无会话只给提示页（webui.py:851-855），令牌只能经 unix socket 领取：目录 0700+属主校验（webauth.py:62-70,224）、socket 0600（webauth.py:239-240）、peer uid 校验（webauth.py:192-208,270）、单次+60s（webauth.py:102-110，`pop` 即一次性）、socket 建链有 stale 检测+属主 inode 才删（webauth.py:225-237,293-295）。
  - B2 无界→FIXED：8 连接上限立返 503（webui.py:554-568）、5s 硬 deadline 斩 slowloris（webui.py:614-627）、队列 32 上限+`LoopBusy` 503（webui.py:130-141,776-777）、`DRAIN_MAX=8`+wake 合并（webui.py:184-201）。
  - M1 prompt 泄漏→FIXED：`title_private`（codex_desktop.py:285）+`web_title` 只给 id 前 8 位（webui.py:223-227,415），私有标题禁作规则（webui.py:500-501）。
  - M2 写错 manager→FIXED：`_prepare` 用同一 batch 先更新真实 manager，读不到即 409 拒写（webui.py:330-347,468）。
  - M3 保存不回滚→FIXED：候选先落盘成功才换内存，失败抛 `RulesSaveError`→500（rules.py:325-346,369-381；webui.py:786-787）。
  - M4 OverflowError→FIXED：`_number` 限界（rules.py:180-190,210），逐条隔离+整体兜底（rules.py:307,269-271）。
  - M5 后台线程→FIXED：进程级 `_BG_POLL_LOCK`（daemon.py:25,201）+`done` 即醒+`join(6s)`（daemon.py:227,339-341），无死锁、退出最长 stall 6s 可接受。

  **新问题**
  - MAJOR：一次性 URL 经 `open`/webbrowser 的 **argv** 传递（daemon.py:38、cli.py:106-112），macOS 上 `ps` 对本机所有用户可见，而兑换端 `/auth` 无 uid 校验（webui.py:842-849）。其他本地用户轮询 ps 可抢在浏览器（冷启动秒级）前兑换，盗走会话；即使抢输也稳定烧掉 token→用户永远打不开页面（DoS）。socket 的“仅本用户文件系统”防线在 argv 处被绕过。修：把 URL 写进 0700 run 目录下的 0600 `.webloc`/重定向文件，`open` 该文件路径，使 argv 不含 token。
  - MINOR：daemon.py:40 `open` 失败时把含 token 的完整 URL 写进日志。修：日志只记 `url.split("/auth?",1)[0]`。
  - MINOR：HTTP/1.0 短连接+每 2s 轮询，8 连接上限下多开标签偶发 503（可自愈）；如需更稳可放 keep-alive 或调上限。

  修复 argv 一项即可转 GO。

