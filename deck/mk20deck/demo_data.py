"""SIM demo sessions covering every base state (used by simulator + tests)."""
from __future__ import annotations

from .model import Capabilities, Mode, Session, SessionId, State, Tool

_DEMO = [
    ("01a14a13-e433-7790-9fee-1b69ee1f2369", "PRD整合", State.RUNNING, "xhigh", "整合 R3 决策到 PRD v1.2"),
    ("01a14a09-70e1-7803-a65b-4bb22c168da4", "评审R2", State.DONE_UNREAD, "xhigh", "71 项裁决已输出"),
    ("01a0d4a1-b703-7b01-839d-616b3dfb6d38", "品牌视频", State.IDLE, "high", "生成宝子品牌视频素材"),
    ("01a1293b-d4b4-7023-ad67-84a23dcf8990", "SSH检查", State.UNKNOWN, "medium", "检查 192.168.88.216 的 SSH"),
    ("01a126ae-00b6-7da3-a2fc-0e613f717659", "TF卡固件", State.ERROR, "xhigh", "turn 失败"),
    ("01a1119e-5438-70e1-a853-8ed131942e29", "风道修复", State.THINKING, "high", "Fix airflow defects"),
    ("01a1153e-31fe-7c90-b681-b10e201d1787", "OTA发布", State.NEEDS_APPROVAL, "medium", "等待批准：git push"),
    ("01a114c4-44b8-7511-8eaf-0e40a1f33295", "NAS部署", State.NEEDS_INPUT, "low", "请确认部署目标"),
    # 9..15: only with n=15 (full PRD 1.5 L1 grid); the default 8 keep existing scenarios stable
    ("01a1c001-0000-7000-8000-000000000009", "固件1.5", State.RUNNING, "high", "L1 新键位联调"),
    ("01a1c001-0000-7000-8000-000000000010", "键位文档", State.IDLE, "medium", "README 键位表"),
    ("01a1c001-0000-7000-8000-000000000011", "路由测试", State.THINKING, "xhigh", "router 回归"),
    ("01a1c001-0000-7000-8000-000000000012", "副屏渲染", State.RUNNING, "high", "副屏提示更新"),
    ("01a1c001-0000-7000-8000-000000000013", "听写修正", State.DONE_UNREAD, "medium", "PTT 后删字验证"),
    ("01a1c001-0000-7000-8000-000000000014", "日志清理", State.IDLE, "low", "rotate 旧日志"),
    ("01a1c001-0000-7000-8000-000000000015", "OTA回滚", State.RUNNING, "high", "rollback guard"),
]
N_DEFAULT = 8


def demo_sessions(mode: Mode = Mode.DESKTOP, tools=(Tool.CODEX,), n: int = N_DEFAULT) -> list[Session]:
    out = []
    for i, (tid, title, st, eff, summ) in enumerate(_DEMO[:n]):
        tool = tools[i % len(tools)]
        out.append(Session(SessionId(mode, tid if mode == Mode.DESKTOP else f"%{i + 1}",
                                     "" if mode == Mode.DESKTOP else "4242-1700000000"),
                           tool, title, state=st, cwd="/Users/dev/workspace/mk20-agent-deck", cwd_confirmed=True,
                           effort=eff, summary=summ, source_note="SIM", state_reason="SIM 场景",
                           updated_ms=1_792_244_000_000 - i * 60_000))
    return out


DESKTOP_CAPS = Capabilities(focus=None, new_session=None, skill_current="当前线程预填未验证",
                            skill_new_thread=None, send="需辅助功能确认 composer，P0 未启用",
                            stop="中断接口不可达（仅 AX），P0 禁用", approve="请在 App 内处理（审批代答为 P1）",
                            effort_set="此会话不支持", precise_state="状态受限")
