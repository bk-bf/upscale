#!/usr/bin/env python3
import sys

diff_file, out_map, thresh, max_run = (
    sys.argv[1], sys.argv[2], float(sys.argv[3]), int(sys.argv[4]))

diffs = [float(l.split()[1]) for l in open(diff_file) if l.strip()]
n = len(diffs) + 1

mapping = [1]
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
