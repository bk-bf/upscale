#!/usr/bin/env bash
set -uo pipefail

TAG=${1:?need a tag}
REGIME=${REGIME:-${2:-A}}
R=$(cd "$(dirname "$0")/.." && pwd)
cd "$R/.."

# shellcheck source=/dev/null
. "$R/harness/box.env"

CM="-o ControlMaster=auto -o ControlPath=/tmp/.upscale-research-%r@%h:%p -o ControlPersist=8h"
SSH="ssh $CM $BOX_SSH"

mkdir -p "$R/runs"
LOG="$R/runs/$TAG.$REGIME.txt"

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

eval "BASE=\${BASE_FPS_$REGIME:-}"
SPD="n/a"
if [ -n "${BASE:-}" ] && [ "${BASE:-0}" != "0" ]; then
  SPD=$(awk -v a="$FPS" -v b="$BASE" 'BEGIN{printf "%+.1f%%", 100*(a-b)/b}')
fi

printf 'tag=%s regime=%s fps=%s speedup=%s gate=%s%s gpu=%s idle=%s cpu=%s disk_mb=%s\n' \
  "$TAG" "$REGIME" "$FPS" "$SPD" "$GATE" "${WHY:+($WHY)}" "$GPU" "$IDLE" "$CPU" "$DISK"
