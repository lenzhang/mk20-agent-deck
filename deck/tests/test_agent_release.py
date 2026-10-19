"""Host-safe release flag contract: only config, argv and scoped descriptor fragments execute."""
import os
from pathlib import Path
import subprocess
import pytest
from conftest import DECK, scope_gadget_fn

LIB = DECK / "firmware/release/share/mk20lib.sh"
DAEMON = (DECK / "firmware/release/sbin/mk20d").read_text()
GADGET = (DECK / "firmware/release/share/gadget.sh").read_text()


def resolved(tmp_path, config="", **env):
    conf = tmp_path / "mk20.conf"
    conf.write_text(config)
    script = f'. "{LIB}"; cfg_load; cfg_print; echo "exported=$MK20_HID_SCROLL"'
    r = subprocess.run(["sh", "-c", script], env=dict(PATH=os.environ["PATH"], MK20_ROOT=str(tmp_path),
                       MK20_CONF=str(conf), **env), text=True, capture_output=True, check=True)
    return dict(line.split("=", 1) for line in r.stdout.splitlines())


@pytest.mark.parametrize("feature,override", [("AGENT_CTL", "AGENT_CTL"), ("HID_SCROLL", "HID_SCROLL"), ("K2_ESTOP", "K2_ESTOP")])
def test_release_switches(tmp_path, feature, override):
    key = "FEATURE_" + feature
    assert resolved(tmp_path)[key] == "0"
    assert resolved(tmp_path, f"{key}=1\n")[key] == "1"
    assert resolved(tmp_path, f"{key}=yes\n")[key] == "0"
    for value in ("0", "1"):
        assert resolved(tmp_path, f"{key}={1-int(value)}\n", **{"MK20_"+override: value})[key] == value
    assert resolved(tmp_path, f"{key}=1\n", **{"MK20_"+override: "bad"})[key] == "1"


def argv(ctl, scroll, root, k2="0"):
    assert DAEMON.count("      set --\n") == 1
    body = DAEMON[DAEMON.index('      set --\n'):DAEMON.index('      t0=$(cut -d.', DAEMON.index('      set --\n'))]
    r = subprocess.run(["sh", "-c", body + '\nprintf "%s\\n" "$@"'], capture_output=True, text=True, check=True,
                       env=dict(PATH=os.environ["PATH"], FEATURE_AGENT_CTL=ctl, FEATURE_HID_SCROLL=scroll, FEATURE_K2_ESTOP=k2, R=str(root)))
    return r.stdout.split()


def test_default_launch_unchanged(tmp_path):
    assert argv("0", "0", tmp_path) == []
    launch = next(l for l in DAEMON.splitlines() if '"$KB" --run' in l)
    assert launch.replace(' "$@"', '') == ('      "$KB" --run --host "$R/dev/ttyGS0" $HA --status "$KST" '
        '--lock "$R/tmp/mk20-kbd.lock" --exit-file "$KSTOP" </dev/null >>"$KL" 2>&1 5>&-; rc=$?')
    assert "--ctl-allow" not in launch
    assert argv("0", "0", tmp_path, "1") == ["--k2-estop"]
    assert argv("1", "1", tmp_path) == ["--ctl", str(tmp_path / "run/mk20/kbd.sock"), "--hid-scroll"]


@pytest.mark.parametrize("conf,override,want", [("", None, "0"), ("1", None, "1"), ("1", "0", "0"),
                                               ("0", "1", "1"), ("1", "bad", "1")])
def test_descriptor_and_launch_cannot_disagree(tmp_path, conf, override, want):
    env = {} if override is None else {"MK20_HID_SCROLL": override}
    c = resolved(tmp_path, f"FEATURE_HID_SCROLL={conf}\n" if conf else "", **env)
    assert c["FEATURE_HID_SCROLL"] == c["exported"] == want
    assert ("--hid-scroll" in argv("0", c["FEATURE_HID_SCROLL"], tmp_path)) == (want == "1")
    start = GADGET.index('if [ "${MK20_HID_SCROLL:-0}" = 1 ]; then')
    body = scope_gadget_fn(GADGET[start:GADGET.index('\ncat /tmp/hid.desc', start)], tmp_path, process_helpers=False)
    # bash printf decodes the descriptor's existing \\x escapes, as BusyBox ash does.
    subprocess.run(["bash", "-c", body], env=dict(PATH=os.environ["PATH"], MK20_HID_SCROLL=c["exported"]), check=True)
    desc = (tmp_path / "hid.desc").read_bytes()
    assert (b"\x09\x38\x15\x81" in desc) == (want == "1")
    assert (b"\x05\x0c\x0a\x38\x02" in desc) == (want == "1")
    assert 'MK20_HID_SCROLL=%s\\n' in DAEMON and '"$FEATURE_HID_SCROLL" > "$TM/gadget.env.tmp"' in DAEMON
