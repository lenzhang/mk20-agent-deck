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
heartbeat_fd = None
heartbeat_at = 0.0
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
        global heartbeat_at
        if heartbeat_fd is not None and time.monotonic() >= heartbeat_at:
            os.write(heartbeat_fd, P.encode_frame(P.CMD_JSON, b'{"method":"ui.heartbeat"}', 0xFFFFFFFE))
            heartbeat_at = time.monotonic() + 1
        r, _, _ = select.select([fd], [], [], max(0, end - time.time()))
        if not r: break
        try: out += os.read(fd, 65536)
        except OSError: break
    return out

def start(tmp, extra, env=None, hid=None, stalled_host=False):
    fb = os.path.join(tmp, "fb"); os.makedirs(fb, exist_ok=True)
    for i in range(1, 22): open(os.path.join(fb, f"fb{i}"), "wb").close()
    gm, gpath, gs = mkpty()
    hm, hpath, hs = mkpty()
    if stalled_host:
        # Fill the daemon->host direction BEFORE launching: its first send must hit backpressure,
        # without an earlier daemon timeout having enabled the legacy fail-fast path. Never read hm.
        os.set_blocking(hs, False)
        for _ in range(3):
            while True:
                try: os.write(hs, b"x" * 4096)
                except BlockingIOError: break
            while True:
                try: os.write(hs, b"x")
                except BlockingIOError: break
            time.sleep(0.01)
    st = os.path.join(tmp, "kbd.status")
    p = subprocess.Popen([BIN, "--run", "--gd32", gpath, "--host", hpath, "--fb", fb, "--bl", "", "--status", st,
                          "--knob-timeout-ms", "200", "--lock", os.path.join(tmp, "kbd.lock")] + (["--hid", hid, "--hid-stop-file", hid + ".stop"] if hid else ["--no-hid"]) + extra,
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

def stalled_host_safety(tmp):
    import socket
    for stop in ("socket", "reserved_key", "human", "k2_triple", "k2_hold"):
        case = os.path.join(tmp, stop); os.mkdir(case)
        hidp = os.path.join(case, "hid"); os.mkfifo(hidp)
        hr = os.open(hidp, os.O_RDONLY | os.O_NONBLOCK)
        sockp = os.path.join(case, "kbd.sock")
        flags = ["--no-gd32-write", "--ctl", sockp, "--ctl-allow"]
        if stop.startswith("k2_"): flags += ["--k2-estop"]
        if stop == "k2_hold": flags += ["--k2-release"]
        p, gm, hm, fb, st, keep = start(case, flags, hid=hidp, stalled_host=True)
        cs = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); cs.settimeout(2)
        try:
            end = time.monotonic() + 3
            while not os.path.exists(sockp) and time.monotonic() < end: time.sleep(0.01)
            cs.connect(sockp)
            with cs.makefile("rwb", buffering=0) as cf:
                def rq(obj):
                    cf.write((json.dumps(obj) + "\n").encode())
                    while True:
                        j = json.loads(cf.readline())
                        if "ev" not in j: return j
                def reports(n, timeout):
                    got = b""; end = time.monotonic() + timeout
                    while len(got) < n:
                        r, _, _ = select.select([hr], [], [], max(0, end - time.monotonic()))
                        if not r: break
                        got += os.read(hr, 4096)
                    return got
                check(reports(9, 1) == b"\x01" + bytes(8), f"{stop}: startup all-up")
                if stop == "reserved_key": check(rq({"op": "screen.page.show", "page_id": 1})["ok"], "show reserved page")
                tok = rq({"op": "lease.acquire", "ttl_ms": 5000})["token"]
                check(rq({"op": "act", "token": tok, "seq": 1, "kind": "key", "usage": 4, "down": 1})["ok"], f"{stop}: hold key")
                check(reports(9, 1) == bytes([1, 0, 0, 4, 0, 0, 0, 0, 0]), f"{stop}: agent key down")
                check(json.load(open(st))["host_tx_timeouts"] == 0, f"{stop}: no prior daemon host timeout")
                # Queue competing work while stopped so readiness is deterministic on resume.
                # Old production order waits 200 ms in the first host send before reaching the stop.
                p.send_signal(signal.SIGSTOP); os.waitpid(p.pid, os.WUNTRACED)
                while True:   # top off any tty buffering that settled during startup; still never read hm
                    try: os.write(keep[1], b"x")
                    except BlockingIOError: break
                if stop == "socket":
                    os.write(hm, P.encode_frame(P.CMD_JSON, b'{"method":"getInfo"}', 1))
                    cf.write(b'{"op":"estop"}\n')
                else:
                    frames = gd32_frame([0x16, 1, 104, 104, 0, 0])  # harmless rotation before stop
                    if stop.startswith("k2_"): frames += gd32_frame([0x16, 1, 103, 103, 0, 0]) * (3 if stop == "k2_triple" else 1)
                    else: frames += gd32_frame([0x16, 1, 3 if stop == "reserved_key" else 0, 4 if stop == "reserved_key" else 2, 0, 0])
                    os.write(gm, frames)
                t0 = time.monotonic(); p.send_signal(signal.SIGCONT)
                hold_ms = 1000 if stop == "k2_hold" else 0
                got = reports(9, (hold_ms + 100) / 1000); dt = (time.monotonic() - t0) * 1000
                check(got == b"\x01" + bytes(8) and hold_ms - 50 <= dt <= hold_ms + 100,
                      f"stalled host {stop}: all-up {got.hex()} in {dt:.1f} ms (hold {hold_ms} ms)")
                if stop == "human":
                    check(not rq({"op": "status"})["leased"], "stalled host: human revoked lease")
                else:
                    reason = {"socket": "socket", "reserved_key": "page_key", "k2_triple": "k2_triple", "k2_hold": "k2_hold"}[stop]
                    while True:
                        ev = json.loads(cf.readline())
                        if ev.get("ev") == "estop": break
                    check(ev.get("reason") == reason, f"stalled host {stop}: event {ev}")
                print(f"e2e: initially stalled host {stop} -> all-up in {dt:.1f} ms")
        finally:
            p.send_signal(signal.SIGCONT); p.send_signal(signal.SIGTERM); p.wait(3)
            cs.close()
            for fd in (hr, gm, hm, *keep): os.close(fd)

def main():
    global heartbeat_fd, heartbeat_at
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
        # U2: boot is HOME, attach cannot navigate. Enter host L1 physically,
        # negotiate three heartbeats spanning two seconds, then provide a fresh frame.
        os.write(gm, gd32_frame([0x16, 1, 1, 1, 0, 0]) + gd32_frame([0x16, 0, 1, 1, 0, 0]))
        os.write(hm, P.encode_frame(P.CMD_JSON, b'{"method":"ui.hello","version":1}', 6))
        par.feed(read_for(hm, 0.05))
        for _ in range(3):
            os.write(hm, P.encode_frame(P.CMD_JSON, b'{"method":"ui.heartbeat"}', 7))
            par.feed(read_for(hm, 1.02))
        heartbeat_fd, heartbeat_at = hm, time.monotonic()
        boot = io.BytesIO(); Image.new("RGB", (640, 656)).save(boot, "JPEG")
        os.write(hm, P.encode_frame(P.CMD_SHOW_JPG, boot.getvalue(), 8))
        par.feed(read_for(hm, 0.5))
        reserved_before = open(os.path.join(fb, "fb20"), "rb").read()
        # key + K2 events
        os.write(gm, gd32_frame([0x16, 1, 0, 0, 0, 0]) + gd32_frame([0x16, 1, 0x69, 0x69, 0, 0]) + gd32_frame([5, 0, 0x67, 0x67, 0, 0]))
        evs = [P.classify_json(f.json()) for f in par.feed(read_for(hm, 0.5)) if f.cmd == P.CMD_JSON and f.json().get("method") == "keyStateChanged"]
        check(len(evs) == 2 and all(isinstance(e, P.KeyEvent) for e in evs), f"two events (echo swallowed): {evs}")
        if len(evs) == 2:
            check((evs[0].col, evs[0].row, evs[0].pressed) == (0, 0, True), f"key coords {evs[0]}")
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
        check(len(px) == 32768 and px == reserved_before, f"fb20 reserved face survives poisoned JPEG {v:#06x}")
        # setBacklight (bl disabled -> success, no files)
        os.write(hm, P.encode_frame(P.CMD_JSON, json.dumps(P.set_backlight(80)).encode(), 11))
        a = [f.json() for f in par.feed(read_for(hm, 0.5)) if f.cmd == P.CMD_JSON and f.json().get("ack_method") == "setBacklight"]
        check(a and a[0].get("ack_method") == "setBacklight" and a[0].get("success") is True and a[0].get("result") == 80, f"backlight {a}")
        time.sleep(1.2)
        s = json.load(open(st))
        check(s["knob_init"] == "ok" and s["knob_ok"] == 6 and s["panels_written"] == 21 and s["echo_frames"] == 1
              and s["key_events"] == 3 and s["knob_events"] == 1 and s["host"] == 1, f"status {s}")
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
        # fw 1.2.0: hidg0 node deleted + re-created after gadget setup -> detected (~1 s check); the stale fd is
        # PARKED (kept open: closing it after a re-bind kills the new cdev, codex r121), the new node opened,
        # all-up then the held RAlt re-sent on it; nothing more goes to the stale one
        os.rename(hidp, hidp + ".old"); os.mkfifo(hidp)
        hr2 = os.open(hidp, os.O_RDONLY | os.O_NONBLOCK)
        t0 = time.time()
        while fd_mode(p.pid, hidp) is None and time.time() - t0 < 3: time.sleep(0.05)
        check(fd_mode(p.pid, hidp) == 1 and fd_mode(p.pid, hidp + ".old") == 1, "hidg0 replacement: old parked, new opened")
        b = read_for(hr2, 0.3); r5 = [b[i:i + 9] for i in range(0, len(b), 9)]
        check(r5 == [b"\x01" + bytes(8), b"\x01\x40" + bytes(7)], f"replacement: all-up then held RAlt {r5}")
        check(hid_reports() == [], "nothing written to the dead node")
        time.sleep(1.2)
        s = json.load(open(st))
        check(s["hid_open"] == 1 and s["hid_replaced"] == 1 and s["hid_reopens"] == 1, f"status hid counters {s}")
        hr_old, hr = hr, hr2
        # teardown (stop file): current AND parked fds closed before any UDC/configfs change
        open(hidp + ".stop", "w").close(); time.sleep(0.4)
        check(fd_mode(p.pid, hidp) is None and fd_mode(p.pid, hidp + ".old") is None, "stop file closes current + parked")
        hid_reports(); os.unlink(hidp + ".stop"); time.sleep(1.5)
        check(fd_mode(p.pid, hidp) == 1, "reopened after teardown")
        hid_reports(); os.write(gm, gd32_frame([0x16, 1, 3, 0, 0, 0])); hid_reports()
        # host stops reading (bridge blocked): replies time out (bounded), GD32/HID keep being serviced
        par.feed(read_for(hm, 0.2))
        flood = P.encode_frame(P.CMD_JSON, json.dumps({"method": "getInfo"}).encode(), 77) * 300
        sent = 0; t0 = time.time()
        while sent < len(flood) and time.time() - t0 < 3:
            _, w, _ = select.select([], [hm], [], 0.2)
            if w:
                try: sent += os.write(hm, flood[sent:sent + 4096])
                except BlockingIOError: pass
        t1 = time.time()
        os.write(gm, gd32_frame([0x16, 0, 3, 0, 0, 0]))   # PTT release while the host is not reading
        rel = []
        while time.time() - t1 < 1.5 and not rel: rel = [r for r in hid_reports() if r == b"\x01" + bytes(8)]
        check(rel and time.time() - t1 < 1.5, f"HID serviced while host not reading ({time.time() - t1:.2f}s)")
        read_for(hm, 1.0)   # drain; replies resume
        os.write(hm, P.encode_frame(P.CMD_JSON, json.dumps({"method": "getInfo"}).encode(), 78))
        got = []; t2 = time.time()
        while time.time() - t2 < 3 and not got:
            got = [f for f in par.feed(read_for(hm, 0.3)) if f.cmd == P.CMD_JSON and f.frame_id == 78]
        check(got, "host replies resume after the stall")
        time.sleep(1.1); s = json.load(open(st))
        check(s["host_tx_timeouts"] >= 1, f"host_tx_timeouts counted {s.get('host_tx_timeouts')}")
        # single writer: a second instance on the same lock exits 3 without touching ttyS1
        p2 = subprocess.run([BIN, "--run", "--no-host", "--no-hid", "--gd32", "/nonexistent", "--status", "",
                             "--lock", os.path.join(tmp, "kbd.lock")], capture_output=True, timeout=10)
        check(p2.returncode == 3, f"second instance refused rc={p2.returncode} {p2.stderr[-200:]}")
        os.write(gm, gd32_frame([0x16, 1, 3, 0, 0, 0])); hid_reports()
        heartbeat_fd = None
        p.send_signal(signal.SIGTERM); p.wait(3)
        check(p.returncode == 0, "clean exit")
        r4 = hid_reports()
        check(r4 == [b"\x01" + bytes(8)], f"all-up on SIGTERM {r4}")
        os.close(hr); os.close(hr_old)

        # agent A1: --ctl control socket on the real binary: lease, act -> hidg0, e-stop latency + all-up, human preempt
        hidc = os.path.join(tmp, "hidg0c"); os.mkfifo(hidc)
        hrc = os.open(hidc, os.O_RDONLY | os.O_NONBLOCK)
        sockp = os.path.join(tmp, "kbd.sock")
        p, gm, hm, fb, st, keep = start(tmp, ["--no-gd32-write", "--no-host", "--ctl", sockp, "--ctl-allow"], hid=hidc)
        import socket as _s
        t0 = time.time()
        while not os.path.exists(sockp) and time.time() - t0 < 3: time.sleep(0.05)
        check(os.path.exists(sockp) and (os.stat(sockp).st_mode & 0o777) == 0o600, "kbd.sock 0600")
        cs = _s.socket(_s.AF_UNIX, _s.SOCK_STREAM); cs.connect(sockp); cf = cs.makefile("rwb", buffering=0)
        def rq(obj):
            cf.write((json.dumps(obj) + "\n").encode())
            while True:
                j = json.loads(cf.readline())
                if "ev" not in j: return j
        def drain():
            time.sleep(0.05); b = b""
            while True:
                try: d = os.read(hrc, 4096)
                except BlockingIOError: break
                if not d: break
                b += d
            return b
        drain()
        tok = rq({"op": "lease.acquire", "owner": "e2e", "ttl_ms": 2000})["token"]
        check(rq({"op": "act", "token": tok, "seq": 1, "kind": "key", "usage": 4, "down": 1})["ok"], "act key")
        check(rq({"op": "act", "token": tok, "seq": 2, "kind": "ptr", "buttons": 1, "x": 10, "y": 20})["ok"], "act ptr")
        b = drain()
        check(b == bytes([1, 0, 0, 4, 0, 0, 0, 0, 0]) + bytes([2, 1, 10, 0, 20, 0]), f"act reports {b.hex()}")
        t1 = time.perf_counter()
        cf.write(b'{"op":"estop"}\n')
        got = b""
        while len(got) < 15 and time.perf_counter() - t1 < 1:
            try: got += os.read(hrc, 64)
            except BlockingIOError: time.sleep(0.001)
        dt = (time.perf_counter() - t1) * 1000
        check(got == b"\x01" + bytes(8) + bytes([2, 0, 10, 0, 20, 0]), f"estop all-up {got.hex()}")
        check(dt <= 100, f"estop latency {dt:.1f} ms")
        print(f"e2e: estop -> all-up in {dt:.1f} ms")
        while True:   # consume the estop broadcast + its reply
            j = json.loads(cf.readline())
            if "ev" not in j: break
        check(j.get("ok") and j.get("state") == "stopped", f"estop reply {j}")
        j = rq({"op": "status"}); check(j["state"] == "stopped", f"stopped {j}")
        check(rq({"op": "lease.acquire", "ttl_ms": 2000}).get("err") == "stopped", "no lease after estop")
        cs.close(); p.send_signal(signal.SIGTERM); p.wait(3)
        check(p.returncode == 0 and not os.path.exists(sockp), "ctl clean exit, socket removed")
        # lease expiry + human preempt
        p, gm, hm, fb, st, keep = start(tmp, ["--no-gd32-write", "--no-host", "--ctl", sockp, "--ctl-allow"], hid=hidc)
        t0 = time.time()
        while not os.path.exists(sockp) and time.time() - t0 < 3: time.sleep(0.05)
        cs = _s.socket(_s.AF_UNIX, _s.SOCK_STREAM); cs.connect(sockp); cf = cs.makefile("rwb", buffering=0)
        drain()
        tok = rq({"op": "lease.acquire", "ttl_ms": 300})["token"]
        rq({"op": "act", "token": tok, "seq": 1, "kind": "key", "usage": 5, "down": 1}); drain()
        time.sleep(0.5)
        check(drain() == b"\x01" + bytes(8), "lease expiry -> all-up")
        tok = rq({"op": "lease.acquire", "ttl_ms": 3000})["token"]
        rq({"op": "act", "token": tok, "seq": 1, "kind": "key", "usage": 6, "down": 1}); drain()
        os.write(gm, gd32_frame([0x16, 1, 0, 2, 0, 0]))   # physical key press (not PTT)
        time.sleep(0.3)
        check(drain() == b"\x01" + bytes(8), "human key press preempts agent")
        check(rq({"op": "act", "token": tok, "seq": 2, "kind": "key", "usage": 6, "down": 1}).get("err") == "no_lease", "lease gone after preempt")
        cs.close(); p.send_signal(signal.SIGTERM); p.wait(3)
        # Known release-capable firmware: FIRST held press must fire from the real poll timer.
        p, gm, hm, fb, st, keep = start(tmp, ["--no-gd32-write", "--no-host", "--ctl", sockp, "--ctl-allow",
                                            "--k2-estop", "--k2-release"], hid=hidc)
        t0 = time.monotonic()
        while not os.path.exists(sockp) and time.monotonic() - t0 < 3: time.sleep(0.05)
        cs = _s.socket(_s.AF_UNIX, _s.SOCK_STREAM); cs.settimeout(2); cs.connect(sockp); cf = cs.makefile("rwb", buffering=0)
        drain()
        tok = rq({"op": "lease.acquire", "ttl_ms": 3000})["token"]
        rq({"op": "act", "token": tok, "seq": 1, "kind": "key", "usage": 7, "down": 1}); drain()
        os.write(gm, gd32_frame([0x16, 1, 104, 104, 0, 0]) + gd32_frame([0x16, 1, 105, 105, 0, 0]))
        check(not rq({"op": "status"})["estopped"], "K2 rotation does not stop")
        t0 = time.monotonic(); os.write(gm, gd32_frame([0x16, 1, 103, 103, 0, 0]))
        # No requests or further GD32 events after down: only the poll deadline can wake the loop.
        ready, _, _ = select.select([hrc, cs], [], [], 0.65)
        check(not ready, "cold K2 hold guard (no traffic)")
        stopped = False; up = b""; event_at = up_at = None
        while time.monotonic() - t0 < 1.1 and (not stopped or len(up) < 9):
            ready, _, _ = select.select([hrc, cs], [], [], max(0, t0 + 1.1 - time.monotonic()))
            if hrc in ready:
                up += os.read(hrc, 4096); up_at = time.monotonic() - t0
            if cs in ready:
                ev = json.loads(cf.readline())
                if ev.get("ev") == "estop":
                    stopped = ev.get("reason") == "k2_hold"; event_at = time.monotonic() - t0
        check(stopped and event_at is not None and 0.95 <= event_at <= 1.1, f"timer-only cold K2 hold event at {event_at}")
        check(up == b"\x01" + bytes(8) and up_at is not None and 0.95 <= up_at <= 1.1, f"timer-only cold K2 all-up at {up_at}")
        check(rq({"op": "lease.acquire"}).get("err") == "stopped", "cold K2 hold latches stop")
        cf.close(); cs.close(); p.send_signal(signal.SIGTERM); p.wait(3)
        os.close(hrc)
        stalled_host_safety(tmp)

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
