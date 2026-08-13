#!/usr/bin/env bash
# guard.sh — the time guard. The loop is told to run forever; this is what
# makes "forever" end at eight hours.
#
#   guard.sh --arm 8      set the deadline to eight hours from now
#   guard.sh              exit 0 while there is time left, 1 once there is not
#
# Deliberately a separate file from the loop's own bookkeeping: a loop that
# tracks its own deadline in its own head forgets, drifts, or talks itself out
# of stopping. This one is on disk and cannot be argued with.
set -uo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
D="$R/.deadline"

if [ "${1:-}" = "--arm" ]; then
  H=${2:-8}
  date -d "+$H hours" +%s > "$D"
  echo "deadline armed: $(date -d "@$(cat "$D")" '+%F %T %Z') (${H}h)"
  exit 0
fi

[ -f "$D" ] || { echo "no deadline armed"; exit 0; }
DL=$(cat "$D") || DL=0
NOW=$(date +%s)
LEFT=$((DL - NOW))

if [ "$LEFT" -le 0 ]; then
  echo "EXPIRED — stop experimenting and write the report"
  exit 1
fi
printf 'left: %dh%02dm (until %s)\n' $((LEFT/3600)) $(((LEFT%3600)/60)) "$(date -d "@$DL" '+%H:%M')"
exit 0
