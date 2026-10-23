# Kimi K3 — soak meminfo + adb 480-byte guard (a8dd423..cf0e057), 2026-10-23 10:51 — GO (MINORs 1–4 fixed in the next commit)

• VERDICT: GO

  逐项核对通过：解析正则（mk20_soak.py:52）正确排除 SwapCached/Active(anon)、首值优先、垃圾输入全 null 不崩；awk `$1$2` 拼接形式与解析端一致；斜率最小二乘有 sxx=0 保护（:103），重启清零发生在累积之前（:264-267→299），顺序正确；一步阶跃被双半程规则正确判 flat，测试覆盖到位；守卫按 UTF-8 字节 +NUL 计算（mk20_accept.py:76），边界测试真实；两文件内所有 adb 路径均经 sh()；soak 主循环 catch Exception（:259），ValueError 只会记为失败样本（含 error 字段）不致崩溃，可接受。

  MINOR:
  1. mk20_soak.py:253,326 — 零样本时（如 --hours 0）finally 写出的 summary 无 kernel_mem/kernel_mem_trend 键，新读者需 .get()。修复：summary 初始化即带 `"kernel_mem": None, "kernel_mem_trend": "insufficient"`。
  2. mk20_soak.py:139 — `min(halves) > 50` 意味着启动后中途才开始的泄漏会长期报 flat（测试 :237 还将此固化为预期）。修复：summary 增加近 6h 窗口斜率或 max-half 字段，便于发现晚发泄漏（趋势判定逻辑可不动）。
  3. mk20_soak.py:120-127 — 半程斜率无最小时间跨度（3 点跨几分钟也出值），sunreclaim_half_kb_h 可能出现噪声大数。修复：half 内加 `span >= 0.5h` 下限。
  4. test_mk20_soak.py:176,187 — 依赖仓库外 fixture（本快照缺失会直接 FileNotFoundError）；busybox 不存在时退回宿主机 /bin/sh+awk，弱化 BusyBox 1.27 保真。修复：fixture 缺失时 skip；无 busybox 时 pytest.skip 而非静默降级。

