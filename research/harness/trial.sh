#!/usr/bin/env bash
# trial.sh — run one trial on the box and print ONE line. Run from the repo root.
#
#   research/harness/trial.sh <tag>
#
# One line is the point. This loop runs for eight hours and perhaps a hundred
# trials; if each one dumps a log into the agent's context, the agent runs out
# of context long before it runs out of night. Everything else is written to
# research/runs/<tag>.txt and read only when a trial actually needs debugging.
set -uo pipefail

TAG=${1:?need a tag}
# Regime A is the whole box; regime B starves the CPU to emulate the balance a
# real production box has. See trial-run.sh. A keeper must win in BOTH.
REGIME=${REGIME:-${2:-A}}
R=$(cd "$(dirname "$0")/.." && pwd)
cd "$R/.."

# shellcheck source=/dev/null
. "$R/harness/box.env"

# A shared connection: 4.3 s cold vs 0.7 s multiplexed, measured. Over a hundred
# trials that is an hour of ssh handshakes not spent.
CM="-o ControlMaster=auto -o ControlPath=/tmp/.upscale-research-%r@%h:%p -o ControlPersist=8h"
SSH="ssh $CM $BOX_SSH"

mkdir -p "$R/runs"
LOG="$R/runs/$TAG.$REGIME.txt"

# One GPU, so one trial. This is a correctness lock, not a convenience: the
# night runs as a chain of sessions and mon cuts each one off at a fixed wall
# clock, so a session can be killed with a trial still running on the box while
# the next session starts. Two trials sharing the card would not fail — they
# would quietly produce two wrong fps numbers, which is worse.
exec 9>"$R/runs/.trial.lock"
if ! flock -w "${LOCK_WAIT:-1800}" 9; then
  echo "tag=$TAG regime=$REGIME gate=FAIL(another-trial-holds-the-gpu)"
  exit 1
fi

# Push the two files that can change. Everything else on the box is fixed.
scp -q $CM -P "${BOX_PORT}" "$R/worker.sh" "$R/harness/trial-run.sh" \
    "${BOX_USER}@${BOX_HOST}:/root/research/" || { echo "tag=$TAG gate=FAIL(push)"; exit 1; }

$SSH "RUN_ID='$TAG' REGIME='$REGIME' FRAMES='${FRAMES:-2000}' bash /root/research/trial-run.sh" \
    > "$LOG" 2>>"$R/runs/$TAG.$REGIME.err"

get(){ grep -m1 "^$1:" "$LOG" 2>/dev/null | awk '{print $2}'; }

FPS=$(get fps);            FPS=${FPS:-0}
RC=$(get rc);              RC=${RC:-1}
G1=$(get g1_pixels);       G1=${G1:-0}
G2=$(get g2_encoder);      G2=${G2:-0}
G3=$(get g3_shape);        G3=${G3:-0}
SHA=$(get frame_sha)
XSHA=$(get x264_opts_sha)
GPU=$(get gpu_util_mean);  IDLE=$(get gpu_idle_pct)
CPU=$(get cpu_util_mean);  DISK=$(get peak_disk_mb)

# ------------------------------------------------------------------ the gate
# Evaluated BEFORE fps is allowed to mean anything.
GATE=PASS; WHY=""
if [ -f "$R/baseline.env" ]; then
  # shellcheck source=/dev/null
  . "$R/baseline.env"
  [ "$SHA"  = "$BASE_FRAME_SHA" ] || { GATE=FAIL; WHY="pixels-differ"; }
  [ "$XSHA" = "$BASE_X264_SHA"  ] || { GATE=FAIL; WHY="${WHY:+$WHY,}encoder-settings-changed"; }
fi
[ "$RC" = 0 ] || { GATE=FAIL; WHY="crash(rc=$RC)"; }
[ "$G1" = 1 ] || { GATE=FAIL; WHY="${WHY:+$WHY,}no-frames-to-hash"; }
[ "$G2" = 1 ] || { GATE=FAIL; WHY="${WHY:+$WHY,}encoder-unreadable"; }
[ "$G3" = 1 ] || { GATE=FAIL; WHY="${WHY:+$WHY,}frame-count"; }

# Compared against the baseline for THIS regime — the two are different numbers
# and comparing across them would manufacture a speedup out of thin air.
eval "BASE=\${BASE_FPS_$REGIME:-}"
SPD="n/a"
if [ -n "${BASE:-}" ] && [ "${BASE:-0}" != "0" ]; then
  SPD=$(awk -v a="$FPS" -v b="$BASE" 'BEGIN{printf "%+.1f%%", 100*(a-b)/b}')
fi

printf 'tag=%s regime=%s fps=%s speedup=%s gate=%s%s gpu=%s idle=%s cpu=%s disk_mb=%s\n' \
  "$TAG" "$REGIME" "$FPS" "$SPD" "$GATE" "${WHY:+($WHY)}" "$GPU" "$IDLE" "$CPU" "$DISK"
