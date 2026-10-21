• Four diffs read. Now the docs and full sources to verify the contracts (MAINT-HOOKS, session/CSRF flow, httpd parser, mk20d supervision).

• Now the firmware side — full mk20d and mk20lib.sh.

• mk20d checked. Now mk20lib.sh, mem.c, and the remaining pieces (netcfg/llm parse bounds, mem wipe).

• A few final cross-checks: the ONBOARD-UI-PLAN U3 contract (shared config writers) and whether anything else writes the same config files.

• VERDICT: GO

  四份 diff 均完整通读；另完整核对 web.c、httpd.c/.h、web_store.c/.h、web.h、web_maint.c、web_pages.c、mcp_cli.c、json_util.c、netcfg.c、llm.c(provider_parse)、mem.c、policy.c(agent_redact)、loop.c(record)、mk20d、mk20lib.sh、mk20-agent-child.sh、web_providers.inc、SETTINGS.md、MAINT-HOOKS.md、ONBOARD-UI-PLAN §U3。七个检查面逐项过：

  1. **认证/会话/CSRF**：Host 精确匹配先于一切路由（web.c:453），DNS rebinding 被挡；Origin 存在即必须等于 `http://<host>`；无 Origin 的非 GET 必须带 `Sec-Fetch-Site: same-origin`（:464）；配对路由外所有写操作还要 32 字节 CSRF 常量时间比较（:465, equal():33）。Cookie HttpOnly+SameSite=Strict+Path=/，http 内网正确地不加 Secure。会话 2 个、30 分钟空闲/12 小时绝对过期，过期即擦除并取消名下任务（:52-56）。配对：码 6 位、5 分钟、5 次/码、全局 10 次/10 分钟锁定（web.h:11-15），速率环实现正确。
  2. **配对码**：/dev/urandom 拒绝采样（web.c:98-104）；发布为独占 tmp+0600+fsync+rename（web_store.c:291-307），目录拒组/他人可写；结束/过期/锁定/配成均 pair_end 清码删文件；启动时清旧挑战与 `.web-*.tmp`（mcp_cli.c:175）。码从不进 HTTP 响应或日志。
  3. **维护钩子**：完全符合 MAINT-HOOKS——固定名、独占 `.web-<rand>.tmp` 同目录、0600、写满 fsync、rename、目录 fsync（web_store.c:143-154）；inbox 缺/软链/非 0700 → maint_unavailable 且不建不 chmod（web_maint.c:181-187）；confirm 须自指动作名、web_only 白名单字段；debug 体恰好 `verbose=0|1\n`。消费端（mk20d:153-201）rename 认领、拒软链/硬链/目录、≤128 字节、内容不入日志。无重启/回滚/GD32/gadget 路由。
  4. **配置写**：flock(.apply-lock)+`.apply-in-progress` 先持久后改、成功后清除+目录 fsync，读者遇标记 409（web_store.c:179-197,234-254）；provider/model/base_url/route 全来自内置目录表，key 路径服务端推导（web.c:232-246）；代理 host 仅允许 usb0 对端+三个固定 Mac 地址或 127.0.0.1（web_store.c:219-226）；JSON 解析有节点/深度/UTF-8/NUL/重复键界限（json_util.c），文件 ≤16KB。
  5. **HTTP 解析**：头 8KB/体 64KB/4 连接/5s 慢连接 deadline/每连接 32 请求/CL 溢出防护/TE+CL 拒绝/Expect 417/无 chunked（httpd.c:93-143,232-233）；响应 ≤4MB；空闲时 250ms poll 不空转（:323-329）。128MB 内存下约为界。
  6. **mk20d 守护**：flock 单实例、1..30s 退避、≥60s 复位、模式/IP 变化重启不计崩溃、停止文件与 FAT 开关每秒轮询、无忙循环；未用 stat/timeout/od/realpath（mtime 用 `date -r`），引用、heredoc、`local` 均符合 BusyBox 1.27 ash；`--web-listen` 的 IP 经 v_ip4+`.2`+httpd_address 双重校验，端口固定 8780。
  7. **泄漏面**：密钥只写（本 diff 去掉了长度回显）、状态仅 set/not、0600+属主校验；日志只有固定元数据且经 agent_redact；health/state JSON 经 allow-list+可打印 ASCII 过滤（web_maint.c:83-101）；自检明细截断 ≤12 条 ×120 字符；CSRF 仅由 /api/session 在同源+持 Cookie 下返还（web.c:441，注释已注明权衡）。

  MINOR（均不阻断）:
  - web.c:160-165 无活跃挑战时 /pair 失败不计入限速，本地恶意进程可持续续锁配对（每 10 分钟一轮），仅限可用性；建议无挑战时也记 fails 环。
  - mk20d:17 `mkdir -p "$TM"` 未 chmod 700，且 health() 用可预测临时名 `health.json.tmp.$$`（mk20d:291）；设备全 root 影响小，建议 `chmod 700 "$TM"` 并改 mktemp。
  - web_maint.c:65-81 read_json 未校验 health.json/state.json 属主/权限（仅 S_ISREG+大小）；数据已白名单清洗，建议补 uid/mode 检查与 web_read_config 对齐。
  - web.c:141 pair/start 重发布失败被 `(void)` 吞掉，页面报 started 但键盘无码；失败时建议返回 503。
  - web.h:6 WEB_SESSIONS=2，第三个浏览器配对得 503 直到过期；可文档化。

  已完整读取的 4 份 diff：1-web.c.diff、2-store-maint-httpd.diff、3-mk20d-mk20lib.diff、4-cli-pages-gen.diff。

