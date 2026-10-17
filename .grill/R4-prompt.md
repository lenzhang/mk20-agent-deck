你是 GPT 侧整合者（grill-design 第 4 轮）。仅更新文档，禁止写产品代码，禁止 git push。

输入（/Users/dev/workspace/mk20-agent-deck）：PRD-mk20-agent-deck.md（v1.1）、.grill/R1-claude.md、.grill/R2-gpt.md、.grill/R3-claude.md（最终决策表以 R3 为准）。

任务：
1. 写 `GRILL-DECISIONS.md`：标题、过程说明（多模型：Claude via ccai sg 新加坡出口 / GPT via codex 112 美国出口，见 .grill/PREFLIGHT.md；本轮未向用户提问）、决策表（ID | 决策 | 理由 | 要改的 PRD 章节）、仍需实机/本机 spike 的技术验证项（非用户决策）。
2. 直接编辑 `PRD-mk20-agent-deck.md` → **v1.2**：
   - 头部版本改为「1.2（多模型拷问后，待签字）」，状态行同步；
   - 把 R3 最终决策落到对应章节（状态模型加入 `unknown` 若决定；槽位定义；每模式独立映射；验收标准补齐等）；
   - §10 新增「10.2 多模型拷问决策（G1…）」表，不改 D1–D14 原意（除非 R3 记录了硬矛盾并给出更安全选择，此时就地注明）；
   - §13 修订记录加 v1.2：「多模型拷问（Claude×GPT 4 轮），本轮未向用户提问」；
   - 结尾签字句改为「批准 PRD v1.2」。
   - 保持中文、保持原结构，勿删除既有内容中仍有效的信息。
3. stdout 输出：R4_DONE prd_version=1.2 decisions=N
