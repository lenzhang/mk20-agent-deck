import argparse

from .server import serve

ap = argparse.ArgumentParser(prog="python -m mk20sim", description="MK20 key/menu interaction simulator (SIM)")
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--port", type=int, default=8766)
a = ap.parse_args()
serve(a.host, a.port)
