"""K4 safe-mode executable test: real daemon, fake UART/fbs/HID, no /proc dependency."""
import json
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import tempfile
import time
import tty

BIN = Path(os.environ.get('MK20_TEST_KBD', Path(__file__).resolve().parents[1] / 'mk20-kbd'))


def frame(payload):
    return bytes([0xaa, 0x55, sum(payload) & 255, len(payload), ~len(payload) & 255, *payload, 0xf5, 0x5f])


def wait(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('timed out')


def read(fd, duration=.2):
    result = b''
    end = time.monotonic() + duration
    while time.monotonic() < end:
        if not select.select([fd], [], [], max(0, end - time.monotonic()))[0]:
            break
        result += os.read(fd, 4096)
    return result


def run_case(root, readonly, hold):
    root.mkdir()
    fb = root / 'fb'; fb.mkdir()
    for i in range(1, 22):
        (fb / f'fb{i}').touch()
    gm, gs = pty.openpty(); tty.setraw(gs)
    hm, hs = pty.openpty(); tty.setraw(hs)
    hid = root / 'hid'; os.mkfifo(hid)
    hr = os.open(hid, os.O_RDONLY | os.O_NONBLOCK)
    status = root / 'status'
    sock = root / 'ignored.sock'
    flags = ['--no-gd32-write'] if readonly else []
    if hold:
        flags += ['--k2-release']
    log = open(root / 'log', 'wb')
    p = subprocess.Popen([str(BIN), '--run', '--safe-mode', '--verbose', '--gd32', os.ttyname(gs),
                          '--fb', str(fb), '--bl', '', '--host', os.ttyname(hs), '--hid', str(hid),
                          '--hid-stop-file', str(root / 'hid-stop'), '--status', str(status),
                          '--lock', str(root / 'lock'), '--ctl', str(sock), '--ctl-allow',
                          '--knob-timeout-ms', '100', *flags], stderr=log)
    def state():
        try:
            return json.loads(status.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
    try:
        wire = b''
        if not readonly:
            end = time.monotonic() + 4
            while wire.count(b'\xf5\x5f') < 6 and time.monotonic() < end:
                if select.select([gm], [], [], .2)[0]:
                    chunk = os.read(gm, 4096); wire += chunk; os.write(gm, chunk)
            assert wire == b''.join(frame([5, 0, r, r, 0, 0]) for r in range(100, 106)), wire.hex()
        wait(lambda: state().get('panels_written') == 21)
        s = state()
        assert s['mode'] == 'safe' and s['host'] == 0 and s['hid_open'] == 1, s
        assert s['gd32_rw'] == int(not readonly)
        assert not sock.exists(), 'safe mode must not expose agent control'
        assert read(hr) == bytes([1]) + bytes(8), 'startup all-up'
        snapshots = [(fb / f'fb{i}').read_bytes() for i in range(1, 22)]
        assert all(len(b) == (121552 if i == 20 else 32768) and any(b) for i, b in enumerate(snapshots))
        # Host data cannot trigger JPEG decoding or screen takeover; even the control socket flag is inert.
        os.write(hm, b'A1A55A5Ehost garbage')
        # Existing zero-keycode local PTT mapping still forwards RAlt. Nonzero keycode is already sent by GD32.
        os.write(gm, frame([0x16, 1, 3, 0, 0, 0]))
        assert read(hr) == bytes([1, 0x40]) + bytes(7), 'safe HID down'
        os.write(gm, frame([0x16, 0, 3, 0, 0, 0]))
        assert read(hr) == bytes([1]) + bytes(8), 'safe HID up'
        os.write(gm, frame([0x16, 1, 3, 0, 0, 4]))
        assert read(hr) == b'', 'no double typing for GD32-owned nonzero codes'
        os.write(gm, frame([0x16, 1, 3, 0, 0, 0]))
        assert read(hr) == bytes([1, 0x40]) + bytes(7)
        os.write(gm, frame([0x16, 1, 103, 103, 0, 0]) * (1 if hold else 3))
        assert read(hr, 1.3 if hold else .3) == bytes([1]) + bytes(8), 'K2 e-stop releases HID'
        wait(lambda: state().get('estopped') == 1)
        assert snapshots == [(fb / f'fb{i}').read_bytes() for i in range(1, 22)], 'safe panels stay static'
        assert read(gm) == b'', 'no extra UART writes beyond six allow-listed RAM slots'
        assert read(hm) == b'', 'safe mode needs no host service'
    finally:
        p.send_signal(signal.SIGTERM); p.wait(4)
        for fd in (gm, gs, hm, hs, hr):
            os.close(fd)
        log.close()
    assert p.returncode == 0
    assert b'host garbage' not in (root / 'log').read_bytes()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='k4-safe-') as tmp:
        # Both write policies and both K2 capability paths; all fake device files.
        for i, (readonly, hold) in enumerate(((False, False), (True, True))):
            run_case(Path(tmp) / str(i), readonly, hold)
    print('K4 safe mode: 2 executable scenarios passed (21 panels, HID, K2, GD32 allow-list)')
