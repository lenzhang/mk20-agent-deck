"""State palette (PRD §7.3) and mode badge colours (§6.7.1: badge strip only)."""
from __future__ import annotations

from ..model import Mode, State

BG = (14, 16, 20)
KEY_BG = (22, 25, 30)
CMD_BG = (32, 36, 42)
TEXT = (236, 239, 244)
DIM = (120, 126, 136)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
RED = (239, 68, 68)
SEA_GREEN = (32, 178, 170)

STATE_COLOR = {
    State.IDLE: (196, 200, 208),          # 灰白
    State.THINKING: (59, 130, 246),       # 蓝
    State.RUNNING: (6, 182, 212),         # 青蓝
    State.NEEDS_INPUT: (245, 158, 11),    # 琥珀
    State.NEEDS_APPROVAL: (245, 158, 11), # 琥珀 + 红角标
    State.DONE_UNREAD: (34, 197, 94),     # 绿
    State.ERROR: (239, 68, 68),           # 红
    State.UNASSIGNED: (52, 56, 62),       # 深灰
    State.OFFLINE: (107, 114, 128),       # 灰
    State.UNKNOWN: (120, 128, 140),       # 灰（静态灰底）
}

STATE_TEXT = {
    State.IDLE: "空闲",
    State.THINKING: "思考",
    State.RUNNING: "运行",
    State.NEEDS_INPUT: "待输入",
    State.NEEDS_APPROVAL: "待审批",
    State.DONE_UNREAD: "完成未读",
    State.ERROR: "出错",
    State.UNASSIGNED: "空槽",
    State.OFFLINE: "离线",
    State.UNKNOWN: "未知",
}

MODE_STRIP = {Mode.DESKTOP: (139, 92, 246), Mode.HERDR: (249, 115, 22), Mode.TMUX: (16, 185, 129)}
MODE_NAME = {Mode.DESKTOP: "Codex 桌面", Mode.HERDR: "herdr", Mode.TMUX: "tmux"}


def mix(a, b, t: float):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
