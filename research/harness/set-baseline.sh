#!/usr/bin/env bash
# set-baseline.sh — turn the baseline repeats into the number to beat.
#
# A baseline is not one run. Without knowing the run-to-run spread there is no
# way to tell an improvement from weather, and a loop that cannot tell the
# difference will spend the night keeping noise and reverting real wins.
#
# Writes research/baseline.env, which trial.sh reads to judge every later trial.
set -uo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)

stats(){  # $1 = regime
  local re=$1 f
  local -a fps=()
  for f in "$R"/runs/baseline*."$re".txt; do
    [ -f "$f" ] || continue
    local v; v=$(grep -m1 '^fps:' "$f" | awk '{print $2}') || v=""
    local ok; ok=$(grep -m1 '^rc:' "$f" | awk '{print $2}') || ok=1
    [ -n "$v" ] && [ "$ok" = 0 ] && fps+=("$v")
  done
  [ "${#fps[@]}" -gt 0 ] || { echo "0 0 0 0 0"; return; }
  printf '%s\n' "${fps[@]}" | awk '{v[n++]=$1; s+=$1; if(n==1||$1<mn)mn=$1; if($1>mx)mx=$1}
    END{ m=s/n; printf "%.3f %.3f %.3f %d %.1f\n", m, mn, mx, n, (m>0)?100*(mx-mn)/m:0 }'
}

read -r A_MEAN A_MIN A_MAX A_N A_SPREAD < <(stats A)
read -r B_MEAN B_MIN B_MAX B_N B_SPREAD < <(stats B)

# Every baseline repeat must agree on the pixels. If they do not, the upscaler
# is not deterministic on this box and the whole gate is void — better to fail
# loudly here than to spend eight hours discarding good work.
SHAS=$(grep -h '^frame_sha:' "$R"/runs/baseline*.txt 2>/dev/null | awk '{print $2}' | sort -u)
NSHA=$(printf '%s\n' "$SHAS" | grep -c . ) || NSHA=0
XSHAS=$(grep -h '^x264_opts_sha:' "$R"/runs/baseline*.txt 2>/dev/null | awk '{print $2}' | sort -u)
NXSHA=$(printf '%s\n' "$XSHAS" | grep -c . ) || NXSHA=0

if [ "$NSHA" != 1 ]; then
  echo "REFUSING: baseline repeats disagree on pixels ($NSHA distinct hashes)." >&2
  echo "The upscaler is not reproducible here; the quality gate cannot work." >&2
  exit 1
fi
if [ "$NXSHA" != 1 ]; then
  echo "REFUSING: baseline repeats disagree on x264 options ($NXSHA distinct)." >&2
  exit 1
fi

cat > "$R/baseline.env" <<EOF
# Written by set-baseline.sh — the number every trial is judged against.
BASE_FRAME_SHA=$SHAS
BASE_X264_SHA=$XSHAS
BASE_FPS_A=$A_MEAN
BASE_FPS_B=$B_MEAN
# spread = (max-min)/mean over the repeats. A change smaller than this is weather.
BASE_SPREAD_A=$A_SPREAD
BASE_SPREAD_B=$B_SPREAD
BASE_N_A=$A_N
BASE_N_B=$B_N
EOF

printf 'regime A  n=%s  mean %s fps  (min %s, max %s, spread %s%%)\n' "$A_N" "$A_MEAN" "$A_MIN" "$A_MAX" "$A_SPREAD"
printf 'regime B  n=%s  mean %s fps  (min %s, max %s, spread %s%%)\n' "$B_N" "$B_MEAN" "$B_MIN" "$B_MAX" "$B_SPREAD"
printf 'pixels    one hash across all repeats: %s\n' "${SHAS:0:16}…"
