#!/usr/bin/env bash
# supervise.sh — keep the overnight loop running until the guard expires.
#
#   research/supervise.sh [mon-session-id]
#
# WHY THIS EXISTS IN THIS SHAPE
#
# mon caps a session's wall clock — `threading.Timer(limit, expire)`, started
# once, fired unconditionally. It is a hard cap, not an idle timer, so a session
# is killed at the limit no matter how busy it is. An eight-hour loop inside one
# mon session is therefore impossible; the first attempt died at the 15-minute
# default with "session timed out".
#
# So the night is a CHAIN of sessions, not one long one, and this script is what
# chains them. The loop was built for this without knowing it: every piece of
# state a session needs is already on disk — results.tsv, the git branch,
# baseline.env — precisely so no single process has to hold it.
#
# `mon steer` cannot be the revival mechanism. A session that timed out has no
# session id to resume, and steer answers "that incident has no session to
# continue" — which the first supervisor then burned all twelve of its retries
# on, in two minutes, without ever starting a session.
set -uo pipefail

R=$(cd "$(dirname "$0")" && pwd)
MON=$(command -v mon) || MON=$HOME/Documents/Projects/mon/mon
# systemd user units get a minimal PATH — /usr/bin:/bin — and ~/.local/bin is
# not on it. The last supervisor called `notify` unqualified, it was not found,
# and `|| true` swallowed it: it gave up after twelve revivals and told nobody.
NOTIFY=${NOTIFY:-$HOME/.local/bin/notify}
LOG="$R/runs/supervisor.log"
POLL=${POLL:-60}
MAX_STARTS=${MAX_STARTS:-40}        # ~8h of 1h sessions, with room for churn
MIN_LIFE=${MIN_LIFE:-90}            # a session dying faster than this is broken

ID=${1:-}
mkdir -p "$R/runs"
say(){ printf '%s  %s\n' "$(date '+%F %T')" "$*" >> "$LOG"; }

state_of(){
  "$MON" sessions 2>/dev/null | awk -v id="$1" '$0 ~ id {
      for (i=1; i<=NF; i++)
        if ($i ~ /^(running|done|failed|stopped|error|killed)$/) { print $i; exit }
    }'
}

read -r -d '' CONTINUE <<'EOF' || true
Continue the overnight optimisation loop for this repo. A previous session was
running it and has ended — that is normal and expected, not a failure: mon caps
every session's wall clock, so the night is deliberately run as a chain of
sessions and you are the next link. Nothing is wrong.

Pick up exactly where the last one stopped. Everything you need is on disk:

  git log --oneline -8              what has been kept so far
  cat research/results.tsv          every trial, including the discards
  cat research/baseline.env         the number to beat (absent = baseline not done yet)
  research/harness/guard.sh         how long is left
  research/harness/pace.sh          what you may spend

Read research/PROGRAM.md first — it is the contract. If research/baseline.env
does not exist, your job is PROGRAM.md's "Setup" section: establish the baseline
(3 trials regime A, 2 regime B, then set-baseline.sh) and fill BASELINE.md §3.
If it does exist, do NOT redo the baseline; continue the experiment loop from
where results.tsv leaves off.

Because your session will also be cut off eventually, CHECKPOINT AFTER EVERY
TRIAL: append its row to research/results.tsv and commit, before starting the
next one. Then a session ending mid-thought costs one trial, not an evening.

Do not stop, do not ask whether to continue, do not wait for a human — he is
asleep. The loop ends when guard.sh exits non-zero, and not before.
EOF

say "supervising (poll ${POLL}s, max $MAX_STARTS sessions, notify=$NOTIFY)"
starts=0

while :; do
  if ! "$R/harness/guard.sh" >/dev/null 2>&1; then
    say "guard expired — supervision over"; break
  fi

  if [ -n "$ID" ] && [ "$(state_of "$ID")" = running ]; then
    sleep "$POLL"; continue
  fi

  if [ -s "$R/RESULTS.md" ]; then
    say "RESULTS.md is written — the night finished cleanly"; break
  fi

  if [ "$starts" -ge "$MAX_STARTS" ]; then
    say "started $starts sessions and it will not stay up — giving up"
    "$NOTIFY" upscale-research "upscale loop gave up" \
      "started $starts sessions, none stuck; $("$R/harness/guard.sh" 2>&1) remained" high \
      >> "$LOG" 2>&1 || say "notify failed"
    break
  fi

  # A window that is spent cannot be worked around by starting another session.
  mode=$("$R/harness/pace.sh" 2>/dev/null | awk '$1=="mode:"{print $2}') || mode=""
  if [ "$mode" = STALL ]; then
    nap=$("$R/harness/pace.sh" 2>/dev/null | awk '$1=="sleep_hint_s:"{print $2}') || nap=900
    [ "${nap:-0}" -gt 0 ] 2>/dev/null || nap=900
    [ "$nap" -gt 1800 ] && nap=1800
    say "budget says STALL — waiting ${nap}s rather than starting a session into a rate limit"
    sleep "$nap"; continue
  fi

  starts=$((starts + 1))
  began=$(date +%s)
  say "starting session #$starts"
  out=$("$MON" run "$CONTINUE" --project "$R/.." \
          --title "overnight upscale optimisation loop (link $starts)" \
          --mode acceptEdits --by kirill --tag loop 2>&1) || out=""
  NEW=$(printf '%s\n' "$out" | grep -oE '[0-9]{8}-[0-9]{6}-[0-9a-f]{12}' | sed -n 1p) || NEW=""

  if [ -z "$NEW" ]; then
    say "could not start a session: ${out:0:200}"
    sleep 120; continue
  fi
  ID=$NEW
  say "session $ID is link $starts"

  # A session that dies almost immediately is broken rather than merely capped —
  # no permission to run anything, mon misconfigured, the model unavailable. Do
  # not spin through the whole allowance discovering that forty times.
  sleep "$MIN_LIFE"
  if [ "$(state_of "$ID")" != running ] && [ "$starts" -ge 3 ]; then
    say "three sessions in a row died inside ${MIN_LIFE}s — this is not the cap, something is broken"
    "$NOTIFY" upscale-research "upscale loop cannot start" \
      "sessions die within ${MIN_LIFE}s of starting; $("$R/harness/guard.sh" 2>&1) remained" high \
      >> "$LOG" 2>&1 || say "notify failed"
    break
  fi
done

say "supervisor exiting after $starts sessions"
