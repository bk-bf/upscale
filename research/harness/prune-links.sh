#!/usr/bin/env bash
# prune-links.sh — keep the loop's session list readable overnight.
#
#   prune-links.sh [keep]        default 3
#
# The night runs as a chain of sessions, so every link leaves a finished record
# behind. Forty of them by morning buries every other session on the monitor
# page under one job's bookkeeping.
#
# Keeps the running link plus the most recent few finished ones — enough to see
# what the last links actually did, which is where a post-mortem starts. The
# durable record was never these anyway: results.tsv, the git branch and
# RESULTS.md are what the morning is read from.
#
# Deliberately narrow. It matches on this loop's own title, not on the `loop`
# tag, so any other session filed under that tag is somebody else's and is left
# alone.
set -uo pipefail
KEEP=${1:-3}

python3 - "$KEEP" <<'PY'
import json, os, pathlib, sys

keep = int(sys.argv[1])
S = pathlib.Path(os.path.expanduser("~/.local/state/mon"))
TITLE = "overnight upscale optimisation loop"

rows = []
for f in (S / "incidents").glob("*.json"):
    try:
        d = json.loads(f.read_text())
    except (OSError, ValueError):
        continue
    if d.get("kind") != "session":
        continue
    if not str(d.get("monitor") or "").startswith(TITLE):
        continue
    # Never a running link: throwing away the record of a live session leaves it
    # working with nothing on the page pointing at it.
    if d.get("status") == "diagnosing":
        continue
    rows.append((d.get("ts") or 0, f, d.get("id") or f.stem))

rows.sort(reverse=True)
gone = 0
for _, f, inc_id in rows[keep:]:
    f.unlink(missing_ok=True)
    (S / "transcripts" / f"{inc_id}.jsonl").unlink(missing_ok=True)
    gone += 1
print(f"pruned {gone}, kept {min(len(rows), keep)} finished link(s)")
PY
