你是 Claude 侧对抗审阅（最终短轮）。只读，不改 PRD、不写代码、不向用户提问。
读 /Users/dev/workspace/mk20-agent-deck/PRD-mk20-agent-deck.md（应为 v1.2）与 GRILL-DECISIONS.md。
任务：
1. 核对 v1.2 是否忠实落实 .grill/R3-claude.md 的最终决策（列出遗漏/误落实，若无写「无」）。
2. 只列**残余风险**（≤12 条）：风险 | 触发条件 | 影响 | 缓解/验证项 | 是否阻塞签字（应仅在 PRD 自相矛盾时为是）。
3. 给出一句结论：v1.2 是否可交用户签字。
写入 /Users/dev/workspace/mk20-agent-deck/.grill/R4-risks.md。stdout 输出：RISKS_DONE n=N blocking=B
