#!/usr/bin/env python3
"""Map each frame of a clip to the earlier frame it duplicates.

Anime is animated on 2s/3s, so consecutive frames are often the same cel. They
are not bit-identical after XviD (per-frame compression noise), so the match has
to be perceptual: each frame is reduced to a 64x48 grey thumbnail and compared
to the previous KEPT frame by mean absolute difference. Comparing against the
kept frame rather than the immediate predecessor stops slow drift from
accumulating across a long run of "duplicates".

Writes a mapping file: one line per source frame, giving the 1-based index of
the unique frame whose upscale should be reused.
"""
import subprocess, sys

src, out_map, thresh = sys.argv[1], sys.argv[2], float(sys.argv[3])
W, H = 64, 48
FRAME = W * H

raw = subprocess.run(
    ["ffmpeg", "-v", "error", "-i", src, "-vf", f"scale={W}:{H},format=gray",
     "-f", "rawvideo", "-"],
    capture_output=True).stdout

n = len(raw) // FRAME
mapping = []          # mapping[i] = index of the unique frame to reuse
uniques = []          # indices that must actually be upscaled
ref = None
ref_idx = 0

for i in range(n):
    cur = raw[i * FRAME:(i + 1) * FRAME]
    if ref is None:
        keep = True
    else:
        mad = sum(abs(a - b) for a, b in zip(cur, ref)) / FRAME
        keep = mad > thresh
    if keep:
        ref, ref_idx = cur, i
        uniques.append(i)
    mapping.append(ref_idx)

with open(out_map, "w") as f:
    for i, src_idx in enumerate(mapping):
        f.write(f"{i + 1} {src_idx + 1}\n")

print(f"frames={n} unique={len(uniques)} "
      f"dup={n - len(uniques)} ({100 * (n - len(uniques)) / n:.1f}%) "
      f"threshold={thresh}")
