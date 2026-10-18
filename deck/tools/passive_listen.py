#!/usr/bin/env python3
"""Passive, write-free listen on the MK20 T113 CDC port.

Opens the port (115200 8N1, like KeyboardDevice / the vendor demo), never
writes, and logs every received chunk (hex plus any decoded Protocol A frames)
and every change of the modem-status lines DSR/CD/CTS/RI.  On a Linux ACM
gadget, f_acm raises DSR+DCD (SERIAL_STATE notification) when the device-side
process opens /dev/ttyGS0, so the line log also tells us whether
KeyboardDevice holds its end open.

usage: passive_listen.py PORT SECONDS OUT.jsonl
"""
import json
import sys
import time

import serial
from serial.tools import list_ports

sys.path.insert(0, __file__.rsplit("/tools/", 1)[0])
from mk20deck import protocol as P  # noqa: E402


def main() -> int:
    port, secs, out = sys.argv[1], float(sys.argv[2]), sys.argv[3]
    info = {p.device: (p.vid, p.pid) for p in list_ports.comports()}
    if info.get(port) != (0x1D6B, 0x0104):
        print(f"refusing: {port} is not the MK20 T113 CDC (1d6b:0104), got {info.get(port)}")
        return 2
    s = serial.Serial(port, 115200, timeout=0)
    parser = P.FrameParser()
    t0 = time.time()
    total = 0
    last = None
    with open(out, "w") as f:
        def log(**kw):
            kw["t"] = round(time.time() - t0, 3)
            f.write(json.dumps(kw) + "\n")
            f.flush()
            print(kw, flush=True)

        while time.time() - t0 < secs:
            lines = dict(dsr=s.dsr, cd=s.cd, cts=s.cts, ri=s.ri)
            if lines != last:
                log(ev="lines", **lines)
                last = lines
            n = s.in_waiting
            if n:
                b = s.read(n)
                total += len(b)
                log(ev="rx", n=len(b), hex=b[:256].hex())
                for fr in parser.feed(b):
                    log(ev="frame", id=fr.frame_id, cmd=fr.cmd, data=fr.data[:300].decode("utf-8", "replace"))
            time.sleep(0.01)
        log(ev="end", total_rx=total)
    s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
