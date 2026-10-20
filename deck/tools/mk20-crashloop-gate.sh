#!/bin/bash
# K4 release gate: crash-loop -> safe mode -> exit-safe-mode hook -> normal. One device run, evidence to $E.
#   MK20_ADB=~/mk20-backup/mkadb.sh tools/mk20-crashloop-gate.sh <version>   (good release, normal mode)
# Kills only mk20-kbd (SIGKILL, never pid 1/adbd/USB), 3x within the 300 s window. No reboot, no USB change, no GD32.
export LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8
V=${1:?version}; A=${MK20_ADB:?MK20_ADB (MK20-only adb wrapper)}
E=${MK20_EVIDENCE_DIR:-$HOME/mk20-backup}/k4-crashloop-${V}-$(date +%Y%m%d-%H%M%S); mkdir -p "${E}"
adbs(){ perl -e 'alarm 60; exec @ARGV' "${A}" shell "$1" 2>&1 | tr -d '\r'; }
log(){ echo "$(date '+%F %T') $*" | tee -a "${E}/run.log"; }
snap(){ # $1 = tag
  adbs 'cat /tmp/mk20/maint/state.json; echo; cat /run/mk20/kbd.status; echo; pidof mk20-kbd; cat /proc/uptime' > "${E}/$1-state.txt"
  adbs "/data/mk20/releases/${V}/sbin/mk20d selftest" > "${E}/$1-selftest.txt"
  adbs "/data/mk20/releases/${V}/sbin/mk20d health" > "${E}/$1-health.json"; }
# pidof matches the process name only: `pgrep -f "bin/mk20-kbd --run"` also matched the adb `sh -c` running it, so a
# transient shell pid looked like a restart during backoff (first run 23:01: third kill hit a dead pid, 2/3 counted).
kbdpid(){ adbs 'for p in $(pidof mk20-kbd); do grep -q -- "--run" /proc/$p/cmdline 2>/dev/null && echo $p; done | head -n 1' | tr -dc '0-9'; }
wait_new(){ # wait until mk20-kbd runs with a pid != $1 (max $2 s)
  local i=0 p; while [ ${i} -lt "$2" ]; do p=$(kbdpid); [ -n "${p}" ] && [ "${p}" != "$1" ] && { echo "${p}"; return 0; }; sleep 2; i=$((i+2)); done; return 1; }
adbs 'sh /data/mk20/rescue/loader.sh status' | grep -q "good=${V} pending= " || { log "device not on good=${V}; abort"; exit 2; }
snap 0-pre; grep -q '"mode":"normal"' "${E}/0-pre-state.txt" || { log "not normal before test; abort"; exit 2; }
log "pre: $(head -1 "${E}/0-pre-state.txt")"
p=$(kbdpid); [ -n "${p}" ] || { log "mk20-kbd not running; abort"; exit 2; }
for n in 1 2 3; do
  log "kill ${n}: SIGKILL mk20-kbd pid ${p}"
  adbs "kill -9 ${p}" > /dev/null
  np=$(wait_new "${p}" 90) || { log "no restart within 90 s after kill ${n}"; snap "fail-${n}"; break; }
  sleep 3; [ "$(kbdpid)" = "${np}" ] || { log "pid ${np} did not stay up"; np=$(kbdpid); }
  log "restarted as ${np}"; p=${np}
done
sleep 5; snap 1-after-kills
log "after kills: $(head -1 "${E}/1-after-kills-state.txt")"
SAFE=0; grep -q '"mode":"safe"' "${E}/1-after-kills-state.txt" && grep -q '"reason":"crash-loop"' "${E}/1-after-kills-state.txt" && SAFE=1
log "safe latched=${SAFE}; selftest: $(tr '\n' ' ' < "${E}/1-after-kills-selftest.txt" | cut -c1-300)"
# exit-safe-mode per MAINT-HOOKS.md: exclusive temp in the same inbox, 0600, fsync, atomic rename, empty body
adbs 'cd /tmp/mk20/maint && t=.req.$$ && umask 077 && : > "$t" && sync && mv -f "$t" exit-safe-mode && echo published' > "${E}/hook.txt"
log "hook: $(cat "${E}/hook.txt")"
i=0; while [ ${i} -lt 40 ]; do adbs 'cat /tmp/mk20/maint/state.json' | grep -q '"mode":"normal"' && break; sleep 2; i=$((i+2)); done
sleep 5; snap 2-after-exit
log "after exit-safe-mode: $(head -1 "${E}/2-after-exit-state.txt")"
NORM=0; grep -q '"mode":"normal"' "${E}/2-after-exit-state.txt" && NORM=1
adbs 'dmesg' > "${E}/dmesg.txt"; adbs 'tail -n 80 /data/mk20/logs/mk20d.log' > "${E}/mk20d-log.txt"
BAD=$(grep -cE 'WARNING:|Oops|BUG:|Unable to handle|list_(add|del) corruption' "${E}/dmesg.txt")
ST_SAFE=$(grep -c '^FAIL' "${E}/1-after-kills-selftest.txt"); ST_NORM=$(grep -c '^FAIL' "${E}/2-after-exit-selftest.txt")
HID1=$(grep -o '"hid_open":[0-9-]*' "${E}/1-after-kills-state.txt" | head -1)
R="safe_latched=${SAFE} selftest_fail_in_safe=${ST_SAFE} hid_in_safe=${HID1} back_to_normal=${NORM} selftest_fail_after=${ST_NORM} kernel_warn=${BAD}"
V2=FAIL; [ "${SAFE}" = 1 ] && [ "${NORM}" = 1 ] && [ "${ST_SAFE}" = 0 ] && [ "${ST_NORM}" = 0 ] && [ "${BAD}" = 0 ] && V2=PASS
printf '# K4 crash-loop gate (%s) — %s\n\n%s\n\nEvidence: %s\n' "${V}" "${V2}" "${R}" "${E}" > "${E}/RESULT.md"
log "RESULT ${V2} ${R}"
