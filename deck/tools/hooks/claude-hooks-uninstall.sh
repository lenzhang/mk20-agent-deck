#!/bin/bash
# Remove only the MK20 agent-state hook entries from Claude Code settings files (others untouched).
#   claude-hooks-uninstall.sh [--dry-run] <settings.json>...
# Changed files get a .bak-mk20-<ts> backup. The copied hook script and state records are left in
# $MK20DECK_HOME (rm -r ~/.config/mk20-agent-deck/hooks ~/.config/mk20-agent-deck/agent-state to purge).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${MK20_HOOK_PYTHON:-/opt/homebrew/bin/python3}"
[[ -x "$PY" ]] || PY="$(command -v python3)"
DRY=""   # plain string, not an array: macOS /bin/bash 3.2 + set -u rejects "${empty[@]}"
if [[ "${1:-}" == "--dry-run" ]]; then DRY="--dry-run"; shift; fi
[[ $# -gt 0 ]] || { echo "usage: $0 [--dry-run] <settings.json>..."; exit 2; }
exec "$PY" "$HERE/mk20_hooks_merge.py" uninstall $DRY "$@"
