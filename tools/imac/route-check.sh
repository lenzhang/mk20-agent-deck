#!/bin/sh
# route-check.sh — read-only check that AI traffic (codex/kimi/ccai) leaves via the US home proxy.
# iMac version (2026-10-27). Pass = mihomo group AI -> US-Exit-Hy2 AND exit IP 203.0.113.10
# (chatgpt.com/cdn-cgi/trace via TUN and via the mixed port). Never changes mihomo. Exit 0 = PASS.
API="${MIHOMO_API:-http://127.0.0.1:9090}"; WANT_NODE="${WANT_NODE:-US-Exit-Hy2}"; WANT_IP="${WANT_IP:-203.0.113.10}"
nc() { env -u HTTPS_PROXY -u HTTP_PROXY -u ALL_PROXY -u https_proxy -u http_proxy -u all_proxy curl -s -m 10 "$@"; }
H=""; [ -n "${MIHOMO_SECRET:-}" ] && H="Authorization: Bearer $MIHOMO_SECRET"
api() { if [ -n "$H" ]; then nc -H "$H" "$API$1"; else nc "$API$1"; fi; }
now=$(api /proxies/AI | python3 -c 'import sys,json;print(json.load(sys.stdin).get("now",""))' 2>/dev/null)
mixed=$(api /configs | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("mixed-port") or d.get("port") or "")' 2>/dev/null)
trace() { sed -n -e 's/^ip=//p' -e 's/^loc=/loc=/p' | tr '\n' ' '; }
tun=$(nc https://chatgpt.com/cdn-cgi/trace | trace)
mix=""; [ -n "$mixed" ] && mix=$(nc -x "http://127.0.0.1:$mixed" https://chatgpt.com/cdn-cgi/trace | trace)
echo "$(date '+%F %T %z') AI=${now:-?} tun_exit=[${tun:-none}] mixed:${mixed:-?}_exit=[${mix:-none}]"
ok=1; [ "$now" = "$WANT_NODE" ] || ok=0
case "$tun" in "$WANT_IP "*) ;; *) ok=0;; esac
[ -n "$mixed" ] && case "$mix" in "$WANT_IP "*) ;; *) ok=0;; esac
[ $ok = 1 ] && { echo "ROUTE_OK"; exit 0; } || { echo "ROUTE_FAIL (want AI=$WANT_NODE exit=$WANT_IP) — do not run codex/kimi"; exit 1; }
