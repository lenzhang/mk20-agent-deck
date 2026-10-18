# MK20 firmware shared library (sourced by sbin/mk20d). POSIX sh / BusyBox 1.27 ash.
# Provides: whitelisted config loading (mk20.conf + flag overrides), structured logging with size rotation.
# shellcheck shell=sh disable=SC2034,SC3043
# BusyBox ash supports 'local'; helpers use it so sourcing never clobbers the caller's variables.
R=${MK20_ROOT:-}
MK20_HOME=${MK20_HOME:-$R/data/mk20}
CONF=${MK20_CONF:-$MK20_HOME/config/mk20.conf}
LOGDIR=${MK20_LOGDIR:-$MK20_HOME/logs}
S=$R/mnt/SDCARD

# ---------- structured log: "<epoch> <uptime> <LEVEL> <component> <event> [k=v ...]" ----------
LOG_MAX_KB=${LOG_MAX_KB:-256}; LOG_KEEP=${LOG_KEEP:-3}
log_rotate() { # $1=file
  local _f _i _j; _f=$1; [ -f "$_f" ] || return 0
  [ "$(wc -c < "$_f")" -gt $((LOG_MAX_KB * 1024)) ] || return 0
  _i=$LOG_KEEP; while [ "$_i" -gt 1 ]; do _j=$((_i-1)); [ -f "$_f.$_j" ] && mv -f "$_f.$_j" "$_f.$_i"; _i=$_j; done
  mv -f "$_f" "$_f.1"
}
mk20log() { # $1=LEVEL $2=component $3=event, rest = k=v / text
  local _lv _co _ev _lf; _lv=$1; _co=$2; _ev=$3; shift 3
  mkdir -p "$LOGDIR" 2>/dev/null; _lf=$LOGDIR/mk20d.log
  log_rotate "$_lf"
  echo "$(date +%s) $(cut -d' ' -f1 /proc/uptime) $_lv $_co $_ev $*" | tr -d '\r' >> "$_lf" 2>/dev/null
}

# ---------- config ----------
CFG_WARN=""
cfg_warn() { CFG_WARN="$CFG_WARN $*;"; }
octet() { case "$1" in ''|*[!0-9]*|0?*) return 1;; esac; [ ${#1} -le 3 ] && [ "$1" -le 255 ]; }
v_subnet() { # a.b.c.0/24, not loopback/link-local/multicast/zero
  local a b c d n; case "$1" in */24) ;; *) return 1;; esac; n=${1%/24}
  IFS=. read -r a b c d <<EOT
$n
EOT
  octet "$a" && octet "$b" && octet "$c" && [ "$d" = 0 ] || return 1
  [ "$a" != 0 ] && [ "$a" != 127 ] && [ "$a" -lt 224 ] || return 1
  [ "$a.$b" != 169.254 ] || return 1
  [ "$n" = "$a.$b.$c.0" ]
}
v_mac() { # locally administered unicast xx:xx:xx:xx:xx:xx (lowercase)
  local f; echo "$1" | grep -Eq '^[0-9a-f]{2}(:[0-9a-f]{2}){5}$' || return 1
  f=$(printf '%d' "0x${1%%:*}"); [ $((f & 3)) = 2 ]
}
v_bool() { [ "$1" = 0 ] || [ "$1" = 1 ]; }
v_range() { case "$1" in ''|*[!0-9]*|0?*) return 1;; esac; [ ${#1} -le 6 ] && [ "$1" -ge "$2" ] && [ "$1" -le "$3" ]; }
cfg_set() { # $1=key $2=value (validated)
  case "$1" in
    ECM_SUBNET) v_subnet "$2" ;;
    ECM_DEV_MAC|ECM_HOST_MAC) v_mac "$2" ;;
    FEATURE_COMPOSITE|FEATURE_HID|FEATURE_ADB) v_bool "$2" ;;
    BRIDGE_PORT) v_range "$2" 1024 65535 ;;
    FLAP_DROPS) v_range "$2" 2 50 ;;
    FLAP_WINDOW_S) v_range "$2" 10 600 ;;
    LOG_MAX_KB) v_range "$2" 16 4096 ;;
    LOG_KEEP) v_range "$2" 1 9 ;;
    *) cfg_warn "unknown key $1"; return 0 ;;
  esac || { cfg_warn "invalid $1"; return 0; }
  eval "$1=\$2"
}
cfg_load() {
  local line k v n
  # release defaults (1.0.x keeps the proven 10.20.0.0/24; the M4 release switches the default)
  ECM_SUBNET=${MK20_DEFAULT_SUBNET:-10.20.0.0/24}; ECM_DEV_MAC=02:11:22:33:44:55; ECM_HOST_MAC=02:11:22:33:44:56
  FEATURE_COMPOSITE=1; FEATURE_HID=1; FEATURE_ADB=1; BRIDGE_PORT=17890; FLAP_DROPS=5; FLAP_WINDOW_S=60
  LOG_MAX_KB=256; LOG_KEEP=3; CFG_SRC=defaults
  if [ -f "$CONF" ]; then
    CFG_SRC=$CONF
    if [ "$(wc -c < "$CONF")" -gt 4096 ]; then cfg_warn "config too large, ignored"
    else
      while IFS= read -r line || [ -n "$line" ]; do
        line=$(printf '%s' "$line" | tr -d '\r')
        case "$line" in ''|'#'*) continue;; esac
        k=${line%%=*}; v=${line#*=}
        [ "$k" = "$line" ] && { cfg_warn "bad line"; continue; }
        k=$(echo "$k" | tr -d ' \t'); v=$(echo "$v" | sed 's/^[ \t]*//; s/[ \t]*#.*$//; s/[ \t]*$//')
        echo "$k" | grep -Eq '^[A-Z_]{1,32}$' || { cfg_warn "bad key"; continue; }
        cfg_set "$k" "$v"
      done < "$CONF"
    fi
  fi
  # flag files override the config (operator kill switches)
  [ -f "$S/mk20-no-composite" ] && FEATURE_COMPOSITE=0
  [ -f "$S/mk20-no-adb" ] && FEATURE_ADB=0
  [ -f "$S/mk20-gadget-nohid" ] && FEATURE_HID=0
  [ "$ECM_DEV_MAC" = "$ECM_HOST_MAC" ] && { cfg_warn "dev/host MAC equal, defaults used"; ECM_DEV_MAC=02:11:22:33:44:55; ECM_HOST_MAC=02:11:22:33:44:56; }
  n=${ECM_SUBNET%/24}; ECM_PREFIX=${n%.0}; ECM_IP=$ECM_PREFIX.2; ECM_HOST_IP=$ECM_PREFIX.1; ECM_MASK=255.255.255.0
}
cfg_print() {
  local k
  for k in CFG_SRC ECM_SUBNET ECM_IP ECM_HOST_IP ECM_DEV_MAC ECM_HOST_MAC FEATURE_COMPOSITE FEATURE_HID FEATURE_ADB BRIDGE_PORT FLAP_DROPS FLAP_WINDOW_S LOG_MAX_KB LOG_KEEP; do
    eval "echo \"$k=\$$k\""; done
  [ -n "$CFG_WARN" ] && echo "WARNINGS=$CFG_WARN"
  return 0
}
