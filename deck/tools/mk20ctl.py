#!/usr/bin/env python3
"""MK20 remote-control client over Protocol A (ACM) — talks to the on-device watcher.

  mk20ctl.py info
  mk20ctl.py put LOCAL REMOTE            chunked saveToFile (64K, seek) + setFileCRC verify
  mk20ctl.py rm REMOTE...
  mk20ctl.py ls SUFFIX                   getFilesBySuffix
  mk20ctl.py probe                       saveToFile/setFileCRC semantics on scratch files
  mk20ctl.py job SCRIPT [--wait S]       upload job.sh(+.sha256); wait for @@MK20 job output
  mk20ctl.py ping|reboot [--wait S]
  mk20ctl.py listen [--wait S]           capture @@MK20 blocks (raw tap) to ~/mk20-backup/remote-logs
  mk20ctl.py stage-dryrun DIR            stage dry-run + watcher (see NOTES)
MK20_PORT: serial device (default), or bound:HOST:PORT:IFACE for the TCP bridge pinned to the MK20 interface.
Raw bytes on the port are teed: frames go to DeviceLink, text between @@MK20 markers is captured.
"""
from __future__ import annotations
import argparse, base64, hashlib, json, os, re, sys, threading, time, zlib
from pathlib import Path

sys.path.insert(0, os.environ.get("MK20DECK", "/Users/dev/worktrees/mk20-agent-deck-p0/deck"))
from mk20deck.transport import SerialTransport  # noqa: E402
from mk20deck.link import DeviceLink  # noqa: E402

PORT = os.environ.get("MK20_PORT", "/dev/cu.usbmodem1101")
CHUNK = 64 * 1024
LOGDIR = Path.home() / "mk20-backup" / "remote-logs"
MARK = re.compile(rb"@@MK20 BEGIN (\S+)@@\n(.*?)\n@@MK20 END \1@@", re.S)


class TeeTransport:
    def __init__(self, inner):
        self.inner = inner; self.raw = bytearray(); self.lock = threading.Lock(); self.blocks = []
        LOGDIR.mkdir(parents=True, exist_ok=True)
        self.rawlog = open(LOGDIR / f"raw-{time.strftime('%Y%m%d-%H%M%S')}.bin", "ab")

    def write(self, d): self.inner.write(d)

    def read(self, timeout):
        d = self.inner.read(timeout)
        if d:
            self.rawlog.write(d); self.rawlog.flush()
            with self.lock:
                self.raw.extend(d)
                for m in MARK.finditer(self.raw):
                    tag, body = m.group(1).decode(errors="replace"), m.group(2)
                    self.blocks.append((tag, body))
                    (LOGDIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{tag}.txt").write_bytes(body)
                    print(f"\n===== @@MK20 {tag} =====\n{body.decode(errors='replace')}\n===== end {tag} =====", flush=True)
                last = None
                for m in MARK.finditer(self.raw): last = m
                if last: del self.raw[: last.end()]
                if len(self.raw) > 1 << 20: del self.raw[: len(self.raw) - (1 << 19)]
        return d

    def wait_block(self, prefix, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self.lock:
                for i, (t, b) in enumerate(self.blocks):
                    if t.startswith(prefix):
                        return self.blocks.pop(i)
            time.sleep(0.1)
        return None

    def close(self): self.inner.close(); self.rawlog.close()


def _open_port():
    """MK20_PORT=bound:HOST:PORT:IFACE -> TCP bridge socket pinned to IFACE (never via TUN/another route)."""
    if PORT.startswith("bound:"):
        _, host, port, iface = PORT.split(":", 3)
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from tcp_transport import TcpTransport  # noqa: E402
        return TcpTransport(host, int(port), 5.0, bound_if=iface)
    return SerialTransport(PORT)


def connect():
    t = TeeTransport(_open_port())
    link = DeviceLink(t, ack_timeout=10.0, preamble_flush=0)
    link.start()
    return t, link


def call(link, method, **params):
    req = {"method": method}
    if params: req["parameters"] = params
    return link.call(req)


def crc_of(data: bytes) -> str: return str(zlib.crc32(data) & 0xFFFFFFFF)


def put(link, remote: str, data: bytes, delete_first=True) -> bool:
    if delete_first:
        call(link, "deleteFiles", filePaths=[remote])
    n = len(data); pos = 0; t0 = time.monotonic()
    while True:
        chunk = data[pos: pos + CHUNK]
        a = call(link, "saveToFile", filePath=remote, seek=pos, data=base64.b64encode(chunk).decode())
        if not (a and a.success):
            print(f"  saveToFile FAIL {remote} @ {pos}: {None if a is None else (a.error, a.result)}"); return False
        pos += len(chunk)
        if pos >= n: break
        if n > CHUNK: print(f"\r  {remote}: {pos*100//n}%", end="", flush=True)
    b = call(link, "setFileCRC", filePath=remote, crc=crc_of(data))
    ok = bool(b and b.success)
    nl = "\n" if n > CHUNK else ""
    print(f"{nl}put {remote} bytes={n} crc={crc_of(data)} ok={ok} {time.monotonic()-t0:.1f}s"
          + ("" if ok else f" crcresp={None if b is None else (b.error, b.result)}"))
    return ok


def verify(link, remote, data) -> bool:
    b = call(link, "setFileCRC", filePath=remote, crc=crc_of(data))
    return bool(b and b.success)


def sha_line(data: bytes, name: str) -> bytes:
    return f"{hashlib.sha256(data).hexdigest()}  {name}\n".encode()


def cmd_probe(link):
    p = "/mnt/SDCARD/mk20-probe.txt"; d = b"hello-probe\n" * 3
    r = {}
    r["put"] = put(link, p, d)
    r["crc_wrong_rejected"] = not verify(link, p, b"different")
    r["crc_missing_rejected"] = not verify(link, p + ".nope", d)
    # seek>0 append + overwrite-shorter semantics
    a = call(link, "saveToFile", filePath=p, seek=0, data=base64.b64encode(b"XY").decode())
    r["seek0_short_truncates"] = verify(link, p, b"XY")
    r["seek0_short_keeps_tail"] = verify(link, p, b"XY" + d[2:])
    # nested dir creation
    q = "/mnt/SDCARD/mk20-probe-dir/sub/x.txt"
    a = call(link, "saveToFile", filePath=q, seek=0, data=base64.b64encode(b"x").decode())
    r["mkdir_save_ack"] = bool(a and a.success); r["mkdir_crc"] = verify(link, q, b"x")
    call(link, "deleteFiles", filePaths=[p, q])
    r["deleted"] = not verify(link, p, b"XY") and not verify(link, p, b"XY" + d[2:])
    print(json.dumps(r, indent=2)); return r


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("cmd"); ap.add_argument("args", nargs="*")
    ap.add_argument("--wait", type=float, default=30.0)
    a = ap.parse_args()
    t, link = connect()
    try:
        info = call(link, "getInfo")
        if a.cmd == "info":
            print(json.dumps(info.result if info else None, indent=2, ensure_ascii=False)); return
        if not info: print("getInfo failed"); sys.exit(2)
        if a.cmd == "put":
            sys.exit(0 if put(link, a.args[1], Path(a.args[0]).read_bytes()) else 1)
        if a.cmd == "rm":
            r = call(link, "deleteFiles", filePaths=a.args); print(r and (r.success, r.result)); return
        if a.cmd == "ls":
            r = call(link, "getFilesBySuffix", suffixs=a.args or ["sh"])
            print(json.dumps(r.result if r else None, indent=2, ensure_ascii=False)); return
        if a.cmd == "probe":
            cmd_probe(link); return
        if a.cmd in ("ping", "reboot"):
            name = "cmd-ping" if a.cmd == "ping" else "cmd-reboot"
            ok = put(link, f"/mnt/SDCARD/{name}", b"1\n")
            blk = t.wait_block(a.cmd, a.wait) if ok else None
            print("got" if blk else "no marker (watcher not running? port closed?)"); sys.exit(0 if blk else 3)
        if a.cmd == "job":
            data = Path(a.args[0]).read_bytes()
            ok = put(link, "/mnt/SDCARD/job.sh", data) and put(link, "/mnt/SDCARD/job.sh.sha256", sha_line(data, "job.sh"))
            blk = t.wait_block("job", a.wait) if ok else None
            sys.exit(0 if blk else 3)
        if a.cmd == "listen":
            end = time.monotonic() + a.wait
            while time.monotonic() < end: time.sleep(0.5)
            return
        if a.cmd == "stage-dryrun":
            sys.exit(0 if stage_dryrun(link, Path(a.args[0])) else 1)
        print("unknown cmd"); sys.exit(2)
    finally:
        link.close()


def stage_dryrun(link, d: Path) -> bool:
    """d contains: boot-new.img boot-new.img.sha256 watcher.sh bootswap.sh try-adb.sh mk20-add-adb.sh lunch-stub.sh"""
    img = (d / "boot-new.img").read_bytes(); imgsha = (d / "boot-new.img.sha256").read_bytes()
    assert imgsha.split()[0].decode() == hashlib.sha256(img).hexdigest(), "image sha mismatch"
    assert imgsha.strip().endswith(b"boot-new.img")
    bs = (d / "bootswap.sh").read_bytes(); ta = (d / "try-adb.sh").read_bytes()
    plan = [
        ("/data/mk20/boot-new.img", img),
        ("/data/mk20/boot-new.img.sha256", imgsha),
        ("/mnt/SDCARD/boot-swap.dry", b"1\n"),
        ("/mnt/SDCARD/mk20/watcher.sh", (d / "watcher.sh").read_bytes()),
        ("/mnt/SDCARD/mk20/mk20-add-adb.sh", (d / "mk20-add-adb.sh").read_bytes()),
        ("/mnt/SDCARD/mk20/boot-once/bootswap.sh", bs),
        ("/mnt/SDCARD/mk20/boot-once/bootswap.sh.sha256", sha_line(bs, "bootswap.sh")),
        ("/mnt/SDCARD/mk20/boot-delayed/try-adb.sh", ta),
        ("/mnt/SDCARD/mk20/boot-delayed/try-adb.sh.sha256", sha_line(ta, "try-adb.sh")),
        ("/mnt/SDCARD/lunch.sh", (d / "lunch-stub.sh").read_bytes()),  # LAST: arms the stub
    ]
    for kill in ["/mnt/SDCARD/mk20-no-watch", "/mnt/SDCARD/mk20-no-bootswap"]:
        call(link, "deleteFiles", filePaths=[kill])
    ok = True
    for remote, data in plan:
        if not put(link, remote, data):
            ok = False; break
    print("re-verify:")
    for remote, data in plan:
        v = verify(link, remote, data); ok &= v; print(f"  {'OK ' if v else 'BAD'} {remote}")
    rec = {"time": time.strftime("%F %T"), "ok": ok, "files": {r: crc_of(x) for r, x in plan}}
    (LOGDIR / f"stage-dryrun-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(rec, indent=2))
    print("STAGE", "OK" if ok else "FAILED"); return ok


if __name__ == "__main__":
    main()
