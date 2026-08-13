#!/usr/bin/env bash
# supervise.sh — keep the overnight loop alive until the guard expires.
#
#   research/supervise.sh <mon-session-id>
#
# The loop itself is one long-lived Claude session that iterates internally,
# which is the shape Karpathy's autoresearch uses and is the right one: the
# agent keeps what it learned across experiments — which ideas failed, which
# near-misses are worth combining — and that does not survive being restarted
# from scratch every iteration.
#
# What it does NOT survive is dying at 03:00 with nobody awake. A crash, a
# dropped connection, or an exit on a rate limit ends the night silently and
# the morning report never gets written. That is what this fixes.
#
# It does not re-run the loop from the beginning. `mon steer` resumes the SAME
# conversation, so a revived session still has its context; and everything that
# matters is on disk anyway — results.tsv, the git branch, baseline.env — so
# even a cold resume can pick up where it stopped.
#
# It supervises. It never does research, never edits worker.sh, never has an
# opinion about an experiment.
set -uo pipefail

ID=${1:?need the mon session id}
R=$(cd "$(dirname "$0")" && pwd)
MON=$(command -v mon) || MON=$HOME/Documents/Projects/mon/mon
LOG="$R/runs/supervisor.log"
POLL=${POLL:-120}
MAX_REVIVALS=${MAX_REVIVALS:-12}

mkdir -p "$R/runs"
say(){ printf '%s  %s\n' "$(date '+%F %T')" "$*" >> "$LOG"; }

say "supervising $ID (poll ${POLL}s, max $MAX_REVIVALS revivals)"
revivals=0

while :; do
  # The guard outranks everything. Ask it first, so a loop that finished on
  # time is never mistaken for one that died.
  if ! "$R/harness/guard.sh" >/dev/null 2>&1; then
    say "guard expired — supervision over"
    break
  fi

  state=$("$MON" sessions 2>/dev/null | awk -v id="$ID" '$0 ~ id {
            for (i=1; i<=NF; i++)
              if ($i ~ /^(running|done|failed|stopped|error|killed)$/) { print $i; exit }
          }') || state=""

  if [ "$state" = running ]; then
    sleep "$POLL"; continue
  fi

  # Not running, and there is still time on the clock. Either it finished early
  # or it died; both mean the night is not over.
  if [ -s "$R/RESULTS.md" ]; then
    say "session not running but RESULTS.md is written — treating as a clean finish"
    break
  fi

  if [ "$revivals" -ge "$MAX_REVIVALS" ]; then
    say "session keeps dying ($revivals revivals) — giving up, waking the human"
    notify upscale-research "upscale loop died" \
      "revived $revivals times and it will not stay up; $("$R/harness/guard.sh") remained" high 2>/dev/null || true
    break
  fi

  # A session that stopped because the window is spent must not be steered
  # straight back into a rate limit; wait the window out instead.
  mode=$("$R/harness/pace.sh" 2>/dev/null | awk '$1=="mode:"{print $2}') || mode=""
  if [ "$mode" = STALL ]; then
    nap=$("$R/harness/pace.sh" 2>/dev/null | awk '$1=="sleep_hint_s:"{print $2}') || nap=600
    [ "${nap:-0}" -gt 0 ] 2>/dev/null || nap=600
    [ "$nap" -gt 1800 ] && nap=1800
    say "session down and budget says STALL — waiting ${nap}s for the window"
    sleep "$nap"; continue
  fi

  revivals=$((revivals + 1))
  say "session state='${state:-unknown}' — reviving (#$revivals)"
  "$MON" steer "$ID" "You stopped, and the time guard has not expired — so the night is not over. \
Re-read research/PROGRAM.md if you have lost the thread, check research/harness/guard.sh for how long is left \
and research/harness/pace.sh for what you may spend, then continue the loop from where results.tsv leaves off. \
Do not restart the baseline if research/baseline.env already exists. Do not ask whether to continue." \
    >> "$LOG" 2>&1 || say "steer failed"

  sleep 60
done

say "supervisor exiting after $revivals revivals"
