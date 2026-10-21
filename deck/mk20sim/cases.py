"""Declarative interaction test cases — the single source of truth for pytest,
the CLI (``python -m mk20sim run-cases``) and the web UI test panel.

A case is a JSON file in ``mk20sim/cases/`` (``drafts/`` holds recordings and is
listed in the UI but skipped by pytest/CLI unless asked for)::

    {"name": "single_tap_selects_and_focuses", "group": "gestures", "desc": "...",
     "steps": [
       {"press": "S1", "hold": 40},          # down, hold ms, up (names: harness.KEY_NAMES or "col,row")
       {"down": "PTT"}, {"up": "PTT"},
       {"wait": 100},
       {"knob": "K1", "action": "cw", "n": 3},  # action: cw|ccw|press|down|up
       {"raw": [106, 106, true]},             # firmware-coordinate event
       {"mark": "t"},                         # remember sim time
       {"expect": {"page": "L4", "agent_calls": {"focus": 1}}}
     ]}

Expectations (all keys optional, all must hold):
  page, mode, ptt, scroll, toast_contains        – exact / substring
  selected: true|false                           – a session is / isn't selected
  selected_title: "OTA发布"                       – title of the selected session
  mode_changed / selected_changed / page_changed: true|false  – vs the last "mark"
  agent_calls: {name: n}  (n exact; "*" for any name; {"min": n} also accepted)
  agent_call_within_ms: {"name": "focus", "ms": 350}  – first call ≤ ms after last mark
  hid: ["RightOption down", "Backspace down", ...] – prefixes of the HID outputs, in order
  key_image_size: [128,128], sub_image_size: [428,142], frames_min: n
  settings.<field>: value   – settings mock: active/tab/focus/test or a config key (profile, provider…)
  settings_toast_contains: text
  agent_file: {"provider.json": {...}}   – subset of the agent runtime config written by 应用
  tester_last_route: [provider, proxy_url] – last (mocked) connection test route
Optional top-level "latency": {"input_ms", "input_jitter_ms", "router_ms", "router_jitter_ms",
  "frame_ms", "frame_jitter_ms", "seed"} injects delays for the whole case.
"""
from __future__ import annotations

import base64
import io
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .harness import Latency, Sim, CSim

CASES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cases")


@dataclass
class Case:
    name: str
    group: str
    desc: str
    steps: list[dict]
    path: str = ""
    draft: bool = False
    latency: dict | None = None
    backend: str = "python"

    @property
    def id(self) -> str:
        return f"{self.group}/{self.name}"


@dataclass
class StepFailure:
    step: int           # 1-based index into case.steps
    step_src: dict
    key: str
    expected: Any
    actual: Any

    def __str__(self) -> str:
        return f"step {self.step} {json.dumps(self.step_src, ensure_ascii=False)}: {self.key} expected {self.expected!r}, got {self.actual!r}"


@dataclass
class CaseResult:
    case: Case
    ok: bool
    duration_ms: float          # wall time
    sim_ms: float               # simulated time
    failure: StepFailure | None = None
    error: str | None = None
    screen_png_b64: str | None = None
    outputs: list[dict] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"id": self.case.id, "ok": self.ok, "duration_ms": round(self.duration_ms, 1),
                "sim_ms": round(self.sim_ms, 1), "error": self.error,
                "failure": None if not self.failure else {
                    "step": self.failure.step, "src": self.failure.step_src, "key": self.failure.key,
                    "expected": self.failure.expected, "actual": self.failure.actual, "text": str(self.failure)},
                "screen": self.screen_png_b64, "outputs": self.outputs[-60:]}


def load_cases(directory: str = CASES_DIR, include_drafts: bool = False) -> list[Case]:
    out: list[Case] = []
    for root, _dirs, files in os.walk(directory):
        draft = os.path.basename(root) == "drafts"
        if draft and not include_drafts:
            continue
        for fn in sorted(files):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(root, fn)
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            for c in (d if isinstance(d, list) else [d]):
                out.append(Case(c["name"], c.get("group", "drafts" if draft else "misc"), c.get("desc", ""),
                                c["steps"], p, draft or bool(c.get("draft")), c.get("latency"), c.get("backend", "python")))
    out.sort(key=lambda c: (c.draft, c.group, c.path, c.name))
    return out


def filter_cases(cases: list[Case], pattern: str | None) -> list[Case]:
    if not pattern:
        return cases
    return [c for c in cases if pattern.lower() in c.id.lower() or pattern.lower() in c.desc.lower()]


class _Fail(Exception):
    def __init__(self, f: StepFailure) -> None:
        super().__init__(str(f))
        self.f = f


def _check(sim: Sim, exp: dict, mark: dict, idx: int, src: dict) -> None:
    st = sim.state()

    def need(key: str, expected: Any, actual: Any, ok: bool | None = None) -> None:
        if not (expected == actual if ok is None else ok):
            raise _Fail(StepFailure(idx, src, key, expected, actual))

    for k, v in exp.items():
        if k in ("page", "mode", "ptt", "scroll"):
            need(k, v, st[k])
        elif k == "toast_contains":
            need(k, v, st["toast"], v in (st["toast"] or ""))
        elif k == "selected":
            need(k, bool(v), bool(st["selected"]))
        elif k == "selected_title":
            need(k, v, st["selected"])
        elif k in ("mode_changed", "selected_changed", "page_changed"):
            f = k.split("_")[0]
            need(k, bool(v), st[f] != mark.get(f))
        elif k == "agent_calls":
            for name, n in v.items():
                got = len(sim.agent_calls(None if name == "*" else name))
                if isinstance(n, dict):
                    need(f"agent_calls.{name}", f">= {n['min']}", got, got >= n["min"])
                else:
                    need(f"agent_calls.{name}", n, got)
        elif k == "agent_call_within_ms":
            calls = [o for o in sim.agent_calls(v["name"]) if o.t >= mark.get("t", 0) - 1e-9]
            dt = round((calls[0].t - mark.get("t", 0)) * 1000, 1) if calls else None
            need(k, f"{v['name']} <= {v['ms']} ms", dt, dt is not None and dt <= v["ms"])
        elif k == "hid":
            got = [o.text for o in sim.out("hid")]
            ok = len(got) >= len(v) and all(g.startswith(e) for g, e in zip(got, v))
            need(k, v, got, ok)
        elif k == "key_image_size":
            need(k, list(v), list(sim.key_image(exp.get("key", "S1")).size))
        elif k == "sub_image_size":
            need(k, list(v), list(sim.sub_image().size))
        elif k == "frames_min":
            need(k, f">= {v}", sim.frames, sim.frames >= v)
        elif k == "key":
            pass
        elif k in ("host_health", "stopped", "ctl_page"):
            need(k, v, st[k])
        elif k.startswith("settings."):
            f = k.split(".", 1)[1]
            sv = st["settings"] if isinstance(sim, CSim) else sim.settings.state()
            cur = sv.get(f) if f in sv else sim.settings.cfg(f)
            need(k, v, cur)
        elif k == "agent_file":  # {"provider.json": {"provider": "kimi"}} – subset match on written config
            for fn, want in v.items():
                fp = os.path.join(sim.applier.root, fn)
                got = json.load(open(fp, encoding="utf-8")) if os.path.exists(fp) else None
                sub = {kk: (got or {}).get(kk) for kk in want} if isinstance(want, dict) else got
                need(f"agent_file.{fn}", want, sub)
        elif k == "tester_last_route":
            calls = sim.tester.calls
            need(k, v, list(calls[-1]) if calls else None)
        elif k == "settings_toast_contains":
            t = sim.settings.toast or ""
            need(k, v, t, v in t)
        else:
            raise _Fail(StepFailure(idx, src, k, "known expectation", "unknown key"))


def _do(sim: Sim, s: dict, mark: dict) -> None:
    if "press" in s:
        sim.press(s["press"], s.get("hold", 50))
    elif "down" in s:
        sim.down(s["down"])
    elif "up" in s:
        sim.up(s["up"])
    elif "wait" in s:
        sim.wait(s["wait"])
    elif "knob" in s:
        sim.knob(s["knob"], s.get("action", "press"), s.get("n", 1))
    elif "host" in s:
        sim.inject_host(s["host"])
    elif "config_fault" in s:
        if s["config_fault"] is not True:
            raise ValueError("config_fault must be true")
        sim.inject_config_fault()
    elif "test_result" in s:
        sim.inject_test(s["test_result"], s.get("key_present", False))
    elif "raw" in s:
        sim.raw(*s["raw"])
    elif "mark" in s:
        st = sim.state()
        mark.clear()
        mark.update({"t": sim.now(), "mode": st["mode"], "selected": st["selected"], "page": st["page"]})
    else:
        raise ValueError(f"unknown step {s}")


def run_case(case: Case, observer: Callable[[Sim, float], None] | None = None,
             on_step: Callable[[int, dict, Sim], None] | None = None, sim: Sim | None = None) -> CaseResult:
    owned = sim is None
    if case.backend not in ("python", "c"):
        raise ValueError(f"unknown backend {case.backend!r}")
    sim = sim or (CSim if case.backend == "c" else Sim)(latency=Latency.from_dict(case.latency))
    sim.observer = observer
    w0, s0 = time.perf_counter(), sim.now()
    mark: dict = {"t": s0}
    res = CaseResult(case, True, 0, 0)
    try:
        for i, s in enumerate(case.steps, 1):
            if on_step:
                on_step(i, s, sim)
            if "expect" in s:
                _check(sim, s["expect"], mark, i, s)
            else:
                _do(sim, s, mark)
    except _Fail as e:
        res.ok, res.failure = False, e.f
    except Exception as e:  # noqa: BLE001 — a broken case is a failed case
        res.ok, res.error = False, f"{type(e).__name__}: {e}"
    sim.observer = None
    res.duration_ms = (time.perf_counter() - w0) * 1000
    res.sim_ms = (sim.now() - s0) * 1000
    res.outputs = [o.__dict__ for o in sim.outputs]
    if not res.ok:
        buf = io.BytesIO()
        sim.canvas().save(buf, "PNG")
        res.screen_png_b64 = base64.b64encode(buf.getvalue()).decode()
    if owned and isinstance(sim, CSim):
        sim.close()
    return res


def junit_xml(results: list[CaseResult]) -> str:
    from xml.sax.saxutils import escape, quoteattr
    fails = sum(1 for r in results if not r.ok)
    parts = [f'<?xml version="1.0" encoding="utf-8"?>\n<testsuite name="mk20sim" tests="{len(results)}" '
             f'failures="{fails}" time="{sum(r.duration_ms for r in results) / 1000:.3f}">']
    for r in results:
        parts.append(f'  <testcase classname={quoteattr("mk20sim." + r.case.group)} name={quoteattr(r.case.name)} '
                     f'time="{r.duration_ms / 1000:.3f}">')
        if not r.ok:
            msg = str(r.failure) if r.failure else (r.error or "failed")
            parts.append(f"    <failure message={quoteattr(msg)}>{escape(msg)}</failure>")
        parts.append("  </testcase>")
    parts.append("</testsuite>\n")
    return "\n".join(parts)


def save_draft(name: str, steps: list[dict], desc: str = "") -> str:
    os.makedirs(os.path.join(CASES_DIR, "drafts"), exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)[:60] or "recording"
    p = os.path.join(CASES_DIR, "drafts", f"{safe}.json")
    case = {"name": safe, "group": "drafts", "desc": desc or "Recorded in the web UI — add expect steps, then move "
            "this file out of drafts/ to make pytest run it.", "draft": True,
            "steps": steps + [{"expect": {}}]}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False, indent=1)
    return p
