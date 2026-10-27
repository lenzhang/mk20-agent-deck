#!/bin/sh
# MK20 NCM bulk test: device->host 20 MiB (port 5001) and host->device 20 MiB (port 5002) at once
cd /tmp
cut -d' ' -f1 /proc/uptime > ncm.t0
( dd if=/dev/zero bs=1024 count=20480 2>/dev/null | nc 172.30.250.1 5001; cut -d' ' -f1 /proc/uptime > ncm.t1 ) &
( sleep 300 | nc 172.30.250.1 5002 | wc -c > ncm.dn; cut -d' ' -f1 /proc/uptime > ncm.t2 ) &
wait
echo done > ncm.done
