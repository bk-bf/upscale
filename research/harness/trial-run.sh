#!/usr/bin/env bash
# trial-run.sh — one trial, on the box. READ-ONLY: the research agent never edits this.
#
# This is the `prepare.py` of the setup. It owns:
#   * the fixed work      — a pinned frame count of a pinned source file
#   * the metric          — fps = frames / wall seconds, HIGHER is better
#   * the quality gate    — pass/fail, evaluated BEFORE the metric is allowed to count
#   * the telemetry       — where the wall time actually went
#
# It invokes `worker.sh`, which is the ONLY file the agent may edit.
#
# THE GATE, and why it is a gate rather than a term in the score
# --------------------------------------------------------------
# fps says nothing about quality. An agent maximising fps alone rediscovers, in
# its first hour, every configuration this project already rejected with human
# eyes: a smaller model, frame dedup, a faster preset, a lower CRF. PSNR cannot
# rescue it either — the upscale is *supposed* to differ from the source, so a
# higher PSNR would mean less upscaling.
#
# So quality is not scored, it is constrained:
#
#   G1  the set of upscaled frames is BIT-IDENTICAL to the baseline's
#   G2  the x264 option string in the output is BYTE-IDENTICAL to the baseline's
#   G3  the output has exactly FRAMES video frames
#
# A trial that fails any gate is discarded without its fps being considered.
# This is not a soft preference: a one-pixel difference is a discard.
#
# What that leaves in play is exactly the headroom worth having — scheduling,
# concurrency, I/O placement, overlap. All of it bit-identical by construction.
# What it rules out is every quality trade, automatically, in seconds.
#
# G1 requires the upscaled frames to be visible to this script, so the worker
# must hardlink them into $W/hash (hardlinks are free; production already
# hardlinks its shards). Frames in tmpfs satisfy this perfectly well — RAM
# staging is in scope. Removing the PNG stage altogether is NOT in scope for
# this loop; it would have nothing to hash.
set -uo pipefail

# ------------------------------------------------------------------ constants
# Frozen for the whole run. Changing one invalidates every comparison.
SRC=${SRC:-/root/research/ep1.avi}
FRAMES=${FRAMES:-2000}            # fixed work per trial
BENCH=${BENCH:-/root/bench}
MODEL=${MODEL:-animejanai-suc}
MODEL_DIR=${MODEL_DIR:-$BENCH/models_janai}
BIN=${BIN:-$BENCH/resrgan/realesrgan-ncnn-vulkan}
SCALE=${SCALE:-2}
CRF=${CRF:-18}
PRESET=${PRESET:-veryfast}
ENC_THREADS=${ENC_THREADS:-4}
FFDEC=${FFDEC:-ffmpeg}
TIMEOUT=${TIMEOUT:-1800}          # a trial that runs this long is a failure

RUN_ID=${RUN_ID:-adhoc}

# ------------------------------------------------------------------ regimes
# A win that only exists on one machine is not a win — this box was rented
# cheap and is deliberately bad, and nothing here will ever run in production.
# So every trial declares which hardware BALANCE it ran under, and a change has
# to survive both before it counts:
#
#   A  the whole box — 9.6 CPU quota against a weak GTX 1660 SUPER, so the GPU
#      is the scarce resource
#   B  four cores — CPU is now the scarce resource, which is the balance a real
#      production box has (rental-B ran a 5070 Ti behind the same 9.6 quota:
#      the GPU vastly outran the CPU feeding it)
#
# The GPU cannot be made faster, so relative speed is changed from the other
# side, by starving the CPU. A change that helps under both balances is
# structural. A change that helps under only one is a tuned constant, and tuned
# constants do not transfer: the measured optimum was 8 workers on the desktop
# and 4 on rental-B, for the same pipeline.
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

# ------------------------------------------------------------------ preflight
[ -f "$SRC" ]        || { echo "FATAL: no source $SRC" >&2; exit 2; }
[ -x "$BIN" ]        || { echo "FATAL: no upscaler $BIN" >&2; exit 2; }
[ -f "$MODEL_DIR/$MODEL.param" ] || { echo "FATAL: no model $MODEL_DIR/$MODEL.param" >&2; exit 2; }
[ -f "$WORKER" ]     || { echo "FATAL: no worker $WORKER" >&2; exit 2; }

FPS_R=$(ffprobe -v error -select_streams v -show_entries stream=r_frame_rate -of csv=p=0 "$SRC") || FPS_R=""
[ -n "$FPS_R" ] || { echo "FATAL: cannot read frame rate" >&2; exit 2; }

# A previous trial must not leak into this one. Everything under $W dies.
rm -rf "$W"; mkdir -p "$W/hash"

# ------------------------------------------------------------------ telemetry
# Sampled by the harness, not self-reported by the worker: a worker that
# restructures its phases can no longer be trusted to attribute its own time,
# but the wall clock and the device counters do not care how the code is shaped.
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

# ------------------------------------------------------------------ the trial
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

# ------------------------------------------------------------------ verdicts
FPS_OUT=0; VID_FRAMES=0; G1=0; G2=0; G3=0; FRAME_SHA=""; X264_OPTS=""

if [ "$RC" -eq 0 ] && [ -f "$OUT" ]; then
  # G1 — the pixels. Note what is hashed: DECODED FRAME CONTENT, via ffmpeg's
  # framehash muxer, not the bytes of the .png files.
  #
  # That distinction is the whole gate. PNG is lossless but not canonical: zlib
  # level, filter choice and row strategy all change the file while decoding to
  # identical pixels. Hashing file bytes would therefore reject
  # `-compression_level 1` on extraction — which is a free win, one of the
  # largest available, and touches no pixel. Hashing decoded content accepts it
  # and still rejects a changed model, a changed scale, or a tile seam.
  NHASH=$(find "$W/hash" -name '*.png' -type f 2>/dev/null | wc -l) || NHASH=0
  FRAME_SHA=$(ffmpeg -v error -nostdin -pattern_type glob -i "$W/hash/*.png" \
                -f framehash -hash sha256 - 2>/dev/null \
              | grep -v '^#' | sha256sum | cut -d' ' -f1) || FRAME_SHA=""

  # G2 — the encoder settings, read back from what x264 actually wrote into the
  # bitstream, not from what the script claims it passed. The SEI carries the
  # full option line, `threads=` and `crf=` included, so a quality trade cannot
  # hide behind a script that still *says* crf 18.
  #
  # No early-exiting stage anywhere in this pipeline. `grep -m1` and `head -1`
  # both exit as soon as they have their line, which SIGPIPEs the producer, and
  # under `pipefail` a SIGPIPEd producer reads as a failed pipeline — so the
  # capture silently becomes the empty string and the gate quietly stops
  # gating. AGENTS.md records this trap shipping twice; it caught this script
  # twice more during setup. Take every match, then trim in the shell.
  head -c 400000 "$OUT" > "$W/.head" 2>/dev/null
  X264_OPTS=$(strings "$W/.head" | grep -o 'options:.*') || X264_OPTS=""
  X264_OPTS=${X264_OPTS%%$'\n'*}

  # G3 — the shape. Counting is not identity, but a wrong count is always wrong.
  VID_FRAMES=$(ffprobe -v error -count_frames -select_streams v \
                 -show_entries stream=nb_read_frames -of csv=p=0 "$OUT" 2>/dev/null) || VID_FRAMES=0

  [ -n "$FRAME_SHA" ] && [ "$NHASH" = "$FRAMES" ] && G1=1
  [ -n "$X264_OPTS" ] && G2=1
  [ "$VID_FRAMES" = "$FRAMES" ] && G3=1
  FPS_OUT=$(awk -v f="$FRAMES" -v w="$WALL" 'BEGIN{printf "%.3f", (w>0)?f/w:0}')
fi

# telemetry summary
read -r GPU_MEAN GPU_ZERO CPU_MEAN PEAK_RSS PEAK_DISK < <(
  # The trailing newline matters: without it `read` hits EOF and returns 1, the
  # `||` fallback fires, and every telemetry field silently reads zero.
  awk -F, 'NF>=6{n++; g+=$2; if($2+0==0)z++; c+=$4; if($5>rm)rm=$5; if($6>dm)dm=$6}
           END{ if(n==0)n=1; printf "%.1f %.1f %.1f %d %d\n", g/n, 100*z/n, c/n, rm, dm }' "$TEL" 2>/dev/null
) || { GPU_MEAN=0; GPU_ZERO=0; CPU_MEAN=0; PEAK_RSS=0; PEAK_DISK=0; }

# ------------------------------------------------------------------ the block
# Same shape as autoresearch's train.py summary: one grep-able key per line.
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
