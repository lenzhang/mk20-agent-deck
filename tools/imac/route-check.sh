#!/bin/sh
# route-check.sh — read-only check that AI traffic (codex/kimi/ccai) leaves via the US home exit.
# iMac version (2026-10-27). User decision 2026-10-27 10:21 SGT: the exit IP is what matters;
# US-Exit-Hy2 (preferred, better anti-blocking) and US-Exit-SS (SS) back each other up.
# PASS = exit IP 203.0.113.10 via TUN and via the mixed port, with the mihomo AI group on one of
# $OK_NODES; WARN when the active node is not $PREF_NODE. Never changes mihomo. Exit 0 = PASS.
API="${MIHOMO_API:-http://127.0.0.1:9090}"; WANT_IP="${WANT_IP:-203.0.113.10}"
PREF_NODE="${PREF_NODE:-US-Exit-Hy2}"; OK_NODES="${OK_NODES:-US-Exit-Hy2 US-Exit-SS}"
nc() { env -u HTTPS_PROXY -u HTTP_PROXY -u ALL_PROXY -u https_proxy -u http_proxy -u all_proxy curl -s -m 10 --noproxy 127.0.0.1 "$@"; }
api() { if [ -n "${MIHOMO_SECRET:-}" ]; then nc -H "Authorization: Bearer $MIHOMO_SECRET" "$API$1"; else nc "$API$1"; fi; }
# follow nested groups: AI -> (group ->) ... -> leaf node
leaf() { python3 -c '
import json,sys,urllib.request,urllib.parse
api=sys.argv[1]; name="AI"; chain=[]
op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
for _ in range(6):
    try: d=json.load(op.open(api+"/proxies/"+urllib.parse.quote(name),timeout=8))
    except Exception: break
    chain.append(name)
    if not d.get("now"): break
    name=d["now"]
print(" > ".join(chain)); print(name)' "$API"; }
L=$(leaf); chain=$(printf '%s\n' "$L" | sed -n 1p); now=$(printf '%s\n' "$L" | sed -n 2p)
mixed=$(api /configs | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("mixed-port") or d.get("port") or "")' 2>/dev/null)
trace() { sed -n -e 's/^ip=//p' -e 's/^loc=/loc=/p' | tr '\n' ' '; }
tun=$(nc https://chatgpt.com/cdn-cgi/trace | trace)
mix=""; [ -n "$mixed" ] && mix=$(nc -x "http://127.0.0.1:$mixed" https://chatgpt.com/cdn-cgi/trace | trace)
echo "$(date '+%F %T %z') AI=${now:-?} (${chain:-?}) tun_exit=[${tun:-none}] mixed:${mixed:-?}_exit=[${mix:-none}]"
ok=1; case " $OK_NODES " in *" $now "*) ;; *) ok=0; echo "node '$now' is not one of: $OK_NODES";; esac
case "$tun" in "$WANT_IP "*) ;; *) ok=0;; esac
[ -n "$mixed" ] && case "$mix" in "$WANT_IP "*) ;; *) ok=0;; esac
if [ $ok = 1 ]; then
  [ "$now" = "$PREF_NODE" ] || echo "WARN active node is $now, not the preferred $PREF_NODE (SS backup in use)"
  echo "ROUTE_OK"; exit 0
fi
echo "ROUTE_FAIL (want exit $WANT_IP via one of: $OK_NODES) — do not run codex/kimi"; exit 1
