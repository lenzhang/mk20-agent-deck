"""Command line entry points."""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time

from .adapters.codex_desktop import CodexDesktopAdapter
from .adapters.herdr import HerdrAdapter
from .adapters.tmux import TmuxAdapter
from .config import CONFIG_DIR, DeckConfig, load_config, load_state, save_state
from .controller import Controller
from .layout import DeviceLayout
from .model import Mode

log = logging.getLogger("mk20deck")


def build_adapters(cfg: DeckConfig):
    return {
        Mode.DESKTOP: CodexDesktopAdapter(cfg.codex),
        Mode.HERDR: HerdrAdapter(cfg.herdr),
        Mode.TMUX: TmuxAdapter(cfg.tmux),
    }


def build_controller(cfg: DeckConfig, adapters, sim: bool = False) -> Controller:
    state = load_state(cfg.state_path)
    from .agentcfg import AgentConfigApplier
    from .settings import LoggingApplier, SettingsPage, SettingsStore
    settings = (SettingsPage(store=SettingsStore.temp(), applier=LoggingApplier()) if sim
                else SettingsPage(store=SettingsStore(), applier=AgentConfigApplier()))
    return Controller(cfg, adapters, state=state, sim=sim, settings=settings,
                      on_state_change=lambda st: save_state(cfg.state_path, st))


def cmd_run(args) -> int:
    from .hw.power import PowerMonitor
    from .supervisor import Supervisor, default_renderer_factory
    from .transport import find_ports, open_transport

    cfg = load_config(args.config)
    if args.port:
        cfg.port = args.port
    ctl = build_controller(cfg, build_adapters(cfg))
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    Supervisor(ctl, find_ports, open_transport, default_renderer_factory(cfg), cfg,
               power=PowerMonitor.default(), bg_adapters=build_adapters(cfg)).run(stop)
    return 0


def cmd_shots(args) -> int:
    from .sim import attention_shots, shots
    fn = attention_shots if getattr(args, "scenario", "default") == "attention" else shots
    made = fn(args.out, animations=not args.static)
    for n, cap in made:
        print(f"{os.path.join(args.out, n)}  {cap}")
    return 0


def cmd_serve(args) -> int:
    from .daemon import Daemon
    from .render.canvas import Renderer
    from .sim import demo_adapters, serve
    cfg = load_config(args.config)
    bg = None
    if args.source == "real":
        adapters = build_adapters(cfg)
        ctl = build_controller(cfg, adapters)
        bg = build_adapters(cfg)
    else:
        ctl = Controller(cfg, demo_adapters(), sim=True)
    d = Daemon(ctl, Renderer(DeviceLayout.default(), animations=cfg.animations), None,
               cfg.poll_interval_s, cfg.heartbeat_s, bg_poll_s=cfg.background_poll_s, bg_adapters=bg)
    serve(d, args.host, args.http_port, args.source)
    return 0


def cmd_probe(args) -> int:
    cfg = load_config(args.config)
    ad = CodexDesktopAdapter(cfg.codex)
    t0 = time.monotonic()
    sessions = ad.poll()
    dt = (time.monotonic() - t0) * 1000
    rows = [{"id": s.sid.native, "title": s.title[:24], "state": s.state.value, "why": s.state_reason,
             "effort": s.effort, "cwd": s.cwd, "src": s.source_note} for s in sessions]
    if args.json:
        print(json.dumps({"poll_ms": round(dt, 1), "limited": ad.status_limited(), "sessions": rows},
                         ensure_ascii=False, indent=1))
    else:
        print(f"poll {dt:.1f} ms · {ad.status_limited()}")
        for r in rows:
            print(f"{r['state']:12} {r['id'][:8]} {r['effort'] or '-':6} {r['title']:24} {r['why']}")
    return 0


def cmd_probe_tmux(args) -> int:
    """Read-only (display-message / list-panes / ps): never sends keys, never reads pane contents."""
    cfg = load_config(args.config)
    ad = TmuxAdapter(cfg.tmux)
    sessions = ad.poll()
    rows = [{"pane": s.sid.native, "tool": s.tool.value, "title": s.title, "state": s.state.value,
             "why": s.state_reason, "src": s.source_note, "where": s.summary} for s in sessions]
    if args.json:
        print(json.dumps({"generation": ad.generation, "socket": ad.socket_path, "limited": ad.status_limited(),
                          "panes": rows}, ensure_ascii=False, indent=1))
    else:
        print(f"tmux {ad.generation} {ad.socket_path} · {ad.status_limited()}")
        for r in rows:
            print(f"{r['pane']:5} {r['tool']:6} {r['state']:14} {r['title'][:20]:20} {r['src']} · {r['where']}")
    return 0


def _port_users(port: str) -> dict:
    """Read-only: who holds the CDC port (lsof) and whether ScreenKey runs (pgrep). Never kills."""
    from .adapters.base import SubprocessRunner
    r = SubprocessRunner()
    pids = [p for p in r.run(["lsof", "-t", port], timeout=5).stdout.split() if p.strip()]
    sk = r.run(["pgrep", "-lif", "screenkey"], timeout=5).stdout.strip().splitlines()
    return {"pids": pids, "screenkey": sk}


def cmd_devtest(args) -> int:
    from .hw.devtest import ConsolePrompter, DevTest, DevTestOptions, HeadlessPrompter, step_plan
    from .hw.power import PowerMonitor
    from .hw.usbenum import enumerate_usb
    from .transport import find_ports, open_transport

    if args.headless:
        # guided entirely on the device: host mode starts with backlight 0, so a
        # backlight write is required for the user to see anything.
        args.allow_device_writes = True
        if args.backlight is None:
            args.backlight = 80
    if args.backlight is not None and not args.allow_device_writes:
        print("--backlight 需要同时给出 --allow-device-writes")
        return 2
    out = args.out or os.path.join(CONFIG_DIR, "devtest", time.strftime("%Y%m%d-%H%M%S"))
    opts = DevTestOptions(interactive=not args.auto, port=args.port or "auto", fps_seconds=args.fps_seconds,
                          keys=not args.skip_keys, knobs=not args.skip_knobs, timing=not args.skip_timing,
                          reconnect=not args.skip_reconnect, sleep=args.with_sleep, via=not args.skip_via,
                          probe_reject=not args.no_reject_probe, allow_device_writes=args.allow_device_writes,
                          backlight_level=args.backlight, step_timeout=args.step_timeout,
                          headless=args.headless, start_timeout=args.start_timeout,
                          overall_timeout=args.overall_timeout if args.headless else None,
                          key_subset=tuple(k.strip() for k in args.keys.split(";") if k.strip()) if args.keys else (),
                          only=tuple(x.strip() for x in args.only.split(",") if x.strip()) if args.only else ())
    via_open = None
    if opts.via:
        via_open = _via_open_factory(allow_writes=False)
    hid_monitor = None
    if args.headless:
        from .hw.hidwatch import HidActivityMonitor
        hid_monitor = HidActivityMonitor.macos()
        print(f"无头模式：按设备屏幕提示操作；开始门限 {opts.start_timeout:.0f}s，之后总时限 {opts.overall_timeout:.0f}s，"
              f"每步 {opts.step_timeout:.0f}s；HID 监视={'on' if hid_monitor else 'off'}；输出 {out}", flush=True)
        for i, st in enumerate(step_plan(opts), 1):
            print(f"  {i:2}. {st}", flush=True)
    t = DevTest(opts, HeadlessPrompter() if args.headless else ConsolePrompter(), out, find_ports, open_transport,
                enumerate_usb=enumerate_usb, port_users=_port_users, via_open=via_open, power=PowerMonitor.default(),
                hid_monitor=hid_monitor)
    rep = t.run()
    for name, s in rep["sections"].items():
        print(f"{name:10} {s['status']}")
    print(f"\n报告：{os.path.join(out, 'devtest.md')}\n      {os.path.join(out, 'devtest.json')}")
    return 0 if all(s["status"] in ("pass", "skip", "manual") for s in rep["sections"].values()) else 1


def _via_open_factory(allow_writes: bool):
    def open_():
        from .hw.via import HidapiLink, ViaClient, ViaError, find_via_devices
        devs = find_via_devices()
        if not devs:
            raise ViaError("未找到 VIA raw HID 接口（4250:426f usage 0xFF60/0x61）")
        return ViaClient(HidapiLink(devs[0]["path"]), allow_writes=allow_writes)
    return open_


def cmd_usb(args) -> int:
    from .hw.usbenum import enumerate_usb, mk20_summary
    devs, src = enumerate_usb()
    print(json.dumps({"source": src, **mk20_summary(devs)}, ensure_ascii=False, indent=1))
    return 0


def cmd_via_backup(args) -> int:
    from .hw.via import backup
    client = _via_open_factory(False)()
    b = backup(client, rows=args.rows, cols=args.cols, encoders=args.encoders)
    out = args.out or os.path.join(CONFIG_DIR, "backups", f"via-{time.strftime('%Y%m%d-%H%M%S')}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    b.save(out)
    d = b.data
    print(f"VIA v{d['protocol_version']} · {d['layers']} 层 · 矩阵 {d['matrix']['rows']}×{d['matrix']['cols']}"
          f"（{'自动' if d['matrix']['detected'] else '手动'}）· 宏 {d['macros']['size']} B · sha256 {d['raw_keymap_sha256'][:12]}")
    if d["keymap_vs_raw_mismatch"]:
        print(f"⚠ 逐键读值与原始缓冲不一致 {len(d['keymap_vs_raw_mismatch'])} 处，矩阵尺寸可能不对")
    print(f"已保存：{out}")
    return 0


def cmd_via_restore(args) -> int:
    from .hw.via import Backup, apply_restore, plan_restore
    b = Backup.load(args.file)
    client = _via_open_factory(args.yes_write_keymap)()
    ops = plan_restore(client, b, include_macros=args.macros)
    print(f"需要写入 {len(ops)} 项（键位/旋钮{'/宏' if args.macros else ''}）")
    for op in ops[:40]:
        print("  ", op[:4] if op[0] != "macros" else ("macros", f"{len(op[1])} B"))
    if not ops:
        return 0
    if not args.yes_write_keymap:
        print("演练模式：未写入。确认后加 --yes-write-keymap 执行（写入后逐项回读校验）。")
        return 0
    errors = apply_restore(client, ops)
    print("校验失败：" + "; ".join(errors) if errors else "已恢复并回读校验通过")
    return 1 if errors else 0


def cmd_via_set(args) -> int:
    """Set one keymap cell (e.g. PTT = KC_RALT at L0 r3 c0) after a fresh backup; prints the rollback."""
    from .hw.via import backup, keycode_name, parse_keycode, set_one
    kc = parse_keycode(args.keycode)
    pre = backup(_via_open_factory(False)(), rows=args.rows, cols=args.cols)
    out = os.path.join(CONFIG_DIR, "backups", f"via-pre-set-{time.strftime('%Y%m%d-%H%M%S')}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    pre.save(out)
    cur = pre.data["keymap"][args.layer][args.row][args.col]
    print(f"L{args.layer} r{args.row} c{args.col}: {keycode_name(cur)} -> {keycode_name(kc)}；备份 {out}")
    if not args.yes_write_keymap:
        print("演练模式：未写入。确认后加 --yes-write-keymap。")
        return 0
    old, new = set_one(_via_open_factory(True)(), args.layer, args.row, args.col, kc)
    print(f"已写入并回读校验：0x{old:04X} -> 0x{new:04X}")
    print(f"回滚：python3 -m mk20deck via-restore {out} --yes-write-keymap")
    return 0


def cmd_gd32_flash(args) -> int:
    from .hw import gd32dfu
    plan = gd32dfu.plan_flash(args.image, allow_unknown=args.allow_unknown_image)
    print(f"镜像 {plan.image}\n  sha256 {plan.sha256}\n  {plan.known or '未知镜像'} · {plan.size} B · "
          f"SP 0x{plan.sp:08x} · reset 0x{plan.reset:08x}")
    print("  dfu-util -l:", "\n    ".join(["", *[l for l in plan.dfu_listing.splitlines() if "Found DFU" in l]]) or " -")
    for p in plan.problems:
        print("  ✗", p)
    print("命令：" + " ".join(plan.argv))
    if not args.i_understand_this_reflashes_gd32:
        print("演练模式：未刷写。确认后加 --i-understand-this-reflashes-gd32（见 deck/RECOVERY.md）。")
        return 0 if plan.ok else 2
    rc, out = gd32dfu.execute(plan)
    print(out)
    return rc


def cmd_launchd(args) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    tpl = os.path.join(here, "..", "packaging", "com.leo.mk20-agent-deck.plist.template")
    with open(tpl, encoding="utf-8") as f:
        s = f.read()
    s = s.replace("@PYTHON@", sys.executable).replace("@WORKDIR@", os.path.abspath(os.path.join(here, "..")))
    logdir = os.path.join(CONFIG_DIR, "logs")
    os.makedirs(logdir, exist_ok=True)  # launchd will not create StandardOutPath's directory
    s = s.replace("@LOGDIR@", logdir)
    print(s)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mk20deck")
    p.add_argument("--config", default=None)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="drive the MK20 over CDC-ACM")
    r.add_argument("--port", default=None)
    r.set_defaults(fn=cmd_run)
    s = sub.add_parser("sim-shots", help="render simulator screenshots")
    s.add_argument("--out", default="artifacts")
    s.add_argument("--static", action="store_true")
    s.add_argument("--scenario", choices=("default", "attention"), default="default")
    s.set_defaults(fn=cmd_shots)
    pt = sub.add_parser("probe-tmux", help="read-only tmux discovery: pane ids, commands, hook-derived states")
    pt.add_argument("--json", action="store_true")
    pt.set_defaults(fn=cmd_probe_tmux)
    w = sub.add_parser("sim-serve", help="interactive web simulator")
    w.add_argument("--source", choices=("demo", "real"), default="demo")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--http-port", type=int, default=8765)
    w.set_defaults(fn=cmd_serve)
    pr = sub.add_parser("probe-codex", help="read-only dump of Codex Desktop thread states")
    pr.add_argument("--json", action="store_true")
    pr.set_defaults(fn=cmd_probe)
    dt = sub.add_parser("devtest", help="on-device validation harness (read-only by default)")
    dt.add_argument("--port", default=None)
    dt.add_argument("--out", default=None)
    dt.add_argument("--auto", action="store_true", help="non-interactive: skip guided steps")
    dt.add_argument("--fps-seconds", type=float, default=5.0)
    dt.add_argument("--step-timeout", type=float, default=20.0)
    dt.add_argument("--skip-keys", action="store_true")
    dt.add_argument("--skip-knobs", action="store_true")
    dt.add_argument("--skip-timing", action="store_true")
    dt.add_argument("--skip-reconnect", action="store_true")
    dt.add_argument("--skip-via", action="store_true")
    dt.add_argument("--with-sleep", action="store_true")
    dt.add_argument("--no-reject-probe", action="store_true")
    dt.add_argument("--allow-device-writes", action="store_true", help="permit non-display writes (backlight)")
    dt.add_argument("--backlight", type=int, default=None)
    dt.add_argument("--headless", action="store_true",
                    help="no terminal input: guided on the device screens, auto-advance/skip, automatic HID-leak check")
    dt.add_argument("--keys", default="", help='subset of keys, document col,row separated by ";" e.g. "1,1;2,1"')
    dt.add_argument("--only", default="", help="comma list of sections, e.g. knobs,ptt,reconnect")
    dt.add_argument("--start-timeout", type=float, default=180.0, help="headless: wait for the first key press")
    dt.add_argument("--overall-timeout", type=float, default=300.0, help="headless: guided-phase budget after start")
    dt.set_defaults(fn=cmd_devtest)
    ub = sub.add_parser("usb-list", help="read-only USB enumeration with MK20 roles")
    ub.set_defaults(fn=cmd_usb)
    vb = sub.add_parser("via-backup", help="read-only export of the GD32 VIA keymap/encoders/macros")
    vb.add_argument("--out", default=None)
    vb.add_argument("--rows", type=int, default=None)
    vb.add_argument("--cols", type=int, default=None)
    vb.add_argument("--encoders", type=int, default=2)
    vb.set_defaults(fn=cmd_via_backup)
    vs = sub.add_parser("via-set", help="set one VIA keymap cell (fresh backup first; dry-run unless --yes-write-keymap)")
    vs.add_argument("layer", type=int)
    vs.add_argument("row", type=int)
    vs.add_argument("col", type=int)
    vs.add_argument("keycode", help="KC_RALT / RALT / 0xE6")
    vs.add_argument("--rows", type=int, default=4)
    vs.add_argument("--cols", type=int, default=5)
    vs.add_argument("--yes-write-keymap", action="store_true")
    vs.set_defaults(fn=cmd_via_set)
    vr = sub.add_parser("via-restore", help="restore a VIA backup (dry-run unless --yes-write-keymap)")
    vr.add_argument("file")
    vr.add_argument("--macros", action="store_true")
    vr.add_argument("--yes-write-keymap", action="store_true")
    vr.set_defaults(fn=cmd_via_restore)
    gf = sub.add_parser("gd32-flash", help="GD32 DFU re-flash (dry-run unless --i-understand-this-reflashes-gd32)")
    gf.add_argument("--image", required=True)
    gf.add_argument("--allow-unknown-image", action="store_true")
    gf.add_argument("--i-understand-this-reflashes-gd32", action="store_true")
    gf.set_defaults(fn=cmd_gd32_flash)
    ld = sub.add_parser("launchd-plist", help="print the LaunchAgent plist for this install")
    ld.set_defaults(fn=cmd_launchd)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
