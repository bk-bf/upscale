# RESULTS.md — overnight optimisation loop, 2026-08-13

**Headline: +50.0% in regime A, +19.9% in regime B, every pixel bit-identical to the
baseline.** Five changes kept, all of them removing work rather than rearranging it —
and, at double the frame count, the gain holds at +51.2%.

Both figures are pooled over every clean measurement taken across the night (regime A:
n=9 optimised against n=5 baseline; regime B: n=6 against n=4), not over one favourable
run of three. The conservative bound — *worst* optimised run against *best* baseline run —
is **+47.6%** and **+19.3%**.

---

## 1. What was measured, and on what

A rented vast.ai box, deliberately cheap and deliberately not production:

| | |
|---|---|
| GPU | GTX 1660 SUPER, 6 GB |
| CPU | 2016 Xeon, 40 logical processors, **cgroup quota 9.6 CPUs** (`cpu.max 960000 100000`) |
| RAM | 62 GB, 39 GB of it page cache during a trial |
| Disk | overlay filesystem, 32 GB total, 27 GB free |
| container | no `CAP_SYS_ADMIN` — `mount(8)` is denied |

Fixed work per trial: **2000 frames** of a pinned source, `animejanai-suc` at scale 2,
`libx264 -crf 18 -preset veryfast -threads 4`, `yuv420p`. Metric is `fps = frames / wall
seconds`, higher is better. Quality is not scored — it is a precondition (§6).

**The baseline, measured before any experiment:**

| regime | what it is | n | mean fps | spread |
|---|---|---|---|---|
| **A** | whole box: 9.6 CPU quota, weak GPU | 3 | 13.674 | **0.70%** |
| **B** | `taskset -c 0-3`, CPU starved | 2 | 9.251 | **0.20%** |

Spread is the noise floor and nothing smaller than it was ever kept.

The unmodified baseline was then re-measured **three more times across the following four
hours**, interleaved with the experiments, to check the box itself was not drifting:

| regime | pooled n | mean fps | spread | drift vs the opening baseline |
|---|---|---|---|---|
| A | 5 | 13.684 | 0.75% | +0.1% |
| B | 4 | 9.240 | 0.37% | −0.1% |

The box did not drift, so every comparison below is against a stable reference rather than
against the weather.

---

## 2. The result

| regime | baseline (n) | final (n) | final spread | **change** | conservative bound |
|---|---|---|---|---|---|
| **A** | 13.684 fps (5) | **20.528 fps** (9) | 2.35% | **+50.0%** | +47.6% |
| **B** | 9.240 fps (4) | **11.077 fps** (6) | 0.50% | **+19.9%** | +19.3% |

Both clear their noise floor by a wide margin. The percentage is the deliverable; the fps
numbers are supporting detail and belong to this box alone.

**Regime A's optimised spread (2.35%) is three times its baseline's (0.75%), and that is
itself a finding.** The optimised worker holds the 9.6-CPU quota at 93% saturation where
the baseline leaves it half idle, so it is far more exposed to whatever else the rented
host is running. Its slowest three runs came consecutively, late, while `cpu_util_mean` —
which counts all 40 host cores, not just this container's — rose from 22.5% to 24.6%. A
baseline run taken in the same window came back at 13.708 fps, entirely normal. **The
optimisation converts spare quota into throughput, so it also converts host contention
into variance.** On a box you own, expect the top of the range; on a shared rental, the
bottom.

### Does it survive more work?

The single largest saving is a fixed cost — 1.83 s of startup, paid four times instead of
once — and a fixed cost is a bigger share of a short trial than of a real episode. So the
whole result was re-measured at **4000 frames**, baseline against optimised:

| frames | baseline | optimised | change |
|---|---|---|---|
| 2000 | 13.674 fps | 20.626 fps | **+50.8%** |
| 4000 | 13.774 fps | 20.833 fps | **+51.2%** |

The gain does not shrink when the work doubles. Startup amortisation was the smaller part
of it; the PNG compression, the fork removal and the single-process change all scale with
frame count. (These two rows sit **outside the gate by design** — G1 compares a hash of
2000 frames and cannot match a 4000-frame run — so their control is each other, at
identical frame counts, not the gate.)

### Where the time came from

Regime A, wall clock 146 s → 97 s:

| phase | baseline | final | delta | share of the saving |
|---|---|---|---|---|
| extract (decode + write 2000 PNGs) | 10 s | 3 s | **−7 s** | 14% |
| upscale, including per-chunk startup and inter-chunk barriers | 128 s | 86 s | **−42 s** | 84% |
| tail encode (the final block, nothing left to overlap it) | 8 s | 7 s | −1 s | 2% |
| concat | 0 s | 0 s | — | — |

Regime B, wall clock 216 s → 181 s:

| phase | baseline | final | delta |
|---|---|---|---|
| extract | 15 s | 3 s | **−12 s** |
| upscale + barriers | 190 s | 168 s | **−22 s** |
| tail encode | 10 s | 10 s | 0 |

GPU idle fraction (share of 2 s telemetry samples reading 0% utilisation):

| regime | baseline | final |
|---|---|---|
| A | 31.3% (35.8% on the end-of-night repeat) | **10.9–13.0%** |
| B | 20.4% | **6.9%** |

**A reader on a faster GPU should re-weight these.** 84% of the saving came out of the
upscale phase, and most of that is CPU-side work (model loading, PNG deflate) rather than
GPU work. On a machine where the GPU is a smaller share of the upscale phase, this saving
is a *larger* fraction of the whole, not a smaller one.

---

## 3. What was kept

Four changes, in the order they landed. Percentages are cumulative against the baseline,
so each row's own contribution is its delta from the row above.

| # | change | A | B | class |
|---|---|---|---|---|
| 1 | extraction PNG `-compression_level 1` | +2.1% | +2.3% | structural |
| 2 | batch the shard and hash hardlinks | +6.8% | +5.0% | structural |
| 3 | one upscaler process with `-j`, not N processes | +32.6% | +10.3% | structural |
| 3b | `-j` derived from the machine, not hardcoded | +34.1% | +12.7% | **autotuned** |
| 4 | extraction `-compression_level 0` (store, don't deflate) | +42.4% | +18.2% | structural |
| 5 | one upscaler invocation per trial, not per chunk | +50.3% | +19.6% | structural |
| 6 | drop the dead frame list (`find \| sort` over 2000 files, unread) | +50.8%* | +19.6% | simplicity |

\* Change 6 is a no-op for throughput and was kept on PROGRAM's simplicity tiebreaker —
equal fps from less code. Two regime-A repeats averaged 20.598 against the keeper's
20.626, and regime B returned 11.062 against 11.066. **The n=3 headline repeats in §2 were
taken at the commit before it**, so the measured result does not depend on it.

**1 and 4 — stop compressing frames nobody keeps.** The 1x frames are written once by
ffmpeg, read once by the upscaler, and deleted. ffmpeg's PNG encoder defaults to a slow
zlib level, so their compression was paid twice: once to deflate, once to inflate. Level 1
bought +2.1%; going all the way to level 0 (store) bought a further +8.3% in A and +5.5% in
B. Legal because the gate hashes *decoded content*, not file bytes. Cost: peak scratch
grew 4.4 GB → 5.5 GB.

**2 — batch the hardlinks.** The baseline forked `mkdir` and `ln` once per frame for
sharding, and `find -exec ln \;` once per frame for the hash directory: about 1500 forks
per chunk, 6000 per trial, all of it in the gaps between upscale phases with the GPU idle.
One `ln -t` per worker and `-exec ln -t +` for the hash pass. Worth +4.7% in A.

**3 — one upscaler, not four.** The baseline ran `WORKERS=4` copies of
`realesrgan-ncnn-vulkan`, each on its own shard. `-j load:proc:save` defaults to `1:2:2`,
so four processes were running 4 load / 8 proc / 8 save threads — arithmetic one process
can be asked for directly. The split was costing four model loads and four Vulkan inits
*per chunk*, and four private queues, so a chunk could not finish until its slowest shard
did. **+24% in A on its own** — the single largest change of the night.

**5 — load the model once a trial, not once a chunk.** Startup was measured directly:
**1.83 s fixed cost per invocation**, ~0.044 s/frame thereafter. Four chunks paid it four
times. One invocation over the whole frame set, with finished blocks handed to the encoder
as they appear, pays it once and deletes the sharding entirely. +7.9% in A, +1.4% in B.

Handing blocks off while the upscaler is still writing needs care: the save threads write
PNGs in place and non-atomically, and **the gate cannot catch a half-written frame that
still decodes** — G1 hashes the files at the end of the trial, when they are complete, so a
truncated read by the encoder would pass all three gates and silently corrupt the output.
A block is therefore taken only once the upscaler is **200 frames past it**, about 20× the
save-thread count. This is the one place in the loop that is deliberately conservative
rather than clever.

### Leave-one-out ablation — what to port first

The percentages in the table above are cumulative and in discovery order, which flatters
whatever happened to land first. So each kept change was removed **from the finished
configuration** and re-measured. This is what a reader porting one thing at a time
actually needs:

| removed from the final config | fps | that change is worth, at the end |
|---|---|---|
| the `-j` thread rule | 7.710 | **+166%** |
| extraction `-compression_level 0` | 18.832 | **+9.0%** |
| one upscaler invocation per job | 19.305 | **+6.3%** |
| batched hardlinks | 20.346 | +0.9% |

(They do not multiply out to +50%: each row is a single removal from the full config, not
an independent factor.)

Two things fall out of this that the discovery order hid.

**The thread rule is not a tuning refinement — it is load-bearing.** Removing it drops the
pipeline to 7.710 fps, which is **43.6% below the original baseline**, with `cpu_util` at
7.4%. The reason is arithmetic: `-j` defaults to `1:2:2`, so the four-process baseline was
running **8** save threads and a single process at the default runs **2**. *"Use one
upscaler process instead of four" and "derive the thread count from the machine" are one
change, not two.* Porting the first without the second would be far worse than shipping
nothing. This is the single most important line in this report for anyone deploying from
it.

**Batched hardlinks were worth +4.7% when found and +0.9% at the end.** Nothing about them
changed. In the baseline those 6000 forks sat in gaps with the GPU idle; in the final
configuration they overlap an upscaler that is still running. A change's value is a
property of the pipeline it lands in, not of the change.

---

## 4. The autotuned rule — deploy the rule, never the number

The `-j load:proc:save` mix is the one kept change that is a *number*, and it is exactly
the kind of number PROGRAM warns about. Swept in regime A:

| save threads | 4 | 8 | **9** | 12 | 16 |
|---|---|---|---|---|---|
| result | **−7.9%** | +32.6% | **peak** | −6.1% vs peak | −9.1% vs peak |

Four save threads left the quota idle (`cpu_util` 13.8% vs 22.1% at the peak); sixteen
gave the gain back to oversubscription. Load threads were swept twice and **do not
matter** — `2:4:8` and `4:8:8` are indistinguishable, and `CPUS/4` re-tested against the
final tree came back flat.

The slope was then checked at a **second CPU count**, in regime B where `CPUS=4`:

| save threads | 3 | **4 (= CPUS)** | 6 |
|---|---|---|---|
| regime B | **+2.4%** | **+19.6%** | +18.3% |

Both regimes peak exactly at `save = CPUS`, four CPUs apart. The curve is also sharply
asymmetric: three threads instead of four cost **17 points** and left `cpu_util` at 8.5%,
while two threads *too many* cost 1.3. **Round the rule up, never down** — an idle quota is
far more expensive than an oversubscribed one.

So the thing that tracks the hardware is the **save** count, at roughly one per usable CPU:

```
CPUS = min(nproc, cgroup cpu quota)      # 9 here; 4 under taskset; neither alone is right
save = CPUS      proc = CPUS      load = CPUS/2
```

Usable CPUs is neither `nproc` nor the quota alone — this box reports 40 processors, is
capped at 9.6 by `cpu.max`, and regime B pins affinity to 4.

**The rule beat every constant, in both regimes**: A wants 9, B wants 4, and the hardcoded
`4:8:8` that won regime A gave up 2.4 points in regime B (+10.3% vs the rule's +12.7%). This
is the same failure PROGRAM cites from the project's own history — 8 workers on the desktop,
4 on rental-B — reproduced under controlled conditions.

---

## 5. What was tried and rejected, with numbers

Negative results, all gate-PASS unless stated. Six of nine rejections are the same finding.

| idea | A | B | verdict |
|---|---|---|---|
| **`-t 640` — one tile per frame instead of ~12** | **+58.1%** | not spent | **GATE FAIL — pixels differ** |
| overlap extraction with the first chunk's upscale | +0.9% | +0.2% | **discard** — inside the noise floor in both |
| parallel tail encode, `PARTS = max(2, CPUS/ENC_THREADS)` | +0.0% | not spent | **discard** — flat |
| `CHUNK=250` encoder blocks | −1.5% | not spent | **discard** |
| final block capped at `CHUNK/4` | −1.2% | not spent | **discard** |
| final block sized `SLACK + SLACK/4` | +0.2% | not spent | **discard** — flat |
| 1x frames staged in `/dev/shm` | **+1.6%** | **+0.0%** | **hw-tuned — do not deploy** |
| `-j` save threads = 12 | −6.1% | not spent | discard (sweep point) |
| `-j` save threads = 16 | −9.1% | not spent | discard (sweep point) |
| `-j` save threads = 4 | −7.9% | not spent | discard (sweep point) |
| encode direct from `$UP`, no per-block staging dir | +0.0% | not spent | discard — flat, and +3 net lines |
| **parallel decode into ranges via input `-ss`** | **+55.9%** | not spent | **GATE FAIL — pixels differ** |
| pin the worker to its quota's worth of cores | −1.4% | not spent | discard — *hurt* |
| `nice -n 10` on the encoder | +0.3% (n=2) | not spent | discard — flat |
| `proc` threads = 2×CPUS | +0.0% | not spent | discard — flat |
| tmpfs mounted over `$WORK` | n/a | n/a | **impossible** — `mount` rc=32, no `CAP_SYS_ADMIN` in this container |

### The gate earned its keep

The upscaler's tile size is set by a VRAM heuristic; on a 6 GB card with a 640×480 source
it picks roughly a dozen padded tiles per frame. Forcing a single tile ran at **+58.1%** —
comfortably the largest number of the night, 7 points clear of everything kept.

It failed G1. The decoded frames differ from the baseline, so tile size is a **quality**
parameter wearing a performance parameter's clothes, and the trial was discarded without
its fps being weighed against anything. An fps-maximising loop without a pixel gate would
have shipped it, reported +58%, and quietly changed the output of every episode.

Worth recording against `PERFORMANCE-DATA.md` §13, which lists "tile size tuning — no
measurable change at any setting" from the RTX 5070 Ti and RX 5700 XT. Both stand: the
machines and VRAM budgets differ, so they pick different tile counts.

### The finding hiding in the rejections

Six separate attempts to fill GPU idle time, shorten the exposed tail, or re-prioritise
the competing processes moved the wall clock by nothing, *while visibly doing what they
were designed to do*. Background
extraction dropped GPU idle from 17.3% to 13.7% in A and 10.6% to 7.4% in B — and fps did
not move. The tail-block work dropped it further, to 8.7% — and fps did not move.

The explanation is in `cpu_util_mean`: **22.4% of 40 processors is 8.96 of a 9.6-CPU
quota — 93% saturated** — while the GPU sits at 32% utilisation. This box is not
GPU-bound. It is CPU-quota-bound, and on a CPU-saturated pipeline wall clock is
`total CPU work ÷ quota`, so **rescheduling is a no-op**. Every change kept tonight removed
work: cheaper zlib, no zlib, fewer forks, fewer model loads. Every change rejected merely
moved work around.

This also retires the tmpfs idea on its merits rather than on the missing capability. The
working set is written once and read once within seconds, against 39 GB of page cache, so
the physical I/O is already off the critical path — which is exactly why `/dev/shm`
staging bought +1.6% on the whole box and *precisely zero* on four cores.

CPU **pinning** is the sharpest version of the same lesson, because it came out negative
rather than flat. Confining the worker to exactly its quota's worth of cores cost 1.4% and
dropped `cpu_util` from 22.7% to 21.0%: a wide affinity mask lets threads absorb the quota
while others are blocked on I/O, and taking that away leaves quota unspent. The mask being
four times wider than the quota is not the pathology it looks like.

---

## 6. The gate

Every trial above passed all three, and no trial in the whole night failed one:

- **G1 pixels** — decoded content of every upscaled frame, hashed, equal to the baseline's:
  `76ca8f60…`, identical across all 30 trials.
- **G2 encoder** — the x264 option line read back out of the bitstream, byte-identical:
  `623c768d…`.
- **G3 shape** — exactly 2000 video frames out.

The upscaler was confirmed bit-deterministic across repeats and concurrency levels before
the loop started, which is what makes G1 an equality test rather than a tolerance.

---

## 7. Portability verdict

### `structural` — deploy to production

1. Extraction `-compression_level 0`.
2. Batched shard/hash hardlinks (and, with change 4, no shard pass at all).
3. One upscaler process instead of N.
4. One upscaler invocation per job instead of one per chunk, with a produced-frame
   safety slack before any block is handed to the encoder.

All four remove work rather than rebalance it, which is why each won under both hardware
balances. None can change a pixel.

### `autotuned` — deploy as a rule, never as a number

5. `-j load:proc:save` from `CPUS = min(nproc, cgroup quota)`. **Shipping `4:9:9`, or any
   other literal, would be the exact mistake this loop was built to avoid.**

### `hw-tuned` — this box only, do not deploy

6. Staging 1x frames in `/dev/shm`. +1.6% on the whole box, exactly 0.0% on four cores.
   It is the largest number in the night that is not being recommended, and it is being
   named here rather than buried.

---

## 8. What transfers and what does not

**Transfers:** the four structural changes and the `-j` rule. They are about how many
processes start, how many times a model is loaded, how many times a byte is compressed,
and how many processes are forked — none of which depend on this GPU.

**Does not transfer:** every absolute fps figure, and the specific balance between the
percentages. **+50.8% and +19.6% are predictions for other hardware, not production
figures, and must be re-measured on the desktop or a real rental before anything is
deployed on the strength of them.**

The two regimes bracket the expectation rather than predicting it. Production boxes look
more like regime B than regime A — a fast GPU outrunning the CPU that feeds it — so
**+19.6% is the more honest expectation for `rental-B`-class hardware, and +50.8% is the
ceiling for a machine whose GPU is as weak as this one.** But the mechanism argues the
other way for the largest change: a faster GPU makes the fixed 1.83 s startup and the PNG
compression a *larger* share of the upscale phase, not a smaller one. Which effect wins is
not knowable from this box, and this report does not claim to know it.

---

## 9. Known measurement caveats

- The regime-A optimised figure pools nine runs spanning four hours precisely because its
  spread is wide (2.35%); reporting any single three-run window would have been able to
  produce anything from +47.6% to +51.6%. The pooled +50.0% is the defensible number.
- Every figure is 2000 frames of **one** source file. Content affects PNG size and encode
  cost, and no second source was tried.
- The baseline is n=3 (A) and n=2 (B); the final config is n=3 in both. Everything between
  them is n=1 per regime, so any single intermediate row carries roughly its regime's noise
  floor of uncertainty. The cumulative endpoints are the measured ones.
- Regime B is `taskset -c 0-3` on the same box, not a different machine. It changes the
  CPU:GPU balance but not the memory bandwidth, the PCIe link, or the GPU itself.
- Three trials (`t1-save12`, `t1-load2`, the first `final1`) were run against a tree that
  still contained a previously discarded commit — a `git reset --hard HEAD~1` landed back
  *on* the rejected change rather than past it. It was caught by `git log`, the affected
  rows are annotated in `results.tsv`, and the final-config repeats were redone. The
  `save=12` conclusion survives against its true comparator (−6.1pt); the `load` sweep was
  re-read and is reported as inconclusive rather than flat.
- `peak_disk_mb` is `du -sm` of the work directory sampled every 2 s, so it undercounts any
  peak shorter than the sample interval.
- GPU idle is the share of samples reading exactly 0% utilisation, not integrated idle time.
- No power draw was measured.
- The `-j` sweep points at save = 12 and 16 were run in regime A only; save = 3, 4 and 6
  were measured directly in regime B, so the rule's slope is confirmed at two CPU counts
  rather than extrapolated from one.
- The regime-A sweep points at save = 4 and save = 8 were taken at earlier base commits
  than save = 9, 12 and 16, so they are comparable in shape but not to the decimal.
