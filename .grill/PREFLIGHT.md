# PREFLIGHT — 模型网络出口核验（2026-10-17 21:18–21:25 SGT / 08:18–08:25 CDT）

> 无密钥：节点 server/password/UUID 均未记录。

## 代理客户端
- 运行中：`/Users/dev/bin/mihomo -d ~/.config/mihomo`（PID 322）；external-controller `127.0.0.1:9090`（只读查询，**未改任何设置**）
- 入口：`mixed-port 7890`（通用）；listener `7895`（钉死 SG 组，ccai sg 专用）
- 系统代理（scutil）关闭；另有 TUN（utun1，网关 198.18.0.1）兜底

## Codex（GPT 侧）— 要求：经 "112" 美国节点
| 项 | 证据 |
|----|------|
| codex 配置 | `~/.codex/config.toml` 的 shell env 段：`HTTP(S)_PROXY/ALL_PROXY=http://127.0.0.1:7890`；`proxy_url = http://127.0.0.1:7890` |
| 启动方式 | 显式包装 `.grill/codex-env.sh`（导出同一组 7890 代理变量后 exec codex） |
| mihomo 规则 | `DOMAIN-SUFFIX,openai.com,AI`、`chatgpt.com,AI`、`DOMAIN-KEYWORD,openai,AI`；`ipinfo.io → AI` |
| 策略组 `AI` | Selector，**now = `US-Exit-Hy2`**（Hysteria2，alive=true）；候选 US-Exit-SS / AI-Auto / SG / HK2-Hy2 |
| 实时连接链 | `api.openai.com` / `chat.openai.com` → rule `DomainSuffix openai.com` → chains **`['US-Exit-Hy2','AI']`** |
| 出口 ipinfo（codex 同 env） | **203.0.113.10 · Los Angeles, California, US · AS3257 GTT**（config 注释：112 家宽出口同 203.0.113.10 → 与用户「112、尾号 55」一致） |
| chatgpt.com cdn-cgi/trace（同 env） | ip=203.0.113.10, loc=**US**, colo=LAX |
| api.openai.com 可达 | HTTP 401（未带鉴权，链路通） |
| **结论** | ✅ codex 走 112 美国家宽出口 |

## Claude（ccai sg）— 要求：经 SG 专用入口
| 项 | 证据 |
|----|------|
| `ccai check` | glm/grok/kimi/flash 端点 HTTP 200；全局 AI 出口 203.0.113.10 US（非 sg 会话用） |
| ccai sg 机制 | 子进程注入 `HTTPS_PROXY=http://127.0.0.1:7895`，启动前 `sg_exit_check` 要求 country==SG |
| :7895 出口 ipinfo | **116.14.13.40 · Singapore, SG · AS9506 Singtel** |
| :7895 ipify | 116.14.13.40 |
| ccai sg 启动自检输出 | `SG 入口(:7895) 出口: 116.14.13.40 | Singapore, SG` |
| **结论** | ✅ Claude 走 SG 出口 |

## 对照（未走显式代理）
| 路径 | IP | 说明 |
|------|----|------|
| 无代理 env curl ipinfo | 203.0.113.10 US | 被 TUN 接管，按规则同走 AI→112 |
| 无代理 env curl ipify | 154.36.145.60 | 走 Match→FOREIGN，证明不同规则出口不同 |

## 每轮复检
每轮启动模型前重跑：codex → `codex-env.sh curl ipinfo` 须 US/203.0.113.10 且 `AI.now=US-Exit-Hy2`；Claude → `:7895` 须 SG。复检记录追加在下方。

### 复检日志
- 21:20:06 +08 · R1-pre · claude · 116.14.13.40 SG · PASS
- 21:24:50 +08 · R2-pre · codex · AI.now=US-Exit-Hy2 exit=203.0.113.10 US · PASS
- 21:32:32 +08 · R3-pre · claude · 116.14.13.40 SG · PASS
- 21:36:19 +08 · R4-pre · codex · AI.now=US-Exit-Hy2 exit=203.0.113.10 US · PASS
- 21:41:57 +08 · R5-pre · claude · 116.14.13.40 SG · PASS
