"""K1 scroll-back helper for Codex Desktop (macOS): build, install and run ``mk20deck-scroll``.

Only this small ad-hoc signed binary needs macOS Accessibility (it posts scroll-wheel events into the Codex window);
the Python daemon never does.  ``mk20deck scroll-setup`` compiles ``tools/macos/mk20deck-scroll.m`` with the Command
Line Tools' clang into ``$MK20DECK_HOME/bin/mk20deck-scroll`` (default ``~/.config/mk20-agent-deck/bin``), signs it
with the stable identifier ``com.leo.mk20deck-scroll`` and asks macOS for the permission (``--request``: the system
dialog + an entry in 系统设置 → 隐私与安全性 → 辅助功能).

The grant belongs to the binary's code hash: the helper is rebuilt only when its source (or the build flags) changed
AND the installed binary still matches what was built (``mk20deck-scroll.sha256`` next to it: source digest + binary
digest), so a re-run of scroll-setup keeps an existing grant and a corrupted / replaced binary is rebuilt.
"""
from __future__ import annotations

import glob
import hashlib
import os
import sys
from dataclasses import dataclass, field

from .adapters.base import Runner, SubprocessRunner

NAME = "mk20deck-scroll"
IDENTIFIER = "com.leo.mk20deck-scroll"
EXIT_OK, EXIT_USAGE, EXIT_UNTRUSTED, EXIT_NO_WINDOW, EXIT_EVENT = 0, 2, 3, 4, 5
SETUP_HINT = "回看需要辅助功能授权：运行 mk20deck scroll-setup"
SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
CFLAGS = ("-fobjc-arc", "-O2", "-Wall", "-mmacosx-version-min=12.0", "-framework", "AppKit",
          "-framework", "ApplicationServices")


def home() -> str:
    return os.path.expanduser(os.environ.get("MK20DECK_HOME", "~/.config/mk20-agent-deck"))


def default_path() -> str:
    return os.path.join(home(), "bin", NAME)


def source_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "tools", "macos", NAME + ".m"))


def source_digest(src: str) -> str:
    h = hashlib.sha256()
    with open(src, "rb") as f:
        h.update(f.read())
    h.update(("\0".join(CFLAGS) + "\0" + IDENTIFIER).encode())
    return h.hexdigest()


def stamp_path(path: str) -> str:
    return path + ".sha256"


def file_digest(path: str) -> str | None:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def up_to_date(path: str, src: str) -> bool:
    """True when the stamp names this source AND the installed binary is still the one that was built."""
    try:
        with open(stamp_path(path), encoding="utf-8") as f:
            src_d, _, bin_d = f.read().strip().partition(" ")
    except OSError:
        return False
    return (src_d == source_digest(src) and len(bin_d) == 64 and os.access(path, os.X_OK)
            and bin_d == file_digest(path))


def compile_argv(src: str, out: str, sysroot: str | None = None) -> list[str]:
    argv = ["clang"]
    if sysroot:
        argv += ["-isysroot", sysroot]
    return argv + [*CFLAGS, "-o", out, src]


def sign_argv(path: str) -> list[str]:
    return ["codesign", "--force", "--sign", "-", "--identifier", IDENTIFIER, path]


def sdk_candidates(runner: Runner) -> list[str | None]:
    """The toolchain default first, then its ``MacOSX.sdk`` link, then every other installed SDK (newest first): a
    Command Line Tools update can install a newer SDK than its linker reads (seen here 2026-10-25: MacOSX27.0)."""
    out: list[str | None] = [None]
    r = runner.run(["xcrun", "--sdk", "macosx", "--show-sdk-path"], timeout=20)
    sdk = r.stdout.strip() if r.returncode == 0 else ""
    if sdk:
        base = os.path.dirname(sdk)
        link = os.path.join(base, "MacOSX.sdk")
        if os.path.isdir(link):
            out.append(link)
        for p in sorted(glob.glob(os.path.join(base, "MacOSX*.sdk")), reverse=True):
            if not os.path.islink(p) and p not in out:
                out.append(p)
    return out


@dataclass
class BuildResult:
    ok: bool
    message: str
    built: bool = False
    log: list[str] = field(default_factory=list)


def build(runner: Runner, path: str | None = None, src: str | None = None, force: bool = False) -> BuildResult:
    """Compile + sign into ``path`` (atomic replace) unless it is up to date with ``src``."""
    path, src = path or default_path(), src or source_path()
    if not os.path.isfile(src):
        return BuildResult(False, f"找不到 helper 源码：{src}")
    if not force and up_to_date(path, src):
        return BuildResult(True, f"helper 已是最新：{path}")
    if runner.which("clang") is None:
        return BuildResult(False, "没有 clang：请先安装 Xcode Command Line Tools（xcode-select --install）")
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = f"{path}.build-{os.getpid()}"
    log: list[str] = []
    try:
        for sysroot in sdk_candidates(runner):
            r = runner.run(compile_argv(src, tmp, sysroot), timeout=120)
            log.append(f"clang{' -isysroot ' + sysroot if sysroot else ''}: exit {r.returncode}")
            if r.returncode == 0:
                break
            log.extend(line for line in (r.stderr or "").splitlines()[-3:])
        else:
            return BuildResult(False, "helper 编译失败（见上面的 clang 输出）", log=log)
        s = runner.run(sign_argv(tmp), timeout=30)
        if s.returncode != 0:
            log.append((s.stderr or "").strip()[:200])
            return BuildResult(False, "codesign 签名失败", log=log)
        os.chmod(tmp, 0o755)
        os.replace(tmp, path)
        with open(stamp_path(path), "w", encoding="utf-8") as f:
            f.write(f"{source_digest(src)} {file_digest(path) or ''}\n")
        return BuildResult(True, f"已编译并签名（{IDENTIFIER}）：{path}", built=True, log=log)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def trusted(runner: Runner, path: str | None = None, request: bool = False) -> tuple[bool | None, str]:
    """(True / False / None = helper missing or broken, its output)."""
    path = path or default_path()
    if not os.access(path, os.X_OK):
        return None, ""
    r = runner.run([path, "--request" if request else "--check"], timeout=10)
    out = (r.stdout or "").strip()
    if r.returncode == EXIT_OK and "trusted: yes" in out:
        return True, out
    if r.returncode == EXIT_UNTRUSTED:
        return False, out
    return None, (out + " " + (r.stderr or "").strip()).strip()


def main_setup(args, runner: Runner | None = None, out=print, platform: str | None = None) -> int:
    """``mk20deck scroll-setup``: build/install the helper if needed, then ask macOS for Accessibility for it only.
    Exit 0 = trusted (K1 scroll-back works in Codex Desktop), 1 = waiting for the switch, 2 = could not set up."""
    runner = runner or SubprocessRunner()
    if (platform or sys.platform) != "darwin":
        out("scroll-setup 只用于 macOS（Codex 桌面回看）；tmux 回看不需要它。")
        return 2
    path = getattr(args, "helper", None) or default_path()
    b = build(runner, path, force=getattr(args, "rebuild", False))
    for line in b.log:
        out("  " + line)
    out(b.message)
    if not b.ok:
        return 2
    ok, info = trusted(runner, path)
    if ok:
        out("辅助功能：已授权 · K1 回看可在 Codex 桌面使用（tmux 回看不需要授权）")
        return 0
    if ok is None:
        out(f"helper 无法运行：{info or path}")
        return 2
    if getattr(args, "open_settings", False):
        runner.run(["open", SETTINGS_URL], timeout=10)
    ok, info = trusted(runner, path, request=True)
    if ok:
        out("辅助功能：已授权 · K1 回看可在 Codex 桌面使用")
        return 0
    if "responsible: parent" in info:
        out("注意：helper 没能以自己的身份运行，macOS 会把授权记到终端 / Python 上（请把这个情况告诉维护者）")
    out("\n".join([
        "",
        "还差一步：只给 mk20deck-scroll 这个小程序授权「辅助功能」（不需要给 Python 或终端授权）。",
        "  1. macOS 会弹出「“mk20deck-scroll”想使用辅助功能控制这台电脑」：点「打开系统设置」。",
        "     没有弹窗（以前问过一次）：打开 系统设置 → 隐私与安全性 → 辅助功能，",
        "     或运行 mk20deck scroll-setup --open-settings。",
        "  2. 在辅助功能列表里找到 mk20deck-scroll，打开它右边的开关（按提示输入登录密码或用触控 ID）。",
        f"     列表里没有它：点列表下方「+」，按 ⌘⇧G 输入 {path} ，选中后点「打开」，再打开开关。",
        "  3. 再运行一次 mk20deck scroll-setup，看到「已授权」即完成；mk20deck 不用重启。",
        "  若开关已经打开却仍显示未授权（helper 更新后会这样）：在列表里选中 mk20deck-scroll 点「−」删除，",
        "     再从第 1 步做一遍。",
    ]))
    return 1
