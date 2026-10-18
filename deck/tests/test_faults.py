import pathlib, pytest
from conftest import FW, build_release, install_files

L = FW / "rescue/loader.sh"
CRASH = pathlib.Path(__file__).parent / "crashshims"


def invariant(dev):
    r = dev.run(L, "boot")
    if r.returncode == 11:
        return "stock"
    assert r.returncode == 0, r
    active = dev.read("tmp/mk20/release-active")
    good, pend = dev.read("data/mk20/state/good"), dev.read("data/mk20/state/pending")
    att = dev.read("data/mk20/state/attempts")
    assert active == good or (active == pend and att and int(att) >= 1), (active, good, pend, att)
    assert not dev.p(f"data/mk20/state/failed-{active}").exists() or active == pend
    return active


def base(dev):
    for v in ("1.0.0", "1.0.1"):
        install_files(dev, build_release(dev.tmp, v), v)
    assert dev.run(L, "switch", "1.0.0").returncode == 0
    assert dev.run(L, "boot").returncode == 0
    assert dev.run(L, "confirm", "1.0.0").returncode == 0


STEPS = [("switch", "1.0.1"), ("boot",), ("boot",), ("confirm", "1.0.1"), ("rollback",)]


@pytest.mark.parametrize("step", range(len(STEPS)))
@pytest.mark.parametrize("n", range(1, 9))
def test_power_cut_anywhere(dev, step, n):
    base(dev)
    for i, s in enumerate(STEPS):
        env = None
        if i == step:
            env = {"MOCK_CRASH_AT": str(n), "PATH": f"{CRASH}:{dev.env['PATH']}"}
            (dev.mock / "crash.n").unlink(missing_ok=True)
        dev.run(L, *s, env=env)
        if i == step:
            crashed = (dev.mock / "crashed").exists()
            break
    calls = int((dev.mock / "crash.n").read_text()) if (dev.mock / "crash.n").exists() else 0
    assert crashed or calls < n, "fault point reached but no crash"
    # after the cut: every subsequent boot honours the invariant, and a second boot too
    invariant(dev)
    invariant(dev)


def test_fault_injection_really_fires(dev):
    base(dev)
    dev.run(L, "switch", "1.0.1", env={"MOCK_CRASH_AT": "2", "PATH": f"{CRASH}:{dev.env['PATH']}"})
    assert (dev.mock / "crashed").exists()
