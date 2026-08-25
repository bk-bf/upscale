#!/usr/bin/env bash
set -euo pipefail
R=$(cd "$(dirname "$0")" && pwd)
cd "$R/.."

HOURS=${1:-8}

MON=$(command -v mon) || MON=$HOME/Documents/Projects/mon/mon
[ -x "$MON" ] || { echo "cannot find mon" >&2; exit 1; }
TAG=${TAG:-$(date +%b%d | tr 'A-Z' 'a-z')}
BRANCH="autoresearch/$TAG"

if git rev-parse --verify "$BRANCH" >/dev/null 2>&1; then
  AHEAD=$(git rev-list --count "main..$BRANCH") || AHEAD=1
  [ "$AHEAD" = 0 ] || {
    echo "branch $BRANCH already has $AHEAD commits — this must be a fresh run" >&2; exit 1; }
  echo "reusing empty branch $BRANCH from a previous launch"
  git checkout -q "$BRANCH"
else
  git checkout -q -b "$BRANCH"
fi

"$R/harness/guard.sh" --arm "$HOURS"

notify --register upscale-research "$R/start-loop.sh" \
  "overnight upscale optimisation loop: finished, or died early" 2>/dev/null || true

read -r -d '' PROMPT <<'EOF' || true
Run the overnight optimisation loop for this repo. Your instructions are
research/PROGRAM.md — read it first, in full, then research/BASELINE.md. Follow
PROGRAM.md exactly; it is the contract, not a suggestion.

Start with PROGRAM.md's "Setup" section: establish the baseline yourself (3
repeats in regime A, 2 in regime B, then set-baseline.sh) and fill in
BASELINE.md §3. Only then begin experimenting.

The short version, so you cannot mistake the shape of the job:

  * Metric is fps. Quality is NOT scored, it is a hard gate: any trial whose
    pixels differ from the baseline is discarded without its fps counting.
  * A win must hold on hardware that is not this box. Regime A then regime B;
    anything that only wins in one is hw-tuned and gets reverted.
  * You edit exactly one file: research/worker.sh. Everything else is read-only.
  * research/harness/guard.sh ends the night. Until it exits non-zero, do not
    stop, do not ask whether to continue, do not wait for a human — he is asleep.
  * research/harness/pace.sh governs spending. Obey its mode so the 5-hour
    window finishes at 90%+ rather than being exhausted by 03:00.

Machine access: the GPU box is reached with the settings in
research/harness/box.env. It is a rented Vast.ai instance and holds nothing
precious — but it is the only GPU, so trials are strictly serial and a side
agent must never start one.

Do not touch: libexec/, upscale, install.sh, the systemd units, ~/.upscale-queue,
or anything under /mnt/media. This is a benchmark, not a pipeline change. The
production scripts are read-only reference.

Traps that have already cost this project time, from AGENTS.md — they apply to
you: `var=$(cmd)` adopts cmd's exit status under `set -e`; `head -1` on a live
pipeline SIGPIPEs the producer and pipefail calls that a failure; `pgrep -f X`
over ssh matches the ssh command carrying X, so bracket it as `[X]`; and never
edit a script while it is running, which corrupted a trial during setup.

When guard.sh expires: write research/RESULTS.md as PROGRAM.md specifies,
append the durable rows to PERFORMANCE-DATA.md in its data-only style, commit,
and send exactly one notification:

  notify upscale-research "upscale research done" "<one line: best structural win, its speedup in both regimes, trials run>"

If you die early or cannot proceed, send that same notification saying so
instead of failing silently.
EOF

"$MON" run "$PROMPT" \
  --project "$(pwd)" \
  --title "overnight upscale optimisation loop (${HOURS}h)" \
  --mode acceptEdits \
  --by kirill \
  --tag loop

echo
echo "branch:   $BRANCH"
echo "deadline: $(date -d "@$(cat "$R/.deadline")" '+%F %T %Z')"
echo "follow:   mon sessions   ·   dashboard Monitor page"
