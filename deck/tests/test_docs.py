"""M5/M6: the firmware docs must match the code (paths, flags, config defaults, kernel table)."""
import re
from pathlib import Path

D = Path(__file__).resolve().parents[1]
FW = D / "firmware"
DOCS = {n: (FW / n).read_text() for n in ("RELEASE.md", "INSTALL.md", "RECOVERY.md")}
CODE = "\n".join(p.read_text() for p in list((FW / "release").rglob("*.sh")) + [FW / "release/sbin/mk20d"]
                 + list((FW / "rescue").glob("*.sh")))


def test_referenced_tools_exist():
    for name, text in DOCS.items():
        for t in set(re.findall(r"tools/([A-Za-z0-9_.-]+\.(?:sh|py|txt))", text)):
            assert (D / "tools" / t).is_file(), f"{name} references missing tools/{t}"


def test_flag_files_are_implemented():
    flags = set(re.findall(r"^\| `(mk20-[a-z-]+)` \|", DOCS["RECOVERY.md"], re.M))
    assert {"mk20-no-composite", "mk20-no-adb", "mk20-gadget-nohid", "mk20-no-release", "mk20-no-watch"} <= flags
    for f in flags:
        assert f in CODE, f"documented flag {f} not read by any firmware script"
    for c in ("cmd-reboot", "cmd-rollback", "cmd-ping", "job.sh"):
        assert c in DOCS["RECOVERY.md"] and c in CODE


def test_config_defaults_match_mk20lib():
    lib = (FW / "release/share/mk20lib.sh").read_text()
    doc = DOCS["INSTALL.md"]
    for key, val in (("ECM_SUBNET", "172.30.250.0/24"), ("BRIDGE_PORT", "17890"), ("FLAP_DROPS", "5"),
                     ("FLAP_WINDOW_S", "60"), ("LOG_MAX_KB", "256"), ("LOG_KEEP", "3")):
        assert re.search(rf"\b{key}={re.escape(val)}\b", lib), key
        assert f"`{key}`" in doc and val in doc, key
    for key in re.findall(r"^    ([A-Z_]+)\) v_", lib, re.M):
        assert key in doc, f"config key {key} undocumented"


def test_kernel_table_matches_notes_and_allowlist():
    table = dict(re.findall(r"^\| (\w+)(?: \(running\))? \| `([0-9a-f]{64})` \|", DOCS["RECOVERY.md"], re.M))
    assert set(table) == {"stock", "ep3", "ep5out"}
    for name, sha in table.items():
        note = FW / "kernel-notes" / sha[:12] / "RECOVERY.txt"
        assert note.is_file() and sha in note.read_text() and name in note.read_text()
    for sha in re.findall(r"^[0-9a-f]{64}$", (FW / "KERNELS").read_text(), re.M):
        assert sha in table.values(), f"KERNELS sha {sha[:12]} missing from RECOVERY.md kernel table"


def test_changelog_has_current_version():
    v = (FW / "VERSION").read_text().strip()
    assert f"| {v} |" in DOCS["RELEASE.md"], f"RELEASE.md changelog lacks {v}"


def test_mac_undo_documented():
    for name in ("INSTALL.md", "RECOVERY.md"):
        assert "linklocal-remove" in DOCS[name] and "revert --force" in DOCS[name]
    assert "networksetup -removenetworkservice \"MK20 link-local\"" in DOCS["INSTALL.md"]


def test_whole_card_restore_never_points_at_unsafe_images():
    """--whole-image dd's raw to the disk: never next to the PhoenixCard vendor image or the per-partition dir."""
    texts = dict(DOCS)
    texts.update({str(p): p.read_text() for p in sorted((FW / "kernel-notes").glob("*/RECOVERY.txt"))})
    for name, text in texts.items():
        for m in re.finditer(r"--whole-image\s+(\S+)", text):
            assert "MK20_V1.0" not in m.group(1) and "live-221" not in m.group(1), (name, m.group(0))
    assert "must never be `dd`'d" in DOCS["RECOVERY.md"] and "IMAGEWTY" in DOCS["RECOVERY.md"]
    tool = (FW.parent / "tools/tf-restore-boot.sh").read_text()
    assert "IMAGEWTY" in tool and "is a directory" in tool



def test_k4_release_contract_and_default():
    hooks = (FW / "MAINT-HOOKS.md").read_text()
    assert (FW / "VERSION").read_text().strip() == "1.3.0"
    for name in ("restart-kbd", "exit-safe-mode", "debug", "state.json", "recent_crashes", "last_rc"):
        assert name in hooks
    for doc in DOCS.values():
        assert "MAINT-HOOKS.md" in doc and "1.3.0" in doc
    assert "1.3.0-rc1" in DOCS["RELEASE.md"] and "rc1" in DOCS["RELEASE.md"]
    assert "FEATURE_KBD=1;" in (FW / "release/share/mk20lib.sh").read_text()
    plan = (D / "KD-REPLACE-PLAN.md").read_text()
    k4 = plan[plan.index("**K4 release integration"):plan.index("**On-device checks still needed")]
    assert "~~" not in k4 and "to be removed" not in k4 and "FEATURE_KBD=1" in k4
