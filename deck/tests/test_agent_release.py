"""Host-safe release flag contract: only config, argv and scoped descriptor fragments execute."""
import os
import subprocess
import pytest
from conftest import BB, DECK, scope_gadget_fn
from test_mk20d import conf, g1, rel, run_boot  # noqa: F401 (fixtures)
from test_mk20d_kbd import kb, stop_sup, wait  # noqa: F401 (fixture)

LIB = DECK / "firmware/release/share/mk20lib.sh"
GADGET = (DECK / "firmware/release/share/gadget.sh").read_text()


def resolved(tmp_path, config="", **env):
    conf = tmp_path / "mk20.conf"
    conf.write_text(config)
    script = f'. "{LIB}"; cfg_load; cfg_print; echo "exported=$MK20_HID_SCROLL"'
    r = subprocess.run(["sh", "-c", script], env=dict(PATH=os.environ["PATH"], MK20_ROOT=str(tmp_path),
                       MK20_CONF=str(conf), **env), text=True, capture_output=True, check=True)
    return dict(line.split("=", 1) for line in r.stdout.splitlines())


@pytest.mark.parametrize("feature,override", [("AGENT_CTL", "AGENT_CTL"), ("HID_SCROLL", "HID_SCROLL"), ("K2_ESTOP", "K2_ESTOP"), ("K2_RELEASE", "K2_RELEASE")])
def test_release_switches(tmp_path, feature, override):
    key = "FEATURE_" + feature
    assert resolved(tmp_path)[key] == "0"
    assert resolved(tmp_path, f"{key}=1\n")[key] == "1"
    assert resolved(tmp_path, f"{key}=yes\n")[key] == "0"
    for value in ("0", "1"):
        assert resolved(tmp_path, f"{key}={1-int(value)}\n", **{"MK20_"+override: value})[key] == value
    assert resolved(tmp_path, f"{key}=1\n", **{"MK20_"+override: "bad"})[key] == "1"


FEATURES = ("AGENT_CTL", "HID_SCROLL", "K2_ESTOP", "K2_RELEASE")


def descriptor_script(tmp_path):
    start = GADGET.index('if [ "${MK20_HID_SCROLL:-0}" = 1 ]; then')
    return scope_gadget_fn(GADGET[start:GADGET.index('\ncat /tmp/hid.desc', start)],
                           tmp_path, process_helpers=False)


@pytest.mark.skipif(BB is None or not os.path.isdir("/proc/self"), reason="boot harness requires Linux and BusyBox")
@pytest.mark.parametrize("configured,overrides,enabled", [
    ((), {}, ()),
    (("AGENT_CTL",), {}, ("AGENT_CTL",)),
    (("HID_SCROLL",), {}, ("HID_SCROLL",)),
    (("K2_ESTOP",), {}, ("K2_ESTOP",)),
    (("K2_RELEASE",), {}, ("K2_RELEASE",)),
    (FEATURES, {}, FEATURES),
    (FEATURES, {"MK20_" + k: "0" for k in FEATURES}, ()),
    ((), {"MK20_" + k: "1" for k in FEATURES}, FEATURES),
    (("HID_SCROLL",), {"MK20_HID_SCROLL": "bad"}, ("HID_SCROLL",)),
])
def test_boot_supervisor_flags_and_descriptor(kb, rel, tmp_path, configured, overrides, enabled):
    """Real boot -> new supervisor shell -> fake mk20-kbd argv, with config changed AFTER gadget setup."""
    dev = kb
    conf(dev, "FEATURE_KBD=1\n" + "".join(f"FEATURE_{k}=1\n" for k in configured))
    dev.env.update(overrides)
    gadget = rel / "share/gadget.sh"
    # Only the descriptor fragment executes; scope_gadget_fn covers every helper path.
    gadget.write_text(gadget.read_text() + descriptor_script(tmp_path) +
                      '\nprintf "FEATURE_KBD=1\\n' +
                      "".join(f"FEATURE_{k}={int(k not in enabled)}\\n" for k in FEATURES) +
                      '" > "$MK20_ROOT/data/mk20/config/mk20.conf"\n')
    assert not dev.p("run/mk20").exists()
    try:
        run_boot(dev, rel)
        assert wait(lambda: (dev.mock / "kbd.args").exists())
        args = (dev.mock / "kbd.args").read_text().splitlines()[0].split()
        root = str(dev.root)
        expected = ["--run", "--host", f"{root}/dev/ttyGS0", "--hid", f"{root}/dev/hidg0",
                    "--hid-stop-file", f"{root}/tmp/mk20-router-stop", "--status", f"{root}/run/mk20/kbd.status",
                    "--lock", f"{root}/tmp/mk20-kbd.lock", "--exit-file", f"{root}/tmp/mk20-kbd-stop"]
        if "AGENT_CTL" in enabled:
            expected += ["--ctl", f"{root}/run/mk20/kbd.sock"]
        expected += [flag for key, flag in (("HID_SCROLL", "--hid-scroll"), ("K2_ESTOP", "--k2-estop"),
                                           ("K2_RELEASE", "--k2-release")) if key in enabled]
        assert args == expected and "--ctl-allow" not in args
        assert dev.p("run/mk20").is_dir()  # supervisor creates the socket directory
        boot_env = dict(l.split("=", 1) for l in dev.read("tmp/mk20/gadget.boot.env").splitlines())
        saved_env = dict(l.split("=", 1) for l in dev.read("tmp/mk20/gadget.env").splitlines())
        scroll = "HID_SCROLL" in enabled
        assert boot_env["MK20_HID_SCROLL"] == saved_env["MK20_HID_SCROLL"] == str(int(scroll))
        desc = (tmp_path / "hid.desc").read_bytes()
        assert (b"\x09\x38\x15\x81" in desc) == ("--hid-scroll" in args) == scroll
        assert (b"\x05\x0c\x0a\x38\x02" in desc) == scroll
    finally:
        stop_sup(dev, rel)


def test_default_descriptor_bytes(tmp_path):
    # Golden stock descriptor: defaults must remain byte-identical.
    subprocess.run(["bash", "-c", descriptor_script(tmp_path)],
                   env=dict(PATH=os.environ["PATH"], MK20_HID_SCROLL="0"), check=True)
    assert (tmp_path / "hid.desc").read_bytes() == bytes.fromhex(
        "05010906a1018501050719e029e71500250175019508810295017508810395057501050819012905910295017503"
        "910395067508150025650507190029658100c005010902a10185020901a1000509190129031500250195037501"
        "810295017505810305010930093116000026ff7f751095028102c0c0")
