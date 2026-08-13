# PROGRAM.md — the overnight optimisation loop

You are running an autonomous research loop against the `upscale` pipeline. It is
Karpathy's `autoresearch` loop with two things swapped: the metric is **throughput**
instead of `val_bpb`, and quality — which throughput cannot see — is enforced as a
**hard gate** instead of being scored.

Read `BASELINE.md` before your first experiment. It holds the machine, the constants, and
the controls that were run to make the measurements trustworthy.

---

## Setup — do this first, before any experiment

The harness is built and validated; the science is yours from here.

1. **Read `BASELINE.md` in full**, then this file in full.
2. **Check the box answers and the harness still works:**
   ```bash
   research/harness/guard.sh          # how long you have
   research/harness/pace.sh           # what you may spend
   ```
3. **Establish the baseline yourself.** This is experiment zero and it is not optional —
   every later number is a ratio against it, and a baseline of one run cannot tell an
   improvement from weather:
   ```bash
   for i in 1 2 3; do research/harness/trial.sh "baseline$i" A; done
   for i in 1 2;   do research/harness/trial.sh "baseline$i" B; done
   research/harness/set-baseline.sh
   ```
   `set-baseline.sh` refuses to write a baseline whose repeats disagree on pixels. If it
   refuses, stop and report — the gate cannot work and the night is void.
4. **Fill in `BASELINE.md` §3** with the table it printed: mean, min, max, n and spread
   per regime. The spread is your noise floor; anything smaller is not an improvement.
5. Confirm `research/results.tsv` has its header row, then start the loop below.

A single validated trial already exists from setup for reference — 400 frames, regime A,
all three gates passing. It is *not* a baseline: wrong frame count, n=1.

---

## The metric

**fps = frames ÷ wall seconds, higher is better.** Fixed work per trial (a pinned frame
count of a pinned source file), so wall time is the only thing that moves. This is the
inverse of the autoresearch setup, where time was fixed and quality moved.

## The gate — read this twice

fps says nothing about quality. An agent maximising fps alone will rediscover, in its
first hour, every configuration this project already rejected **with human eyes**: a
smaller model, frame dedup, a faster preset, a lower CRF. PSNR cannot rescue it either —
the upscale is *supposed* to differ from the source, so a higher PSNR would mean *less*
upscaling.

So quality is not a term in the score. It is a precondition:

| gate | what it checks |
|---|---|
| **G1 pixels** | the decoded content of every upscaled frame, hashed, must equal the baseline's exactly |
| **G2 encoder** | the x264 option line read back out of the bitstream must be byte-identical |
| **G3 shape** | the output has exactly `FRAMES` video frames |

A trial that fails any gate is **discarded without its fps being considered**. Not
weighed, not traded off — discarded. A one-pixel difference is a discard.

This is measured, not assumed: the upscaler was verified bit-deterministic across
repeat runs and across concurrency levels before the loop started (`BASELINE.md` §4).

**What the gate leaves you is the good half of the search space.** Scheduling,
concurrency, I/O placement and overlap are bit-identical by construction — and they are
where the headroom is. Roughly 40% of end-to-end wall time is not upscaling at all
(PERFORMANCE-DATA §3 vs §4: 41.7 fps upscaling, 24.8 fps end-to-end). Nobody has ever
swept that systematically.

## The second gate: it has to work on hardware that is not this box

This box was rented cheap and is deliberately bad — a GTX 1660 SUPER behind a 2016 Xeon.
**Nothing here will ever run in production.** An optimisation that only wins on this
machine is worthless, and it is very easy to produce by accident: tune a constant until
it fits one balance point and call it a discovery.

That failure has already happened in this project's data. The measured optimum worker
count was **8 on the desktop and 4 on rental-B** — same pipeline, same model, opposite
answers. A hardcoded `WORKERS=6` learned here would be actively wrong in production.

So every trial declares a **regime**, and a keeper must win in both:

| regime | what it is | what it emulates |
|---|---|---|
| **A** | the whole box: 9.6 CPU quota, weak GPU | GPU is the scarce resource |
| **B** | four cores only (`taskset`) | CPU is the scarce resource — the balance a *real* box has, where a fast GPU outruns the CPU feeding it |

The GPU cannot be made faster, so the balance is changed from the other side by starving
the CPU. Run regime A first; only spend a regime B slot on something that already won.

```bash
research/harness/trial.sh <tag> A
research/harness/trial.sh <tag> B
```

**Classify every result before you keep it:**

- **`structural`** — removes work, removes waiting, or removes a serialisation. Wins in
  both regimes because it is not trading one resource for another. *These are the only
  results that are worth anything.* Overlapping two phases, not loading the model four
  times, not writing bytes to disk that nobody reads.
- **`autotuned`** — a constant that must change per machine, but which you can express as
  a rule derived from what the machine reports at runtime (CPU quota, GPU memory, core
  count). Portable **only** as the rule, never as the number.
- **`hw-tuned`** — wins in one regime, not the other. **Never keep it in `worker.sh`.**
  Record it in `results.tsv` and in the report as a tuning observation for this hardware,
  and move on.

A change that wins +15% in regime A and loses in regime B is a *discard*, no matter how
large the A number is. Say so plainly in the report rather than burying it.

## What you may and may not change

**You edit exactly one file: `research/worker.sh`.** Everything else is read-only —
`harness/`, `BASELINE.md`, and the production scripts in `libexec/`.

Frozen, because changing them changes the output: `MODEL`, `SCALE`, `CRF`, `PRESET`,
`ENC_THREADS`, `pix_fmt`, `libx264`. The gate enforces this; do not waste trial slots
confirming it.

In play, because none of them can change a pixel: worker count, chunk size, where files
live, when phases start, what overlaps what, process priority, CPU affinity, how frames
are staged and deleted, how many processes vs how many threads.

Do not install packages. Do not re-litigate anything in `BASELINE.md` §6 — those were
measured and rejected already.

---

## Running a trial

```bash
research/harness/trial.sh <tag>
```

Prints exactly one line:

```
tag=tmpfs fps=13.412 speedup=+8.4% gate=PASS gpu=61.2 idle=12.0 cpu=74 disk_mb=6100
```

The full block is in `research/runs/<tag>.txt`; per-2s telemetry stays on the box. **Read
the one line. Only open the file when a trial needs debugging** — you have eight hours
and perhaps a hundred trials, and a loop that dumps logs into its own context runs out of
context long before it runs out of night.

One GPU: trials are strictly serial. Never run two at once, and never let a side agent
start one.

---

## Attack order — easiest first, hardest last

Work down this list. It is ordered by (measured headroom ÷ implementation risk), not by
how interesting the idea is. Do not skip ahead to Tier 3 because it sounds clever; the
cheap wins are cheap precisely because they are structural.

### Tier 1 — free wins, no restructuring

1. **PNG compression level on extraction.** Extraction costs 200–290 s per episode
   (PERFORMANCE-DATA §9) and ffmpeg's PNG encoder defaults to a slow zlib level. The gate
   hashes *decoded* content, so `-compression_level 1` is explicitly legal.
2. **Stage frames in tmpfs.** The box has 62 GB RAM and 32 GB of disk. A trial's frames
   are ~4 GB. Disk was the binding constraint on the old box; here it need not be touched
   at all.
3. **Overlap extraction with upscaling.** The baseline extracts *every* frame before the
   first upscale starts. That is the single largest serialisation in the trial.
4. **One process with `-j load:proc:save` instead of N processes.** N processes each load
   the model and pay their own startup. Same arithmetic, far less overhead — structural,
   and it should win under either balance.
5. **Worker count and chunk size — sweep them to *learn*, not to hardcode.** The optimum
   of 4 workers was measured on an RTX 5070 Ti; the desktop's was 8. Whatever this box
   prefers is a fact about this box. The portable deliverable is a **rule** — workers as
   a function of the CPU quota and the GPU's throughput — not a number. If you cannot
   find a rule that wins in both regimes, report the sweep as data and leave the default
   alone.

### Tier 2 — real restructuring, still pixel-safe

7. Segmented/parallel decode instead of one serial ffmpeg pass.
8. CPU affinity — keep x264 off the cores feeding the GPU. The quota is 9.6 CPUs on a
   slow 2016 Xeon; contention here is real.
9. More than one chunk encoding concurrently.
10. Kill the per-chunk hardlink shard pass (thousands of syscalls and directory churn).
11. `nice`/`ionice` so encode never preempts the GPU feeder.

### Tier 3 — hardest, attempt last

12. A persistent upscaler process instead of one spawn per chunk.
13. Deeper pipelining: extract, upscale and encode all in flight simultaneously.

**Out of scope:** removing the PNG stage entirely. G1 needs frames to hash. Note it in
the report as future work rather than attempting it.

---

## The loop

The experiment runs on branch `autoresearch/<tag>`.

LOOP until the time guard expires:

1. `research/harness/guard.sh` — if it exits non-zero, stop experimenting and write the
   report. This is the only thing that ends the loop.
2. `research/harness/pace.sh` — read `mode` and `side_agents`, and obey them (below).
3. Pick the next idea, highest tier first. State the hypothesis and the phase you expect
   it to move *before* running it.
4. Edit `research/worker.sh`. `git commit`.
5. `research/harness/trial.sh <tag>`.
6. Append one row to `research/results.tsv`.
7. **gate=FAIL → `git reset --hard HEAD~1`.** Always. Never "keep it anyway, it is only a
   rounding difference in one frame".
8. gate=PASS and fps did not improve beyond noise (`BASELINE.md` §3 gives the run-to-run
   spread — a change inside it is *not* an improvement) → `git reset --hard HEAD~1`.
9. gate=PASS and fps improved in regime A → **re-run the same commit in regime B.**
   - improved in both → `structural`, keep the commit.
   - improved in A, flat or worse in B → `hw-tuned`, **reset**. Log it as a tuning
     observation, not as a win.
   - the win is a constant that clearly depends on the machine → rewrite it as a rule
     derived from detected hardware, re-trial, and keep only if the rule wins in both.

Simplicity is a tiebreaker, as upstream: a small gain that adds ugly complexity is not
worth keeping; equal fps from *less* code is a win, keep it.

### results.tsv

Tab-separated, untracked, one row per trial including crashes:

```
ts	commit	tier	regime	fps	delta_pct	gate	class	gpu_util	gpu_idle	peak_disk_mb	status	description
```

`status` is `keep`, `discard`, `gate-fail`, or `crash`. `class` is `structural`,
`autotuned`, `hw-tuned`, or `-` for anything that never passed the gate. Never omit a
row — a discarded idea is a result, and the report needs the failures as much as the wins.

---

## Budget: 90%+ of every 5-hour window, and no idle hours

The subscription meters in rolling 5-hour windows. The failure to avoid is burning the
whole allowance in three hours and then sitting rate-limited through the rest of the
night — an eight-hour loop that idles for two of them did six hours of work.

`research/harness/pace.sh` reads Anthropic's *own* utilisation figure and compares it to
where a straight line to 92% says you should be. Obey `mode`:

| mode | what to do |
|---|---|
| `NOMINAL` | proceed normally, one side agent at most |
| `EXPAND` | behind pace — spend the surplus on **breadth**: more hypotheses per trial, deeper telemetry analysis, a reviewer for the next diff. Not on filler. |
| `THROTTLE` | ahead of pace — no side agents, minimal reasoning, let the GPU carry the wall clock. Prefer queueing a long trial over thinking about a short one. |
| `STALL` | window nearly spent — **queue unattended trials and stop reasoning**. Sleep `sleep_hint_s`. The GPU is free; your tokens are not. |

`STALL` is not idling. Trials cost no tokens while they run, so a stall is when you line
up a back-to-back sweep the box can grind through unattended and read the results after
the window rolls.

**Side agents.** Never more than `side_agents` concurrently. Each gets a bounded question
and must answer in ≤15 lines. They never run trials, never edit `worker.sh`, and never
touch git. Good uses: characterising where a run's telemetry says the time went; checking
a diff for gate-safety before it costs a trial slot; drafting a results table.

---

## Ending

When `guard.sh` exits non-zero, stop experimenting immediately — mid-idea is fine — and
write `research/RESULTS.md`:

- the baseline and the box it was measured on, restated so the report stands alone;
- every trial, grouped by tier, with its gate verdict;
- the winning configuration, its fps in **both regimes**, and the telemetry showing *why*
  it won — which phase shrank, and what the GPU idle fraction did;
- what was tried and rejected, with numbers — a negative result with a measurement is a
  result, an unrecorded one is waste;
- **a portability verdict for every kept change**, split into three lists: `structural`
  (deploy to production), `autotuned` (deploy as a rule, never as a number), and
  `hw-tuned` (this box only — do not deploy). If a change is in the third list, say so
  even if it was the biggest number of the night;
- **what transfers to production and what does not.** This box is not `rental-B`. Report
  ratios and structural findings, and say plainly where an absolute number is specific to
  a GTX 1660 SUPER behind a slow Xeon;
- the honest caveats, in the style of PERFORMANCE-DATA §14.

Then append the durable measurements to `PERFORMANCE-DATA.md` in its existing data-only
style, every row carrying its provenance.

## Do not stop early

Do not pause to ask whether to continue. The human is asleep. If you run out of ideas,
re-read the telemetry, re-read `libexec/upscale-worker-remote`, combine near-misses, or
attack the next tier. The loop ends when `guard.sh` says so, and not before.
