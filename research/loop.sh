#!/usr/bin/env bash
# loop.sh — the autoresearcher's own session manager. Owns the night.
#
#   research/loop.sh          run it (usually as a systemd user unit)
#
# This script starts Claude sessions, decides when the next one begins, and
# stops when the guard expires. `mon` is not involved in any of that: it only
# gets shown the session, via harness/session.py writing an incident and a
# transcript into its store, so the work is readable from a phone and tagged as
# a loop on the dashboard.
#
# That split is deliberate and was learned the hard way. mon caps every session
# it runs with a timer started once and fired unconditionally — a hard wall
# clock, not an idle timeout — so the first attempt at an eight-hour loop died
# at fifteen minutes with "session timed out", and `mon steer` could not revive
# it because a session that never finished has no id to resume.
#
# The night is therefore a CHAIN of sessions. The loop was built for that
# without knowing it: every piece of state a link needs is already on disk —
# results.tsv, the git branch, baseline.env — so no single process has to hold
# it, and a link being cut off costs at most the trial it was running.
set -uo pipefail

R=$(cd "$(dirname "$0")" && pwd)
LOG="$R/runs/loop.log"
NOTIFY=${NOTIFY:-$HOME/.local/bin/notify}   # absolute: a systemd unit's PATH has no ~/.local/bin
MODEL=${MODEL:-opus}
MAX_LINKS=${MAX_LINKS:-40}
MIN_LIFE=${MIN_LIFE:-90}                    # a link dying faster than this is broken, not finished

mkdir -p "$R/runs"
say(){ printf '%s  %s\n' "$(date '+%F %T')" "$*" >> "$LOG"; }

PROMPT_FILE="$R/runs/.continue.md"
cat > "$PROMPT_FILE" <<'EOF'
Continue the overnight optimisation loop for this repo. A previous session was
running it and has ended — that is normal, not a failure. The night is
deliberately run as a chain of sessions and you are the next link.

Pick up exactly where the last one stopped. Everything you need is on disk:

  git log --oneline -8              what has been kept so far
  cat research/results.tsv          every trial, including the discards
  cat research/baseline.env         the number to beat (absent = baseline not done)
  research/harness/guard.sh         how long is left
  research/harness/pace.sh          what you may spend

Read research/PROGRAM.md first — it is the contract. If research/baseline.env
does not exist, your job is PROGRAM.md's "Setup" section: establish the
baseline (3 trials regime A, 2 regime B, then set-baseline.sh) and fill in
BASELINE.md §3. If it does exist, do NOT redo the baseline; continue the
experiment loop from where results.tsv leaves off.

Your session will end eventually too, so CHECKPOINT AFTER EVERY TRIAL: append
its row to research/results.tsv and commit before starting the next one. Then
a link ending mid-thought costs one trial rather than an evening.

If research/STEER.txt exists, read it first — it is a message from Kirill — and
then delete it so the next link does not act on it twice.

Do not stop, do not ask whether to continue, do not wait for a human — he is
asleep. The loop ends when guard.sh exits non-zero, and not before.
EOF

say "loop manager up: model=$MODEL, max $MAX_LINKS links, notify=$NOTIFY"
links=0
short_deaths=0

while :; do
  if ! "$R/harness/guard.sh" >/dev/null 2>&1; then
    say "guard expired — the night is over"
    break
  fi

  if [ -s "$R/RESULTS.md" ]; then
    say "RESULTS.md is written — finished cleanly"
    break
  fi

  if [ "$links" -ge "$MAX_LINKS" ]; then
    say "ran $links links and it is still not done — stopping rather than churning"
    "$NOTIFY" upscale-research "upscale loop stopped" \
      "ran $links sessions without finishing; $("$R/harness/guard.sh" 2>&1) remained" high \
      >> "$LOG" 2>&1 || say "notify failed"
    break
  fi

  # A spent window cannot be worked around by starting another session; it can
  # only be waited out. Trials cost no tokens, so this is also when a queued
  # sweep would be grinding away on the box.
  mode=$("$R/harness/pace.sh" 2>/dev/null | awk '$1=="mode:"{print $2}') || mode=""
  if [ "$mode" = STALL ]; then
    nap=$("$R/harness/pace.sh" 2>/dev/null | awk '$1=="sleep_hint_s:"{print $2}') || nap=900
    [ "${nap:-0}" -gt 0 ] 2>/dev/null || nap=900
    [ "$nap" -gt 1800 ] && nap=1800
    say "budget says STALL — waiting ${nap}s rather than starting into a rate limit"
    sleep "$nap"
    continue
  fi

  links=$((links + 1))
  began=$(date +%s)
  say "link $links starting"
  ID=$(python3 "$R/harness/session.py" \
         --prompt-file "$PROMPT_FILE" \
         --project "$R/.." \
         --title "overnight upscale optimisation loop (link $links)" \
         --tag loop --model "$MODEL" 2>>"$LOG" | sed -n 1p) || ID=""
  rc=$?
  lived=$(( $(date +%s) - began ))
  say "link $links ended after ${lived}s (rc=$rc, id=${ID:-none})"

  # A link that dies almost immediately is broken rather than merely finished —
  # no permission to run anything, the model unavailable, claude missing. Do not
  # spend the whole allowance discovering that forty times.
  if [ "$lived" -lt "$MIN_LIFE" ]; then
    short_deaths=$((short_deaths + 1))
    if [ "$short_deaths" -ge 3 ]; then
      say "three links in a row died inside ${MIN_LIFE}s — something is broken, not finished"
      "$NOTIFY" upscale-research "upscale loop cannot run" \
        "sessions die within ${MIN_LIFE}s; $("$R/harness/guard.sh" 2>&1) remained" high \
        >> "$LOG" 2>&1 || say "notify failed"
      break
    fi
    sleep 30
  else
    short_deaths=0
  fi
done

say "loop manager exiting after $links links"
