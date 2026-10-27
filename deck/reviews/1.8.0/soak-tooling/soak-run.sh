#!/bin/sh
# MK20 fw 1.8.0-rc1 24 h soak runner on the iMac. Started by launchd job com.leo.mk20-soak-180 under
# `caffeinate -i -s` (bootstrapped from this dir, not ~/Library/LaunchAgents: it does not rerun at login).
# Runs the frozen soak tool in ~/mk20-deploy/deck (detached 4eb998f), then the final verdict/push.
set -u
D="$HOME/mk20-backup/soak"; L="$D/launchd"
export MK20_ADB="$HOME/mk20-backup/mkadb.sh" PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
[ -e "$L/started.marker" ] && { echo "$(date '+%F %T') refuse: already started once ($L/started.marker)"; exit 0; }
touch "$L/started.marker"
cd "$HOME/mk20-deploy/deck" || exit 1
echo "$(date '+%F %T %z') soak start (pid $$)"
"$HOME/mk20-deploy/.venv/bin/python" tools/mk20_soak.py --hours 24 --interval 300 --out "$D"
echo "$(date '+%F %T %z') soak exit rc=$?"
/bin/sh "$L/soak-push.sh" final
