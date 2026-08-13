#!/usr/bin/env bash
# pace.sh — the budget governor. Answers one question: "may I spend, and how much?"
#
# The failure this exists to prevent: burning the whole 5-hour allowance in the
# first three hours and then sitting rate-limited through the rest of the night.
# An eight-hour loop that idles for two of them did six hours of work.
#
# It reads Anthropic's OWN utilisation figure — not an estimate reconstructed
# from token counts. `/api/plan` on the usage dashboard proxies
# GET https://api.anthropic.com/api/oauth/usage, which returns the same
# `five_hour` percentage claude.ai shows on its Usage page. A cost-proxy was
# tried for this and does not work: ccusage buckets into clock-aligned 5h
# blocks while Anthropic's window rolls from the first message, so the two
# never line up.
#
# The pacing rule is a straight line. At any moment inside the window,
# spending SHOULD be at TARGET x (fraction of window elapsed). Ahead of that
# line, slow down; behind it, spend the surplus on breadth. The surplus is real
# work, not filler: more hypotheses examined per trial, deeper analysis of the
# telemetry, a second opinion on a diff before it burns a trial slot.
#
# Why 5-hour pacing is even possible here: a trial is ~5 minutes of GPU wall
# clock during which the agent spends nothing. Tokens go on thinking between
# trials, and that is the dial this turns.
set -uo pipefail

API=${API:-https://dashboard.callmedaddy.dedyn.io/api/plan}
TARGET=${TARGET:-92}       # aim to finish each window at this %; user asked for 90+
CEIL=${CEIL:-96}           # above this, stop spending and let the window roll
WINDOW=18000               # 5h in seconds
R=$(cd "$(dirname "$0")/.." && pwd)

J=$(curl -sk --max-time 20 "$API" 2>/dev/null) || J=""
if [ -z "$J" ]; then
  # The governor must never be the reason the loop stops. Unknown budget is
  # treated as "proceed, but do not expand".
  echo "mode:            NOMINAL"
  echo "five_hour_pct:   unknown"
  echo "note:            dashboard unreachable — pacing blind, holding steady"
  echo "side_agents:     1"
  echo "sleep_hint_s:    0"
  exit 0
fi

read -r P RESET P7 < <(printf '%s' "$J" | python3 -c '
import sys, json, datetime
d = json.load(sys.stdin)
fh = d.get("five_hour") or {}
sd = d.get("seven_day") or {}
p = fh.get("pct")
r = fh.get("resets_at")
ep = 0
if r:
    ep = int(datetime.datetime.fromisoformat(r).timestamp())
print(p if p is not None else -1, ep, sd.get("pct") if sd.get("pct") is not None else -1)
') || { echo "mode:            NOMINAL"; echo "note:            unparseable plan payload"; echo "side_agents:     1"; echo "sleep_hint_s:    0"; exit 0; }

NOW=$(date +%s)
MODE=NOMINAL; SLEEP=0; AGENTS=1; NOTE=""

if [ "${RESET:-0}" -gt "$NOW" ]; then
  START=$((RESET - WINDOW))
  ELAPSED=$(awk -v n="$NOW" -v s="$START" -v w="$WINDOW" 'BEGIN{f=(n-s)/w; if(f<0)f=0; if(f>1)f=1; printf "%.4f", f}')
  LEFT=$((RESET - NOW))
else
  ELAPSED=0; LEFT=$WINDOW
fi

TGT=$(awk -v t="$TARGET" -v e="$ELAPSED" 'BEGIN{printf "%.1f", t*e}')
DRIFT=$(awk -v p="$P" -v t="$TGT" 'BEGIN{printf "%+.1f", p-t}')

# Weekly cap outranks the 5-hour pacing: exhausting it ends the night entirely.
if awk -v p="$P7" 'BEGIN{exit !(p>=95)}'; then
  MODE=STALL; AGENTS=0; SLEEP=1800
  NOTE="seven-day cap nearly spent — protecting it"
elif awk -v p="$P" -v c="$CEIL" 'BEGIN{exit !(p>=c)}'; then
  MODE=STALL; AGENTS=0; SLEEP=$LEFT
  NOTE="window nearly spent — queue unattended trials and stop reasoning until reset"
elif awk -v d="$DRIFT" 'BEGIN{exit !(d>6)}'; then
  MODE=THROTTLE; AGENTS=0; SLEEP=0
  NOTE="ahead of pace — no side agents, minimal reasoning, let trials carry the wall clock"
elif awk -v d="$DRIFT" 'BEGIN{exit !(d<-8)}'; then
  MODE=EXPAND; AGENTS=2; SLEEP=0
  NOTE="behind pace — spend the surplus on breadth, not on idling"
else
  NOTE="on pace"
fi

printf 'mode:            %s\n' "$MODE"
printf 'five_hour_pct:   %s\n' "$P"
printf 'target_pct_now:  %s\n' "$TGT"
printf 'drift:           %s\n' "$DRIFT"
printf 'window_left_min: %s\n' "$((LEFT / 60))"
printf 'seven_day_pct:   %s\n' "$P7"
printf 'side_agents:     %s\n' "$AGENTS"
printf 'sleep_hint_s:    %s\n' "$SLEEP"
printf 'note:            %s\n' "$NOTE"

if [ -f "$R/.deadline" ]; then
  D=$(cat "$R/.deadline") || D=0
  printf 'loop_left_min:   %s\n' "$(( (D - NOW) / 60 ))"
fi
