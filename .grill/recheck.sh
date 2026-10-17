#!/bin/bash
# usage: recheck.sh <round-label> <claude|codex>
G=/Users/dev/workspace/mk20-agent-deck/.grill; L="$1"; W="$2"; TS=$(date '+%H:%M:%S %Z')
if [[ $W == claude ]]; then
  R=$(curl -s --max-time 20 -x http://127.0.0.1:7895 https://ipinfo.io/json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("ip"),d.get("country"))' 2>/dev/null)
  [[ "$R" == *" SG" ]] && OK=1 || OK=0
else
  NOW=$(curl -s --max-time 5 http://127.0.0.1:9090/proxies/AI | python3 -c 'import json,sys; print(json.load(sys.stdin).get("now"))' 2>/dev/null)
  IP=$($G/codex-env.sh curl -s --max-time 20 https://ipinfo.io/json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("ip"),d.get("country"))' 2>/dev/null)
  R="AI.now=$NOW exit=$IP"
  [[ "$NOW" == *112* && "$IP" == *" US" ]] && OK=1 || OK=0
fi
echo "- $TS · $L · $W · $R · $([[ $OK == 1 ]] && echo PASS || echo FAIL)" | tee -a $G/PREFLIGHT.md
[[ $OK == 1 ]]
