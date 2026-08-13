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

WORKERS=${WORKERS:-4}      # measured optimum on the old box under a 9.6 CPU quota
CHUNK=${CHUNK:-500}        # 4 chunks per trial, so overlap between them is visible

W=$WORK
FDIR="$W/frames"; UP="$W/up"; SEGDIR="$W/seg"
mkdir -p "$FDIR" "$UP" "$SEGDIR" "$HASHDIR"

say(){ printf '%s  %s\n' "$(date +%H:%M:%S)" "$*"; }
die(){ printf 'worker: %s\n' "$*" >&2; exit 1; }
pngs(){ find "$1" -maxdepth 1 -name '*.png' -type f 2>/dev/null | wc -l; }

# ---------------------------------------------------------------- 1. extract
a=$(date +%s)
# -compression_level 1: ffmpeg's PNG encoder defaults to a slow zlib level, and
# these frames are read back once by the upscaler and then deleted. Cheap zlib
# pays twice — less work to write them, less to inflate them again. The gate
# hashes decoded content, not file bytes, so this cannot move a pixel.
"$FFDEC" -v error -nostdin -i "$IN" -frames:v "$FRAMES" -fps_mode passthrough \
         -compression_level 1 "$FDIR/%06d.png" -y
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

  # Shard the chunk across workers by hardlink — no copy, no extra bytes.
  rm -rf "$W/shard"; mkdir -p "$W/shard"
  for ((k=i; k<i+n; k++)); do
    d="$W/shard/$(( (k-i) % WORKERS ))"; mkdir -p "$d"; ln -f "${ALL[$k]}" "$d/"
  done

  a=$(date +%s)
  wpids=()
  for ((p=0; p<WORKERS; p++)); do
    "$BIN" -i "$W/shard/$p" -o "$UP" -m "$MODEL_DIR" -n "$MODEL" -s "$SCALE" -f png \
      >/dev/null 2>>"$W/upscale.err" &
    wpids+=($!)
  done
  for wp in "${wpids[@]}"; do wait "$wp" || die "an upscale worker failed"; done
  b=$(date +%s)
  got=$(pngs "$UP") || got=0
  [ "$got" = "$n" ] || die "chunk $idx upscaled $got of $n"

  # The pixel gate. Hardlinks, so this costs no bytes and no copy time.
  find "$UP" -maxdepth 1 -name '*.png' -type f -exec ln -f {} "$HASHDIR/" \;

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
say "done"
