"""M2: mk20d config whitelist, structured log rotation, vendor/kernel gates, health JSON, boot env contract."""
import json, shutil, pathlib, os, stat
import pytest
from conftest import FW, P4, sha, BB

REL = FW / "release"


@pytest.fixture
def rel(dev, tmp_path):
    """Copy of the real release tree with a recording gadget.sh / add-adb so boot can run in a fake root."""
    d = tmp_path / "rel"
    shutil.copytree(REL, d)
    (d / "MANIFEST").write_text("name=mk20-fw\nversion=9.9.9\n--\n")
    (d / "share/gadget.sh").write_text('env | grep "^MK20_" | sort > "$MK20_ROOT/tmp/mk20/gadget.env"; echo "$*" > "$MK20_ROOT/tmp/mk20/gadget.args"\n')
    (d / "share/mk20-add-adb.sh").write_text('echo addadb >> "$MK20_ROOT/tmp/mk20/addadb.ran"\n')
    vend = []
    for name in ("KeyboardDevice", "appLunch.sh", "setusbconfig", "qt_env.sh"):
        f = dev.p(f"data/{name}")
        f.write_text(f"vendor {name}\n")
        vend.append(f"{sha(f)}  /data/{name}")
    (d / "share/vendor.sha256").write_text("\n".join(vend) + "\n")
    k = dev.p("data/mk20/kernel"); k.mkdir(parents=True)
    (k / "ALLOWLIST").write_text(f"{P4} ep5out acm,adb,ecm,hid\n")
    (dev.mock / "wdt").write_text("running")
    dev.mounts = dev.tmp / "mounts"; dev.mounts.write_text("")
    dev.env.update({"MK20_REL": str(d), "MK20_VERSION": "9.9.9", "MK20_MOUNTS": str(dev.mounts),
                    "MK20_SETTLE": "0", "MK20_STABLE": "0", "MK20_SELFTEST_GAP": "0", "MK20_MAINT": "0", "MK20_ADB_KEEPALIVE": "0"})
    return d


def conf(dev, text):
    c = dev.p("data/mk20/config"); c.mkdir(parents=True, exist_ok=True)
    (c / "mk20.conf").write_text(text)


def cfg(dev, rel):
    r = dev.run(rel / "sbin/mk20d", "config")
    assert r.returncode == 0, r.stderr
    return dict(l.split("=", 1) for l in r.stdout.splitlines())


def test_defaults_unchanged(dev, rel):
    c = cfg(dev, rel)
    assert c["CFG_SRC"] == "defaults" and c["ECM_IP"] == "10.20.0.2" and c["ECM_HOST_IP"] == "10.20.0.1"
    assert c["BRIDGE_PORT"] == "17890" and c["FEATURE_COMPOSITE"] == "1" and "WARNINGS" not in c


def test_valid_config(dev, rel):
    conf(dev, "# comment\r\nECM_SUBNET=172.30.250.0/24\r\nBRIDGE_PORT = 17999  # trailing\nFEATURE_HID=0\nECM_DEV_MAC=0a:00:00:00:00:01\nLOG_KEEP=5\n")
    c = cfg(dev, rel)
    assert c["ECM_IP"] == "172.30.250.2" and c["ECM_HOST_IP"] == "172.30.250.1"
    assert c["BRIDGE_PORT"] == "17999" and c["FEATURE_HID"] == "0" and c["ECM_DEV_MAC"] == "0a:00:00:00:00:01"
    assert c["LOG_KEEP"] == "5" and "WARNINGS" not in c


@pytest.mark.parametrize("line,key", [
    ("ECM_SUBNET=169.254.7.0/24", "ECM_SUBNET"), ("ECM_SUBNET=127.0.0.0/24", "ECM_SUBNET"),
    ("ECM_SUBNET=10.20.0.1/24", "ECM_SUBNET"), ("ECM_SUBNET=10.20.0.0/16", "ECM_SUBNET"),
    ("ECM_SUBNET=10.256.0.0/24", "ECM_SUBNET"), ("ECM_SUBNET=230.1.1.0/24", "ECM_SUBNET"),
    ("ECM_SUBNET=010.20.0.0/24", "ECM_SUBNET"), ("ECM_SUBNET=$(reboot)", "ECM_SUBNET"),
    ("ECM_DEV_MAC=01:11:22:33:44:55", "ECM_DEV_MAC"), ("ECM_DEV_MAC=00:11:22:33:44:55", "ECM_DEV_MAC"),
    ("ECM_DEV_MAC=02:11:22:33:44", "ECM_DEV_MAC"), ("BRIDGE_PORT=80", "BRIDGE_PORT"),
    ("BRIDGE_PORT=1e4", "BRIDGE_PORT"), ("FEATURE_ADB=yes", "FEATURE_ADB"), ("FLAP_DROPS=1", "FLAP_DROPS"),
    ("LOG_MAX_KB=999999999999", "LOG_MAX_KB"), ("BRIDGE_PORT=`id`", "BRIDGE_PORT"),
])
def test_invalid_values_keep_defaults(dev, rel, line, key):
    conf(dev, line + "\n")
    c = cfg(dev, rel)
    assert c["ECM_IP"] == "10.20.0.2" and c["BRIDGE_PORT"] == "17890" and c["ECM_DEV_MAC"] == "02:11:22:33:44:55"
    assert c["FEATURE_ADB"] == "1" and c["FLAP_DROPS"] == "5" and c["LOG_MAX_KB"] == "256"
    assert f"invalid {key}" in c["WARNINGS"]


def test_unknown_and_injection_keys_ignored(dev, rel):
    marker = dev.tmp / "pwned"
    conf(dev, f"PATH=/evil\nFOO=1\nx;touch {marker}=1\nnoequals\nlower=1\n")
    c = cfg(dev, rel)
    assert "unknown key PATH" in c["WARNINGS"] and "bad key" in c["WARNINGS"] and "bad line" in c["WARNINGS"]
    assert not marker.exists()


def test_oversize_config_ignored(dev, rel):
    conf(dev, "ECM_SUBNET=172.30.250.0/24\n" + "#" * 5000 + "\n")
    c = cfg(dev, rel)
    assert c["ECM_IP"] == "10.20.0.2" and "too large" in c["WARNINGS"]


def test_key_internal_whitespace_rejected(dev, rel):
    conf(dev, "FEATURE_ ADB=0\n  FEATURE_HID = 0 \n")
    c = cfg(dev, rel)
    assert c["FEATURE_ADB"] == "1" and c["FEATURE_HID"] == "0" and "bad key" in c["WARNINGS"]


def test_warnings_reset_each_load(dev, rel):
    lib = rel / "share/mk20lib.sh"
    conf(dev, "FOO=1\n")
    s = dev.tmp / "twice.sh"
    s.write_text(f". {lib}\ncfg_load; cfg_load; echo \"$CFG_WARN\"\n")
    out = dev.run(s).stdout.strip()
    assert out.count("unknown key FOO") == 1


def test_env_default_subnet_validated(dev, rel):
    r = dev.run(rel / "sbin/mk20d", "config", env={"MK20_DEFAULT_SUBNET": "1.2.3.4;reboot"})
    assert "ECM_IP=10.20.0.2" in r.stdout
    r = dev.run(rel / "sbin/mk20d", "config", env={"MK20_DEFAULT_SUBNET": "172.30.250.0/24"})
    assert "ECM_IP=172.30.250.2" in r.stdout


def test_flags_override_config(dev, rel):
    conf(dev, "FEATURE_COMPOSITE=1\nFEATURE_ADB=1\nFEATURE_HID=1\n")
    for f in ("mk20-no-composite", "mk20-no-adb", "mk20-gadget-nohid"):
        dev.p(f"mnt/SDCARD/{f}").write_text("")
    c = cfg(dev, rel)
    assert c["FEATURE_COMPOSITE"] == c["FEATURE_ADB"] == c["FEATURE_HID"] == "0"


def test_equal_macs_rejected(dev, rel):
    conf(dev, "ECM_DEV_MAC=02:00:00:00:00:01\nECM_HOST_MAC=02:00:00:00:00:01\n")
    c = cfg(dev, rel)
    assert c["ECM_DEV_MAC"] != c["ECM_HOST_MAC"] and "MAC equal" in c["WARNINGS"]


def test_log_rotation(dev, rel):
    lib = rel / "share/mk20lib.sh"
    script = dev.tmp / "logt.sh"
    script.write_text(f'. {lib}\nLOG_MAX_KB=1; LOG_KEEP=2\ni=0; while [ $i -lt 200 ]; do mk20log INFO test ev "n=$i padding=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"; i=$((i+1)); done\n')
    r = dev.run(script)
    assert r.returncode == 0, r.stderr
    logs = dev.p("data/mk20/logs")
    names = sorted(p.name for p in logs.iterdir())
    assert names == ["mk20d.log", "mk20d.log.1", "mk20d.log.2"]
    for p in logs.iterdir():
        assert p.stat().st_size <= 1024 + 200
    last = (logs / "mk20d.log").read_text().splitlines()[-1].split()
    assert last[2:5] == ["INFO", "test", "ev"] and last[5] == "n=199" and last[0].isdigit()


def run_boot(dev, rel):
    r = dev.run(rel / "sbin/mk20d", "boot", env={"PATH": dev.env["PATH"]}, timeout=120)
    return r, dev.p("data/mk20/logs/mk20d.log").read_text()


@pytest.fixture
def g1(dev):
    """Fake configfs/devices present in MK20_ROOT for boot wait + selftest."""
    g = dev.p("sys/kernel/config/usb_gadget/g1"); (g / "configs/c.1/acm.usb0").mkdir(parents=True)
    (g / "UDC").write_text("musb-hdrc.1.auto\n")
    dev.p("dev/ttyGS0").write_text("")
    (dev.mock / "pgrep.out").write_text("123\n")
    return g


def test_boot_composite_env_contract(dev, rel, g1):
    conf(dev, "ECM_SUBNET=172.30.250.0/24\nBRIDGE_PORT=17999\nFEATURE_ADB=0\n")
    r, log = run_boot(dev, rel)
    env = dict(l.split("=", 1) for l in dev.read("tmp/mk20/gadget.env").splitlines())
    assert dev.read("tmp/mk20/gadget.args") == "--boot"
    assert env["MK20_ECM_IP"] == "172.30.250.2" and env["MK20_BRIDGE_PORT"] == "17999"
    assert env["MK20_NOADB"] == "1" and env["MK20_NOHID"] == "0" and env["MK20_REL"] == str(rel)
    assert env["MK20_GADGET_LOG"].endswith("data/mk20/logs/gadget.log")
    assert "INFO mk20d boot" in log and "subnet=172.30.250.0/24" in log
    h = json.loads(dev.read("data/mk20/state/selftest-last.json"))
    assert h["version"] == "9.9.9" and h["kernel"]["name"] == "ep5out" and h["vendor"] == "ok"


def test_boot_vendor_mismatch_fails_closed(dev, rel, g1):
    dev.p("data/setusbconfig").write_text("tampered\n")
    r, log = run_boot(dev, rel)
    assert not dev.p("tmp/mk20/gadget.env").exists()
    assert dev.read("tmp/mk20/addadb.ran") == "addadb"
    assert "vendor mismatch:/data/setusbconfig" in log and "stock ACM+KD+adb" in log


def test_boot_vendor_bindmount_skipped(dev, rel, g1):
    dev.p("data/setusbconfig").write_text("our shim bind-mounted\n")
    dev.mounts.write_text("tmpfs /data/setusbconfig tmpfs rw 0 0\n")
    run_boot(dev, rel)
    assert dev.p("tmp/mk20/gadget.env").exists()


@pytest.mark.parametrize("line", ["/dev/root /data/setusbconfig ext4 rw 0 0", "tmpfs /data/setusbconfig.x tmpfs rw 0 0",
                                  "tmpfs /x/data/setusbconfig tmpfs rw 0 0"])
def test_boot_vendor_foreign_mount_not_skipped(dev, rel, g1, line):
    dev.p("data/setusbconfig").write_text("tampered\n")
    dev.mounts.write_text(line + "\n")
    r, log = run_boot(dev, rel)
    assert not dev.p("tmp/mk20/gadget.env").exists() and "vendor mismatch" in log


def test_boot_kernel_without_ecm_or_unknown(dev, rel, g1):
    dev.p("data/mk20/kernel/ALLOWLIST").write_text(f"{P4} stock acm,adb\n")
    r, log = run_boot(dev, rel)
    assert not dev.p("tmp/mk20/gadget.env").exists() and "lacks ecm" in log


@pytest.mark.parametrize("allow", ["", "deadbeef stock acm,adb\n", f"{P4}\n"])
def test_boot_unknown_kernel_no_usb_changes(dev, rel, g1, allow):
    dev.p("data/mk20/kernel/ALLOWLIST").write_text(allow)
    r, log = run_boot(dev, rel)
    assert not dev.p("tmp/mk20/gadget.env").exists() and not dev.p("tmp/mk20/addadb.ran").exists()
    assert "no USB changes" in log


def test_boot_kernel_without_hid(dev, rel, g1):
    dev.p("data/mk20/kernel/ALLOWLIST").write_text(f"{P4} ep3 acm,adb,ecm\n")
    run_boot(dev, rel)
    env = dict(l.split("=", 1) for l in dev.read("tmp/mk20/gadget.env").splitlines())
    assert env["MK20_NOHID"] == "1"


def test_boot_no_composite_flag_no_adb(dev, rel, g1):
    dev.p("mnt/SDCARD/mk20-no-composite").write_text("")
    dev.p("mnt/SDCARD/mk20-no-adb").write_text("")
    r, log = run_boot(dev, rel)
    assert not dev.p("tmp/mk20/gadget.env").exists() and not dev.p("tmp/mk20/addadb.ran").exists()


def test_boot_confirms_on_pass_not_on_fail(dev, rel, g1):
    ld = dev.p("tmp/mk20/rescue-loader.sh")
    ld.write_text('echo "$*" >> "$MK20_ROOT/tmp/mk20/loader.calls"\n')
    dev.p("mnt/SDCARD/mk20-no-composite").write_text("")
    run_boot(dev, rel)
    assert dev.read("tmp/mk20/loader.calls") == "confirm 9.9.9"
    ld.write_text('echo "$*" >> "$MK20_ROOT/tmp/mk20/loader.calls2"\n')
    (dev.mock / "wdt").write_text("offline")
    r, log = run_boot(dev, rel)
    assert not dev.p("tmp/mk20/loader.calls2").exists() and "ERROR mk20d selftest" in log


def test_selftest_and_health_json(dev, rel, g1):
    r = dev.run(rel / "sbin/mk20d", "selftest")
    assert r.returncode == 0, r.stdout
    assert "PASS kd=1" in r.stdout and "WARN composite down" in r.stdout
    (dev.mock / "pgrep.out").write_text("1\n2\n")
    r = dev.run(rel / "sbin/mk20d", "selftest")
    assert r.returncode == 1 and "FAIL kd=2" in r.stdout
    h = json.loads(dev.run(rel / "sbin/mk20d", "health").stdout)
    assert h["selftest"]["pass"] is False and h["kd"] == 2 and h["watchdog"] is True
    assert h["maint"] is False   # no pid file (BusyBox 1.27 `kill -0 ""` succeeds: regression)
    assert h["usb"]["udc"] == "musb-hdrc.1.auto" and h["net"]["subnet"] == "10.20.0.0/24"
    assert json.loads(dev.read("tmp/mk20/health.json")) == h


def test_selftest_composite_requires_bridge_and_hid(dev, rel, g1):
    (g1 / "configs/c.1/ecm.usb0").mkdir(); (g1 / "configs/c.1/hid.usb0").mkdir()
    r = dev.run(rel / "sbin/mk20d", "selftest")
    assert r.returncode == 1 and "FAIL no hidg0" in r.stdout and "FAIL bridge not on 10.20.0.2:17890" in r.stdout


def test_health_json_escapes_hostile_config(dev, rel, g1):
    conf(dev, 'FOO"\\=1\nBAR\x01\x1b=2\n')
    h = json.loads(dev.run(rel / "sbin/mk20d", "health").stdout)
    assert "bad key" in h["config"]["warnings"]


def test_maint_rotates_and_trims(dev, rel, g1):
    logs = dev.p("data/mk20/logs"); logs.mkdir(parents=True)
    conf(dev, "LOG_MAX_KB=16\nLOG_KEEP=2\n")
    (logs / "gadget.log").write_text("g" * 20000)
    b = dev.p("tmp/mk20/bridge.log"); b.write_text("b" * 300000)
    import subprocess, time
    e = dict(dev.env); e["MK20_MAINT_S"] = "1"
    p = subprocess.Popen([BB, "sh", str(rel / "sbin/mk20d"), "maint"], env=e)
    try:
        for _ in range(100):
            if (logs / "gadget.log.1").exists() and dev.p("tmp/mk20/health.json").exists() and b.stat().st_size <= 65536:
                break
            time.sleep(0.1)
    finally:
        p.kill(); p.wait()
    assert (logs / "gadget.log.1").stat().st_size == 20000 and not (logs / "gadget.log").exists()
    assert b.stat().st_size == 65536
    assert json.loads(dev.read("tmp/mk20/health.json"))["maint"] is True


def test_maint_single_instance(dev, rel, g1):
    import subprocess, time
    e = dict(dev.env); e["MK20_MAINT_S"] = "30"
    p1 = subprocess.Popen([BB, "sh", str(rel / "sbin/mk20d"), "maint"], env=e)
    try:
        time.sleep(0.8)
        p2 = subprocess.run([BB, "sh", str(rel / "sbin/mk20d"), "maint"], env=e, timeout=10)
        assert p2.returncode == 0 and p1.poll() is None      # second instance exits at once
        h = json.loads(dev.run(rel / "sbin/mk20d", "health").stdout)
        assert h["maint"] is True
    finally:
        p1.kill(); p1.wait()
    h = json.loads(dev.run(rel / "sbin/mk20d", "health").stdout)
    assert h["maint"] is False


def test_stock_fallback_keeps_adb_despite_config(dev, rel, g1):
    conf(dev, "FEATURE_ADB=0\n")
    dev.p("mnt/SDCARD/mk20-no-composite").write_text("")
    run_boot(dev, rel)
    assert dev.read("tmp/mk20/addadb.ran") == "addadb"


def test_gadget_env_written_only_when_gates_pass(dev, rel, g1):
    conf(dev, "BRIDGE_PORT=17999\n")
    run_boot(dev, rel)
    env = dict(l.split("=", 1) for l in dev.read("tmp/mk20/gadget.env").splitlines())
    assert env["MK20_BRIDGE_PORT"] == "17999" and env["MK20_ECM_IP"] == "10.20.0.2"
    dev.p("data/setusbconfig").write_text("tampered\n")
    run_boot(dev, rel)
    assert not dev.p("tmp/mk20/gadget.env").exists()


def test_selftest_waits_for_gadget_failsafe(dev, rel, g1):
    dev.p("tmp/mk20/gadget.busy").write_text("")
    r = dev.run(rel / "sbin/mk20d", "boot", env={"MK20_BUSY_CAP": "5"}, timeout=60)
    log = dev.read("data/mk20/logs/mk20d.log")
    assert "waited 5s for gadget failsafe" in log


def test_shim_mounted_with_composite_down_fails(dev, rel, g1):
    dev.mounts.write_text("tmpfs /data/setusbconfig tmpfs rw 0 0\n")
    r = dev.run(rel / "sbin/mk20d", "selftest")
    assert r.returncode == 1 and "FAIL shim still mounted on /data/setusbconfig" in r.stdout


def test_symlinked_config_ignored(dev, rel, tmp_path):
    t = tmp_path / "real.conf"; t.write_text("ECM_SUBNET=172.30.250.0/24\n")
    c = dev.p("data/mk20/config"); c.mkdir(parents=True)
    (c / "mk20.conf").symlink_to(t)
    cf = cfg(dev, rel)
    assert cf["ECM_IP"] == "10.20.0.2" and "not a regular file" in cf["WARNINGS"]


def test_vendor_list_incomplete_fails_closed(dev, rel, g1):
    lines = (rel / "share/vendor.sha256").read_text().splitlines()
    (rel / "share/vendor.sha256").write_text(lines[0] + "\n")
    r, log = run_boot(dev, rel)
    assert not dev.p("tmp/mk20/gadget.env").exists() and "vendor list incomplete" in log


def test_vendor_last_line_without_newline_checked(dev, rel, g1):
    v = rel / "share/vendor.sha256"; v.write_text(v.read_text().rstrip("\n"))
    dev.p("data/qt_env.sh").write_text("tampered\n")       # the last entry
    r, log = run_boot(dev, rel)
    assert "vendor mismatch:/data/qt_env.sh" in log


def test_health_status_field(dev, rel, g1):
    h = json.loads(dev.run(rel / "sbin/mk20d", "health").stdout)
    assert h["status"] == "degraded"          # composite down = degraded
    (dev.mock / "wdt").write_text("offline")
    h = json.loads(dev.run(rel / "sbin/mk20d", "health").stdout)
    assert h["status"] == "fail"


def test_kd_count_pattern_matches_device_cmdline():
    """1.0.3 field failure: `pgrep -x KeyboardDevice` never matches on BusyBox 1.27 (cmdline /data/KeyboardDevice)."""
    import re
    src = (REL / "sbin/mk20d").read_text()
    line = next(l for l in src.splitlines() if l.startswith("kd_count()"))
    assert "pgrep -f '^/data/KeyboardDevice'" in line and "pgrep -x" not in line.split("#")[0]
    assert re.search(r"^/data/KeyboardDevice", "/data/KeyboardDevice") and not re.search(r"^/data/KeyboardDevice", "sha256sum /data/KeyboardDevice")
