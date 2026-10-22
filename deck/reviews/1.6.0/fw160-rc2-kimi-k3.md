# Kimi K3 — fw 1.6.0-rc2 delta (78d7042..d4f0d7c), 2026-10-22 14:08 — GO

• VERDICT: GO

  **逐项复核（rc1 六项）**
  - MAJOR1 PTT 丢-up 兜底：FIXED。每次按下记 `hid_at`（onboard.c:702),120 s 封顶（:40,:569-572)，重复 down 重启窗口（:736),`onboard_timeout` 唤醒（:1002)。同页 ui.page 给 owner 1/4 设 `keep_until`(:396-398),10 s 后 owner1 强制 host up、owner4 取消（:557-564),GD32 down 再确认（:672)。e-stop/SUSPECT/loss/hello 均经 `release_mac`/`boundary` 清 owner 2(:465,:515,:214)，不会触发迟到的二次 release。
  - MAJOR2 暂停期重复 down 清 owner:FIXED。`onboard.c:695` 只吞不清，up 照常送达（:673-679)；测试覆盖有/无同页请求两种。
  - MINOR1 回L1 暂停期导航：FIXED。stop 取消 owner 4(:466)，释放仅在 `!suspended` 时导航（:677)。
  - MINOR2 Mac 侧代际围栏：FIXED。hello 全程持 `_gen_lock`(link.py:262-263，含 epoch 自增），锁内失配即弃（:389,:433);daemon 收到 "stale" 后按新 epoch 重决（daemon.py:429-431,492-493)。围栏闭合，无 TOCTOU。
  - MINOR3 PROBING 期 K2 被拉回：FIXED。`_k2_target` 对新于已知 belief 的 tap 绝对跟随（daemon.py:368-377)，含于上次 ack 的 tap 仍相对切换；与 ui.page 交叉的场景推演进终态一致。
  - MINOR4 日志 token:FIXED 并实测。`redact_query`(webui.py:784-789）对正常行、%22 注入、send_error 引用的畸形行均脱敏。

  **新发现问题**
  - MINOR（新，仅硬件故障可达）:PTT 120 s 与 `keep_until` 都允许被重复 down 无限续期（onboard.c:672,:702,:736)。边沿触发链路下语义正确（重复 down 意味 up 丢失、键仍按着），但若 GD32 故障周期性发伪造 down,RAlt 永不释放，封顶失效。建议加重启次数上限（如绝对 10 min）或在 UI.md 写明此残余风险。
  - MINOR(1.5.1 已有，rc2 机制可顺手修）：暂停期丢失 GD32 up 的 owner-1 主机键无界——5 s 重同步只清 `pressed` 不清 `key_owner`(onboard.c:578-582),Mac 侧可能长期看到 "pressed"，直到下一次同页 ui.page 的 `keep_until` 才兜底。建议 5 s 重同步时对 owner 1 一并 `release_key`。

  其余排查均无问题：急停延迟不变（stop 仅加 20 次循环；fb20 判定 :683 仍在各闸之前）；暂停期无新输入路径（仅释放类上行，符合设计）；同页 10 s 窗对正常长按（600 ms 拾取、回L1）充足；`onboard_host_flush` 与 `keep_until` 的交错均退化为无害 no-op。随机不变量测试（120×45 步）设计正确。验证：test_page_sync.py 新增用例 5/5 通过；`redact_query` 直接实测通过；onboard 侧新增用例因快照缺构建环境未跑，以源码逐行核对代替。rc2 可上机验收。
