#!/bin/bash
# Merge the MK20 agent-state hook into Claude Code settings files (idempotent, keeps existing hooks).
#   claude-hooks-install.sh [--dry-run] <settings.json>...
# e.g. for ccai: ~/.claude/settings.json ~/.claude/settings-clean.json ~/.claude/settings-glm.json
# (ccai copies settings-clean/-glm over settings.json at launch, so all of them need the hook).
# Copies mk20-agent-state-hook.py to $MK20DECK_HOME/hooks (default ~/.config/mk20-agent-deck);
# each changed settings file gets a .bak-mk20-<ts> backup; malformed JSON is refused untouched.
# --dry-run prints the resulting JSON diff and writes nothing (no copy either).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DECK_HOME="${MK20DECK_HOME:-$HOME/.config/mk20-agent-deck}"
DEST="$DECK_HOME/hooks"
PY="${MK20_HOOK_PYTHON:-/opt/homebrew/bin/python3}"
[[ -x "$PY" ]] || PY="$(command -v python3 || true)"
[[ -n "$PY" && -x "$PY" ]] || { echo "python3 not found (set MK20_HOOK_PYTHON)"; exit 2; }
DRY=""   # plain string, not an array: macOS /bin/bash 3.2 + set -u rejects "${empty[@]}"
if [[ "${1:-}" == "--dry-run" ]]; then DRY="--dry-run"; shift; fi
[[ $# -gt 0 ]] || { echo "usage: $0 [--dry-run] <settings.json>..."; exit 2; }
q() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }
# exec: the hook's parent pid is then the claude process itself (the deck checks it is alive)
ENVP=""
if [[ -n "${MK20DECK_HOME:-}" ]]; then ENVP="env MK20DECK_HOME=$(q "$MK20DECK_HOME") "; fi
CMD="exec ${ENVP}$(q "$PY") -I -S $(q "$DEST/mk20-agent-state-hook.py") claude"
if [[ -z "$DRY" ]]; then
  # validate every file before copying anything (the merge tool also refuses as a whole)
  out="$("$PY" "$HERE/mk20_hooks_merge.py" install --dry-run --command "$CMD" "$@")" || {
    printf '%s\n' "$out" | grep '^refuse' || true; exit 2; }
  install -d -m 0700 "$DEST"
  install -m 0755 "$HERE/mk20-agent-state-hook.py" "$DEST/mk20-agent-state-hook.py"
  echo "hook script: $DEST/mk20-agent-state-hook.py"
fi
exec "$PY" "$HERE/mk20_hooks_merge.py" install $DRY --command "$CMD" "$@"
