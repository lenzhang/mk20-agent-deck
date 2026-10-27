"""1.8.0: CDC-NCM network function (macOS 15 Intel: the AppleUserECM dext asserts, no enX for ECM) with ECM fallback.

Covers the real gadget.sh function-selection block (run under BusyBox with a stubbed insmod / modules list), the
re-arm with an NCM composite (real rollback/rearm block via test_ecm_watchdog's harness), mk20d's knob plumbing
(cfg, gadget.env, boot env, health), and the f_ncm patch's size arithmetic against the MTU gadget.sh sets."""
import json, os, re, subprocess
import pytest
from conftest import BB, FW, DECK
from test_mk20d import rel, g1, conf, cfg, run_boot  # noqa: F401  (fixtures)
from test_ecm_watchdog import Rearm, links, no_hid_no_adb, UDC  # noqa: F401

GS = (FW / "release/share/gadget.sh").read_text()
MK20D = (FW / "release/sbin/mk20d").read_text()
PATCH = (DECK / "kernel/stock-patch/f_ncm-mk20.patch").read_text()
KO = FW / "release/lib/usb_f_ncm.ko"

# ---- setup_main's network-function block, run for real ----
FN_BLOCK = GS[GS.index("# ---- functions ----"):GS.index('> $C/strings/0x409/configuration 2>/dev/null') +
              len('> $C/strings/0x409/configuration 2>/dev/null')]
USE_NCM_LINE = re.search(r"^USE_NCM=.*$", GS, re.M).group(0)   # the knob + flag file, as gadget.sh reads them
HELPERS = "\n".join(l for l in GS.splitlines() if l.startswith(("net_fn() {", "fn_mtu() {")))
FN_STUBS = r'''log() { echo "$*" >> $T/log; }
die() { echo "DIE $*" >> $T/calls; exit 1; }
insmod() { echo "insmod ${1##*/}" >> $T/calls
  case "$1" in *usb_f_ncm.ko) [ -f $T/ncm.refused ] && return 1;; esac
  [ -f "$1" ] || return 1; m=${1##*/}; echo "${m%.ko} 16384 0 - Live 0xbf000000" >> $T/modules; return 0; }
# netdev model: a function instance registers the lowest free usbN at mkdir and frees it at rmdir ($T/net/usbN = owner)
mkdir() { command mkdir "$@" || return 1; for a; do case "$a" in */functions/*.usb0)
  grep -qx "${a##*/}" $T/net/* 2>/dev/null && continue
  n=0; while [ -e $T/net/usb$n ]; do n=$((n+1)); done; echo "${a##*/}" > $T/net/usb$n;; esac; done; }
rmdir() { command rmdir "$@" || return 1; for a; do case "$a" in */functions/*.usb0)
  for nd in $T/net/usb*; do [ -f "$nd" ] && [ "$(cat "$nd")" = "${a##*/}" ] && rm -f "$nd"; done;; esac; done; return 0; }
'''


class Fn:
    def __init__(self, dev, t):
        self.dev, self.t = dev, t
        for d in ("g/functions", "g/configs/c.1/strings/0x409", "lib", "sd", "net"):
            (t / d).mkdir(parents=True, exist_ok=True)
        (t / "lib/usb_f_ncm.ko").write_text("ncm"); (t / "lib/usb_f_ecm.ko").write_text("ecm")
        (t / "modules").write_text(""); (t / "calls").write_text(""); (t / "log").write_text("")
        body = (USE_NCM_LINE + "\n" + FN_BLOCK).replace("/proc/modules", f"{t}/modules").replace("/mnt/SDCARD", f"{t}/sd")
        assert "/proc/" not in body and "/mnt/" not in body and "/sys/" not in body
        (t / "fn.sh").write_text(f'''set -u
T={t}; G=$T/g; C=$G/configs/c.1; LOG=$T/gadget.log; NCMKO=$T/lib/usb_f_ncm.ko; ECMKO=$T/lib/usb_f_ecm.ko
DEV_MAC=02:11:22:33:44:55; HOST_MAC=02:11:22:33:44:56; NOADB=1; MK20_NOHID=1
{FN_STUBS}
{HELPERS}
{body}
echo "NETFN=$NETFN NETMTU=$NETMTU net_fn=$(net_fn)"
''')

    def run(self, **env):
        return subprocess.run([BB, "sh", str(self.t / "fn.sh")], env=dict(self.dev.env, **env), capture_output=True,
                              text=True, timeout=30)

    def calls(self):
        return (self.t / "calls").read_text().splitlines()

    def log(self):
        return (self.t / "log").read_text()

    def links(self):
        return sorted(p for p in os.listdir(self.t / "g/configs/c.1") if p != "strings")

    def cfgname(self):
        return (self.t / "g/configs/c.1/strings/0x409/configuration").read_text().strip()

    def usb0(self):
        """Which function instance owns the usb0 netdev (gadget.sh configures usb0 by name)."""
        p = self.t / "net/usb0"
        return p.read_text().strip() if p.exists() else None

    def stale(self, f):
        """A leftover instance from an earlier composite: its netdev is registered (usb0 on a single-function box)."""
        self.fdir(f).mkdir(); (self.t / "net/usb0").write_text(f + "\n")

    def fdir(self, f):
        return self.t / "g/functions" / f


@pytest.fixture
def fn(dev, tmp_path):
    return Fn(dev, tmp_path / "fn")


def test_ncm_is_the_default_function(fn):
    r = fn.run()
    assert r.returncode == 0, (r.stderr, fn.calls())
    assert r.stdout.split() == ["NETFN=ncm", "NETMTU=462", "net_fn=ncm"]
    assert fn.calls() == ["insmod usb_f_ncm.ko"]                        # ECM module never loaded
    assert fn.links() == ["ncm.usb0"] and fn.cfgname() == "NCM+HID+ADB" and fn.usb0() == "ncm.usb0"
    assert os.path.islink(fn.t / "g/configs/c.1/ncm.usb0") and not fn.fdir("ecm.usb0").exists()
    # the MACs keep their 1.1.0 semantics: device .55, host (the Mac's interface) .56
    assert (fn.fdir("ncm.usb0") / "dev_addr").read_text().strip() == "02:11:22:33:44:55"
    assert (fn.fdir("ncm.usb0") / "host_addr").read_text().strip() == "02:11:22:33:44:56"
    assert "WARN" not in fn.log()


def test_ncm_module_already_loaded_is_not_reloaded(fn):
    (fn.t / "modules").write_text("usb_f_ncm 16384 0 - Live 0xbf000000\n")
    r = fn.run()
    assert r.returncode == 0 and r.stdout.split()[0] == "NETFN=ncm" and fn.calls() == []


@pytest.mark.parametrize("why", ["missing", "refused", "mkdir"])
def test_ncm_failure_falls_back_to_ecm(fn, why):
    if why == "missing":
        (fn.t / "lib/usb_f_ncm.ko").unlink()
    elif why == "refused":
        (fn.t / "ncm.refused").touch()                                  # e.g. vermagic/unknown symbol
    else:
        fn.fdir("ncm.usb0").write_text("")                              # mkdir -p fails (not a directory)
    r = fn.run()
    assert r.returncode == 0, (r.stderr, fn.calls())
    assert r.stdout.split() == ["NETFN=ecm", "NETMTU=478", "net_fn=ecm"]
    assert "insmod usb_f_ecm.ko" in fn.calls() and fn.links() == ["ecm.usb0"] and fn.cfgname() == "ECM+HID+ADB"
    assert fn.usb0() == "ecm.usb0"
    assert "WARN NCM unavailable" in fn.log() and "-> ECM fallback" in fn.log()
    assert (fn.fdir("ecm.usb0") / "host_addr").read_text().strip() == "02:11:22:33:44:56"


@pytest.mark.parametrize("how", ["knob", "flag"])
def test_ncm_disabled_builds_the_ecm_composite_without_touching_ncm(fn, how):
    env = {"MK20_NCM": "0"} if how == "knob" else {}
    if how == "flag":   # the real flag-file line from the top of gadget.sh runs in the harness
        (fn.t / "sd/mk20-no-ncm").touch()
    r = fn.run(**env)
    assert r.returncode == 0 and r.stdout.split()[0] == "NETFN=ecm"
    assert fn.calls() == ["insmod usb_f_ecm.ko"] and "WARN" not in fn.log() and not fn.fdir("ncm.usb0").exists()
    assert fn.usb0() == "ecm.usb0"


@pytest.mark.parametrize("stale,netfn", [("ecm.usb0", "ncm"), ("ncm.usb0", "ecm")])
def test_the_other_functions_stale_instance_is_removed(fn, stale, netfn):
    """A function instance left from an earlier composite (live switch NCM<->ECM) owns usb0 until rmdir: it must be
    removed BEFORE the chosen instance is created, else the new netdev registers as usb1 and usb0 disappears."""
    fn.stale(stale)
    r = fn.run(MK20_NCM="1" if netfn == "ncm" else "0")
    assert r.returncode == 0 and r.stdout.split()[0] == f"NETFN={netfn}"
    assert not fn.fdir(stale).exists() and fn.links() == [f"{netfn}.usb0"]
    assert fn.usb0() == f"{netfn}.usb0" and sorted(os.listdir(fn.t / "net")) == ["usb0"]


def test_ncm_fallback_after_removing_a_stale_ecm_instance_still_gets_usb0(fn):
    fn.stale("ecm.usb0"); (fn.t / "ncm.refused").touch()
    r = fn.run()
    assert r.returncode == 0 and r.stdout.split()[0] == "NETFN=ecm" and fn.usb0() == "ecm.usb0"
    assert fn.links() == ["ecm.usb0"] and "-> ECM fallback" in fn.log()


def test_a_stale_instance_that_cannot_be_removed_fails_setup(fn):
    fn.fdir("ecm.usb0").mkdir(); (fn.fdir("ecm.usb0") / "busy").write_text("")   # rmdir fails (not empty)
    r = fn.run()
    assert r.returncode == 1 and fn.calls()[-1] == "DIE stale ecm.usb0" and fn.links() == []   # -> EXIT failsafe


def test_setup_uses_the_functions_mtu_and_records_it():
    setup = GS[GS.index("setup_main() {"):]
    assert 'ifconfig usb0 mtu "$NETMTU" 2>>"$LOG" || die "mtu"' in setup and "mtu 478" not in setup
    assert setup.index("NETMTU=$(fn_mtu $NETFN)") < setup.index('ifconfig usb0 mtu "$NETMTU"')
    assert 'echo "fn=$NETFN" >> /tmp/mk20/usb0.setup' in setup
    assert setup.index('echo "ll=$LLOK" > /tmp/mk20/usb0.setup') < setup.index('echo "fn=$NETFN" >> /tmp/mk20/usb0.setup')


def test_teardown_and_stock_check_know_ncm():
    assert "rmdir $G/functions/ncm.usb0 $G/functions/ecm.usb0 $G/functions/hid.usb0 2>/dev/null" in GS
    so = GS[GS.index("stock_ok() {"):GS.index("rb_status() {")]
    assert "[ ! -e $C/ncm.usb0 ]" in so and "[ ! -e $C/ecm.usb0 ]" in so


def test_genv_whitelist_and_mk20d_carry_the_ncm_knob():
    loader = GS[GS.index('if [ -f "$GENV" ]; then'):GS.index("ECM_IP=${MK20_ECM_IP:-")]
    assert "MK20_NCM) MK20_NCM=$v;;" in loader
    assert "MK20_NCM=%s" in MK20D and '"$LLIP" "$FEATURE_NCM" > "$TM/gadget.env.tmp"' in MK20D
    assert "MK20_LL_IP=$LLIP MK20_NCM=$FEATURE_NCM \\\n      sh \"$TM/gadget-boot.sh\" --boot" in MK20D


# ---- mk20d / mk20lib ----
def test_cfg_feature_ncm_default_config_flag_env(dev, rel):
    assert cfg(dev, rel)["FEATURE_NCM"] == "1"
    conf(dev, "FEATURE_NCM=0\n")
    c = cfg(dev, rel); assert c["FEATURE_NCM"] == "0" and "WARNINGS" not in c
    conf(dev, "FEATURE_NCM=yes\n")
    c = cfg(dev, rel); assert c["FEATURE_NCM"] == "1" and "invalid FEATURE_NCM" in c["WARNINGS"]
    conf(dev, "")
    dev.p("mnt/SDCARD").mkdir(parents=True, exist_ok=True); dev.p("mnt/SDCARD/mk20-no-ncm").write_text("")
    assert cfg(dev, rel)["FEATURE_NCM"] == "0"
    dev.p("mnt/SDCARD/mk20-no-ncm").unlink()
    r = dev.run(rel / "sbin/mk20d", "config", env={"MK20_NCM": "0"})
    assert "FEATURE_NCM=0" in r.stdout.splitlines()


@pytest.mark.parametrize("flag", [False, True])
def test_boot_passes_and_records_the_ncm_knob(dev, rel, g1, flag):
    if flag:
        dev.p("mnt/SDCARD/mk20-no-ncm").write_text("")
    r, log = run_boot(dev, rel)
    env = dict(l.split("=", 1) for l in dev.read("tmp/mk20/gadget.env").splitlines())   # the stub dumps its env
    assert env["MK20_NCM"] == ("0" if flag else "1"), log
    assert f"ncm={0 if flag else 1}" in log


def test_composite_up_and_health_with_ncm(dev, rel, g1):
    (g1 / "configs/c.1/ncm.usb0").mkdir()
    h = json.loads(dev.run(rel / "sbin/mk20d", "health").stdout)
    assert h["usb"]["composite"] is True and h["net"]["fn"] == "ncm"
    os.rmdir(g1 / "configs/c.1/ncm.usb0"); (g1 / "configs/c.1/ecm.usb0").mkdir()
    h = json.loads(dev.run(rel / "sbin/mk20d", "health").stdout)
    assert h["usb"]["composite"] is True and h["net"]["fn"] == "ecm"
    os.rmdir(g1 / "configs/c.1/ecm.usb0")
    h = json.loads(dev.run(rel / "sbin/mk20d", "health").stdout)
    assert h["usb"]["composite"] is False and h["net"]["fn"] == ""


# ---- re-arm with an NCM composite (real rollback/rearm block) ----
def _ncm(ra):
    os.rmdir(ra.t / "g/configs/c.1/ecm.usb0"); (ra.t / "g/configs/c.1/ncm.usb0").mkdir()
    (ra.t / "if/usb0.mtu").write_text("462\n")


@pytest.fixture
def rn(dev, tmp_path):
    h = Rearm(dev, tmp_path / "rn")
    _ncm(h)
    yield h
    for p in h.procs:
        p.kill(); p.wait()


def test_rearm_ncm_keeps_a_live_usb0(rn):
    no_hid_no_adb(rn); rn.bridge_loop(); rn.kernel(recreate=False)
    r = rn.run()
    assert r.returncode == 0, rn.log()
    assert not [x for x in rn.calls() if x.startswith("ifconfig")] and "fn=ncm" in rn.log() and "usb0=kept" in rn.log()


def test_rearm_ncm_reconfigures_a_recreated_usb0_at_the_ncm_mtu(rn):
    no_hid_no_adb(rn); rn.bridge_loop(); rn.kernel(recreate=True)
    r = rn.run()
    assert r.returncode == 0, rn.log()
    c = rn.calls()
    assert "ifconfig usb0 mtu 462" in c and "ifconfig usb0 mtu 478" not in c, c
    assert (rn.ifc("usb0.addr"), rn.ifc("usb0.mtu"), rn.ifc("usb0:1.addr")) == ("172.30.250.2", "462", "169.254.7.2")
    assert "-> mtu 462, 172.30.250.2" in rn.log() and "usb0=reconfigured" in rn.log()


def test_rearm_ncm_with_the_wrong_mtu_restores_it(rn):
    """Same ifindex but MTU 478 on an NCM link (not what setup_main left): the PIO limit is restored."""
    (rn.t / "if/usb0.mtu").write_text("478\n")
    no_hid_no_adb(rn); rn.bridge_loop(); rn.kernel(recreate=False)
    r = rn.run()
    assert r.returncode == 0, rn.log()
    assert "ifconfig usb0 mtu 462" in rn.calls() and rn.ifc("usb0.mtu") == "462"


def test_rearm_refuses_without_a_network_function(rn):
    os.rmdir(rn.t / "g/configs/c.1/ncm.usb0")
    r = rn.run()
    assert r.returncode == 6 and rn.calls() == [] and "no ncm.usb0/ecm.usb0 in the composite" in rn.log()


# ---- f_ncm-mk20.patch vs the MTU gadget.sh sets ----
def _define(name):
    m = re.search(rf"^\+#define {name}\s+(\d+)", PATCH, re.M)
    assert m, name
    return int(m.group(1))


def test_patch_keeps_every_ntb_within_one_pio_request():
    out_max, in_cap, seg = _define("MK20_NTB_OUT_MAX"), _define("MK20_NTB_IN_CAP"), _define("MK20_MAX_SEG")
    mtu = int(re.search(r'fn_mtu\(\) \{ if \[ "\$1" = ncm \]; then echo (\d+);', GS).group(1))
    assert out_max <= 512 and in_cap <= 512 and in_cap % 512 != 0          # no ZLP pad byte onto a 512-byte IN
    assert seg == 14 + mtu                                                   # wMaxSegmentSize = device frame
    # NTB16 with one full-size datagram: NTH16 12 + pad<=3 (div 4) + frame + NDP16 8 + entry 4 + terminator 4
    assert 12 + 3 + seg + 8 + 4 + 4 <= in_cap
    # the shipped TX guard (transcribed from the patch, evaluated for NTB16) never drops a frame that fits and
    # never lets through one that would overrun the in_cap-sized NTB skb
    g = re.search(r"if \((ALIGN\(opts->nth_size.*?)> max_size\)", PATCH, re.S).group(1)
    g = re.sub(r"\n\+\s*", " ", g)
    for k, v in {"opts->nth_size": "12", "opts->ndp_size": "8", "dgram_idx_len": "4", "ndp_align": "4",
                 "div": "4", "rem": "0", "skb->len": "L"}.items():
        g = g.replace(k, v)
    assert re.fullmatch(r"[ALIGN()0-9L+\-*, ]+", g), g

    def need(L):    # worst-case fresh NTB16: NTH 12, dgram pad 0, frame, NDP pad <= 3, NDP 8, entry 4, terminator 4
        return 12 + L + 3 + 8 + 4 + 4
    for L in range(14, 600):
        dropped = eval(g, {"ALIGN": lambda x, a: (x + a - 1) // a * a, "L": L}) > in_cap
        assert dropped == (need(L) > in_cap), (L, g)
    assert eval(g, {"ALIGN": lambda x, a: (x + a - 1) // a * a, "L": seg}) <= in_cap   # full-MTU frames are sent
    # host->device: the same single-datagram NTB fits the advertised OUT size, and u_ether's OUT request
    # (max(14+mtu+20, dwNtbOutMaxSize)) stays <= 512
    assert 12 + 3 + seg + 8 + 4 + 4 <= out_max and max(14 + mtu + 20, out_max) <= 512
    assert "#define NTB_OUT_SIZE\t\tMK20_NTB_OUT_MAX" in PATCH
    assert "min_t(unsigned, ncm->port.fixed_in_len, MK20_NTB_IN_CAP)" in PATCH
    assert ".wMaxSegmentSize =\tcpu_to_le16(MK20_MAX_SEG)" in PATCH
    assert "ncm->netdev->stats.tx_dropped++;\n+\t\t\tdev_kfree_skb_any(skb);\n+\t\t\treturn NULL;" in PATCH
    assert "if (net_ratelimit())" in PATCH


def test_shipped_module_matches_the_stock_kernel():
    b = KO.read_bytes()
    assert b[:4] == b"\x7fELF" and b[4] == 1 and b[18] == 40               # ELF32, EM_ARM
    assert b"vermagic=5.4.61 SMP preempt mod_unload ARMv7 p2v8 \x00" in b
    assert b"name=usb_f_ncm\x00" in b and b"alias=usbfunc:ncm\x00" in b and b"depends=\x00" in b
    ecm = (FW / "release/lib/usb_f_ecm.ko").read_bytes()
    assert b"vermagic=5.4.61 SMP preempt mod_unload ARMv7 p2v8 \x00" in ecm
