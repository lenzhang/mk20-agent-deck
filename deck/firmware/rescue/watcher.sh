#!/bin/sh
# MK20 rescue watcher v3 (rescue tier: never part of a release). Started by the boot stub as: sh <tmp copy> <home>
# where <home> holds the verified rescue set {watcher.sh, hardreset.sh, loader.sh}(+.sha256).
# Polls /mnt/SDCARD every 2 s:
#   cmd-reboot        -> hardreset (WDT); refused -> report, keep running (never `reboot`: PSCI hang)
#   cmd-rollback      -> loader rollback (other A/B slot) + hardreset
#   cmd-ping          -> pong over ttyGS0
#   job.sh(+.sha256)  -> verify, run once (300 s cap, whole process tree killed on timeout), log, emit
# Boot: boot-once jobs; then the release loader (rc 0 = release launched -> legacy boot-every skipped);
# otherwise legacy boot-every jobs; boot-delayed after ~120 s.
# Kill switch: /mnt/SDCARD/mk20-no-watch. Test env: MK20_ROOT, MK20_WATCHER_LIB=1 (define functions only).
R=${MK20_ROOT:-}
S=$R/mnt/SDCARD
D=$S/mk20
H=${1:-$D}
L=$D/logs
TM=$R/tmp/mk20
mkdir -p "$L" "$TM" 2>/dev/null
WLOG=$L/watcher.log
log() { echo "$(cut -d' ' -f1 /proc/uptime) $*" >> "$WLOG"; }

shaok() { # $1=file ; expects $1.sha256 with hex first field
  [ -f "$1" ] && [ -f "$1.sha256" ] || return 1
  e=$(awk '{print $1}' "$1.sha256"); g=$(sha256sum "$1" | awk '{print $1}')
  [ -n "$e" ] && [ "$e" = "$g" ]
}
# children of a pid (field 4 of /proc/<pid>/stat, after the "(comm)" which may contain spaces)
kids() { for s in /proc/[0-9]*/stat; do pp=$(sed 's/.*) //' "$s" 2>/dev/null | cut -d' ' -f2); [ "$pp" = "$1" ] && { p=${s#/proc/}; echo "${p%/stat}"; }; done; }
tree() { echo "$1"; for c in $(kids "$1"); do tree "$c"; done; }
# freeze the whole tree, TERM, thaw, 2 s grace, then KILL anything left (rescanned)
kill_tree() {
  ps_=$(tree "$1"); for p in $ps_; do kill -STOP "$p" 2>/dev/null; done
  ps_=$(for p in $ps_; do tree "$p"; done | sort -u)
  for p in $ps_; do kill "$p" 2>/dev/null; kill -CONT "$p" 2>/dev/null; done
  sleep 2
  for p in $ps_; do kill -9 "$p" 2>/dev/null; done
}
# verified copy of a rescue script into tmpfs; prints the copy path
rescue_copy() { # $1=name: capture ONE expected digest, copy to a fresh tmpfs file, hash the copy, rename into place
  f=$H/$1; e=$(awk '{print $1}' "$f.sha256" 2>/dev/null)
  echo "$e" | grep -Eq '^[0-9a-f]{64}$' || return 1
  c=$TM/rescue-$1; rm -f "$c.new"; cp "$f" "$c.new" 2>/dev/null || return 1
  [ "$(sha256sum "$c.new" | awk '{print $1}')" = "$e" ] || { rm -f "$c.new"; return 1; }
  mv -f "$c.new" "$c" && echo "$c"
}
do_hardreset() { # $1=by
  if c=$(rescue_copy hardreset.sh); then sh "$c" "$1"; rc=$?
  elif [ -f "$D/hardreset.sh" ]; then log "rescue hardreset unverified; using $D/hardreset.sh"; sh "$D/hardreset.sh" "$1"; rc=$?
  else rc=9; fi
  log "hardreset returned rc=$rc (no reset happened)"
  echo "reboot FAILED rc=$rc (see logs/hardreset.log); power-cycle needed" > "$TM/rb"; emit "$TM/rb" reboot-failed
}

emit() { # $1=file $2=tag
  f=$1; tag=$2
  j=0; while [ ! -e "$R/dev/ttyGS0" ] && [ $j -lt 60 ]; do sleep 1; j=$((j+1)); done
  [ -e "$R/dev/ttyGS0" ] || { log "emit $tag: no ttyGS0"; return; }
  { printf '\n@@MK20 BEGIN %s@@\n' "$tag"; tail -c 16384 "$f"; printf '\n@@MK20 END %s@@\n' "$tag"; } > "$TM/emit.$$" 2>/dev/null
  ( exec cat "$TM/emit.$$" >> "$R/dev/ttyGS0" 2>/dev/null 9>&- ) & cp=$!
  ( exec 9>&-; sleep 10; kill $cp 2>/dev/null ) & kp=$!
  wait $cp 2>/dev/null; kill $kp 2>/dev/null
  rm -f "$TM/emit.$$"
}

N=0
runjob() { # $1=src script  $2=tag   (moves src away first => once; staged bytes re-verified)
  N=$((N+1)); tag="$2-$N"
  r=$TM/run-$tag.sh; out=$L/$2.log
  e=$(awk '{print $1}' "$1.sha256" 2>/dev/null)
  mv -f "$1" "$r"; rm -f "$1.sha256"
  [ -e "$1" ] && { log "run $tag: cannot consume $1 (FAT ro?) -> not run"; rm -f "$r"; sleep 30; return 1; }
  [ "$(sha256sum "$r" 2>/dev/null | awk '{print $1}')" = "$e" ] || { log "run $tag: staged copy sha mismatch, not run"; rm -f "$r"; return 1; }
  log "run $tag"
  { echo "== $tag uptime=$(cut -d' ' -f1 /proc/uptime)"; } > "$out"
  ( exec sh "$r" >> "$out" 2>&1 </dev/null 9>&- ) & jp=$!
  ( exec 9>&-; i=0; while [ $i -lt "${MK20_JOB_TIMEOUT:-300}" ]; do sleep 1; kill -0 $jp 2>/dev/null || exit 0; i=$((i+1)); done
    exec 8>"$TM/kwrite.lock"; w=0; while ! flock -n 8; do w=$((w+1)); [ $w = 1 ] && echo "TIMEOUT deferred: p4 write in progress" >> "$out"; sleep 2; kill -0 $jp 2>/dev/null || exit 0; done
    kill_tree $jp; echo "TIMEOUT: killed job process tree" >> "$out" ) & kp=$!
  wait $jp 2>/dev/null; rc=$?; kill $kp 2>/dev/null; echo "rc=$rc" >> "$out"
  [ "$(wc -c < "$out")" -gt 65536 ] && tail -c 65536 "$out" > "$out.t" && mv "$out.t" "$out"
  sync
  emit "$out" "$tag"
  log "done $tag"
}

[ "${MK20_WATCHER_LIB:-}" = 1 ] && return 0 2>/dev/null

exec 9>"$TM/watch.lock"
flock -n 9 || exit 0
echo $$ > "$TM/watch.pid"
[ -f "$WLOG" ] && [ "$(wc -c < "$WLOG")" -gt 32768 ] && tail -c 8192 "$WLOG" > "$WLOG.t" && mv "$WLOG.t" "$WLOG"
log "watcher v3 start pid=$$ home=$H"

wait_kd() { i=0; while ! pgrep -f KeyboardDevice >/dev/null 2>&1 && [ $i -lt 90 ]; do sleep 1; i=$((i+1)); done; sleep 8; }

(
  exec 9>&-
  wait_kd
  for f in "$D"/boot-once/*.sh; do
    [ -f "$f" ] || continue
    if shaok "$f"; then runjob "$f" "boot-$(basename "$f" .sh)"
    else log "boot-once sha FAIL $f"; mv -f "$f" "$f.bad"; fi
  done
  lrc=99
  rescue_copy hardreset.sh >/dev/null || log "no verified hardreset in $H"
  if c=$(rescue_copy loader.sh); then sh "$c" boot >> "$L/loader.log" 2>&1; lrc=$?; log "loader rc=$lrc"
  else log "no verified loader in $H (legacy mode)"; fi
  # rc 0: release running. rc 11: releases installed but none runnable -> stock + adb only (no legacy mutations).
  # rc 10/12/99: no release / disabled / no loader -> legacy boot-every jobs.
  if [ "$lrc" != 0 ]; then
    for f in "$D"/boot-every/*.sh; do
      [ "$lrc" = 11 ] && [ "$(basename "$f")" != adb.sh ] && continue
      [ -f "$f" ] || continue
      [ -f "$S/mk20-no-bootevery" ] && break
      if shaok "$f"; then cp -f "$f" "$TM/every-$(basename "$f")"; cp -f "$f.sha256" "$TM/every-$(basename "$f").sha256"
        runjob "$TM/every-$(basename "$f")" "every-$(basename "$f" .sh)"
      else log "boot-every sha FAIL $f"; fi
    done
  fi
  sleep 110
  for f in "$D"/boot-delayed/*.sh; do
    [ -f "$f" ] || continue
    [ -f "$S/mk20-no-watch" ] && break
    if shaok "$f"; then runjob "$f" "delayed-$(basename "$f" .sh)"
    else log "delayed sha FAIL $f"; mv -f "$f" "$f.bad"; fi
  done
  echo "watcher alive pid=$$ uptime=$(cut -d' ' -f1 /proc/uptime) uname=$(uname -r) loader_rc=$lrc" > "$TM/hb"
  emit "$TM/hb" heartbeat
) &

fat_budget() {
  for f in "$L"/*.log; do [ -f "$f" ] && [ "$(wc -c < "$f")" -gt 65536 ] && tail -c 16384 "$f" > "$f.t" && mv -f "$f.t" "$f"; done
  fr=$(df -k "$S" 2>/dev/null | awk 'NR==2{print $4}')
  if [ -n "$fr" ] && [ "$fr" -lt 1024 ]; then
    for f in "$L"/*.log; do [ -f "$f" ] && tail -c 4096 "$f" > "$f.t" && mv -f "$f.t" "$f"; done
    rm -f "$L"/*.log.t "$S"/job.sh.bad; log "FAT low (${fr} KiB): logs trimmed"
  fi
}
fat_budget; FB=0
while :; do
  FB=$((FB+1)); [ $FB -ge 30 ] && { FB=0; fat_budget; }
  [ -f "$S/mk20-no-watch" ] && { log "kill switch; exit"; exit 0; }
  if [ -f "$S/cmd-reboot" ]; then
    rm -f "$S/cmd-reboot"; [ -e "$S/cmd-reboot" ] && { log "cmd-reboot: cannot remove (FAT ro?) -> ignored"; sleep 30; continue; }; log "cmd-reboot"; echo "rebooting $(cut -d' ' -f1 /proc/uptime)" > "$TM/rb"
    emit "$TM/rb" reboot; sync; sleep 1
    do_hardreset watcher
  fi
  if [ -f "$S/cmd-rollback" ]; then
    rm -f "$S/cmd-rollback"; [ -e "$S/cmd-rollback" ] && { log "cmd-rollback: cannot remove (FAT ro?) -> ignored"; sleep 30; continue; }; log "cmd-rollback"
    if c=$(rescue_copy loader.sh); then sh "$c" rollback > "$TM/rbk" 2>&1; rc=$?; else echo "no verified loader" > "$TM/rbk"; rc=9; fi
    emit "$TM/rbk" rollback; [ $rc = 0 ] && { sync; do_hardreset rollback; }
  fi
  if [ -f "$S/cmd-ping" ]; then
    rm -f "$S/cmd-ping"
    echo "pong pid=$$ uptime=$(cut -d' ' -f1 /proc/uptime) kd=$(pgrep -f KeyboardDevice | head -1) adbd=$(pgrep -x adbd) home=$H" > "$TM/pong"
    emit "$TM/pong" ping
  fi
  if [ -f "$S/job.sh" ] && [ -f "$S/job.sh.sha256" ]; then
    if shaok "$S/job.sh"; then runjob "$S/job.sh" job
    else
      sleep 2
      if shaok "$S/job.sh"; then runjob "$S/job.sh" job
      else log "job sha FAIL"; mv -f "$S/job.sh" "$S/job.sh.bad"; rm -f "$S/job.sh.sha256"
        echo "job sha256 mismatch" > "$TM/jb"; emit "$TM/jb" job-shafail; fi
    fi
  fi
  sleep 2
done
