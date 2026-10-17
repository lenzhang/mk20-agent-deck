你是 GPT 侧产品决策者（grill-design 第 2 轮）。只做文档工作，禁止写产品代码，禁止修改 PRD 本体。

输入：
- `/Users/dev/workspace/mk20-agent-deck/PRD-mk20-agent-deck.md`（v1.1，D1–D14 已决策，不得推翻，除非硬矛盾）
- `/Users/dev/workspace/mk20-agent-deck/.grill/R1-claude.md`（Claude 的前沿问题）

要求：
1. 对 R1 的每个问题 F*（以及 Missing ACs / Desktop gaps）给出 **DECISION**（明确选项，不得回问用户）、理由、对应 PRD 章节、拟议 PRD 补丁（简短原文级文字）。
2. 以下待定项默认采纳（除非与 D1–D14 硬矛盾，需说明）：
   - Q1: agent 槽 = Codex Desktop 会话线程（thread）；单击选中该线程（聚焦行为遵循 PRD §6.3：单击选中，双击/聚焦键置前）——若与「单击聚焦」措辞冲突，请指出并按已决策 D11/§6.3 调和
   - Q2: P0 = 尽力而为状态 + `unknown` 状态 + 手动聚焦；精确状态（needs_approval 等）为 P1
   - Q3: 每模式独立槽位映射，切换时恢复
   - Q4: Phase-1 固定四技能（D5），动态映射后置
3. 挑战 R1 中薄弱、过度设计或与已决策冲突的点（标 CHALLENGE）。
4. 若发现 D1–D14 硬矛盾：记录并选更安全的产品选项 + 理由。
5. 输出写入 `/Users/dev/workspace/mk20-agent-deck/.grill/R2-gpt.md`，结构：Meta（GPT/codex exec）→ Decision table（ID | 问题 | DECISION | 理由 | PRD 章节）→ Challenges → Proposed PRD patches（按章节）→ Hard-contradiction log。
6. 写完在 stdout 输出：R2_DONE decisions=N
