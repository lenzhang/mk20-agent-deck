#!/bin/sh
# Called only by mk20d agent, with stdout/stderr on tmpfs agent.log.
# Best effort priority isolation: failures remain visible but never gate boot/OTA.
if ( echo 500 > "/proc/$$/oom_score_adj" ) 2>/dev/null && [ "$(cat "/proc/$$/oom_score_adj" 2>/dev/null)" = 500 ]; then
  echo 'agent priority: oom_score_adj=500 verified'
else
  echo 'WARN agent priority: oom_score_adj=500 unavailable'
fi
if renice 10 -p "$$" >/dev/null 2>&1 && [ "$(sed 's/^.*) //' "/proc/$$/stat" 2>/dev/null | awk '{print $17}')" = 10 ]; then
  echo 'agent priority: nice=10 verified'
else
  echo 'WARN agent priority: nice=10 unavailable or not verified'
fi
exec "$@"
