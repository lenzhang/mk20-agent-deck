# Kimi K3 — fw 1.6.0-rc4 delta (071a145..6fdc24e), 2026-10-22 16:37 — GO

• VERDICT: GO

  **三项复核**
  - MINOR(keep 对残留）:FIXED。无 host 分支清 owner 1 时同清 keep 对（onboard.c:135),pending up 冲刷同清（:144)，新按下兜底清（:734)；切断 latch 不动，仍只由 GD32 up 清除（:703)，符合设计。release_key(:180)/boundary(:226)/stop(:481)/loss(:300-302）本就清。
  - MINOR(owner-1 重复 down):FIXED。新增 :726 在 keycode/suspend/SUSPECT 闸口之前吞掉 host_route 上 owner==1 的重复 down；真实按下=一下一上，下一次 up 经 release_key 释放（:702-709)。
  - NIT（日志恒 10 s):FIXED。:577-578 改打 `(now-press_at)/1000`,press_at 现于每次新按下记录（:734)。

  **手势与急停排查**：长按拾取（600 ms）为一下一上，不经 :726；真机 e2e 断言 6 次物理 down 仅产 [down,up] 且 0.85 s 触发 pick-up(test_page_sync_e2e.py:406-432)。tap、K1/K2(knob 路径 :681 先于一切）、fb20 模式键（:713 急停判定在 :726 之前，agent 激活时 :568 先释放再 stop)、option 进入（host→device，与按键无关）、suspend 期重复 down(rc3 走 :730,rc4 走 :726，均吞且保 owner）均不受影响；socket/K2/堵 worker 急停路径未触。

  **e2e 改动合法性**：合理，非掩盖。旧测试把 fb1 按住数分钟不放——真实 GD32 每次按下必有 up，持续 down 只有幻影故障才出现；rc4 下旧写法等价于"丢 up 后再按"（被吞正是新语义）。补发 up(e2e_run.py:320-322）并断言恰好一个 up，是双向强校验。

  **副作用裁决**：丢 up 后槽位变 option 数字，首按被吞、其 up 释放旧 owner、次按正常打字——自愈，代价与 rc3 已接受的 latch 吞按一致，UI.md rc4 节已写明。可接受。

  **新发现**
  - MINOR（既有残留，rc4 未变更）：无同页请求时 owner-1 丢 up 无任何时间 bound,Mac 侧长期视为按住（仅 boundary/e-stop 5 s 重同步/loss 可解）。建议 UI.md 注明或加兜底上限。
  - NIT:owner 6/7→1 转换（:173,:181-182）不刷 press_at；若同页 ui.page 抢在 flush 前亚毫秒级武装 keep，切除日志时长失真。仅日志，概率极低。

  rc4 可上机验收。
