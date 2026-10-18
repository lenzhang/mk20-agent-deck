#!/bin/bash
# Remove the MK20 hook probe only if hooks.json is byte-identical to what we installed.
set -euo pipefail
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
STATE="$HOME/.config/mk20-agent-deck/hooks-install"
[[ -f "$STATE/hooks.json.sha256" ]] || { echo "nothing installed by mk20"; exit 0; }
if [[ ! -e "$CODEX_HOME/hooks.json" ]]; then echo "hooks.json already gone"; rm -rf "$STATE"; exit 0; fi
NOW=$(shasum -a 256 "$CODEX_HOME/hooks.json" | awk '{print $1}')
if [[ "$NOW" != "$(cat "$STATE/hooks.json.sha256")" ]]; then
  echo "refuse: hooks.json was edited after install; remove the mk20 entries manually"; exit 3
fi
rm -f "$CODEX_HOME/hooks.json"
rm -rf "$STATE"
echo "removed $CODEX_HOME/hooks.json (restored to no-hooks state)"
# Note: trust records written by the App (config.toml hooks state) are left as-is.
