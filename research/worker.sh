#!/usr/bin/env bash
set -euo pipefail

IN="${1:?need source}"; OUT="${2:?need output}"

WORKERS=${WORKERS:-4}
CHUNK=${CHUNK:-500}

W=$WORK
FDIR="$W/frames"; UP="$W/up"; SEGDIR="$W/seg"
mkdir -p "$FDIR" "$UP" "$SEGDIR" "$HASHDIR"

say(){ printf '%s  %s\n' "$(date +%H:%M:%S)" "$*"; }
die(){ printf 'worker: %s\n' "$*" >&2; exit 1; }
pngs(){ find "$1" -maxdepth 1 -name '*.png' -type f 2>/dev/null | wc -l; }

a=$(date +%s)
"$FFDEC" -v error -nostdin -i "$IN" -frames:v "$FRAMES" -fps_mode passthrough \
         "$FDIR/%06d.png" -y
got=$(pngs "$FDIR") || got=0
[ "$got" = "$FRAMES" ] || die "extracted $got of $FRAMES"
b=$(date +%s)
say "phase extract ${a} ${b} $((b-a))s"

mapfile -t ALL < <(find "$FDIR" -maxdepth 1 -name '*.png' -type f | sort)
[ "${#ALL[@]}" = "$FRAMES" ] || die "listed ${#ALL[@]}, expected $FRAMES"

i=0; idx=0; enc_pid=""
NCHUNK=$(( (FRAMES + CHUNK - 1) / CHUNK ))
while [ "$i" -lt "$FRAMES" ]; do
  idx=$((idx+1))
  seg=$(printf '%s/seg_%04d.mkv' "$SEGDIR" "$idx")
  n=$CHUNK; [ $((i+n)) -gt "$FRAMES" ] && n=$((FRAMES-i))

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

  find "$UP" -maxdepth 1 -name '*.png' -type f -exec ln -f {} "$HASHDIR/" \;

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

a=$(date +%s)
find "$SEGDIR" -name 'seg_*.mkv' | sort |
  sed -e "s/'/'\\\\''/g" -e "s|^|file '|" -e "s|$|'|" > "$SEGDIR/list"
ffmpeg -v error -nostdin -f concat -safe 0 -i "$SEGDIR/list" \
       -map 0:v:0 -c copy -f matroska "$OUT.part" -y
mv "$OUT.part" "$OUT"
b=$(date +%s)
say "phase concat ${a} ${b} $((b-a))s"
say "done"
