# feature/deck-1.8.1 RESUME (时间 SGT)

分支自 feature/ncm-gadget 52ae248；worktree ~/worktrees/mk20-181；docker 测试用 ~/mk20-build/wt-181（detached，完成后删除）。

- 2026-10-29 merge 完成：457ff15 gd32-notice，0a36ceb k1-scroll（K1 维持 10-25 决定：旋转=滚动历史，按下=最新；未改 GD32 键位）
- 2026-10-29 长按批准 + SNAP 完成：f7d81e6（权限 1 是/2 是且不再问 长按 approve_hold_s=1.0 + 进度环 + 闪光 + afplay Glass；3 否 立即；L4 MODE 长按 = screencapture -x -c）
- 2026-10-29 Linux docker 全量 @f7d81e6：1519 passed / 5 skipped；agent 398；ui 41405；tools 83/1 skipped
- 2026-10-29 codex r1（gpt-6-sol；gpt-6.1-sol 本账号不可用）FAIL → eed2917 修复（tick 间隔 / link ack 过期 / 其它输入 取消；SNAP 放 worker 线程）
- 2026-10-29 codex r2 PASS（928c06d，README 守卫 + 残余风险）
- 2026-10-29 16:02 进行中：HEAD 复测 docker deck pytest + Mac 全量（日志 deck/reviews/1.8.1/*-head.log）；备份已建 ~/mk20-backup/pre-1.8.1-20261029（ROLLBACK.sh，旧部署 5f3dccb）
- 下一步：测试过 → git -C ~/mk20-deploy checkout --detach <HEAD> → kickstart → 验证 已连接 tcp://172.30.250.2:17890 → 摄像头快照；失败跑 ROLLBACK.sh
- 2026-10-29 16:35 Mac 全量 @928c06d：922 passed / 603 skipped / 4 failed（均为已知计时：agent_backoff、selftest[crashing]、hook_interrupts_backoff×2）
- 2026-10-29 17:20 Linux docker deck pytest @928c06d：1524 passed / 5 skipped；开始部署
