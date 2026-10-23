# Kimi K3 — fw 1.7.0-rc2 delta (b26203a..a5cc8c1), 2026-10-24 04:23 — GO (NITs: non-atomic usb0.setup write, ll=0 log text)

• VERDICT: GO

  旧项核验（均 FIXED):
  - MAJOR 孤儿 ping:mk20d:298-313 每轮把每个 ping 直接后台（pid 即 ping)，轮末 kill+wait 精确回收，wait 退出码判应答（312)，弃用 .ecm-reply 标志文件。未回收僵尸占住 pid，故 kill 无误杀复用 pid 的竞态；BusyBox ping 不捕 TERM,wait 必收尸，无物活过本轮。fancy 探测保留（297，真机 -c/-W 已验证）;test_ecm_watchdog.py:309-327 fancy/nonfancy 双模式断言零存活且 argv 正确。
  - MINOR staging:gadget.sh:363 拒 `releases/.staging.tar`/`.incoming-*`，与 loader.sh:225/229/251 路径一致；崩溃残留由 loader.sh:138 启动清理，残留期仅 rc=4 拒（WARN 不重启），方向安全。
  - MINOR 别名：setup 记录 ll=(gadget.sh:585，先于 bridge 启动 595),re-arm 读记录、无记录回退探测现状（404-405),ra_has_cfg/ra_listeners/ra_netcfg 按 RLL 门控（370-380),ll=0 机器首 re-arm 不再 rc=2→重启。

  新问题（仅 NIT):
  - gadget.sh:585 记录非原子写（echo >；对比 mk20d:318 tmp+mv)：崩溃空窗留空文件→sed 无匹配→回退现状探测，且 re-arm 与 setup 互斥（448)，无实害，建议顺手改 tmp+mv。/tmp 为 tmpfs 不跨重启，篡改需 root（已出局）。
  - gadget.sh:393 失败日志在 ll=0 时仍提 $LL_IP，仅文案误导。
  - 应答改为轮末统一判定（311-312)：每轮必跑满 1 s，最坏晚 1 s，总窗口 ≤ECM_PROBE_S(274）不变，可接受。

  rc1 回归：无——早停条件（客户端增长/RX 增长/UDC 离开）与最终 rc 逻辑（315）保持，8 s 窗口不变；其余 10 文件与二进制与 rc1 相同。按要求未复跑套件，测试绿以 RELEASE.md 声明为准，上机验收项照旧。

