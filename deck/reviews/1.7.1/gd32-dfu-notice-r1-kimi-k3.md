# Kimi K3 — GD32 DFU notice r1 (release/1.7.1..3b158eb), 2026-10-25 16:29 — GO

• VERDICT: GO

  线程安全没问题：watch 线程只在锁内写 `sighting`，loop 线程经 `daemon.py:527` / `supervisor.py:60` 读 `stuck`（原子引用），`set_gd32_dfu` 仅在 supervisor 单线程调用（`_wait` 与 `d.run` 同一线程），状态变更经 `_touch()` 正确触发一帧。subprocess 干净：无 shell、`timeout=5`、`stdin=DEVNULL`、`run()` 超时自动 kill+wait 无僵尸；30 s/次、每次 ~30 ms，CPU/电池可忽略。解析对缺失键、非 int vid/pid、垃圾输入均有兜底且测试覆盖；hub 匹配基于真实 fixture 验证。两次确认彻底排除上电 ~1 s 的 bootloader 闪现；失败轮不清状态，无误撤。从不向设备发任何东西，符合硬性要求。横幅只覆盖 y≥92 底部块，标题栏/跨模式提醒/toast 仍可见，option/move 行被遮可接受（此时按键本就全失效），无回归（`alert` 默认空）。

  次要问题（不阻塞）：

  1. `hw/gd32watch.py:328` `known_hubs` 进程内永不过期：MK20 拔走后若别的 hub 复用同一 locationID 且挂 Maple 板，会误报横幅/WARNING。建议：仅当该 hub 最近一次见到 T113 在 N 分钟内才沿用，或 T113 连续缺席 M 轮后清除。
  2. `daemon.py:640` Mac 休眠 paused 分支不执行 `step()`，`gd32_dfu` 在睡眠期间不刷新，arrange 页状态可能陈旧；唤醒即恢复。可接受，或在 paused 分支加一行 `set_gd32_dfu`。
  3. `cli.py:76` watch 线程 start 后从不 join（daemon 线程兜底）。退出瞬间若 probe 在飞，可能留一行 ResourceWarning；建议 `finally` 里 `th.join(6)`（> TIMEOUT_S）。
  4. `gd32watch.py:291` 顶层无父节点的 T113（parent=-1）会被 `t113_by_parent[-1]` 收录，理论上可让无父 Maple 误配对；实测 ioreg 不会出现，可忽略或加 `n.parent >= 0` 过滤。

  测试 477 行覆盖确认/撤除/限频/失败/假 Maple/端到端/渲染字形，质量高。未跑长套件（按要求）。

