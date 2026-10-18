#!/bin/sh
# Composite: ECM (macOS-native) + combined HID (kbd+abs pointer) + ADB (FFS) if EP budget allows.
# Requires the binary-patched STOCK kernel (ep3 ISO→INT + ep5out INT-capable, see
# NOTES-stock-kernel-patch.md) + /data/mk20/usb_f_ecm.ko (insmod'ed below; HID/FFS are built in).
# Failsafe: on any error, restore stock serial via /data/setusbconfig serial.
#
# EP budget: f_hid (5.4) ALWAYS needs INT IN + INT OUT. ECM notify INT IN + HID IN/OUT = 3 INT:
#   ep3 (INT, patched) , ep4 (INT), ep5out (bulk+INT, patched). Bulk: ep1, ep2 pairs (ECM, ADB).
#   Link ECM BEFORE HID (configfs binds in link order) or ECM notify finds no INT IN endpoint.

set -eu
LOG=/mnt/SDCARD/gadget-ecm-hid.log
UDC=$(ls /sys/class/udc 2>/dev/null | head -1)
G=/sys/kernel/config/usb_gadget/g_mk20
FAILSAFE() {
  echo "FAILSAFE: $*" | tee -a "$LOG"
  /data/setusbconfig serial 2>>"$LOG" || true
  # also try legacy path
  setusbconfig serial 2>>"$LOG" || true
}
trap 'FAILSAFE trap' INT TERM

mkdir -p /mnt/SDCARD
echo "=== $(date) UDC=$UDC ===" >>"$LOG"
[ -n "$UDC" ] || { FAILSAFE no-udc; exit 1; }

# Stop stock gadget / KD that holds UDC
killall KeyboardDevice 2>/dev/null || true
killall qt_app2 adbd 2>/dev/null || true
sleep 1
# Unbind existing gadgets
for g in /sys/kernel/config/usb_gadget/*; do
  [ -d "$g" ] || continue
  echo "" > "$g/UDC" 2>/dev/null || true
done

mount -t configfs none /sys/kernel/config 2>/dev/null || true
mkdir -p "$G"
cd "$G"

echo 0x1d6b > idVendor
echo 0x0104 > idProduct
echo 0x0100 > bcdDevice
echo 0x0200 > bcdUSB

mkdir -p strings/0x409
echo "mk20" > strings/0x409/serialnumber
echo "Waveshare" > strings/0x409/manufacturer
echo "MK20 ECM+HID+ADB" > strings/0x409/product

mkdir -p configs/c.1/strings/0x409
echo "ECM+HID+ADB" > configs/c.1/strings/0x409/configuration
echo 250 > configs/c.1/MaxPower

# --- ECM ---
grep -q '^usb_f_ecm ' /proc/modules || insmod /data/mk20/usb_f_ecm.ko
mkdir -p functions/ecm.usb0
# Stable locally-administered MACs
echo "02:11:22:33:44:55" > functions/ecm.usb0/dev_addr
echo "02:11:22:33:44:56" > functions/ecm.usb0/host_addr
ln -sf functions/ecm.usb0 configs/c.1/

# --- combined HID: report ID 1 = keyboard, report ID 2 = absolute pointer ---
mkdir -p functions/hid.usb0
echo 1 > functions/hid.usb0/protocol
echo 1 > functions/hid.usb0/subclass
echo 8 > functions/hid.usb0/report_length
# Minimal combined descriptor (boot keyboard 8B + abs mouse 6B with report IDs)
printf '\x05\x01\x09\x06\xa1\x01\x85\x01\x05\x07\x19\xe0\x29\xe7\x15\x00\x25\x01\x75\x01\x95\x08\x81\x02\x95\x01\x75\x08\x81\x03\x95\x05\x75\x01\x05\x08\x19\x01\x29\x05\x91\x02\x95\x01\x75\x03\x91\x03\x95\x06\x75\x08\x15\x00\x25\x65\x05\x07\x19\x00\x29\x65\x81\x00\xc0\x05\x01\x09\x02\xa1\x01\x85\x02\x09\x01\xa1\x00\x05\x09\x19\x01\x29\x03\x15\x00\x25\x01\x95\x03\x75\x01\x81\x02\x95\x01\x75\x05\x81\x03\x05\x01\x09\x30\x09\x31\x16\x00\x00\x26\xff\x7f\x75\x10\x95\x02\x81\x02\xc0\xc0' > /tmp/hid.desc
# Use report_desc via cat binary — configfs wants binary write
cat /tmp/hid.desc > functions/hid.usb0/report_desc
ln -sf functions/hid.usb0 configs/c.1/

# --- ADB FunctionFS (optional; skip if mkdir fails) ---
ADB_OK=0
if mkdir -p functions/ffs.adb 2>>"$LOG"; then
  ln -sf functions/ffs.adb configs/c.1/ || true
  mkdir -p /dev/usb-ffs/adb
  mount -t functionfs adb /dev/usb-ffs/adb 2>>"$LOG" || true
  ADB_OK=1
fi

# Bind
echo "$UDC" > UDC 2>>"$LOG" || { FAILSAFE bind-failed; exit 1; }
echo "bound $UDC" >>"$LOG"

# Network: fixed 192.168.7.2/24 (host typically .1)
sleep 1
if [ -d /sys/class/net/usb0 ]; then
  ip link set usb0 up 2>>"$LOG" || ifconfig usb0 up 2>>"$LOG" || true
  ip addr add 192.168.7.2/24 dev usb0 2>>"$LOG" || \
    ifconfig usb0 192.168.7.2 netmask 255.255.255.0 2>>"$LOG" || true
  echo "usb0 192.168.7.2/24" >>"$LOG"
fi

# Start adbd if FFS ready
if [ "$ADB_OK" = 1 ]; then
  # Give FFS ep nodes a moment
  i=0
  while [ $i -lt 50 ]; do
    [ -e /dev/usb-ffs/adb/ep0 ] && break
    i=$((i+1)); sleep 0.1
  done
  adbd &
  echo "adbd started" >>"$LOG"
fi

# PTY bridge for Protocol A over TCP (KeyboardDevice still wants /dev/ttyGS0)
if [ -x /data/ttygs_tcp_bridge ]; then
  # Move real GS aside if present
  if [ -e /dev/ttyGS0 ] && [ ! -L /dev/ttyGS0 ]; then
    mv /dev/ttyGS0 /dev/ttyGS0.real 2>/dev/null || true
  fi
  /data/ttygs_tcp_bridge --listen 0.0.0.0:17890 --link /dev/ttyGS0 >>"$LOG" 2>&1 &
  echo "pty bridge on :17890" >>"$LOG"
fi

echo "gadget-ok" > /mnt/SDCARD/gadget-ecm-hid.status
exit 0
