#!/usr/bin/env bash
# budget-report.sh — what the night actually spent, from the governor's own log.
#
# The first run of this loop could not answer "did it hold near 90%?" at all.
# pace.sh was reading Anthropic's real five-hour utilisation every few minutes
# and discarding each reading, and by morning the seven-day window had reset,
# so the number was gone for good. This turns that log into the answer.
#
# Reports per five-hour window, because that is the thing being paced. Windows
# are cut where utilisation drops — a reset is the only way the percentage goes
# down — rather than on a clock, since the window rolls from first use and does
# not align to anything.
set -uo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
BLOG=${1:-$R/runs/budget.log}

[ -s "$BLOG" ] || { echo "no budget log at $BLOG"; exit 1; }

awk -F'\t' '
NR == 1 { next }
$2 == "unknown" { gaps++; next }
{
  pct = $2 + 0
  # A drop means the window rolled. Anything else is the same window continuing.
  if (n > 0 && pct < last - 1) { flush(); reset() }
  if (n == 0) { start = $1 }
  n++; sum += pct; last = pct
  if (pct > peak) peak = pct
  end = $1
  if (pct >= 90) at90++
  if ($7 == "THROTTLE") thr++
  if ($7 == "EXPAND")   expd++
  if ($7 == "STALL")    stl++
  next
}
function reset() { n = 0; sum = 0; peak = 0; at90 = 0; thr = 0; expd = 0; stl = 0 }
function flush() {
  if (n == 0) return
  w++
  printf "  window %d  %s -> %s\n", w, substr(start, 12, 5), substr(end, 12, 5)
  printf "    peak %.0f%%   mean %.0f%%   readings %d\n", peak, sum / n, n
  printf "    at >=90%%: %d of %d readings (%.0f%%)\n", at90, n, 100 * at90 / n
  printf "    modes: throttle %d, expand %d, stall %d\n", thr, expd, stl
  peaks += peak; wins++
}
END {
  flush()
  print ""
  if (wins > 0) printf "across %d window(s): mean peak utilisation %.0f%%\n", wins, peaks / wins
  if (gaps > 0) printf "%d reading(s) lost — dashboard unreachable\n", gaps
  if (wins > 0 && peaks / wins < 90)
    printf "VERDICT: under target. The allowance was not spent; the loop could have gone deeper.\n"
  else if (wins > 0)
    printf "VERDICT: on target (>=90%% peak).\n"
}
' "$BLOG"
