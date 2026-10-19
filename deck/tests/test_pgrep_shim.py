"""Portable checks for independent synthetic process lists in the shared pgrep shim."""
import os
import subprocess

import pytest
from conftest import SHIMS


@pytest.mark.parametrize("changed", ["kd", "kbd", "router", "out"])
def test_keyed_process_lists_are_independent(tmp_path, changed):
    (tmp_path / "pgrep.keyed").touch()
    patterns = {"kd": "^/data/KeyboardDevice", "kbd": "bin/mk20-kbd --run",
                "router": "bin/mk20-router --src", "out": "mk20-flapmon.sh"}
    pids = {"kd": "", "kbd": "456\n", "router": "123\n", "out": "789\n"}
    for name, value in pids.items():
        (tmp_path / f"pgrep.{name}").write_text(value)

    def check():
        for name, pattern in patterns.items():
            r = subprocess.run(["sh", str(SHIMS / "pgrep"), "-f", pattern],
                               env=dict(os.environ, MOCK_DIR=str(tmp_path)), capture_output=True, text=True)
            assert r.returncode == (0 if pids[name] else 1), (name, r.stderr)
            assert r.stdout == pids[name]

    check()
    pids[changed] = "999\n" if not pids[changed] else ""
    (tmp_path / f"pgrep.{changed}").write_text(pids[changed])
    check()
