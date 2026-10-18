#!/bin/sh
# MK20 watcher v2 — polls /mnt/SDCARD every 2s. Started by lunch stub.
#   cmd-reboot                      -> sync + hardware WDT reset (hardreset.sh), reboot as fallback
#   job.sh + job.sh.sha256          -> verify, run once (300s cap), log, emit over ttyGS0
#   mk20/boot-once/*.sh(+.sha256)   -> run once at boot (after KD up), emit log
#   mk20/boot-delayed/*.sh(+.sha256)-> run once ~120s after boot, emit log
# Kill switch: /mnt/SDCARD/mk20-no-watch (checked every loop).
S=/mnt/SDCARD
D=$S/mk20
L=$D/logs
mkdir -p "$L" /tmp/mk20 2>/dev/null
exec 9>/tmp/mk20/watch.lock
flock -n 9 || exit 0
echo $$ > /tmp/mk20/watch.pid
WLOG=$L/watcher.log
# keep watcher log small (FAT)
[ -f "$WLOG" ] && [ "$(wc -c < "$WLOG")" -gt 32768 ] && tail -c 8192 "$WLOG" > "$WLOG.t" && mv "$WLOG.t" "$WLOG"
log() { echo "$(cat /proc/uptime | cut -d' ' -f1) $*" >> "$WLOG"; }
log "watcher start pid=$$"

emit() { # $1=file $2=tag
  f=$1; tag=$2
  j=0; while [ ! -e /dev/ttyGS0 ] && [ $j -lt 60 ]; do sleep 1; j=$((j+1)); done
  [ -e /dev/ttyGS0 ] || { log "emit $tag: no ttyGS0"; return; }
  { printf '\n@@MK20 BEGIN %s@@\n' "$tag"; tail -c 16384 "$f"; printf '\n@@MK20 END %s@@\n' "$tag"; } > /tmp/mk20/emit.$$ 2>/dev/null
  ( exec cat /tmp/mk20/emit.$$ >> /dev/ttyGS0 2>/dev/null 9>&- ) & cp=$!
  ( exec 9>&-; sleep 10; kill $cp 2>/dev/null ) & kp=$!
  wait $cp 2>/dev/null; kill $kp 2>/dev/null
  rm -f /tmp/mk20/emit.$$
}

shaok() { # $1=script ; expects $1.sha256 with hex first field
  [ -f "$1.sha256" ] || return 1
  e=$(awk '{print $1}' "$1.sha256"); g=$(sha256sum "$1" | awk '{print $1}')
  [ "$e" = "$g" ]
}

N=0
runjob() { # $1=src script  $2=tag   (moves src away first => once)
  N=$((N+1)); tag="$2-$N"
  r=/tmp/mk20/run-$tag.sh; out=$L/$2.log
  mv -f "$1" "$r"; rm -f "$1.sha256"
  log "run $tag"
  { echo "== $tag uptime=$(cut -d' ' -f1 /proc/uptime)"; } > "$out"
  ( exec sh "$r" >> "$out" 2>&1 </dev/null 9>&- ) & jp=$!
  ( exec 9>&-; sleep 300; kill $jp 2>/dev/null && echo "TIMEOUT killed" >> "$out" ) & kp=$!
  wait $jp 2>/dev/null; rc=$?; kill $kp 2>/dev/null; echo "rc=$rc" >> "$out"
  # cap FAT log size
  [ "$(wc -c < "$out")" -gt 65536 ] && tail -c 65536 "$out" > "$out.t" && mv "$out.t" "$out"
  sync
  emit "$out" "$tag"
  log "done $tag"
}

wait_kd() { i=0; while ! pgrep -f KeyboardDevice >/dev/null 2>&1 && [ $i -lt 90 ]; do sleep 1; i=$((i+1)); done; sleep 8; }

# boot-once jobs (after KD so output reaches the host)
(
  exec 9>&-
  wait_kd
  for f in "$D"/boot-once/*.sh; do
    [ -f "$f" ] || continue
    if shaok "$f"; then runjob "$f" "boot-$(basename "$f" .sh)"
    else log "boot-once sha FAIL $f"; mv -f "$f" "$f.bad"; fi
  done
  # every-boot jobs (sha-gated, NOT consumed): e.g. boot-every/adb.sh (persistent ACM+adb)
  for f in "$D"/boot-every/*.sh; do
    [ -f "$f" ] || continue
    [ -f $S/mk20-no-bootevery ] && break
    if shaok "$f"; then cp -f "$f" "/tmp/mk20/every-$(basename "$f")"; cp -f "$f.sha256" "/tmp/mk20/every-$(basename "$f").sha256"
      runjob "/tmp/mk20/every-$(basename "$f")" "every-$(basename "$f" .sh)"
    else log "boot-every sha FAIL $f"; fi
  done
  sleep 110
  for f in "$D"/boot-delayed/*.sh; do
    [ -f "$f" ] || continue
    [ -f $S/mk20-no-watch ] && break
    if shaok "$f"; then runjob "$f" "delayed-$(basename "$f" .sh)"
    else log "delayed sha FAIL $f"; mv -f "$f" "$f.bad"; fi
  done
  # heartbeat so host can confirm watcher alive
  echo "watcher alive pid=$$ uptime=$(cut -d' ' -f1 /proc/uptime) uname=$(uname -r)" > /tmp/mk20/hb
  emit /tmp/mk20/hb heartbeat
) &

while :; do
  [ -f $S/mk20-no-watch ] && { log "kill switch; exit"; exit 0; }
  if [ -f $S/cmd-reboot ]; then
    rm -f $S/cmd-reboot; log "cmd-reboot"; echo "rebooting $(cut -d' ' -f1 /proc/uptime)" > /tmp/mk20/rb
    emit /tmp/mk20/rb reboot; sync; sleep 1
    # soft reboot hangs in PSCI on this board -> hardware WDT reset (see hardreset.sh); reboot only as last resort
    [ -f $D/hardreset.sh ] && sh $D/hardreset.sh watcher
    reboot
  fi
  if [ -f $S/cmd-ping ]; then
    rm -f $S/cmd-ping
    echo "pong pid=$$ uptime=$(cut -d' ' -f1 /proc/uptime) kd=$(pgrep -f KeyboardDevice | head -1) adbd=$(pgrep -x adbd)" > /tmp/mk20/pong
    emit /tmp/mk20/pong ping
  fi
  if [ -f $S/job.sh ] && [ -f $S/job.sh.sha256 ]; then
    if shaok $S/job.sh; then runjob $S/job.sh job
    else
      sleep 2  # maybe sha written before job finished uploading; retry once
      if shaok $S/job.sh; then runjob $S/job.sh job
      else log "job sha FAIL"; mv -f $S/job.sh $S/job.sh.bad; rm -f $S/job.sh.sha256
        echo "job sha256 mismatch" > /tmp/mk20/jb; emit /tmp/mk20/jb job-shafail; fi
    fi
  fi
  sleep 2
done
