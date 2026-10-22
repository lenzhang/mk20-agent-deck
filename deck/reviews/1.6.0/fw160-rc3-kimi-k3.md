# Kimi K3 — fw 1.6.0-rc3 delta (d4f0d7c..071a145), 2026-10-22 15:17 — GO

• VERDICT: GO

  **两个 MINOR 复核**
  - MINOR1（幻影 down 续期）:FIXED。`press_at` 只在原始按下记录（onboard.c:732 `prev!=2`),120 s 窗可被重复 down 续、10 min 绝对封顶不可续（:583-588);`keep_max` 与同页请求同时设定且只在释放时清零（:406-410,:177),down 续期取 `min(now+10s, keep_max)`(:696-697)，封顶 60 s 触发切除（:569-576)。`onboard_timeout` 取两者较早唤醒（:1034-1037)。风暴测试两种路径均断言 10 min/60 s 切除。
  - MINOR2(5 s 重同步不清 owner):FIXED。重同步条件加入 `owned`(:595-597)，并对残留 owner 逐一 `release_key`(:603),owner 1 经 `pending_up` 发 host up；测试断言 stop 后 owner 2/4/5-7 已清、5 s 时 3 个 host up、暂停可结束、迟到 up 无副作用。

  **cut_latch 专项**:latch 只由该键的 GD32 up 清除（:699),up 处理先于一切闸口（suspend/grace/SUSPECT 均可达），故不会永久卡死；e-stop/hello/loss/boundary 不清它是有意的（故障 GD32 跨会话仍在），代价是丢-up 后下一次真按被吞一次、其释放即复位——UI.md 已写明。fb20 e-stop 判定（:709）在 latch(:711）之前，模式键被切后照样急停（有测试）。暂停期无新输入到 Mac（仅释放类）。

  **新发现**
  - MINOR(pre-existing,rc3 未覆盖）:live 页面上普通 host 键（owner 1）的重复 down 每次都会再转发 "pressed":true(:725-761)，且无时间 bound——幻影 down 风暴可让 Mac 看到无限重复按下。建议与 owner≥5 一样吞掉重复 down(:713 扩展），或在 UI.md 注明此残余。
  - MINOR（新，极冷门）:`onboard_host_flush` 清 owner 1 时不清 keep 对（onboard.c:132)，残留的 keep 使 :408 `!keep_max` 拒绝重武装；断线 10 s 内重连再按同键，旧 keep 到期会提前切掉新按下并上 latch。修复：该循环加 `o->keep_until[i]=o->keep_max[i]=0;`。
  - NIT:keep 切除日志恒打 "10 s"(:574)，续期后实际可达 60 s，建议打印实际时长。

  rc3 可上机验收。
