# Wrapper: lets MK20_PORT be a pyserial URL (e.g. socket://169.254.7.2:17890 via the ECM PTY bridge).
import runpy, sys, serial
_orig = serial.Serial
def _url(port, *a, **k):
    return serial.serial_for_url(port, *a, **k) if "://" in str(port) else _orig(port, *a, **k)
serial.Serial = _url
sys.argv = [sys.argv[0].replace("mk20ctl-url.py", "mk20ctl.py")] + sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
