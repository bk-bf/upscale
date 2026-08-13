#!/usr/bin/env bash
# worker.sh — THE ONLY FILE THE RESEARCH AGENT EDITS.
#
# One trial's worth of the pipeline: decode $FRAMES frames of $IN, upscale them
# 2x, encode, concatenate. Same shape as libexec/upscale-worker-remote, reduced
# to a fixed slice and with the audio remux dropped (audio is `-c copy`, costs
# nothing, and is not an optimisation target).
#
# This file is the BASELINE. It is deliberately a faithful copy of what actually
# ran for 30 hours on the Bleach library, so that anything measured against it
# is measured against production, not against a strawman.
#
# CONTRACT with harness/trial-run.sh — break any of these and the trial is
# discarded, not scored:
#
#   in    $1 = source file, $2 = output .mkv
#   env   WORK HASHDIR FRAMES MODEL MODEL_DIR BIN SCALE CRF PRESET ENC_THREADS
#         FFDEC FPS_R
#   out   $2 exists, video-only, exactly $FRAMES frames
#   out   every upscaled PNG is hardlinked into $HASHDIR  ← this is the pixel gate
#
# FROZEN — changing any of these changes the output and the trial is discarded:
#   MODEL, SCALE           the AI output itself
#   CRF, PRESET, ENC_THREADS, pix_fmt, libx264   the encoder's quality settings
#
# IN PLAY — none of these can change a pixel:
#   WORKERS, CHUNK, where files live, when phases start, what overlaps what,
#   process priority, CPU affinity, how frames are staged and deleted.
set -euo pipefail

IN="${1:?need source}"; OUT="${2:?need output}"

CHUNK=${CHUNK:-500}        # 4 chunks per trial, so overlap between them is visible

# -j load:proc:save for the upscaler, derived from the machine, never hardcoded.
#
# The three threads do very different work: `load` inflates a 1x PNG, `save`
# deflates a 2x one (four times the pixels), and `proc` mostly waits on the GPU.
# Swept on a box with a 9.6-CPU quota: save=4 gave -7.9% and left the quota
# idle, save=8 was the peak, save=16 gave back nine points to oversubscription.
# So what tracks the hardware is the SAVE count, at roughly one per usable CPU.
# The number 8 would be wrong on any other machine; the rule should not be.
#
# Usable CPUs is neither `nproc` nor the cgroup quota alone: this box reports 40
# processors, is capped at 9.6 by cpu.max, and regime B pins affinity to 4.
# Whichever is smallest is what the process actually gets.
cpus(){
  local aff quota p n
  aff=$(nproc)                       # nproc honours sched_getaffinity
  quota=$aff
  if [ -r /sys/fs/cgroup/cpu.max ]; then
    read -r p n < /sys/fs/cgroup/cpu.max
    [ "$p" != max ] && [ "${n:-0}" -gt 0 ] && quota=$((p / n))
  elif [ -r /sys/fs/cgroup/cpu/cpu.cfs_quota_us ]; then
    p=$(cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us)
    n=$(cat /sys/fs/cgroup/cpu/cpu.cfs_period_us)
    [ "${p:-0}" -gt 0 ] && [ "${n:-0}" -gt 0 ] && quota=$((p / n))
  fi
  [ "$quota" -lt "$aff" ] && aff=$quota
  [ "$aff" -lt 2 ] && aff=2
  echo "$aff"
}
CPUS=$(cpus)
SAVE=$CPUS                 # deflating 2x PNGs is the scarce work
PROC=$CPUS                 # cheap: these block on the GPU, not the CPU
LOAD=$(( CPUS / 2 )); [ "$LOAD" -lt 1 ] && LOAD=1
JOBS=${JOBS:-$LOAD:$PROC:$SAVE}

W=$WORK
FDIR="$W/frames"; UP="$W/up"; SEGDIR="$W/seg"
mkdir -p "$FDIR" "$UP" "$SEGDIR" "$HASHDIR"

say(){ printf '%s  %s\n' "$(date +%H:%M:%S)" "$*"; }
die(){ printf 'worker: %s\n' "$*" >&2; exit 1; }
pngs(){ find "$1" -maxdepth 1 -name '*.png' -type f 2>/dev/null | wc -l; }

# ---------------------------------------------------------------- 1. extract
a=$(date +%s)
# -compression_level 0: store, do not deflate. These frames are read back once
# by the upscaler and then deleted, and on a box whose CPU quota is the binding
# constraint their compression is pure cost — paid twice, once to deflate them
# and once to inflate them again. Uncompressed they are ~3x the bytes, on a disk
# with 27 GB free that nothing else is competing for. The gate hashes decoded
# content, not file bytes, so this cannot move a pixel.
"$FFDEC" -v error -nostdin -i "$IN" -frames:v "$FRAMES" -fps_mode passthrough \
         -compression_level 0 "$FDIR/%06d.png" -y
got=$(pngs "$FDIR") || got=0
[ "$got" = "$FRAMES" ] || die "extracted $got of $FRAMES"
b=$(date +%s)
say "phase extract ${a} ${b} $((b-a))s"

mapfile -t ALL < <(find "$FDIR" -maxdepth 1 -name '*.png' -type f | sort)
[ "${#ALL[@]}" = "$FRAMES" ] || die "listed ${#ALL[@]}, expected $FRAMES"

# ---------------------------------------------------------------- 2. chunks
i=0; idx=0; enc_pid=""
NCHUNK=$(( (FRAMES + CHUNK - 1) / CHUNK ))
while [ "$i" -lt "$FRAMES" ]; do
  idx=$((idx+1))
  seg=$(printf '%s/seg_%04d.mkv' "$SEGDIR" "$idx")
  n=$CHUNK; [ $((i+n)) -gt "$FRAMES" ] && n=$((FRAMES-i))

  # Select the chunk's frames by hardlink — no copy, no extra bytes. One `ln`
  # call: `ln` is an external binary, so the obvious per-frame loop forks CHUNK
  # times per chunk, in the gap between two upscale phases, with the GPU idle
  # waiting for it.
  rm -rf "$W/shard"; mkdir -p "$W/shard"
  ln -f -t "$W/shard" "${ALL[@]:i:n}"

  # ONE upscaler process carrying all the threads, not one process per shard.
  # `-j load:proc:save` defaults to 1:2:2, so N processes ran N load, 2N proc
  # and 2N save threads — the same arithmetic this asks one process for. What
  # the split cost was N model loads and N Vulkan inits per chunk, and N private
  # queues, so a chunk could not end until its slowest shard did.
  a=$(date +%s)
  "$BIN" -i "$W/shard" -o "$UP" -m "$MODEL_DIR" -n "$MODEL" -s "$SCALE" -f png \
    -j "$JOBS" \
    >/dev/null 2>>"$W/upscale.err" || die "the upscaler failed"
  b=$(date +%s)
  got=$(pngs "$UP") || got=0
  [ "$got" = "$n" ] || die "chunk $idx upscaled $got of $n"

  # The pixel gate. Hardlinks, so this costs no bytes and no copy time.
  # `-exec … +` batches the arguments; `\;` spawned one `ln` per frame.
  find "$UP" -maxdepth 1 -name '*.png' -type f -exec ln -f -t "$HASHDIR" {} +

  # Hand the chunk to a background encoder by rename, so the GPU starts the next
  # chunk immediately. Wait on the PREVIOUS encode specifically — a bare `wait`
  # would reap it and break this one.
  encdir="$W/enc_$idx"; rm -rf "$encdir"; mv "$UP" "$encdir"; mkdir -p "$UP"
  [ -n "$enc_pid" ] && { wait "$enc_pid" || die "background encode failed"; }
  (
    ffmpeg -v error -nostdin -framerate "$FPS_R" -pattern_type glob -i "$encdir/*.png" \
           -c:v libx264 -crf "$CRF" -preset "$PRESET" -threads "$ENC_THREADS" \
           -pix_fmt yuv420p "$seg" -y || exit 1
    rm -rf "$encdir"
  ) &
  enc_pid=$!

  say "phase chunk$idx ${a} ${b} $((b-a))s  $((i+n))/$FRAMES"
  i=$((i+n))
done
[ -n "$enc_pid" ] && { wait "$enc_pid" || die "final background encode failed"; }

# ---------------------------------------------------------------- 3. concat
a=$(date +%s)
find "$SEGDIR" -name 'seg_*.mkv' | sort |
  sed -e "s/'/'\\\\''/g" -e "s|^|file '|" -e "s|$|'|" > "$SEGDIR/list"
ffmpeg -v error -nostdin -f concat -safe 0 -i "$SEGDIR/list" \
       -map 0:v:0 -c copy -f matroska "$OUT.part" -y
mv "$OUT.part" "$OUT"
b=$(date +%s)
say "phase concat ${a} ${b} $((b-a))s"
say "cpus=$CPUS jobs=$JOBS"
say "done"
