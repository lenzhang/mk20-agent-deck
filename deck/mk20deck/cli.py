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
    return Controller(cfg, adapters, state=state, sim=sim,
                      on_state_change=lambda st: save_state(cfg.state_path, st))


def cmd_run(args) -> int:
    from .daemon import Daemon
    from .link import DeviceLink
    from .render.canvas import Renderer
    from .transport import SerialTransport, find_ports

    cfg = load_config(args.config)
    if args.port:
        cfg.port = args.port
    ctl = build_controller(cfg, build_adapters(cfg))
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        ports = [cfg.port] if cfg.port != "auto" else find_ports()
        if not ports:
            log.info("MK20 CDC port not found; retrying")
            stop.wait(2)
            continue
        try:
            tr = SerialTransport(ports[0])
        except Exception as e:  # noqa: BLE001 — e.g. port busy (ScreenKey); never kill it (PRD §8)
            log.warning("cannot open %s: %s — if ScreenKey is running, quit it and retry", ports[0], e)
            stop.wait(3)
            continue
        renderer = Renderer(DeviceLayout.default(), animations=cfg.animations, max_fps=cfg.max_fps,
                            jpeg_quality=cfg.jpeg_quality)
        d = Daemon(ctl, renderer, None, cfg.poll_interval_s, cfg.heartbeat_s, cfg.knob_map)
        link = DeviceLink(tr, on_key=d.on_device_key, ack_timeout=cfg.ack_timeout_s,
                          preamble_flush=cfg.preamble_flush)
        d.link = link
        try:
            link.start()
            renderer.layout = link.handshake(cfg.fw_coord_base)
            log.info("connected %s layout=%sx%s guessed=%s", ports[0], renderer.layout.width,
                     renderer.layout.height, renderer.layout.guessed)
            if cfg.backlight is not None:
                from . import protocol as P
                link.call(P.set_backlight(cfg.backlight))
            ctl.presses.clear()  # never replay gestures across reconnects
            d.run(stop)
        except Exception as e:  # noqa: BLE001
            log.warning("link error: %s", e)
        finally:
            if stop.is_set():
                d.offline_frame("守护进程已退出")
            link.close()
        stop.wait(1)
    return 0


def cmd_shots(args) -> int:
    from .sim import shots
    made = shots(args.out, animations=not args.static)
    for n, cap in made:
        print(f"{os.path.join(args.out, n)}  {cap}")
    return 0


def cmd_serve(args) -> int:
    from .daemon import Daemon
    from .render.canvas import Renderer
    from .sim import demo_adapters, serve
    cfg = load_config(args.config)
    if args.source == "real":
        adapters = build_adapters(cfg)
        ctl = build_controller(cfg, adapters)
    else:
        ctl = Controller(cfg, demo_adapters(), sim=True)
    d = Daemon(ctl, Renderer(DeviceLayout.default(), animations=cfg.animations), None,
               cfg.poll_interval_s, cfg.heartbeat_s)
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


def cmd_launchd(args) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    tpl = os.path.join(here, "..", "packaging", "com.leo.mk20-agent-deck.plist.template")
    with open(tpl, encoding="utf-8") as f:
        s = f.read()
    s = s.replace("@PYTHON@", sys.executable).replace("@WORKDIR@", os.path.abspath(os.path.join(here, "..")))
    s = s.replace("@LOGDIR@", os.path.join(CONFIG_DIR, "logs"))
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
    s.set_defaults(fn=cmd_shots)
    w = sub.add_parser("sim-serve", help="interactive web simulator")
    w.add_argument("--source", choices=("demo", "real"), default="demo")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--http-port", type=int, default=8765)
    w.set_defaults(fn=cmd_serve)
    pr = sub.add_parser("probe-codex", help="read-only dump of Codex Desktop thread states")
    pr.add_argument("--json", action="store_true")
    pr.set_defaults(fn=cmd_probe)
    ld = sub.add_parser("launchd-plist", help="print the LaunchAgent plist for this install")
    ld.set_defaults(fn=cmd_launchd)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
