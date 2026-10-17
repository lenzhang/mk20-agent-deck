#!/bin/bash
# Reversible install of the MK20 logging-only hook probe into ~/.codex/hooks.json.
# Refuses to touch an existing hooks.json (P0 spike only installs on a clean slate).
set -euo pipefail
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
HERE="$(cd "$(dirname "$0")" && pwd)"
STATE="$HOME/.config/mk20-agent-deck/hooks-install"
PY="${MK20_HOOK_PYTHON:-/opt/homebrew/bin/python3}"
[[ -x "$PY" ]] || PY="$(command -v python3)"
if [[ -e "$CODEX_HOME/hooks.json" ]]; then
  echo "refuse: $CODEX_HOME/hooks.json already exists (not managed by mk20)"; exit 2
fi
mkdir -p "$STATE"
install -m 0755 "$HERE/mk20_hook_log.py" "$STATE/mk20_hook_log.py"
CMD="$PY $STATE/mk20_hook_log.py"
"$PY" - "$CMD" > "$CODEX_HOME/hooks.json" <<'PYEOF'
import json, sys
cmd = sys.argv[1]
h = lambda: [{"hooks": [{"type": "command", "command": cmd, "timeout": 5}]}]
print(json.dumps({"hooks": {e: h() for e in
      ("SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest", "PostToolUse", "Stop")}}, indent=2))
PYEOF
shasum -a 256 "$CODEX_HOME/hooks.json" | awk '{print $1}' > "$STATE/hooks.json.sha256"
date '+%Y-%m-%dT%H:%M:%S%z' > "$STATE/installed_at"
echo "installed $CODEX_HOME/hooks.json (sha256 $(cat "$STATE/hooks.json.sha256"))"
