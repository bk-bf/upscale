# BASELINE.md — the specs to beat

The measurements this loop is judged against, and the controls run to make them
trustworthy. Data and method only; conclusions belong in `RESULTS.md`.

Provenance codes match `PERFORMANCE-DATA.md`: `BENCH` a deliberate benchmark, `OBS` a
single observation, `CFG` read from a config or sysfs.

---

## 1. Why this file exists instead of PERFORMANCE-DATA.md

`PERFORMANCE-DATA.md` records the completed Bleach run on **`rental-B`, an RTX 5070 Ti**.
This loop runs on a different, deliberately cheap box. **Its numbers are not comparable
to that file's and must never be reported as beating them.**

| | `rental-B` (the production run) | `research-box` (this loop) |
|---|---|---|
| GPU | RTX 5070 Ti | GTX 1660 SUPER, 6 GB, 125 W |
| CPU | 20 threads, 9.6 quota | Xeon E5-2630 v4 @ 2.20 GHz, 40 threads, 9.6 quota |
| RAM | 62.6 GB | 62 GB |
| disk | 32 GB overlay | 32 GB overlay, 29 GB free |

Same CPU quota, far weaker GPU, and a 2016 CPU. So the CPU:GPU balance here is the
**opposite** of production's: on `rental-B` the GPU outran the CPU feeding it; here the
GPU is the slow part. That is exactly why a second regime exists (§5) — an optimisation
tuned to this balance would be worthless, or wrong, in production.

**The deliverable of this loop is a set of percentages, not a set of fps numbers.**

The baseline in §3 is measured on *this* rental, and every result is expressed as a
percentage change against it. That percentage is the thing assumed to carry to the other
rentals and to the desktop — a change that removes a serialisation or stops doing
redundant work removes roughly the same *proportion* of wall clock wherever it runs, even
though the absolute fps will be completely different on a 5070 Ti.

Three qualifications on that assumption, in decreasing order of how much they matter:

1. It holds for **structural** changes and not for tuned constants. Regime B (§5) exists
   to tell those apart before a percentage is believed.
2. The proportion shifts if a change targets a phase whose *share* of wall clock differs
   on the target machine. Cutting extraction time by half is worth more on a box where
   the GPU is fast and extraction dominates than on this one, where the GPU is the slow
   part. So report which phase moved, not just the total — a reader on other hardware can
   then re-weight it.
3. **A percentage measured here is a prediction, not a result, until it is re-measured on
   the machine that will actually run it.** Say so in the report; do not present a number
   from this box as a production figure.

**What transfers:** structural findings and percentage deltas, with the caveats above.
**What does not:** any absolute fps, and any tuned constant.

## 2. Fixed constants — changing one invalidates every comparison

| parameter | value | provenance |
|---|---|---|
| source | `Bleach - S01E01 … SDTV.avi`, 640×480 mpeg4, 33 438 frames, 23.976 fps | OBS |
| source md5 | `cdd877aa9fd9fd6b504289a30442140b`, verified identical on both machines | OBS |
| work per trial | **first 2000 frames** — one production-sized chunk | CFG |
| model | `animejanai-suc` (2× AnimeJaNai V2 SuperUltraCompact), ncnn | CFG |
| upscaler | `realesrgan-ncnn-vulkan` v0.2.0 | CFG |
| scale | 2× (640×480 → 1280×960) | CFG |
| encoder | libx264, CRF 18, preset veryfast, `-threads 4`, yuv420p | CFG |
| decoder | system ffmpeg 6.1.1 (cross-checked against static 7.0.2) | CFG |
| baseline workers / chunk | 4 / 500 | CFG |

Two deliberate deviations from production, both to make trials comparable:

- **Audio is dropped.** Production remuxes it with `-c copy`; it costs nothing and is not
  an optimisation target. Removing it removes a whole class of trim-mismatch noise.
- **`-threads 4` is pinned explicitly.** Production lets x264 choose. Left to choose, the
  thread count would drift with CPU contention and change the bitstream, which would make
  the encoder gate fire on unrelated experiments.

`CHUNK=500` (4 chunks per trial) rather than production's 2000, so that overlap *between*
chunks is visible inside a 2000-frame trial at all.

## 3. The baseline

Measured 2026-08-13 01:10–01:36 UTC on the rented box, 2000 frames per trial,
commit `4dd82f2`. Every repeat produced the same frame hash (`76ca8f60…`) and the
same x264 option string, so the gate is armed and meaningful.

| regime | n | mean fps | min | max | spread |
|---|---|---|---|---|---|
| **A** — whole box, 9.6 CPU quota | 3 | **13.674** | 13.615 | 13.717 | **0.7%** |
| **B** — `taskset 0-3`, CPU-starved | 2 | **9.251** | 9.242 | 9.259 | **0.2%** |

**Spread is the noise floor.** A change smaller than it is weather, not an
improvement. Regime A's 0.7% is the binding one — treat anything under **+1.0%
in regime A** as no change and revert it. Regime B's 0.2% comes from only two
repeats and should not be read as the tighter instrument; it is the smaller
sample, not the quieter one.

Regime B is 32% slower than A on identical work, which is the point: starving
the CPU moves the bottleneck off the GPU, and a keeper has to survive both
balances.

## 4. Control: is the upscaler even deterministic?

The entire quality gate assumes that identical input produces byte-identical output. If
it did not, a re-run of the baseline would fail its own gate. Measured before anything
else, 40 frames, hashing decoded frame content (BENCH):

| run | condition | hash |
|---|---|---|
| A | one process | `e39f8077…f0b1a5ea` |
| B | identical repeat | `e39f8077…f0b1a5ea` |
| C | same frames sharded across **4 concurrent** processes | `e39f8077…f0b1a5ea` |

**Bit-identical across repeats and across concurrency.** So the gate is sound, and worker
count cannot change a pixel — concurrency is free to be optimised.

## 5. The two regimes

A win that only exists on one machine is not a win. Every trial declares a regime:

| regime | what it is | which resource is scarce |
|---|---|---|
| **A** | the whole box, 9.6 CPU quota | the GPU |
| **B** | `taskset -c 0-3`, four cores | the CPU — production's balance |

The GPU cannot be made faster, so the balance is shifted from the other side by starving
the CPU. A change that wins in both is *structural*. A change that wins in only one is a
tuned constant — and this project has already measured tuned constants failing to
transfer: the optimum worker count was **8 on the desktop and 4 on `rental-B`**, same
pipeline, opposite answers (PERFORMANCE-DATA §6, §7).

## 6. Already measured and rejected — do not re-litigate

From the production run (PERFORMANCE-DATA §13). Anything here that changes pixels is now
also blocked automatically by the gate.

| configuration | measurement | why it is closed |
|---|---|---|
| perceptual frame dedup | 45–53% fewer frames | judders on slow pans — rejected by eye |
| 800p/720p intermediate | no change to doubled lines | rejected by eye |
| `waifu2x` upconv_7 | +10% fps | quality rated worse by viewer |
| `waifu2x` cunet | −50% fps | slower *and* worse |
| tile size, every setting | 0%, flat | no effect to find |
| a lighter model at 1 worker | 0% | GPU was not saturated; the gain only appears after concurrency |

## 7. What the harness measures per trial

`fps` is the metric; everything else is there to explain *why* it moved.

| field | meaning |
|---|---|
| `fps` | frames ÷ wall seconds — the metric |
| `g1_pixels` / `g2_encoder` / `g3_shape` | the gates; all must pass before fps counts |
| `gpu_util_mean` | mean GPU utilisation over the run, sampled every 2 s |
| `gpu_idle_pct` | fraction of samples with the GPU at 0% — the headroom number |
| `cpu_util_mean`, `peak_rss_mb`, `peak_disk_mb` | where the machine was actually spending |

`gpu_idle_pct` is the one to watch. Profiling during the production run found the card
idle 62% of the time, and moving encode and PNG deletion off its critical path took that
to 33% (PERFORMANCE-DATA §6). That is the same class of win this loop is hunting.

## 8. Known caveats

- One box, one GPU, one source episode. Regime B emulates a different CPU:GPU balance by
  starving the CPU; it does **not** emulate a faster GPU, which cannot be done here.
- 2000 frames is one chunk of one episode. Per-episode costs that happen once — decoder
  cross-check, final concat, delivery — are under-weighted relative to a full 33 000-frame
  episode.
- Trials are serial on one GPU, so all of them share whatever else the box is doing. The
  box is otherwise idle, but it is a rented multi-tenant machine.
- No power draw measured, as in the production data.
- The box clock runs ~30 min behind the orchestration node; timestamps in `runs/*.txt`
  are box-local and are not comparable to the loop's own log.
