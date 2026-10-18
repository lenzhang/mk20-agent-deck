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
#   install <tar> <sha256> [--no-switch]
#                   M3 installer: pinned tarball sha, free-space check, safe tar listing (one top dir mk20-fw-<v>/,
#                   regular files/dirs only), extract to releases/.incoming-<v>, verify MANIFEST + no unlisted files,
#                   atomic rename to releases/<v>, then `switch <v>` (pending; next boot = probation) and prune.
#                   Idempotent: an identical installed <v> is reused. rc 0 ok, 1 refused/failed, 13 busy.
#   prune           drop releases that are not good/pending/in a slot (keeps MK20_KEEP=1 extra newest) + leftovers
#   uninstall       back to legacy mode (slots moved aside; next boot = stock ACM+KD+adb + rescue watcher)
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
oneline() { [ "$(printf '%s' "$1" | wc -l)" = 0 ]; }
is_semver() { oneline "$1" && echo "$1" | grep -Eq '^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$'; }
is_hex64() { oneline "$1" && echo "$1" | grep -Eq '^[0-9a-f]{64}$'; }
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
  sync; mv -f "$SL/.$t.tmp" "$SL/$t" || return 1; sync
  [ "$(slot_read $t)" = "$n $1" ] || { log "slot $t write did not verify"; return 1; }
  log "slot $t <- $1 (gen $n)"
}
p4sha() {
  [ -n "${MK20_P4_SHA:-}" ] && { echo "$MK20_P4_SHA"; return; }
  [ -s "$T/p4.sha" ] || sha "$R/dev/by-name/boot" > "$T/p4.sha"
  cat "$T/p4.sha"
}
# verify_release <v> [dest]: every MANIFEST file (sha, safe relative path); kernel allow-list; optional verified copy
verify_release() { verify_dir "$REL/$1" "$1" "${2:-}"; }
# verify_dir <dir> <v> [dest]
verify_dir() {
  d=$1; v=$2; M=$d/MANIFEST; dest=${3:-}
  is_semver "$v" && [ -f "$M" ] || { echo "no manifest"; return 1; }
  [ "$(kv version "$M")" = "$v" ] || { echo "manifest version mismatch"; return 1; }
  k=$(kv kernels "$M"); p=$(p4sha)
  is_hex64 "$p" || { echo "cannot read running p4 sha"; return 2; }
  case ",$k," in *",$p,"*) ;; *) echo "kernel $p not in allow-list"; return 2;; esac
  if [ -n "$dest" ]; then rm -rf "$dest"; [ ! -e "$dest" ] && mkdir -p "$dest" && cp "$M" "$dest/MANIFEST" || { echo "cannot stage $dest"; return 1; }; fi
  n=0
  # file lines: "<sha256> <octal mode> <relative path>" after the "--" separator
  sed -n '/^--$/,$p' "$M" | sed 1d > "$T/.mf.$$"
  if LC_ALL=C grep -q '[^ -~]' "$T/.mf.$$"; then echo "control/non-ASCII chars in manifest"; rm -f "$T/.mf.$$"; return 1; fi
  [ "$(cut -d' ' -f3 "$T/.mf.$$" | sort | uniq -d | wc -l)" = 0 ] || { echo "duplicate paths"; rm -f "$T/.mf.$$"; return 1; }
  grep -q ' sbin/mk20d$' "$T/.mf.$$" || { echo "sbin/mk20d not listed"; rm -f "$T/.mf.$$"; return 1; }
  while read -r fs fm fp; do
    [ -n "$fs" ] || continue
    is_hex64 "$fs" || { echo "bad sha line"; rm -f "$T/.mf.$$"; return 1; }
    case "$fp" in ''|/*|*..*|*' '*|*//*|./*) echo "unsafe path $fp"; rm -f "$T/.mf.$$"; return 1;; esac
    case "$fm" in [0-7][0-7][0-7]) ;; *) echo "bad mode $fm (no setuid/sticky)"; rm -f "$T/.mf.$$"; return 1;; esac
    [ -f "$d/$fp" ] && [ ! -L "$d/$fp" ] || { echo "missing or symlink $fp"; rm -f "$T/.mf.$$"; return 1; }
    [ "$(sha "$d/$fp")" = "$fs" ] || { echo "sha mismatch $fp"; rm -f "$T/.mf.$$"; return 1; }
    if [ -n "$dest" ]; then
      mkdir -p "$dest/$(dirname "$fp")" && cp "$d/$fp" "$dest/$fp" && [ "$(sha "$dest/$fp")" = "$fs" ] && chmod "$fm" "$dest/$fp" \
        || { echo "copy failed/mismatch $fp"; rm -f "$T/.mf.$$"; return 1; }; fi
    n=$((n+1))
  done < "$T/.mf.$$"
  rm -f "$T/.mf.$$"
  [ "$n" -gt 0 ] || { echo "empty manifest"; return 1; }
  if [ -n "$dest" ]; then [ -f "$dest/sbin/mk20d" ] || { echo "no sbin/mk20d"; return 1; }; else [ -f "$d/sbin/mk20d" ] || { echo "no sbin/mk20d"; return 1; }; fi
  echo "ok $n files"
}
installed_any() { [ -f "$SL/A" ] || [ -f "$SL/B" ]; }   # a slot was ever written
# rollback_to_good <reason> -> 0 if switched (sets CUR), 1 if impossible
rollback_to_good() {
  g=$(rstate good); bad=$CUR
  wstate "$ST/failed-$bad" "$1"
  # flip the slot FIRST; pending is cleared only once good is committed (a crash in between re-runs this rollback)
  if is_semver "$g" && [ "$g" != "$bad" ] && [ -f "$REL/$g/MANIFEST" ] && slot_write "$g"; then
    rm -f "$ST/pending" "$ST/attempts"; log "ROLLBACK $bad -> $g: $1"; wstate "$ST/last-rollback" "$bad->$g $1"; CUR=$g; return 0
  fi
  rm -f "$ST/pending" "$ST/attempts"   # failed-$bad stays: it can never run as trusted (only good runs non-pending)
  log "ROLLBACK $bad impossible (good='$g'): $1"; wstate "$ST/last-rollback" "$bad->none $1"; return 1
}

cmd=${1:-status}
case "$cmd" in
boot)
  exec 7>"$T/loader.lock"; flock -n 7 || { log "boot: another loader is running"; exit 13; }
  [ -f "$S/mk20-no-release" ] && { log "flag mk20-no-release -> legacy"; exit 12; }
  P=$(pick) || { if installed_any; then log "no valid slot -> stock+rescue"; exit 11; fi; log "no release installed -> legacy"; exit 10; }
  CUR=${P##* }; PEND=$(rstate pending)
  GOOD=$(rstate good)
  if [ -n "$PEND" ] && { [ "$CUR" != "$PEND" ] || [ "$PEND" = "$GOOD" ]; }; then log "stale pending $PEND (current $CUR) cleared"; rm -f "$ST/pending" "$ST/attempts"; PEND=""; fi
  # INVARIANT: a release runs either as `good` or as `pending` on probation. Anything else -> good, else stock.
  if [ "$CUR" != "$PEND" ] && [ "$CUR" != "$GOOD" ]; then
    log "current $CUR is neither good ('$GOOD') nor pending -> switching to good"
    if is_semver "$GOOD" && [ -f "$REL/$GOOD/MANIFEST" ] && [ ! -f "$ST/failed-$GOOD" ] && slot_write "$GOOD"; then CUR=$GOOD; else log "no good release -> stock+rescue"; exit 11; fi
  fi
  if [ "$CUR" = "$PEND" ]; then
    a=$(rstate attempts); is_num "$a" || a=0; a=$((a+1))
    wstate "$ST/attempts" "$a" && [ "$(rstate attempts)" = "$a" ] || { log "cannot record attempt (ro /data?) -> stock+rescue"; exit 11; }   # counted BEFORE launch
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
  ( trap '' HUP INT; MK20_REL=$T/rel MK20_VERSION=$CUR MK20_HOME=$B exec sh "$T/rel/sbin/mk20d" boot </dev/null >/dev/null 2>&1 7>&- ) &
  if [ "$CUR" = "$(rstate pending)" ]; then
    cp -f "$0" "$T/loader-deadline.sh" 2>/dev/null
    dl=$(( $(up) + 480 )); [ "$dl" -lt "$DEADLINE" ] && dl=$DEADLINE
    ( trap '' HUP INT; MK20_DEADLINE=$dl exec sh "$T/loader-deadline.sh" deadline "$CUR" </dev/null >/dev/null 2>&1 7>&- ) &
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
  wstate "$ST/deadline-reset-$v" "$(up)" && [ -f "$ST/deadline-reset-$v" ] || { log "deadline $v: cannot record reset marker -> no reset"; exit 0; }
  log "deadline $v: not confirmed by ${DEADLINE}s -> one hard reset"
  if [ -n "${MK20_HR:-}" ]; then $MK20_HR loader-deadline
  elif [ -f "$T/rescue-hardreset.sh" ]; then sh "$T/rescue-hardreset.sh" loader-deadline
  else log "deadline $v: no verified hardreset staged -> no reset (next boot rolls back)"; fi
  exit 0;;
confirm)
  v=${2:-}; is_semver "$v" || exit 1
  if [ "$(rstate pending)" = "$v" ]; then
    wstate "$ST/good" "$v" && [ "$(rstate good)" = "$v" ] || { log "confirm $v: cannot write good"; exit 1; }
    rm -f "$ST/pending" "$ST/attempts"; log "confirmed $v (good)"; exit 0; fi
  [ "$(rstate good)" = "$v" ] && exit 0
  log "confirm $v refused (pending='$(rstate pending)')"; exit 1;;
switch)
  v=${2:-}; is_semver "$v" || { echo "bad version"; exit 1; }
  [ -n "$(rstate pending)" ] && { echo "refused: $(rstate pending) is pending"; exit 1; }
  P=$(pick); [ "${P##* }" = "$v" ] && { echo "refused: $v already current"; exit 1; }
  r=$(verify_release "$v") || { echo "refused: $r"; exit 1; }
  # first install ever: no good yet -> the current (if any) becomes good implicitly only via confirm
  # pending is recorded BEFORE the slot flips: a crash in between leaves a stale pending (cleared at boot), never an
  # unconfirmed release running as if it were good
  rm -f "$ST/deadline-reset-$v" "$ST/failed-$v"; wstate "$ST/attempts" 0; wstate "$ST/pending" "$v"
  slot_write "$v" || { rm -f "$ST/pending" "$ST/attempts"; echo "slot write failed"; exit 1; }
  echo "ok: $v pending"; exit 0;;
rollback)
  # manual: back to `good` if current is not good; otherwise to the other slot's version ON PROBATION (pending)
  P=$(pick) || { echo "no current"; exit 1; }; cur=${P##* }; sl=${P%% *}; g=$(rstate good)
  if is_semver "$g" && [ "$g" != "$cur" ] && [ -f "$REL/$g/MANIFEST" ]; then
    slot_write "$g" || exit 1; rm -f "$ST/pending" "$ST/attempts"; wstate "$ST/last-rollback" "$cur->$g manual"
    log "manual rollback $cur -> $g (good)"; echo "ok: $cur -> $g"; exit 0; fi
  [ "$sl" = A ] && o=B || o=A; O=$(slot_read $o) || { echo "no valid other slot"; exit 1; }
  ov=${O##* }; [ "$ov" != "$cur" ] || { echo "other slot is the same version"; exit 1; }
  [ -f "$ST/failed-$ov" ] && { echo "other slot $ov is marked failed"; exit 1; }
  wstate "$ST/attempts" 0; wstate "$ST/pending" "$ov"; slot_write "$ov" || { rm -f "$ST/pending" "$ST/attempts"; exit 1; }
  wstate "$ST/last-rollback" "$cur->$ov manual (probation)"; log "manual rollback $cur -> $ov (pending)"; echo "ok: $cur -> $ov (pending)"; exit 0;;
verify) verify_release "${2:-}"; exit $?;;
install)
  tb=${2:-}; want=${3:-}; nosw=${4:-}
  exec 6>"$T/install.lock"; flock -n 6 || { echo "busy: another install is running"; exit 13; }
  [ -f "$tb" ] && [ ! -L "$tb" ] || { echo "no tarball $tb"; exit 1; }
  is_hex64 "$want" || { echo "pinned sha256 required"; exit 1; }
  [ -z "$nosw" ] || [ "$nosw" = --no-switch ] || { echo "bad option $nosw"; exit 1; }
  [ -z "$(rstate pending)" ] || [ -n "$nosw" ] || { echo "refused: $(rstate pending) is pending (confirm or roll back first)"; exit 1; }
  [ "$(sha "$tb")" = "$want" ] || { echo "tarball sha mismatch"; log "install: tarball sha mismatch"; exit 1; }
  mkdir -p "$REL" || { echo "cannot create $REL"; exit 1; }
  sz=$(wc -c < "$tb"); free=$(df -k "$REL" 2>/dev/null | awk 'END{print $(NF-2)}'); is_num "$free" || free=0   # wrap-safe
  [ $((sz / 1024 * 3 + 4096)) -lt "$free" ] || { echo "not enough space on /data (${free} KiB free)"; exit 1; }
  # listing: "<mode> <owner> <size> <date> <time> <name>"; only regular files and dirs, one top-level mk20-fw-<v>/
  tar -tvf "$tb" > "$T/.tl.$$" 2>/dev/null || { rm -f "$T/.tl.$$"; echo "unreadable tarball"; exit 1; }
  if LC_ALL=C grep -q '[^ -~]' "$T/.tl.$$"; then rm -f "$T/.tl.$$"; echo "control/non-ASCII chars in tar listing"; exit 1; fi
  top=$(awk 'NR==1{print $NF}' "$T/.tl.$$"); top=${top%/}; v=${top#mk20-fw-}
  is_semver "$v" && [ "$top" = "mk20-fw-$v" ] || { rm -f "$T/.tl.$$"; echo "bad top-level dir $top"; exit 1; }
  bad=$(awk -v t="$top" '{ m=substr($1,1,1); n=$NF; sub(/\/$/,"",n)
        if ((m!="-" && m!="d") || NF!=6 || (n!=t && index(n, t "/")!=1) || n ~ /(^|\/)\.\.(\/|$)/ || n ~ /^\//) { print; exit } }' "$T/.tl.$$")
  nl=$(wc -l < "$T/.tl.$$"); rm -f "$T/.tl.$$"
  [ -z "$bad" ] || { echo "unsafe tar entry: $bad"; log "install: unsafe tar entry"; exit 1; }
  [ "$nl" -le 200 ] || { echo "too many tar entries"; exit 1; }
  mf=$(tar -xOf "$tb" "$top/MANIFEST" 2>/dev/null | sha256sum | cut -d' ' -f1)
  if [ -d "$REL/$v" ]; then
    if [ "$(sha "$REL/$v/MANIFEST")" = "$mf" ] && verify_release "$v" >/dev/null; then echo "$v already installed (identical)"
    else
      P=$(pick 2>/dev/null); [ "$v" = "$(rstate good)" ] || [ "$v" = "${P##* }" ] && { echo "refused: different $v is good/current"; exit 1; }
      rm -rf "${REL:?}/$v" || exit 1; log "install: replaced stale/corrupt $v"
    fi
  fi
  if [ ! -d "$REL/$v" ]; then
    X=$REL/.incoming-$v; rm -rf "$X"; mkdir -p "$X" || { echo "cannot create $X"; exit 1; }
    tar -xf "$tb" -C "$X" 2>/dev/null || { rm -rf "$X"; echo "extract failed"; exit 1; }
    r=$(verify_dir "$X/$top" "$v") || { rm -rf "$X"; echo "verify failed: $r"; log "install $v: verify failed: $r"; exit 1; }
    VN=$(sed -n '/^--$/,$p' "$X/$top/MANIFEST" | sed 1d | grep -c .)   # verify_dir ran in a subshell
    nf=$(find "$X/$top" -type f | wc -l); no=$(find "$X/$top" ! -type f ! -type d | wc -l)
    [ "$nf" = $((VN + 1)) ] && [ "$no" = 0 ] || { rm -rf "$X"; echo "unlisted files in release"; exit 1; }
    sed -n '/^--$/,$p' "$X/$top/MANIFEST" | sed 1d | while read -r fs fm fp; do [ -n "$fp" ] && chmod "$fm" "$X/$top/$fp"; done
    find "$X/$top" -type d -exec chmod 755 {} \; ; chmod 644 "$X/$top/MANIFEST"
    sync; mv "$X/$top" "$REL/$v" || { rm -rf "$X"; echo "rename failed"; exit 1; }; rmdir "$X" 2>/dev/null; sync
    log "install: $v extracted and verified (tar $want)"
  fi
  verify_release "$v" >/dev/null || { echo "post-install verify failed"; exit 1; }
  [ -n "$nosw" ] && { echo "ok: $v installed (not switched)"; exit 0; }
  P=$(pick 2>/dev/null)
  if [ "${P##* }" = "$v" ]; then echo "ok: $v installed (already current)"
  else out=$(sh "$0" switch "$v") || { echo "switch failed: $out"; exit 1; }; echo "ok: $v installed; $out; reboot to start probation"; fi
  MK20_INSTALL_LOCKED=1 sh "$0" prune >/dev/null 2>&1
  exit 0;;
prune)
  if [ "${MK20_INSTALL_LOCKED:-}" != 1 ]; then exec 6>"$T/install.lock"; flock -n 6 || { echo "busy: install running"; exit 13; }; fi
  keep=" $(rstate good) $(rstate pending) $(slot_read A | cut -d' ' -f2) $(slot_read B | cut -d' ' -f2) $(cat "$T/release-active" 2>/dev/null) "
  extra=$(for d in "$REL"/*; do b=${d##*/}; is_semver "$b" || continue; case "$keep" in *" $b "*) continue;; esac; echo "$b"; done \
    | awk -F. '{printf "%03d%03d%03d %s\n",$1,$2,$3,$0}' | sort -rn | cut -d' ' -f2 | sed -n "$(( ${MK20_KEEP:-1} + 1 )),\$p")
  for b in $extra; do rm -rf "${REL:?}/$b" && log "prune: removed $b"; echo "removed $b"; done
  for d in "$REL"/.incoming-*; do [ -d "$d" ] && rm -rf "$d" && log "prune: removed leftover ${d##*/}"; done
  exit 0;;
uninstall)
  exec 7>"$T/loader.lock"; flock -n 7 || { echo "busy"; exit 13; }
  ts=$(date +%Y%m%d%H%M%S); mkdir -p "$B/uninstalled-$ts" || exit 1
  for f in "$SL/A" "$SL/B" "$ST/good" "$ST/pending" "$ST/attempts"; do [ -f "$f" ] && mv -f "$f" "$B/uninstalled-$ts/"; done; sync
  log "uninstall: slots/state moved to uninstalled-$ts -> legacy mode on next boot"; echo "ok: legacy mode on next boot (releases kept)"; exit 0;;
status)
  echo "current=$(pick 2>/dev/null) good=$(rstate good) pending=$(rstate pending) attempts=$(rstate attempts) active=$(cat "$T/release-active" 2>/dev/null) last_rollback=$(rstate last-rollback)"; exit 0;;
*) echo "usage: $0 boot|confirm <v>|switch <v>|rollback|deadline <v>|status|verify <v>|install <tar> <sha> [--no-switch]|prune|uninstall"; exit 2;;
esac
