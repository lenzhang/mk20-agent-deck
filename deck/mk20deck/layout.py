"""Device canvas layout.

``SHOW_JPG`` carries one JPEG for the *whole* device canvas
(``deviceWidth`` x ``deviceHeight`` from ``getInfo``); the firmware crops it into
the 20 key LCDs and the sub-screen using ``devicePanel.rects``.  Firmware rejects
frames with the wrong resolution ("Incorrect resolution, the device requires the
resolution to be ...").

Until the device reports its panel, we use a *guessed* default: the vendor demo's
RectWidget default size is 640x656, which matches 5x128 = 640 wide and
142 (sub-screen) + 2 + 4x128 = 656 high.  The real layout from ``getInfo``
always overrides this guess (verify on device, PRD §10.1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

KEY_SIZE = 128
SUB_W, SUB_H = 428, 142
COLS, ROWS = 5, 4


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int
    col: int = -1  # firmware coordinates (0-based assumed until verified)
    row: int = -1
    is_key: bool = False


@dataclass
class DeviceLayout:
    width: int
    height: int
    rects: list[Rect]
    model: str = "MK20 (default guess)"
    guessed: bool = True
    # Firmware -> document coordinates.  Document coordinates are 1-based
    # (col 1..5 left->right, row 1..4 top->bottom), PRD D12.
    fw_base: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def keys(self) -> list[Rect]:
        return [r for r in self.rects if r.is_key]

    @property
    def subscreen(self) -> Rect | None:
        non_keys = [r for r in self.rects if not r.is_key]
        if not non_keys:
            return None
        return max(non_keys, key=lambda r: r.w * r.h)

    def key_rect(self, doc_col: int, doc_row: int) -> Rect | None:
        fc, fr = doc_col - 1 + self.fw_base, doc_row - 1 + self.fw_base
        for r in self.keys:
            if r.col == fc and r.row == fr:
                return r
        return None

    def fw_to_doc(self, col: int, row: int) -> tuple[int, int]:
        return col - self.fw_base + 1, row - self.fw_base + 1

    @classmethod
    def default(cls) -> "DeviceLayout":
        rects = [Rect((640 - SUB_W) // 2, 0, SUB_W, SUB_H)]
        top = 656 - ROWS * KEY_SIZE
        for r in range(ROWS):
            for c in range(COLS):
                rects.append(Rect(c * KEY_SIZE, top + r * KEY_SIZE, KEY_SIZE, KEY_SIZE, c, r, True))
        return cls(640, 656, rects)

    @classmethod
    def from_get_info(cls, result: dict[str, Any], fw_base: int = 0) -> "DeviceLayout":
        panel = result.get("devicePanel") or {}
        rects = []
        for o in panel.get("rects") or []:
            try:
                rects.append(Rect(int(o["x"]), int(o["y"]), int(o["width"]), int(o["height"]),
                                  int(o.get("col", -1)), int(o.get("row", -1)), bool(o.get("isKey", False))))
            except (KeyError, TypeError, ValueError):
                continue
        w = int(result.get("deviceWidth") or 0)
        h = int(result.get("deviceHeight") or 0)
        if not rects or not w or not h:
            lay = cls.default()
            lay.extra["getInfo_incomplete"] = True
            return lay
        return cls(w, h, rects, model=str(result.get("deviceModel", "?")), guessed=False, fw_base=fw_base,
                   extra={"deviceVersion": result.get("deviceVersion")})

    def to_get_info(self) -> dict[str, Any]:
        """Inverse of from_get_info (used by the mock device)."""
        return {
            "deviceModel": "MK20-MOCK",
            "deviceVersion": "mock",
            "deviceWidth": self.width,
            "deviceHeight": self.height,
            "devicePanel": {
                "rectCols": COLS, "rectRows": ROWS,
                "rects": [
                    {"x": r.x, "y": r.y, "width": r.w, "height": r.h, "col": r.col, "row": r.row, "isKey": r.is_key}
                    for r in self.rects
                ],
            },
        }
