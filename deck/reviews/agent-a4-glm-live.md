# A4 live GLM run — Mac dev-mbp.lan, 2026-10-19 ~08:00 CDT
Binary: deck/agent/build/mk20-agent-nosan (macOS host, A4 working tree on e2085c7), `--llm-test --trust-clock --timeout 90000`.
net.json: mac_proxy, proxy http://127.0.0.1:7890 (mihomo), no fallbacks. provider.json: provider glm, base_url
https://open.bigmodel.cn/api/coding/paas/v4 (coding plan), route domestic, api_key_file ~/.mk20-secrets/glm.key (0600).
Image: 1280x800 JPEG 27,691 B, red square x100-399/y200-499 (centre 250,350 px), blue circle centre 950,400 r150.
Tools: click{x,y} normalized 0..1000 (coord_mode norm1000 → pixels returned).

| case | model | class | HTTP | result | ms | peak RSS KiB |
|---|---|---|---|---|---|---|
| text_plain | glm-5.3 | ok | 200 | "MK20 OK" | 6382 | 2784 |
| text_tools | glm-5.3 | ok | 200 | click(640,400) px = screen centre | 5934 | 2912 |
| image_plain | glm-5.3-flash | ok | 200 | "A red square is on the left, and a blue circle is on the right." | 1794 | 3040 |
| image_tools | glm-5.3-flash | ok | 200 | click(249,350) px vs truth (250,350) | 5986 | 3056 |
| bad key | glm-5.3 | auth, retryable=false | 401 | exit 1 | 86 | 2832 |
| proxy down (127.0.0.1:7999) | glm-5.3 | connect, retryable=true | 0 | exit 1, no direct attempt | 3 | 2480 |

Key hygiene: key string grepped (in-process, not printed) against CLI stdout+stderr: absent; "Bearer" count 0.
Route check before codex: AI=US-Exit-Hy2 exit=203.0.113.10 (agent-a4-route-check.txt). GLM is domestic → mihomo rules.
