# Wrapper: lets MK20_PORT be a pyserial URL via the ECM PTY bridge, e.g. socket://172.30.250.2:17890 (firmware
# >= 1.1.0; 10.20.0.2 on 1.0.x). Link-local socket://169.254.7.2:17890 needs the MK20 interface (see en-relay.py).
import runpy, sys, serial
_orig = serial.Serial
def _url(port, *a, **k):
    return serial.serial_for_url(port, *a, **k) if "://" in str(port) else _orig(port, *a, **k)
serial.Serial = _url
sys.argv = [sys.argv[0].replace("mk20ctl-url.py", "mk20ctl.py")] + sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
