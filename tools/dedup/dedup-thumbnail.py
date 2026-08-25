#!/usr/bin/env python3
import subprocess, sys

src, out_map, thresh = sys.argv[1], sys.argv[2], float(sys.argv[3])
W, H = 64, 48
FRAME = W * H

raw = subprocess.run(
    ["ffmpeg", "-v", "error", "-i", src, "-vf", f"scale={W}:{H},format=gray",
     "-f", "rawvideo", "-"],
    capture_output=True).stdout

n = len(raw) // FRAME
mapping = []
uniques = []
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
