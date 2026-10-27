#!/bin/sh
# Push MK20 1.8.0-rc1 soak data to Gitea (feature/ncm-gadget, deck/reviews/1.8.0/soak-<start>/).
#   soak-push.sh          partial push (launchd com.leo.mk20-soak-180-push every 3 h); when the soak has ended
#                         (summary running=false, pid gone) it does the final step instead.
#   soak-push.sh final    final: VERDICT.md + summary + commit/push + one PROGRESS.md line on main (once).
set -u
D="$HOME/mk20-backup/soak"; L="$D/launchd"; R="$HOME/worktrees/mk20-ncm"; M="$HOME/mk20-build/mk20-agent-deck"
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
mkdir "$L/push.lockdir" 2>/dev/null || { echo "another push running"; exit 0; }; trap 'rmdir "$L/push.lockdir"' EXIT
[ -e "$L/started.marker" ] || exit 0
J=$(ls -t "$D"/soak-*.jsonl 2>/dev/null | head -1); [ -n "$J" ] && [ "$J" -nt "$L/started.marker" ] || { echo "no soak jsonl yet"; exit 0; }
S=$(basename "$J" .jsonl); SM="$D/$S.summary.json"
[ -e "$L/$S.final-done" ] && { echo "$S final already pushed"; exit 0; }
MODE=${1:-partial}
if [ "$MODE" = partial ] && [ -f "$SM" ] && grep -q '"running": false' "$SM"; then MODE=final; fi
N=$(wc -l < "$J" | tr -d ' ')
cd "$R" || exit 1
[ "$(git symbolic-ref --short HEAD 2>/dev/null)" = feature/ncm-gadget ] || { echo "refuse: $R not on feature/ncm-gadget"; exit 1; }
git pull -q --rebase --autostash || { echo "pull failed"; exit 1; }
DST="deck/reviews/1.8.0/$S"; mkdir -p "$DST"
cp -p "$J" "$DST/"; [ -f "$SM" ] && cp -p "$SM" "$DST/"
[ -f "$L/soak-run.log" ] && cp -p "$L/soak-run.log" "$DST/soak-run.log"
if [ "$MODE" = final ]; then
  /usr/bin/python3 "$L/verdict.py" "$SM" "$J" > "$DST/VERDICT.md" || echo "verdict.py failed" >> "$DST/VERDICT.md"
  V=$(sed -n 's/^Verdict: \([A-Z]*\).*/\1/p' "$DST/VERDICT.md" | head -1); MSG="soak 1.8.0-rc1 $S: FINAL ${V:-?} ($N samples)"
else
  MSG="soak 1.8.0-rc1 $S: partial ($N samples)"
fi
git add "$DST" && git commit -q -m "$MSG" -- "$DST" && echo "committed: $MSG"
git push -q origin feature/ncm-gadget || { echo "push failed"; exit 1; }
[ "$MODE" = final ] || exit 0
SHA=$(git rev-parse --short HEAD)
cd "$M" || exit 1
[ "$(git symbolic-ref --short HEAD 2>/dev/null)" = main ] || { echo "refuse: $M not on main"; exit 1; }
git pull -q --rebase --autostash && \
/usr/bin/python3 "$L/prog.py" "**fw 1.8.0-rc1 24 h soak $S 结束：${V:-?}**（$N 样本；判定与数据 feature/ncm-gadget $SHA deck/reviews/1.8.0/$S/VERDICT.md，自动生成，未打 tag）" && \
git commit -q -m "PROGRESS: 1.8.0-rc1 soak $S ${V:-?}" -- PROGRESS.md && git push -q origin main && touch "$L/$S.final-done" && echo "PROGRESS pushed"
