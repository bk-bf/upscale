#!/usr/bin/env bash
set -uo pipefail

SRC=${SRC:-/root/research/ep1.avi}
FRAMES=${FRAMES:-2000}
BENCH=${BENCH:-/root/bench}
MODEL=${MODEL:-animejanai-suc}
MODEL_DIR=${MODEL_DIR:-$BENCH/models_janai}
BIN=${BIN:-$BENCH/resrgan/realesrgan-ncnn-vulkan}
SCALE=${SCALE:-2}
CRF=${CRF:-18}
PRESET=${PRESET:-veryfast}
ENC_THREADS=${ENC_THREADS:-4}
FFDEC=${FFDEC:-ffmpeg}
TIMEOUT=${TIMEOUT:-1800}

RUN_ID=${RUN_ID:-adhoc}

REGIME=${REGIME:-A}
case "$REGIME" in
  A) CPUWRAP=() ;;
  B) CPUWRAP=(taskset -c 0-3) ;;
  *) echo "FATAL: unknown regime $REGIME" >&2; exit 2 ;;
esac

R=/root/research
W=$R/work
WORKER=${WORKER:-$R/worker.sh}

mkdir -p "$R"
say(){ printf '%s  %s\n' "$(date +%H:%M:%S)" "$*" >&2; }

[ -f "$SRC" ]        || { echo "FATAL: no source $SRC" >&2; exit 2; }
[ -x "$BIN" ]        || { echo "FATAL: no upscaler $BIN" >&2; exit 2; }
[ -f "$MODEL_DIR/$MODEL.param" ] || { echo "FATAL: no model $MODEL_DIR/$MODEL.param" >&2; exit 2; }
[ -f "$WORKER" ]     || { echo "FATAL: no worker $WORKER" >&2; exit 2; }

FPS_R=$(ffprobe -v error -select_streams v -show_entries stream=r_frame_rate -of csv=p=0 "$SRC") || FPS_R=""
[ -n "$FPS_R" ] || { echo "FATAL: cannot read frame rate" >&2; exit 2; }

rm -rf "$W"; mkdir -p "$W/hash"

TEL=$W/telemetry.csv
: > "$TEL"
(
  prev_idle=0; prev_total=0
  while :; do
    g=$(nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits 2>/dev/null | tr -d ' ') || g="0,0"
    read -r _ u n s i rest < /proc/stat
    idle=$i; total=$((u+n+s+i))
    for f in $rest; do total=$((total+f)); done
    if [ "$prev_total" -gt 0 ]; then
      dt=$((total-prev_total)); di=$((idle-prev_idle))
      cpu=$(( dt > 0 ? (100*(dt-di))/dt : 0 ))
    else cpu=0; fi
    prev_idle=$idle; prev_total=$total
    rss=$(ps -eo rss= 2>/dev/null | awk '{s+=$1} END{print int(s/1024)}') || rss=0
    du=$(du -sm "$W" 2>/dev/null | cut -f1) || du=0
    printf '%s,%s,%s,%s,%s\n' "$(date +%s)" "$g" "$cpu" "$rss" "$du" >> "$TEL"
    sleep 2
  done
) &
TEL_PID=$!
trap 'kill "$TEL_PID" 2>/dev/null' EXIT

OUT=$W/out.mkv
export WORK="$W" HASHDIR="$W/hash" FRAMES SRC MODEL MODEL_DIR BIN SCALE CRF PRESET \
       ENC_THREADS FFDEC FPS_R
say "trial $RUN_ID regime $REGIME: $FRAMES frames, $MODEL x$SCALE, crf$CRF $PRESET"

t0=$(date +%s.%N)
timeout "$TIMEOUT" "${CPUWRAP[@]}" bash "$WORKER" "$SRC" "$OUT" > "$W/worker.log" 2>&1
RC=$?
t1=$(date +%s.%N)
kill "$TEL_PID" 2>/dev/null

WALL=$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.1f", b-a}')

FPS_OUT=0; VID_FRAMES=0; G1=0; G2=0; G3=0; FRAME_SHA=""; X264_OPTS=""

if [ "$RC" -eq 0 ] && [ -f "$OUT" ]; then
  NHASH=$(find "$W/hash" -name '*.png' -type f 2>/dev/null | wc -l) || NHASH=0
  FRAME_SHA=$(ffmpeg -v error -nostdin -pattern_type glob -i "$W/hash/*.png" \
                -f framehash -hash sha256 - 2>/dev/null \
              | grep -v '^#' | sha256sum | cut -d' ' -f1) || FRAME_SHA=""

  head -c 400000 "$OUT" > "$W/.head" 2>/dev/null
  X264_OPTS=$(strings "$W/.head" | grep -o 'options:.*') || X264_OPTS=""
  X264_OPTS=${X264_OPTS%%$'\n'*}

  VID_FRAMES=$(ffprobe -v error -count_frames -select_streams v \
                 -show_entries stream=nb_read_frames -of csv=p=0 "$OUT" 2>/dev/null) || VID_FRAMES=0

  [ -n "$FRAME_SHA" ] && [ "$NHASH" = "$FRAMES" ] && G1=1
  [ -n "$X264_OPTS" ] && G2=1
  [ "$VID_FRAMES" = "$FRAMES" ] && G3=1
  FPS_OUT=$(awk -v f="$FRAMES" -v w="$WALL" 'BEGIN{printf "%.3f", (w>0)?f/w:0}')
fi

read -r GPU_MEAN GPU_ZERO CPU_MEAN PEAK_RSS PEAK_DISK < <(
  awk -F, 'NF>=6{n++; g+=$2; if($2+0==0)z++; c+=$4; if($5>rm)rm=$5; if($6>dm)dm=$6}
           END{ if(n==0)n=1; printf "%.1f %.1f %.1f %d %d\n", g/n, 100*z/n, c/n, rm, dm }' "$TEL" 2>/dev/null
) || { GPU_MEAN=0; GPU_ZERO=0; CPU_MEAN=0; PEAK_RSS=0; PEAK_DISK=0; }

cat <<EOF
---
run_id:           $RUN_ID
regime:           $REGIME
rc:               $RC
fps:              $FPS_OUT
wall_seconds:     $WALL
frames:           $FRAMES
frames_out:       $VID_FRAMES
frame_sha:        ${FRAME_SHA:-none}
x264_opts_sha:    $(printf '%s' "$X264_OPTS" | sha256sum | cut -d' ' -f1)
g1_pixels:        $G1
g2_encoder:       $G2
g3_shape:         $G3
gpu_util_mean:    $GPU_MEAN
gpu_idle_pct:     $GPU_ZERO
cpu_util_mean:    $CPU_MEAN
peak_rss_mb:      $PEAK_RSS
peak_disk_mb:     $PEAK_DISK
EOF
[ "$RC" -ne 0 ] && { echo "--- worker tail ---"; tail -n 20 "$W/worker.log"; }
exit 0
