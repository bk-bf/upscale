#!/usr/bin/env python3
"""Build a frame-reuse map from per-frame changed-pixel fractions.

A frame reuses its predecessor's upscale only when almost no pixels changed.
Two guards, because freezing real motion is what makes dedup visibly judder:

  threshold  a frame counts as a duplicate only if fewer than this fraction of
             pixels moved at all (measured at full resolution, so localised
             motion is not averaged away)
  max_run    never reuse one upscale for more than this many consecutive
             frames. Anime on 2s/3s gives runs of 2-3; a longer run means the
             threshold is drifting rather than matching true duplicates, so we
             force a fresh frame and cap the damage.
"""
import sys

diff_file, out_map, thresh, max_run = (
    sys.argv[1], sys.argv[2], float(sys.argv[3]), int(sys.argv[4]))

# line i (0-based) is the difference between source frame i+2 and i+1
diffs = [float(l.split()[1]) for l in open(diff_file) if l.strip()]
n = len(diffs) + 1

mapping = [1]        # frame 1 is always unique
run = 0
for i in range(2, n + 1):
    if diffs[i - 2] < thresh and run < max_run:
        mapping.append(mapping[-1])
        run += 1
    else:
        mapping.append(i)
        run = 0

with open(out_map, "w") as f:
    for out_i, src_i in enumerate(mapping, start=1):
        f.write(f"{out_i} {src_i}\n")

uniq = len(set(mapping))
print(f"frames={n} unique={uniq} reused={n - uniq} "
      f"({100 * (n - uniq) / n:.1f}%) thresh={thresh} max_run={max_run}")
