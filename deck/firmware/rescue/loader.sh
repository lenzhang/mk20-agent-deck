#!/bin/sh
# MK20 Agent firmware: release loader (rescue tier, not part of any release).
#   boot            pick the active release (A/B pointer slots), handle pending/attempts/rollback, verify every
#                   file against MANIFEST, copy to tmpfs, launch `sbin/mk20d boot` detached.
#                   rc 0 = release launched, 10 = no release installed (legacy mode), 11 = releases present but
#                   none runnable (stock + rescue), 12 = disabled by flag
#   confirm <v>     mark pending release <v> good (called by mk20d after its self-test passes)
#   switch <v>      make installed release <v> current + pending (installer); refuses while one is pending
#   rollback        make the other valid slot current (manual, cmd-rollback); clears pending
#   deadline <v>    (internal) one hard reset if pending <v> is not confirmed by MK20_DEADLINE s uptime
#   status          print state
#   verify <v>      verify release <v> (no copy)
# Layout (ext4): /data/mk20/{releases/<semver>/,slots/{A,B},state/}. Busybox 1.27 ash compatible.
# Test env: MK20_ROOT (path prefix), MK20_P4_SHA, MK20_UPTIME_CMD, MK20_DEADLINE, MK20_HR (hardreset cmd).
R=${MK20_ROOT:-}
B=$R/data/mk20; REL=$B/releases; SL=$B/slots; ST=$B/state
S=$R/mnt/SDCARD; T=$R/tmp/mk20; LOG=$S/mk20/logs/loader.log
DEADLINE=${MK20_DEADLINE:-600}
mkdir -p "$SL" "$ST" "$T" "$S/mk20/logs" 2>/dev/null
up() { if [ -n "${MK20_UPTIME_CMD:-}" ]; then $MK20_UPTIME_CMD; else cut -d. -f1 /proc/uptime; fi; }
log() { echo "$(up) loader $*" >> "$LOG" 2>/dev/null; }
sha() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1; }
is_semver() { echo "$1" | grep -Eq '^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$'; }
is_hex64() { echo "$1" | grep -Eq '^[0-9a-f]{64}$'; }
is_num() { case "$1" in ''|*[!0-9]*) return 1;; esac; [ ${#1} -le 9 ]; }
wstate() { # $1=file $2=value: tmp + fsync + rename (ext4)
  printf '%s\n' "$2" > "$1.tmp" && sync && mv -f "$1.tmp" "$1" && sync; }
rstate() { [ -f "$ST/$1" ] && head -n 1 "$ST/$1" 2>/dev/null; }
kv() { sed -n "s/^$1=//p" "$2" 2>/dev/null | head -n 1; }

# slot_read A|B -> "gen version" if the slot is valid (checksum, semver, manifest digest matches)
slot_read() {
  f=$SL/$1; [ -f "$f" ] || return 1
  [ "$(wc -c < "$f")" -le 512 ] || return 1
  g=$(kv gen "$f"); v=$(kv version "$f"); m=$(kv manifest_sha "$f"); s=$(kv sum "$f")
  is_num "$g" && is_semver "$v" && is_hex64 "$m" || return 1
  c=$(printf 'gen=%s\nversion=%s\nmanifest_sha=%s\n' "$g" "$v" "$m" | sha256sum | cut -d' ' -f1)
  [ "$c" = "$s" ] || return 1
  [ "$(sha "$REL/$v/MANIFEST")" = "$m" ] || return 1
  echo "$g $v"
}
# pick -> "slot gen version" of the highest valid generation
pick() {
  a=$(slot_read A); b=$(slot_read B); ga=${a%% *}; gb=${b%% *}
  if [ -n "$a" ] && { [ -z "$b" ] || [ "$ga" -ge "$gb" ]; }; then echo "A $a"; return 0; fi
  [ -n "$b" ] && { echo "B $b"; return 0; }
  return 1
}
slot_write() { # $1=version: write the inactive (lower/invalid) slot with gen = max+1
  m=$(sha "$REL/$1/MANIFEST"); is_hex64 "$m" || return 1
  a=$(slot_read A); b=$(slot_read B); ga=${a%% *}; gb=${b%% *}; ga=${ga:-0}; gb=${gb:-0}
  if [ "$ga" -le "$gb" ]; then t=A; n=$((gb+1)); else t=B; n=$((ga+1)); fi
  { printf 'gen=%s\nversion=%s\nmanifest_sha=%s\n' "$n" "$1" "$m"
    printf 'sum=%s\n' "$(printf 'gen=%s\nversion=%s\nmanifest_sha=%s\n' "$n" "$1" "$m" | sha256sum | cut -d' ' -f1)"; } > "$SL/.$t.tmp" || return 1
  sync; mv -f "$SL/.$t.tmp" "$SL/$t"; sync
  log "slot $t <- $1 (gen $n)"
}
p4sha() {
  [ -n "${MK20_P4_SHA:-}" ] && { echo "$MK20_P4_SHA"; return; }
  [ -s "$T/p4.sha" ] || sha "$R/dev/by-name/boot" > "$T/p4.sha"
  cat "$T/p4.sha"
}
# verify_release <v> [dest]: every MANIFEST file (sha, safe relative path); kernel allow-list; optional verified copy
verify_release() {
  v=$1; d=$REL/$v; M=$d/MANIFEST; dest=${2:-}
  is_semver "$v" && [ -f "$M" ] || { echo "no manifest"; return 1; }
  [ "$(kv version "$M")" = "$v" ] || { echo "manifest version mismatch"; return 1; }
  k=$(kv kernels "$M"); p=$(p4sha)
  case ",$k," in *",$p,"*|",any,") ;; *) echo "kernel $p not in allow-list"; return 2;; esac
  [ -n "$dest" ] && { rm -rf "$dest"; mkdir -p "$dest" || return 1; cp -f "$M" "$dest/MANIFEST"; }
  n=0
  # file lines: "<sha256> <octal mode> <relative path>" after the "--" separator
  sed -n '/^--$/,$p' "$M" | sed 1d > "$T/.mf.$$"
  while read -r fs fm fp; do
    [ -n "$fs" ] || continue
    is_hex64 "$fs" || { echo "bad sha line"; rm -f "$T/.mf.$$"; return 1; }
    case "$fp" in ''|/*|*..*|*' '*) echo "unsafe path $fp"; rm -f "$T/.mf.$$"; return 1;; esac
    case "$fm" in [0-7][0-7][0-7]|[0-7][0-7][0-7][0-7]) ;; *) echo "bad mode $fm"; rm -f "$T/.mf.$$"; return 1;; esac
    [ "$(sha "$d/$fp")" = "$fs" ] || { echo "sha mismatch $fp"; rm -f "$T/.mf.$$"; return 1; }
    if [ -n "$dest" ]; then mkdir -p "$dest/$(dirname "$fp")"; cp -f "$d/$fp" "$dest/$fp"
      [ "$(sha "$dest/$fp")" = "$fs" ] || { echo "copy mismatch $fp"; rm -f "$T/.mf.$$"; return 1; }; chmod "$fm" "$dest/$fp"; fi
    n=$((n+1))
  done < "$T/.mf.$$"
  rm -f "$T/.mf.$$"
  [ "$n" -gt 0 ] || { echo "empty manifest"; return 1; }
  [ -f "$d/sbin/mk20d" ] || { echo "no sbin/mk20d"; return 1; }
  echo "ok $n files"
}
installed_any() { [ -f "$SL/A" ] || [ -f "$SL/B" ]; }   # a slot was ever written
# rollback_to_good <reason> -> 0 if switched (sets CUR), 1 if impossible
rollback_to_good() {
  g=$(rstate good); bad=$CUR
  wstate "$ST/failed-$bad" "$1"; rm -f "$ST/pending" "$ST/attempts"
  if is_semver "$g" && [ "$g" != "$bad" ] && [ -f "$REL/$g/MANIFEST" ]; then
    slot_write "$g" && { log "ROLLBACK $bad -> $g: $1"; wstate "$ST/last-rollback" "$bad->$g $1"; CUR=$g; return 0; }
  fi
  log "ROLLBACK $bad impossible (good='$g'): $1"; wstate "$ST/last-rollback" "$bad->none $1"; return 1
}

cmd=${1:-status}
case "$cmd" in
boot)
  [ -f "$S/mk20-no-release" ] && { log "flag mk20-no-release -> legacy"; exit 12; }
  P=$(pick) || { if installed_any; then log "no valid slot -> stock+rescue"; exit 11; fi; log "no release installed -> legacy"; exit 10; }
  CUR=${P##* }; PEND=$(rstate pending)
  if [ "$CUR" = "$PEND" ]; then
    a=$(rstate attempts); is_num "$a" || a=0; a=$((a+1)); wstate "$ST/attempts" "$a"   # counted BEFORE launch
    log "pending $CUR attempt $a"
    if [ "$a" -gt "${MK20_MAX_ATTEMPTS:-1}" ]; then rollback_to_good "not confirmed after $((a-1)) attempt(s)" || exit 11; fi
  fi
  r=$(verify_release "$CUR" "$T/rel"); rc=$?
  if [ $rc != 0 ]; then
    log "verify $CUR failed: $r"
    if [ "$CUR" = "$(rstate pending)" ] && rollback_to_good "verify: $r"; then
      r=$(verify_release "$CUR" "$T/rel") || { log "verify good $CUR failed: $r -> stock+rescue"; exit 11; }
    else exit 11; fi
  fi
  log "launch $CUR ($r)"
  wstate "$T/release-active" "$CUR"
  ( trap '' HUP INT; MK20_REL=$T/rel MK20_VERSION=$CUR MK20_HOME=$B exec sh "$T/rel/sbin/mk20d" boot </dev/null >>"$LOG" 2>&1 ) &
  if [ "$CUR" = "$(rstate pending)" ]; then
    cp -f "$0" "$T/loader-deadline.sh" 2>/dev/null
    ( trap '' HUP INT; exec sh "$T/loader-deadline.sh" deadline "$CUR" </dev/null >/dev/null 2>&1 ) &
  fi
  exit 0;;
deadline)
  v=${2:-}; is_semver "$v" || exit 1
  while [ "$(up)" -lt "$DEADLINE" ]; do
    [ "$(rstate pending)" = "$v" ] || exit 0
    sleep "${MK20_POLL:-5}"
  done
  [ "$(rstate pending)" = "$v" ] || exit 0
  [ -f "$ST/deadline-reset-$v" ] && { log "deadline $v: reset already used -> no reset (next boot rolls back)"; exit 0; }
  wstate "$ST/deadline-reset-$v" "$(up)"; log "deadline $v: not confirmed by ${DEADLINE}s -> one hard reset"
  ${MK20_HR:-sh $S/mk20/hardreset.sh} loader-deadline
  exit 0;;
confirm)
  v=${2:-}; is_semver "$v" || exit 1
  if [ "$(rstate pending)" = "$v" ]; then wstate "$ST/good" "$v"; rm -f "$ST/pending" "$ST/attempts"; log "confirmed $v (good)"; exit 0; fi
  [ "$(rstate good)" = "$v" ] && exit 0
  log "confirm $v refused (pending='$(rstate pending)')"; exit 1;;
switch)
  v=${2:-}; is_semver "$v" || { echo "bad version"; exit 1; }
  [ -n "$(rstate pending)" ] && { echo "refused: $(rstate pending) is pending"; exit 1; }
  P=$(pick); [ "${P##* }" = "$v" ] && { echo "refused: $v already current"; exit 1; }
  r=$(verify_release "$v") || { echo "refused: $r"; exit 1; }
  # first install ever: no good yet -> the current (if any) becomes good implicitly only via confirm
  slot_write "$v" || { echo "slot write failed"; exit 1; }
  wstate "$ST/pending" "$v"; wstate "$ST/attempts" 0; rm -f "$ST/deadline-reset-$v" "$ST/failed-$v"
  echo "ok: $v pending"; exit 0;;
rollback)
  P=$(pick) || { echo "no current"; exit 1; }; cur=${P##* }; sl=${P%% *}
  [ "$sl" = A ] && o=B || o=A; O=$(slot_read $o) || { echo "no valid other slot"; exit 1; }
  ov=${O##* }; [ "$ov" != "$cur" ] || { echo "other slot is the same version"; exit 1; }
  slot_write "$ov" || exit 1; rm -f "$ST/pending" "$ST/attempts"; wstate "$ST/last-rollback" "$cur->$ov manual"
  log "manual rollback $cur -> $ov"; echo "ok: $cur -> $ov"; exit 0;;
verify) verify_release "${2:-}"; exit $?;;
status)
  echo "current=$(pick 2>/dev/null) good=$(rstate good) pending=$(rstate pending) attempts=$(rstate attempts) active=$(cat "$T/release-active" 2>/dev/null) last_rollback=$(rstate last-rollback)"; exit 0;;
*) echo "usage: $0 boot|confirm <v>|switch <v>|rollback|deadline <v>|status|verify <v>"; exit 2;;
esac
