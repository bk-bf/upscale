#!/usr/bin/env bash
# health.sh — one question: is the overnight loop still getting work done?
#
# Exit 0 = healthy (or legitimately finished). Non-zero = a human or an agent
# needs to do something, and stdout says what.
#
# Progress, not liveness. A process being alive proves nothing — the first
# launch sat "running" for eleven minutes while every command it issued was
# refused, and a liveness check would have called that healthy the entire time.
# What cannot be faked is a new trial landing on disk.
#
# It must also tell FINISHED from DIED. A monitor that cannot will page about
# success, which is how this project once sent three alerts for one clean
# shutdown. Guard expired plus a written report is the good ending, not an
# outage.
#
# Nothing here alerts. This is the question; `mon check` owns what happens when
# the answer is no, so there is exactly one path to the phone.
set -uo pipefail

R=$(cd "$(dirname "$0")/.." && pwd)
STALL_MIN=${STALL_MIN:-25}          # a trial is ~4-5 min; 25 is five missed ones
NOW=$(date +%s)

# ---------------------------------------------------------------- finished?
if ! "$R/harness/guard.sh" >/dev/null 2>&1; then
  if [ -s "$R/RESULTS.md" ]; then
    echo "finished: guard expired and RESULTS.md is written"
    exit 0
  fi
  # Give it a grace period — writing the report is the last thing it does.
  DL=$(cat "$R/.deadline" 2>/dev/null) || DL=0
  if [ "$((NOW - DL))" -lt 1800 ]; then
    echo "guard expired $(( (NOW-DL)/60 ))m ago, report still being written"
    exit 0
  fi
  echo "DIED: guard expired $(( (NOW-DL)/60 ))m ago and research/RESULTS.md was never written."
  echo "The night produced $(( $(wc -l < "$R/results.tsv" 2>/dev/null || echo 1) - 1 )) logged trials."
  exit 1
fi

LEFT=$("$R/harness/guard.sh" 2>/dev/null) || LEFT="unknown"

# ---------------------------------------------------------------- supervised?
# Progress alone is too slow a signal for THIS failure. When the session died at
# 00:48 the trial files were still minutes old, so a progress-only check called
# it healthy for another twenty-five minutes while nothing at all was running.
# The supervisor is what makes a dead session self-healing, so its absence is
# the thing that turns a normal session ending into an outage.
SUP=$(systemctl --user is-active upscale-research-loop.service 2>/dev/null) || SUP=unknown
if [ "$SUP" != active ]; then
  echo "DIED: the supervisor is '$SUP', so nothing is chaining sessions, and $LEFT"
  echo "trials logged so far: $(( $(wc -l < "$R/results.tsv" 2>/dev/null || echo 1) - 1 ))"
  echo "A session ending is normal — mon caps every session's wall clock. The"
  echo "supervisor starting the next one is what makes that survivable."
  exit 1
fi

# ---------------------------------------------------------------- progressing?
# Newest of: any trial output, the results log, the baseline. mapfile rather
# than `head -1` on a live pipeline, which SIGPIPEs the producer.
mapfile -t NEWEST < <(find "$R/runs" "$R/results.tsv" "$R/baseline.env" \
                        -maxdepth 1 -type f -newermt "-${STALL_MIN} minutes" 2>/dev/null)

if [ "${#NEWEST[@]}" -gt 0 ]; then
  echo "healthy: ${#NEWEST[@]} file(s) touched in the last ${STALL_MIN}m, $LEFT"
  exit 0
fi

# ---------------------------------------------------------------- stalled
LASTFILE=$(find "$R/runs" -maxdepth 1 -type f -printf '%T@ %p\n' 2>/dev/null | sort -rn | sed -n 1p) || LASTFILE=""
TRIALS=$(( $(wc -l < "$R/results.tsv" 2>/dev/null || echo 1) - 1 ))

echo "STALLED: no trial output for over ${STALL_MIN}m, and $LEFT"
echo "trials logged so far: $TRIALS"
echo "last artefact: ${LASTFILE:-none at all — it never started}"
printf 'supervisor: %s\n' "$(systemctl --user is-active upscale-research-loop.service 2>/dev/null || echo absent)"
printf 'mon session: %s\n' "$(${MON:-$HOME/Documents/Projects/mon/mon} sessions 2>/dev/null | grep -c running || echo 0) running"
exit 1
