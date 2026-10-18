#!/usr/bin/env bash
# List external physical disks and flag likely MK20 TF (~4GB).
set -euo pipefail
echo "=== diskutil list external physical ==="
diskutil list external physical
echo
echo "=== candidates (size 3–5 GB, external) ==="
for d in $(diskutil list external physical | awk '/^\/dev\/disk/{print $1}'); do
  info=$(diskutil info "$d")
  size=$(echo "$info" | awk -F: '/Disk Size/{print $2}' | head -1)
  echo "$d  $size"
  echo "$info" | awk -F: '/Protocol|Device Location|Total Size|Volume Name|Media Name/{print "  "$0}'
done
echo
echo "When ready to restore a WHOLE-card image:"
echo "  ./tf-restore-boot.sh --disk diskN --whole-image ~/mk20-backup/tf/mk20-tf.img --dry-run"
echo "Stock boot.img from SDCARD after a failed swap:"
echo "  open the TF's 8MB FAT volume; copy boot-stock.img to ~/mk20-backup/tf/"
