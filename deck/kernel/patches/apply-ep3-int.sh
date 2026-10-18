#!/usr/bin/env bash
# Apply ep3-iso→ep3-int to a Tina linux-5.4 tree (official USB guide §3.3 example 2).
# Hard-fails unless ep[5] becomes INT end-to-end.
set -euo pipefail
ROOT="${1:?usage: apply-ep3-int.sh <kernel-root>}"
H="$ROOT/drivers/usb/sunxi_usb/include/sunxi_udc.h"
C="$ROOT/drivers/usb/sunxi_usb/udc/sunxi_udc.c"
[[ -f "$H" && -f "$C" ]] || { echo "missing sunxi_udc sources under $ROOT"; exit 1; }
cp -a "$H" "$H.bak-ep3" && cp -a "$C" "$C.bak-ep3"
sed -i \
  -e 's/static const char ep3_iso_name\[\][[:space:]]*=[[:space:]]*"ep3-iso"/static const char ep3_int_name[]     = "ep3-int"/' \
  -e 's/{ep3_iso_name,/{ep3_int_name,/g' \
  -e 's/ep3_iso_name and ep4_int_name/ep3_int_name and ep4_int_name/' \
  "$H"
python3 - "$C" <<'PY'
import re, sys
p = sys.argv[1]
t = open(p).read().replace("ep3_iso_name", "ep3_int_name")
pat = re.compile(
    r"(\.ep\[5\]\s*=\s*\{.*?\.name\s*=\s*ep3_int_name,.*?)"
    r"SW_UDC_EP_ISO_FIFO_SIZE(.*?)SW_UDC_EP_ISO_FIFO_SIZE(.*?)"
    r"USB_EP_CAPS_TYPE_ISO(.*?)USB_ENDPOINT_XFER_ISOC",
    re.S,
)
def repl(m):
    return (
        m.group(1) + "SW_UDC_EP_FIFO_SIZE" + m.group(2) + "SW_UDC_EP_FIFO_SIZE"
        + m.group(3) + "USB_EP_CAPS_TYPE_INT" + m.group(4) + "USB_ENDPOINT_XFER_INT"
    )
t3, n = pat.subn(repl, t, count=1)
if n != 1:
    raise SystemExit(f"FATAL: ep[5] ISO→INT structured replace count={n} (need 1)")
m = re.search(
    r"\.ep\[5\]\s*=\s*\{.*?ep3_int_name.*?bmAttributes\s*=\s*([^\n,]+)",
    t3,
    re.S,
)
if not m or "XFER_INT" not in m.group(1):
    raise SystemExit("FATAL: ep[5] bmAttributes not INT after patch")
if "ep3_iso_name" in t3:
    raise SystemExit("FATAL: ep3_iso_name still present in .c")
open(p, "w").write(t3)
print("patched", p)
PY
# Header checks
grep -q 'ep3_int_name' "$H" || { echo "FATAL: header missing ep3_int_name"; exit 1; }
grep -q 'ep3_iso_name' "$H" && { echo "FATAL: header still has ep3_iso_name"; exit 1; }
rg -n 'ep3_int_name|ep3-iso|TYPE_INT' "$H" "$C" | head -40
echo "OK: ep3→int applied under $ROOT"
