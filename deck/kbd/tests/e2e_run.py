"""End-to-end: the real mk20-kbd binary (`--run`) against a fake GD32 and a fake Mac
console on two ptys, using the console's own codec (deck/mk20deck/protocol.py).
Checks: knob init frames on the wire + echo, key/knob events -> keyStateChanged,
getInfo, ack-paced cmd-100 JPEG -> fb files, setBacklight ack, status file,
--no-gd32-write and the kill file (tty opened O_RDONLY, nothing written)."""
import io, json, os, pty, select, signal, subprocess, sys, tempfile, time, tty
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from mk20deck import protocol as P  # noqa: E402
from PIL import Image  # noqa: E402

BIN = os.path.join(HERE, "..", "mk20-kbd")
fails = 0
def check(cond, what):
    global fails
    if not cond:
        fails += 1
        print("FAIL", what)

def gd32_frame(pl):
    return bytes([0xAA, 0x55, sum(pl) & 0xFF, len(pl), (~len(pl)) & 0xFF]) + bytes(pl) + b"\xF5\x5F"

def mkpty():
    m, s = pty.openpty()
    tty.setraw(m); tty.setraw(s)
    return m, os.ttyname(s), s

def read_for(fd, secs):
    out = b""; end = time.time() + secs
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], max(0, end - time.time()))
        if not r: break
        try: out += os.read(fd, 65536)
        except OSError: break
    return out

def start(tmp, extra, env=None, hid=None):
    fb = os.path.join(tmp, "fb"); os.makedirs(fb, exist_ok=True)
    for i in range(1, 22): open(os.path.join(fb, f"fb{i}"), "wb").close()
    gm, gpath, gs = mkpty()
    hm, hpath, hs = mkpty()
    st = os.path.join(tmp, "kbd.status")
    p = subprocess.Popen([BIN, "--run", "--gd32", gpath, "--host", hpath, "--fb", fb, "--bl", "", "--status", st,
                          "--knob-timeout-ms", "200"] + (["--hid", hid, "--hid-stop-file", hid + ".stop"] if hid else ["--no-hid"]) + extra,
                         stderr=subprocess.PIPE, env=env)
    return p, gm, hm, fb, st, (gs, hs)

def fd_mode(pid, path):
    for fd in os.listdir(f"/proc/{pid}/fd"):
        try:
            if os.readlink(f"/proc/{pid}/fd/{fd}") == path:
                flags = int(open(f"/proc/{pid}/fdinfo/{fd}").read().split("flags:")[1].split()[0], 8)
                return flags & 3
        except OSError: pass
    return None

def main():
    with tempfile.TemporaryDirectory() as tmp:
        hidp = os.path.join(tmp, "hidg0"); os.mkfifo(hidp)
        hr = os.open(hidp, os.O_RDONLY | os.O_NONBLOCK)
        p, gm, hm, fb, st, keep = start(tmp, [], hid=hidp)
        # fake GD32: echo the six frames
        wire = b""; t0 = time.time()
        while wire.count(b"\xF5\x5F") < 6 and time.time() - t0 < 3:
            r, _, _ = select.select([gm], [], [], 0.5)
            if not r: continue
            chunk = os.read(gm, 4096); wire += chunk
            os.write(gm, chunk)  # echo identically, immediately
        want = b"".join(gd32_frame([5, 0, r, r, 0, 0]) for r in range(100, 106))
        check(wire == want, f"knob init wire {wire.hex()}")
        time.sleep(0.3)
        check(fd_mode(p.pid, os.ttyname(keep[0])) == 2, "gd32 opened O_RDWR")
        # console: preamble + getInfo
        par = P.FrameParser()
        os.write(hm, b"0" * 4096)
        os.write(hm, P.encode_frame(P.CMD_JSON, json.dumps({"method": "getInfo"}).encode(), 5))
        fr = par.feed(read_for(hm, 0.5))
        gi = [f for f in fr if f.cmd == P.CMD_JSON]
        check(gi and gi[0].frame_id == 5 and gi[0].json()["ack_method"] == "getInfo"
              and gi[0].json()["result"]["deviceWidth"] == 640, "getInfo ack")
        if gi:
            # field-for-field parity with a real KD V2.32 getInfo capture (only deviceVersion differs)
            kd = json.load(open(os.path.join(HERE, "data", "kd-getinfo-v232.json")))
            ours = gi[0].json()
            kd["result"]["deviceVersion"] = ours["result"]["deviceVersion"]
            check(ours == kd, f"getInfo parity with KD V2.32: {ours} vs {kd}")
            from mk20deck.layout import DeviceLayout
            lay = DeviceLayout.from_get_info(gi[0].json()["result"])
            check(len(lay.keys) == 20, "console layout parses getInfo")
        # key + K2 events
        os.write(gm, gd32_frame([0x16, 1, 3, 4, 0, 0]) + gd32_frame([0x16, 1, 0x69, 0x69, 0, 0]) + gd32_frame([5, 0, 0x67, 0x67, 0, 0]))
        evs = [P.classify_json(f.json()) for f in par.feed(read_for(hm, 0.5)) if f.cmd == P.CMD_JSON]
        check(len(evs) == 2 and all(isinstance(e, P.KeyEvent) for e in evs), f"two events (echo swallowed): {evs}")
        if len(evs) == 2:
            check((evs[0].col, evs[0].row, evs[0].pressed) == (4, 3, True), f"key coords {evs[0]}")
            check((evs[1].col, evs[1].row) == (105, 105), f"K2 cw coords {evs[1]}")
        # JPEG canvas
        im = Image.new("RGB", (640, 656), (0, 0, 0))
        im.paste((255, 0, 0), (512, 528, 640, 656))  # key col4,row3 -> fb20
        b = io.BytesIO(); im.save(b, "JPEG", quality=90)
        os.write(hm, P.encode_frame(P.CMD_SHOW_JPG, b.getvalue(), 9))
        acks = [f for f in par.feed(read_for(hm, 1.0)) if f.cmd == P.CMD_SHOW_JPG]
        check(len(acks) == 1 and acks[0].frame_id == 9 and acks[0].data == b"", "jpeg ack")
        px = open(os.path.join(fb, "fb20"), "rb").read()
        v = px[2 * (64 * 128 + 64)] | px[2 * (64 * 128 + 64) + 1] << 8
        check(len(px) == 32768 and (v >> 11) >= 29 and ((v >> 5) & 63) < 6, f"fb20 red {v:#06x}")
        # setBacklight (bl disabled -> success, no files)
        os.write(hm, P.encode_frame(P.CMD_JSON, json.dumps(P.set_backlight(80)).encode(), 11))
        a = [f.json() for f in par.feed(read_for(hm, 0.5)) if f.cmd == P.CMD_JSON]
        check(a and a[0].get("ack_method") == "setBacklight" and a[0].get("success") is True and a[0].get("result") == 80, f"backlight {a}")
        time.sleep(1.2)
        s = json.load(open(st))
        check(s["knob_init"] == "ok" and s["knob_ok"] == 6 and s["panels_written"] == 21 and s["echo_frames"] == 1
              and s["key_events"] == 1 and s["knob_events"] == 1 and s["host"] == 1, f"status {s}")
        # HID: startup all-up, PTT (doc 1,4 = fw col 0,row 3) holds RAlt
        def hid_reports():
            b = read_for(hr, 0.3)
            return [b[i:i + 9] for i in range(0, len(b), 9)]
        r0 = hid_reports()
        check(r0 == [bytes(9)[:0] + b"\x01" + bytes(8)], f"startup all-keys-up {r0}")
        os.write(gm, gd32_frame([0x16, 1, 3, 0, 0, 0]))
        r1 = hid_reports()
        check(r1 == [b"\x01\x40" + bytes(7)], f"PTT down {r1}")
        par.feed(read_for(hm, 0.2))
        # stop file: hidg0 closed within the 100 ms tick (+ all-up since RAlt is held)
        open(hidp + ".stop", "w").close()
        time.sleep(0.4)
        r2 = hid_reports()
        check(r2 == [b"\x01" + bytes(8)], f"all-up before close on stop file {r2}")
        check(fd_mode(p.pid, hidp) is None, "hidg0 closed while stop file exists")
        os.unlink(hidp + ".stop")
        time.sleep(1.5)
        check(fd_mode(p.pid, hidp) == 1, "hidg0 reopened after stop file removed")
        r3 = hid_reports()
        check(r3 == [b"\x01" + bytes(8)], f"all-up on reopen {r3}")
        os.write(gm, gd32_frame([0x16, 1, 3, 0, 0, 0])); hid_reports()
        # fw 1.2.0: hidg0 node deleted + re-created after gadget setup -> detected (~1 s check), reopened,
        # all-up then the held RAlt re-sent on the new node; nothing more goes to the dead one
        os.rename(hidp, hidp + ".old"); os.mkfifo(hidp)
        hr2 = os.open(hidp, os.O_RDONLY | os.O_NONBLOCK)
        t0 = time.time()
        while fd_mode(p.pid, hidp) is None and time.time() - t0 < 3: time.sleep(0.05)
        check(fd_mode(p.pid, hidp) == 1 and fd_mode(p.pid, hidp + ".old") is None, "hidg0 replacement: old closed, new opened")
        b = read_for(hr2, 0.3); r5 = [b[i:i + 9] for i in range(0, len(b), 9)]
        check(r5 == [b"\x01" + bytes(8), b"\x01\x40" + bytes(7)], f"replacement: all-up then held RAlt {r5}")
        check(hid_reports() == [], "nothing written to the dead node")
        time.sleep(1.2)
        s = json.load(open(st))
        check(s["hid_open"] == 1 and s["hid_replaced"] == 1 and s["hid_reopens"] == 1, f"status hid counters {s}")
        hr_old, hr = hr, hr2
        p.send_signal(signal.SIGTERM); p.wait(3)
        check(p.returncode == 0, "clean exit")
        r4 = hid_reports()
        check(r4 == [b"\x01" + bytes(8)], f"all-up on SIGTERM {r4}")
        os.close(hr); os.close(hr_old)

        # read-only modes: flag and kill file
        kill = os.path.join(tmp, "nowrite")
        open(kill, "w").close()
        for extra, env in (([ "--no-gd32-write"], None), ([], dict(os.environ, MK20_KBD_NO_WRITE_FILE=kill))):
            p, gm, hm, fb, st, keep = start(tmp, extra + ["--no-host"], env)
            got = read_for(gm, 0.6)
            check(got == b"", f"read-only mode wrote {got.hex()} ({extra or 'kill file'})")
            check(fd_mode(p.pid, os.ttyname(keep[0])) == 0, "gd32 opened O_RDONLY")
            os.write(gm, gd32_frame([0x16, 1, 0, 0, 0, 0]))   # local mode: fb1 turns white
            time.sleep(0.3)
            px = open(os.path.join(fb, "fb1"), "rb").read()
            check(px[:2] == b"\xff\xff", "local mode paints pressed key")
            time.sleep(1.0)
            s = json.load(open(st))
            check(s["knob_init"] == "disabled" and s["gd32_rw"] == 0 and s["host"] == 0, f"status ro {s}")
            p.send_signal(signal.SIGTERM); p.wait(3)
    print(f"e2e: {'OK' if not fails else str(fails) + ' failures'}")
    return 1 if fails else 0

sys.exit(main())
