#!/bin/bash
# Reversible install of the MK20 codex hook into ~/.codex/hooks.json: metadata-only log probe
# (mk20_hook_log.py) + deck state records (mk20-agent-state-hook.py, agent-state/codex-<id>.json).
# Refuses to touch an existing hooks.json (installs on a clean slate only; to upgrade an older
# mk20 install run hooks-uninstall.sh first).  --dry-run prints the hooks.json it would write.
# codex 0.163 hook events used: SessionStart UserPromptSubmit PreToolUse PermissionRequest
# PostToolUse Stop Interrupt SessionEnd.  New/changed hooks must be trusted again in codex
# (CLI prompt / App Settings -> Hooks) before they run.
set -euo pipefail
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
HERE="$(cd "$(dirname "$0")" && pwd)"
STATE="$HOME/.config/mk20-agent-deck/hooks-install"
sha256() { if command -v shasum >/dev/null; then shasum -a 256 "$1"; else sha256sum "$1"; fi | awk '{print $1}'; }
PY="${MK20_HOOK_PYTHON:-/opt/homebrew/bin/python3}"
[[ -x "$PY" ]] || PY="$(command -v python3 || true)"
[[ -n "$PY" && -x "$PY" ]] || { echo "python3 not found (set MK20_HOOK_PYTHON)"; exit 2; }
DRY=0
if [[ "${1:-}" == "--dry-run" ]]; then DRY=1; fi
CMD="$PY $STATE/mk20_hook_log.py"
gen() {
"$PY" - "$CMD" <<'PYEOF'
import json, sys
cmd = sys.argv[1]
h = lambda: [{"hooks": [{"type": "command", "command": cmd, "timeout": 5}]}]
print(json.dumps({"hooks": {e: h() for e in
      ("SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest", "PostToolUse", "Stop",
       "Interrupt", "SessionEnd")}}, indent=2))
PYEOF
}
if [[ $DRY -eq 1 ]]; then gen; echo "dry run: nothing written" >&2; exit 0; fi
if [[ -e "$CODEX_HOME/hooks.json" ]]; then
  echo "refuse: $CODEX_HOME/hooks.json already exists (an older mk20 install: run hooks-uninstall.sh first)"; exit 2
fi
mkdir -p "$STATE"
install -m 0755 "$HERE/mk20_hook_log.py" "$STATE/mk20_hook_log.py"
install -m 0755 "$HERE/mk20-agent-state-hook.py" "$STATE/mk20-agent-state-hook.py"
tmp="$(mktemp "$CODEX_HOME/.hooks.json.XXXXXX")"; gen > "$tmp" && mv -f "$tmp" "$CODEX_HOME/hooks.json" || { rm -f "$tmp"; echo "write failed"; exit 1; }   # atomic
sha256 "$CODEX_HOME/hooks.json" > "$STATE/hooks.json.sha256"
date '+%Y-%m-%dT%H:%M:%S%z' > "$STATE/installed_at"
echo "installed $CODEX_HOME/hooks.json (sha256 $(cat "$STATE/hooks.json.sha256"))"
