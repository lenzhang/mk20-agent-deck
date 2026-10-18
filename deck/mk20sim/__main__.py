import argparse
import sys

ap = argparse.ArgumentParser(prog="python -m mk20sim", description="MK20 key/menu interaction simulator (SIM)")
sub = ap.add_subparsers(dest="cmd")
sv = sub.add_parser("serve", help="web UI (default)")
for p in (ap, sv):
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8766)
rc = sub.add_parser("run-cases", help="run declarative cases headless")
rc.add_argument("--filter", default=None, help="substring of group/name or description")
rc.add_argument("--junit", default=None, help="write JUnit XML here")
rc.add_argument("--drafts", action="store_true", help="also run cases in cases/drafts/")
a = ap.parse_args()

if a.cmd == "run-cases":
    from .cases import filter_cases, junit_xml, load_cases, run_case

    cases = filter_cases(load_cases(include_drafts=a.drafts), a.filter)
    results = []
    for c in cases:
        r = run_case(c)
        results.append(r)
        print(f"{'PASS' if r.ok else 'FAIL'}  {c.id:<40} {r.duration_ms:7.1f} ms wall / {r.sim_ms:7.1f} ms sim")
        if not r.ok:
            print(f"      {r.failure or r.error}")
    if a.junit:
        with open(a.junit, "w", encoding="utf-8") as f:
            f.write(junit_xml(results))
    bad = sum(not r.ok for r in results)
    print(f"{len(results) - bad} passed, {bad} failed")
    sys.exit(1 if bad or not results else 0)
else:
    from .server import serve

    serve(a.host, a.port)
