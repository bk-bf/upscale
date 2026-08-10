#!/usr/bin/env bash
# Per-frame "fraction of pixels that changed since the previous frame".
#
# Computed entirely in ffmpeg so it needs no numpy:
#   format=gray            full 640x480, no thumbnailing - localised motion
#                          (a mouth, a slow pan) must not be averaged away
#   tblend=difference      |frame[n] - frame[n-1]|
#   lutyuv if(gt(val,8))   binarise: ignore per-pixel XviD noise below 8/255
#   signalstats YAVG       mean of the binarised plane = 255 * changed-fraction
#
# Output: one "<frame-index> <changed-fraction>" per line.
set -e
SRC="$1"; OUT="$2"
ffmpeg -v error -i "$SRC" -an -vf \
  "format=gray,tblend=all_mode=difference,lutyuv=y='if(gt(val,8),255,0)',signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-" \
  -f null - 2>/dev/null \
  | awk '/^frame:/ {n=$2} /YAVG=/ {split($0,a,"="); printf "%d %.6f\n", n, a[2]/255}' > "$OUT"
echo "wrote $(wc -l < "$OUT") frame metrics to $OUT"
echo "distribution of changed-fraction:"
awk '{print $2}' "$OUT" | sort -g | awk '
  {v[NR]=$1}
  END{printf "  min %.5f  p10 %.5f  p25 %.5f  median %.5f  p75 %.5f  max %.5f\n",
      v[1], v[int(NR*0.10)+1], v[int(NR*0.25)+1], v[int(NR*0.5)], v[int(NR*0.75)], v[NR]}'
for T in 0.0005 0.001 0.002 0.005 0.01; do
  c=$(awk -v t=$T '$2 < t' "$OUT" | wc -l)
  n=$(wc -l < "$OUT")
  printf "  below %-7s : %4d of %d (%.1f%% candidate duplicates)\n" "$T" "$c" "$n" "$(echo "scale=4; $c*100/$n" | bc)"
done
