#!/bin/sh
# M0.5: versioned kernel (boot partition p4) inventory on ext4: /data/mk20/kernel/<sha12>/{boot.img,MANIFEST}
# + ALLOWLIST (full sha -> name -> features). Copies only; legacy paths stay for bootswap/postswap.
K=/data/mk20/kernel; mkdir -p $K
add() { # $1=src $2=name $3=features $4=notes
  [ -f "$1" ] || { echo "missing $1"; return 1; }
  h=$(sha256sum "$1" | awk '{print $1}'); d=$K/${h%${h#????????????}}
  mkdir -p "$d"
  if [ ! -f "$d/boot.img" ] || [ "$(sha256sum "$d/boot.img" | awk '{print $1}')" != "$h" ]; then cp -f "$1" "$d/boot.img.tmp.$$" && sync && [ "$(sha256sum "$d/boot.img.tmp.$$" | awk '{print $1}')" = "$h" ] && mv -f "$d/boot.img.tmp.$$" "$d/boot.img" || { rm -f "$d/boot.img.tmp.$$"; echo "copy FAILED $1"; return 1; }; fi
  { echo "name=$2"; echo "sha256=$h"; echo "size=$(wc -c < "$1")"; echo "features=$3"; echo "source=$1"; echo "notes=$4"
    echo "kernel=Linux 5.4.61 #221 (uname identical on all variants: identify by p4 sha256 only)"; } > "$d/MANIFEST"
  grep -v "^$h " $K/ALLOWLIST 2>/dev/null > $K/ALLOWLIST.tmp; echo "$h $2 $3" >> $K/ALLOWLIST.tmp; mv -f $K/ALLOWLIST.tmp $K/ALLOWLIST
  echo "added $2 $h -> $d"
}
RC=0
add /data/mk20/boot-stock.img stock acm,adb "vendor stock p4 (also /mnt/SDCARD/boot-stock.img)" || RC=1
add /data/mk20/boot-baseline-ep3.img ep3 acm,adb,ecm "ep3 ISO->INT patch (HID ep5out not usable)" || RC=1
add /data/mk20/boot-baseline.img ep5out acm,adb,ecm,hid "ep3 ISO->INT + ep5out INT; current baseline" || RC=1
sync
P=$(sha256sum /dev/by-name/boot | awk '{print $1}'); echo "running p4=$P -> $(grep "^$P " $K/ALLOWLIST | cut -d' ' -f2-)"
cat $K/ALLOWLIST; ls -la $K/*/
exit $RC
