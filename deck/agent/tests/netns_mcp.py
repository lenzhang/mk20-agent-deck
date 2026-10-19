"""Linux-only executable netns isolation gate; uses the production listener C path.
Run by test_mcp.py under unshare -Urn. Failures are reported, never skipped."""
import errno
import socket
import subprocess
import sys

# A tiny test binary, compiled by the same Makefile, uses httpd_listen directly.
from pathlib import Path
root = Path(__file__).resolve().parents[1]
subprocess.run(["make", "-C", str(root), "build/mcp-bind-probe"], check=True)
subprocess.run(["ip", "link", "set", "lo", "up"], check=True)
# Kernels without the dummy module (e.g. CI boxes) still provide bridge; either is
# a software-only interface named usb0 for the SO_BINDTODEVICE check.
if subprocess.run(["ip", "link", "add", "usb0", "type", "dummy"]).returncode != 0:
    subprocess.run(["ip", "link", "add", "usb0", "type", "bridge"], check=True)
for command in (["ip", "addr", "add", "172.30.250.2/24", "dev", "usb0"], ["ip", "link", "set", "usb0", "up"]):
    subprocess.run(command, check=True)
p = subprocess.Popen([str(root / "build/mcp-bind-probe")], stdout=subprocess.PIPE, text=True)
try:
    assert p.stdout.readline().strip() == "ready", "SO_BINDTODEVICE listener failed"
    # Same namespace routes through lo for local delivery; binding the source to
    # lo cannot reach a socket constrained to dummy usb0.
    s = socket.socket(); s.settimeout(.3); s.bind(("127.0.0.1", 0))
    assert s.connect_ex(("172.30.250.2", 8765)) != 0, "non-usb0 source reached listener"
    s.close()
    # Prove the socket itself is active and bound, rather than a dead server.
    occupied = socket.socket()
    try:
        occupied.bind(("172.30.250.2", 8765))
        raise AssertionError("usb0 listener did not retain its bind")
    except OSError as error:
        assert error.errno == errno.EADDRINUSE
    finally:
        occupied.close()
finally:
    p.terminate(); p.wait(timeout=2)
