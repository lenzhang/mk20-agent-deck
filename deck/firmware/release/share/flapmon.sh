#!/bin/sh
# MK20 flap monitor (1.1.2+; was an inline heredoc in gadget.sh). Started detached by gadget.sh:
#   flapmon.sh <udc-state-file> <window_s> <drops> <log> <stopfile> -- <rollback command...>
# Counts link drops; >= <drops> within <window_s> -> touch the gadget.busy marker and run the rollback command once,
# then exit. A drop is: configured -> anything but configured/suspended, or a SHORT suspend (< MK20_FLAP_SUSP_S,
# default 5 s) that ends in anything but configured (a bus reset/glitch the poll caught as idle). A long suspend
# (Mac sleep) is not a flap: suspend, resume and a resume reset / unplug after it are logged, not counted.
# Single instance (flock on fd 8, not inherited by the rollback). The stopfile is re-checked right before rollback.
# The busy marker is cleared after every rollback attempt. A failed rollback is retried (MK20_FLAP_TRIES, default 3,
# MK20_FLAP_RETRY_S apart); if all fail, the persistent mk20-no-composite flag is set and the watcher hard-resets
# (cmd-reboot), so the next boot runs stock until the flag is deleted.
# The rollback command bounds itself (gadget.sh --rollback-now: transition mutex + owner-local supervisor that kills
# and reaps its own cleanup tree after MK20_RB_TIMEOUT), so a hung rollback returns rc!=0 here and is retried/escalated.
# Writes the ready marker (MK20_FLAP_READY, default /tmp/mk20/flapmon.ready) once the lock is held.
# Test env: MK20_UPTIME_CMD (seconds), MK20_FLAP_POLL (default 0.3), MK20_BUSY, MK20_FLAP_LOCK, MK20_FLAP_SUSP_S.
[ $# -ge 7 ] && [ "$6" = -- ] || { echo "usage: flapmon.sh state window drops log stopfile -- cmd..." >&2; exit 2; }
U=$1; W=$2; N=$3; LOG=$4; STOP=$5; shift 6
case "$W$N" in ''|*[!0-9]*) echo "flapmon: window/drops must be integers" >&2; exit 2;; esac
BUSY=${MK20_BUSY:-/tmp/mk20/gadget.busy}; SUSP=${MK20_FLAP_SUSP_S:-5}
LOCK=${MK20_FLAP_LOCK:-/tmp/mk20/flapmon.lock}; mkdir -p "$(dirname "$LOCK")"
exec 8>"$LOCK"; i=0; while ! flock -n 8; do i=$((i+1)); [ $i -lt 10 ] && { sleep 0.5; continue; }
  { echo "flapmon $(cut -d. -f1 /proc/uptime): another monitor holds $LOCK" >> "$LOG"; exit 3; }; done
READY=${MK20_FLAP_READY:-/tmp/mk20/flapmon.ready}; mkdir -p "$(dirname "$READY")"; : > "$READY"   # lock held: gadget.sh waits for this
up() { if [ -n "${MK20_UPTIME_CMD:-}" ]; then $MK20_UPTIME_CMD; else cut -d. -f1 /proc/uptime; fi; }
prev=$(cat "$U" 2>/dev/null); times=""; sus=""
while [ ! -f "$STOP" ]; do
  s=$(cat "$U" 2>/dev/null); now=""
  if [ "$s" != "$prev" ]; then
    if [ "$prev" = configured ] && [ "$s" = suspended ]; then
      sus=$(up); echo "flapmon $sus: host suspend" >> "$LOG"
    elif [ "$prev" = configured ]; then
      now=$(up)
    elif [ "$prev" = suspended ]; then
      t=$(up); d=$((t - ${sus:-$t}))
      if [ "$s" = configured ]; then echo "flapmon $t: host resume after ${d}s" >> "$LOG"
      elif [ -n "$sus" ] && [ "$d" -lt "$SUSP" ]; then now=$t
      else echo "flapmon $t: '$s' after ${d}s suspend (resume reset/unplug, not counted)" >> "$LOG"; fi
      sus=""
    fi
  fi
  if [ -n "$now" ]; then
    nt=""; cnt=0
    for t in $times $now; do [ $((now-t)) -le "$W" ] && { nt="$nt $t"; cnt=$((cnt+1)); }; done; times=$nt
    echo "flapmon $now: link drop ($s), $cnt in ${W}s" >> "$LOG"
    if [ "$cnt" -ge "$N" ]; then
      [ -f "$STOP" ] && exit 0
      echo "flapmon $now: FLAPPING -> rollback" >> "$LOG"
      try=1
      while :; do
        # r5 #6: only clear a marker we created, and never after a refusal (rc 4: another owner holds the mutex and
        # owns gadget.busy; it clears it itself under the mutex). rc 5 = stale generation: our composite is gone.
        mine=0; [ -e "$BUSY" ] || { touch "$BUSY"; mine=1; }
        "$@" 8>&-; rc=$?
        [ "$rc" = 4 ] || [ "$mine" = 0 ] || rm -f "$BUSY"
        [ "$rc" = 0 ] && exit 0
        [ "$rc" = 5 ] && { echo "flapmon $(up): rollback stale (rc 5, newer composite) -> exit" >> "$LOG"; exit 0; }
        [ "$rc" = 3 ] && { echo "flapmon $(up): rollback STUCK (rc 3, it escalated itself) -> no retry" >> "$LOG"; exit 1; }
        echo "flapmon $(up): rollback attempt $try failed rc=$rc" >> "$LOG"
        [ "$try" -ge "${MK20_FLAP_TRIES:-3}" ] && break
        try=$((try+1)); sleep "${MK20_FLAP_RETRY_S:-20}"
      done
      # still not stock: persist stock mode (flag file, read at boot) and hard-reset via the rescue watcher
      echo "flapmon $(up): rollback failed $try times -> ${MK20_FLAP_FLAG:-/mnt/SDCARD/mk20-no-composite} + reboot" >> "$LOG"
      touch "${MK20_FLAP_FLAG:-/mnt/SDCARD/mk20-no-composite}" && touch "${MK20_FLAP_REBOOT:-/mnt/SDCARD/cmd-reboot}"; sync
      exit 1
    fi
  fi
  prev=$s; sleep "${MK20_FLAP_POLL:-0.3}"
done
