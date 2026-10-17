你是 Claude 侧终审（grill-design 第 3 轮）。只做文档工作，禁止写产品代码，禁止修改 PRD 本体。

输入：PRD v1.1、`.grill/R1-claude.md`、`.grill/R2-gpt.md`（均在 /Users/dev/workspace/mk20-agent-deck）。

要求：
1. 对 R2 每条 DECISION 给出 ACCEPT / REJECT / AMEND。仅当安全性或可行性被阻断时 REJECT/AMEND，并给出替代决策（不得回问用户）。
2. 不重开 D1–D14，除非硬矛盾；Q1–Q4 默认采纳方向保持。
3. 产出 **最终决策表**：ID | 决策（最终） | 来源（R2/R3 修订） | PRD 章节 | 是否需人类（应尽量为否）。
4. 写入 `/Users/dev/workspace/mk20-agent-deck/.grill/R3-claude.md`。
5. stdout 输出：R3_DONE accepted=A amended=M rejected=R
