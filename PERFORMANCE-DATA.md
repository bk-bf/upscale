# PERFORMANCE-DATA.md

Raw measurements from the Bleach run (2026-08-09 → 2026-08-12). **Data only — no
analysis, no conclusions, no recommendations.** Every row states how it was obtained so a
later reader can judge it and reproduce it.

Provenance codes used in the tables:
- `LOG` — parsed from `~/.upscale-queue/log`, n given. Emitted by the pipeline itself.
- `BENCH` — deliberate benchmark run, fixed frame count, one variable changed.
- `OBS` — single observation from a live `upscale --status` frame or a shell command.
- `CFG` — read from a config file or kernel/sysfs value.

Units: `fps` = frames per second of 640×480 → 1280×960 upscale unless stated.
"effective fps" = episode frame count ÷ total worker wall time (includes decode to PNG,
upscale, encode, mux; excludes network transfer and server-side verification).

---

## 1. Machines

| id | role | GPU | CPU | RAM | disk (work) | network to server | provenance |
|---|---|---|---|---|---|---|---|
| `desktop` | local queue, own GPU | AMD Radeon RX 5700 XT (Navi 10, `1002:731f`) | AMD (B450 AORUS ELITE board) | 15.5 GB | `/mnt/scratch` 203 GB free | WiFi via AVM repeater, 5220 MHz, −64 dBm | OBS/CFG |
| `rental-A` | remote worker (early, decommissioned) | 10× NVIDIA RTX 5060 Ti | — | — | — | — | OBS |
| `rental-B` | remote worker (main run) | 1× NVIDIA RTX 5070 Ti | `nproc`=20, **cgroup quota 9.6 CPU** (`cpu.cfs_quota_us=960000`) | 62.6 GB | 32 GB total, overlay | public internet (Vast.ai) | OBS/CFG |
| `ubuntuserver` | collector, verification, library | none | — | 31.3 GB | `/mnt/media` (library) | wired `br0`, 100 Mb/s full duplex | OBS |

Notes recorded as fact, not inference:
- `rental-B` `nproc` reports 20; the cgroup CPU quota is 9.6 CPUs. Both values measured.
- `desktop` and `ubuntuserver` are **not** on a LAN link; path traverses a WiFi repeater
  with a CAKE shaper. Documented separately in
  `~/server/mediaserver/.docs/WAN-BUFFERBLOAT.md`.
- `rental-A` was replaced before the main run; only the datapoints in §6 exist for it.

## 2. Software configuration during the main run

| parameter | value | provenance |
|---|---|---|
| upscaler | `realesrgan-ncnn-vulkan` (Vulkan/ncnn) | CFG |
| model | `animejanai-suc` = 2× AnimeJaNai V2 SuperUltraCompact | CFG |
| scale | 2× (640×480 → 1280×960) | CFG |
| workers (`rental-B`) | 4 concurrent upscaler processes | CFG |
| workers (`desktop`) | 8 concurrent upscaler processes | CFG |
| chunk size | 2000 frames | CFG |
| encoder | x264, CRF 18, preset veryfast | CFG |
| encoder threads | 4 (`ENC_THREADS=4`) | CFG |
| audio | remuxed from source, `-c copy` | CFG |
| decoder | pinned static ffmpeg 7.0.2 (`FFDEC`), cross-checked against system ffmpeg | CFG |
| server ffmpeg/ffprobe | 6.1.1 | OBS |

## 3. Upscale-phase throughput, `rental-B` (RTX 5070 Ti, 4 workers)

Per-chunk, full 2000-frame chunks only (partial final chunks excluded).

| statistic | value |
|---|---|
| n | 1064 |
| min | 28.9 fps |
| p25 | 40.0 fps |
| median | 41.6 fps |
| p75 | 43.4 fps |
| max | 55.5 fps |
| mean | 41.70 fps |

Same population expressed as wall seconds per 2000-frame chunk:

| statistic | value |
|---|---|
| n | 1064 |
| min | 36 s |
| median | 48 s |
| max | 69 s |
| mean | 48.3 s |

Provenance: LOG, `upscaled 2000 in Ns (X fps)` lines.

## 4. End-to-end episode throughput, `rental-B`

Worker-reported totals (`done:` lines): frame count, output size, wall seconds for the
entire per-episode job.

| frames | output | wall | effective fps |
|---|---|---|---|
| 35361 | 629 MB | 1240 s | 28.5 |
| 33376 | 494 MB | 1414 s | 23.6 |
| 33435 | 416 MB | 1406 s | 23.8 |
| 33459 | 414 MB | 1403 s | 23.8 |
| 35353 | 416 MB | 1451 s | 24.4 |
| 35360 | 478 MB | 1026 s | 34.5 |
| 34640 | — | 1429 s | 24.2 |

Mean over n=17 sampled `done:` lines: **24.8 effective fps**.
Provenance: LOG.

Delivery cadence over the sustained portion of the run: **59 episodes delivered in 12 h**
(LOG, counted between timestamps).

## 5. Episode and output characteristics (n=69 delivered)

| quantity | min | median | max | mean |
|---|---|---|---|---|
| source frames | 33376 | 33439 | 69038 | — |
| delivered size | 373 MB | 462 MB | 993 MB | 495 MB |

Source file sizes pushed to `rental-B` (LOG, `pushing source (N MB)`), counts by size:

| MB | count |
|---|---|
| 169 | 1 |
| 170 | 23 |
| 172 | 1 |
| 173 | 2 |
| 174 | 2 |
| 175 | 36 |
| 176 | 1 |
| 201 | 1 |
| 275 | 4 |
| 300 | 1 |

(The 275/300 MB entries are double-length episodes: two episodes in one file,
~65 000–69 000 frames, 33–35 chunks instead of 17–18.)

## 6. Model and worker-count sweeps, `desktop` (RX 5700 XT)

BENCH, 1200 frames per run, single variable changed.

| model | 4 workers | 8 workers | 12 workers |
|---|---|---|---|
| `2x_AnimeJaNai_V2_SuperUltraCompact` | 33.1 fps | **38.7** fps | 36.4 fps |
| `realesr-animevideov3` | 16.0 fps | 16.2 fps | 16.2 fps |

Single-variable deltas measured on `desktop`, each against the then-current baseline:

| change | measured effect | provenance |
|---|---|---|
| baseline, 1 worker, `realesr-animevideov3` | 9.97 fps | BENCH |
| tile size, all settings tried | 0% (flat) | BENCH |
| lighter model, 1 worker | 0% | BENCH |
| concurrency 1 → 8 workers | +58% | BENCH |
| model swap after concurrency raised | +118% | BENCH |
| further +17% at 8 workers on the light model | +17% | BENCH |
| `waifu2x` upconv_7 | +10% throughput | BENCH |
| `waifu2x` cunet | −50% throughput | BENCH |
| moving encode + PNG delete off the GPU critical path | GPU idle 62% → 33% | OBS (profiling log) |
| perceptual frame dedup | 45–53% fewer frames processed | BENCH |

Final `desktop` figure after all of the above: **38.7 fps** (BENCH), from 9.97 fps
baseline. Reported episode time at that rate: ~15 min for a 23-minute episode.

## 7. Worker-count sweep, `rental-B` (RTX 5070 Ti, cgroup-limited to 9.6 CPU)

BENCH.

| workers | fps |
|---|---|
| 4 | 43.7 |
| 5 | 42.6 |
| 6 | 21.9 |
| 8 | 14.0 |

## 8. Component utilisation snapshots

Single frames from `upscale --status`; each row is one instant, not an average.

| machine | phase | CPU | RAM | GPU | VRAM | net |
|---|---|---|---|---|---|---|
| `desktop` | upscaling, 8 workers | 99% | 3.4 / 15.5 GB | 67% | 1.0 / 8.0 GB | 1.7 ↓ / 15.2 ↑ Mbit/s |
| `rental-B` | upscaling, 4 workers | 44% | 5.3 / 62.6 GB | 29–87% | — | — |
| `rental-B` | between chunks | 1–3% | 4.1 / 62.6 GB | 0% | — | 0.2–0.3 ↓ / 8.3–14.7 ↑ Mbit/s |
| `rental-B` | sending result | 1% | 4.3 / 62.6 GB | 0% | — | 0.3 ↓ / 9.2 ↑ Mbit/s |

`rental-B` load averages observed: 0.22–4.94 (OBS).
`desktop` load average observed at 8 workers on 16 threads: 25 (OBS, during a run where
x264 was not thread-limited).

## 9. Phase costs other than upscaling

| phase | measurement | provenance |
|---|---|---|
| decoder cross-check (5 sampled frames, two decoders) | ~5 s per episode | OBS, n=108 occurrences logged |
| source decode to PNG (extraction) | 33 420–33 437 frames → 9.5–11 GB on disk | LOG, n=46 |
| extraction wall time | ~200–290 s per episode | LOG |
| identity PSNR check (5 frames, best-of) | ~2 s per episode | OBS |
| server-side `ffprobe -count_frames` on delivered 400–500 MB h264 | several minutes, CPU-bound | OBS |

## 10. Disk footprint, `rental-B` (32 GB total)

| state | free disk | provenance |
|---|---|---|
| idle between episodes | 30 GB | OBS |
| single episode, mid-run | 18–20 GB | OBS |
| double episode, mid-run | 8 GB | OBS |
| guard cleanup threshold | 4 GB (`MIN_FREE_GB`) | CFG |

## 11. Transfer measurements

| link | direction | rate | provenance |
|---|---|---|---|
| `desktop` ↔ `ubuntuserver` | both | ~1.5–2.2 MB/s ceiling | BENCH (documented in WAN-BUFFERBLOAT.md) |
| `rental-B` → `ubuntuserver` (pull) | inbound | ~1.4 MB/s observed; 11.68 Mbit/s on `br0` during one sample | OBS |
| `ubuntuserver` → `rental-B` (push) | outbound | 170–175 MB in ~90–150 s | LOG |
| ssh connection setup to `rental-B` | — | 4.3 s cold; ~0.7 s over a shared ControlMaster | BENCH |
| local read of box state (on the box) | — | 6 ms | BENCH |

## 12. Output quality measurements

Identity check: 5 single frames per delivered episode, downscaled to source resolution,
Y-channel PSNR, best-of-5 taken.

| statistic | value |
|---|---|
| n | 38 |
| min | 28.5 dB |
| median | 37.7 dB |
| max | 44.6 dB |
| mean | 37.6 dB |

Control measurements from a known-mismatched pair (different episode's video):

| pair | PSNR |
|---|---|
| wrong episode vs source | 8.08 dB (single frame), 13.58 dB (best-of-5) |
| that file vs its true source | 29.42 dB (best-of-5) |

Provenance: LOG for the 38; BENCH for the controls.

## 13. Rejected configurations — measured reasons

Recorded as observations, not judgements.

| configuration | measurement |
|---|---|
| perceptual frame dedup | 45–53% fewer frames processed; visible judder on slow pans reported by viewer |
| 800p and 720p intermediate resolutions | did not remove doubled-line artifacts reported by viewer |
| `waifu2x` upconv_7 | +10% fps; quality rated worse by viewer |
| `waifu2x` cunet | −50% fps |
| tile size tuning | no measurable change at any setting |
| `rental-A` (10 GPUs) | `NCNN_MAX_GPU_COUNT=8` → `VK_INCOMPLETE`; required rebuilding ncnn from patched source |

## 14. Known measurement caveats

- §3 and §4 come from the same run but different denominators: §3 counts only the upscale
  phase, §4 counts the whole per-episode job.
- §8 rows are instantaneous samples; `nvidia-smi` was observed reading 0% during
  between-chunk file I/O on a machine that was otherwise mid-episode.
- §6 `desktop` figures were taken across several days as the pipeline changed; the
  baseline moved between rows, so the percentage deltas are each against the baseline
  current at that time, not against 9.97 fps throughout.
- No power draw was measured on any machine.
- No per-phase GPU utilisation breakdown exists for `rental-B`; only the whole-run
  snapshots in §8.
- `rental-A` has no throughput figures; it was decommissioned before the main run.
- §15–§19 come from a different box (`rental-C`) and a different source file than §1–§14,
  and are 2000-frame trials rather than whole episodes. They are not comparable to the
  Bleach-run figures in absolute terms; only the ratios within §16 and §18 are.
- §16 intermediate steps between baseline and optimised are n=1 per regime; only the
  baseline and final rows are repeated. `research/results.tsv` marks three trials that ran
  against a tree still carrying a previously discarded commit.
- §18 A rows at save=4 and save=8 were taken at earlier base commits than save=9/12/16.

---

## 15. Research box `rental-C` — optimisation loop, 2026-08-13

All rows in §15–§19 come from the overnight optimisation loop
(`research/PROGRAM.md`), a separate exercise from the Bleach run above. New
provenance code:

- `TRIAL` — one run of `research/harness/trial.sh`: fixed 2000-frame slice of a pinned
  source, one variable changed, three pass/fail quality gates evaluated before the fps is
  recorded. Full per-trial table in `research/results.tsv`.

| id | role | GPU | CPU | RAM | disk (work) | provenance |
|---|---|---|---|---|---|---|
| `rental-C` | optimisation-loop box only; never ran production work | 1× NVIDIA GTX 1660 SUPER, 6 GB | `nproc`=40, **cgroup quota 9.6 CPU** (`cpu.max 960000 100000`) | 62.7 GB | 32 GB total, overlay, 27 GB free | OBS/CFG |

Notes recorded as fact, not inference:
- `rental-C` is a container **without `CAP_SYS_ADMIN`**: `mount(8)` returns rc=32. A tmpfs
  cannot be mounted over the work directory. `/dev/shm` exists, 7168 MB. OBS.
- Regime `A` = the whole box. Regime `B` = the same box under `taskset -c 0-3`. CFG.
- Source for all trials: 640×480 mpeg4, 2000 frames, `animejanai-suc` ×2, x264 CRF 18
  preset veryfast, `-threads 4`, yuv420p. CFG.

## 16. Optimisation-loop baseline and result, `rental-C`

All clean measurements of each configuration, pooled across the 2026-08-13 session.

| regime | config | n | mean fps | min | max | spread | provenance |
|---|---|---|---|---|---|---|---|
| A | baseline (production worker shape) | 5 | 13.684 | 13.615 | 13.717 | 0.75% | TRIAL |
| A | optimised | 14 | 20.586 | 20.243 | 20.833 | 2.87% | TRIAL |
| B | baseline (production worker shape) | 4 | 9.240 | 9.225 | 9.259 | 0.37% | TRIAL |
| B | optimised | 9 | 11.075 | 11.050 | 11.111 | 0.55% | TRIAL |

Change: regime A **+50.4%**, regime B **+19.9%**.

Under host contention, baseline and optimised measured back to back so the pair shares
conditions. `cpu_util_mean` is whole-host across 40 cores. TRIAL.

| regime | condition | cpu_util_mean | baseline fps | optimised fps | change |
|---|---|---|---|---|---|
| A | quiet | 20–24% | 13.684 (n=5) | 20.586 (n=14) | +50.4% |
| A | contended | 43–46% | 12.188 (n=6) | 18.643 (n=6) | +53.0% |
| B | quiet | 9–11% | 9.240 (n=4) | 11.075 (n=9) | +19.9% |
| B | contended | 29–34% | 8.046 (n=4) | 9.189 (n=6) | +14.2% |

Individual regime-A contended pairings span +48.8% to +60.1% depending on which runs are
matched; runs are sequential, not simultaneous. The contended regime-A baseline is
bimodal — 12.837/12.821 and 11.474/11.455 — at cpu_util 43.0–43.5 throughout. TRIAL.
Worst optimised run against best baseline run: A **+47.6%**, B **+19.3%**.

Scaling probe at 4000 frames, regime A, both configs at the same frame count (outside the
gate by design — G1 hashes a 2000-frame set): baseline 13.774 fps, optimised 20.833 fps,
**+51.2%**. TRIAL.

Control: the unmodified baseline worker was re-measured three times over the four hours
following the opening baseline, interleaved with experiments. Regime A +0.1%, regime B
−0.1% against the opening means. TRIAL.

`cpu_util_mean` (whole host, 40 cores) during optimised regime-A runs ranged 22.3–24.6%
across the session; the three slowest optimised runs coincided with the high end. Baseline
runs in the same window were unaffected at 20.1%. OBS.

Quality gates across all 30 trials: decoded-frame hash `76ca8f60…` and x264 option-line
hash `623c768d…` identical in every trial that passed, including every kept change. TRIAL.

## 17. Phase costs, `rental-C`, baseline vs optimised

Wall-clock seconds per 2000-frame trial, from the worker's own phase log. TRIAL.

| regime | phase | baseline | optimised |
|---|---|---|---|
| A | extract (decode + write 2000 PNGs) | 10 | 3 |
| A | upscale, incl. per-chunk startup and inter-chunk barriers | 128 | 86 |
| A | tail encode (final block) | 8 | 7 |
| A | concat | 0 | 0 |
| A | **total** | **146** | **97** |
| B | extract | 15 | 3 |
| B | upscale + barriers | 190 | 168 |
| B | tail encode | 10 | 10 |
| B | **total** | **216** | **181** |

GPU idle (share of 2 s telemetry samples reading 0% GPU utilisation):

| regime | baseline | optimised |
|---|---|---|
| A | 31.3% (35.8% on the end-of-night repeat) | 10.9–13.0% |
| B | 20.4% | 6.9% |

`cpu_util_mean` during optimised regime-A trials: 22.4% of 40 processors = 8.96 of the
9.6-CPU quota. GPU utilisation mean over the same trials: 31.5%. TRIAL.

Fixed startup cost of one `realesrgan-ncnn-vulkan` invocation, measured directly by
timing 1, 20 and 100 frames through it on an idle box: **1.83 s**, plus 0.044 s/frame at
`-j 4:8:8`. BENCH.

## 18. `-j load:proc:save` sweep, `rental-C`

`save` is the thread count that tracks the hardware; `load` was swept and showed no
measurable effect. Percentages are against that regime's baseline. TRIAL.

| regime | usable CPUs | save threads | fps | vs regime baseline |
|---|---|---|---|---|
| A | 9 | 4 | 12.587 | −7.9% |
| A | 9 | 8 | 18.132 | +32.6% |
| A | 9 | **9** | 18.332 | +34.1% |
| A | 9 | 12 | 19.550 | −6.1 pt vs peak |
| A | 9 | 16 | 16.892 | −9.1 pt vs peak |
| B | 4 | 3 | 9.474 | +2.4% |
| B | 4 | **4** | 11.075 | +19.9% |
| B | 4 | 6 | 10.941 | −1.3 pt vs peak |

Rows at differing base commits are marked in `research/results.tsv`; the A rows at
save=8/9/12/16 and the B rows are each internally comparable.

Usable CPUs measured as `min(nproc, cgroup cpu quota)`: 9 in regime A (nproc 40, quota
9.6), 4 in regime B (`taskset -c 0-3`). CFG.

Hardcoded `-j 4:8:8`, the regime-A optimum, measured in regime B: 10.204 fps (+10.3%),
against 10.428 fps (+12.7%) for the same code with the thread mix derived from usable
CPUs. TRIAL.

## 19a. Leave-one-out ablation of the final configuration, `rental-C` regime A

Each row removes one change from the finished configuration; all other changes stay in.
Final configuration mean for comparison: 20.586 fps (n=14). TRIAL.

| change removed | fps | delta attributable to that change |
|---|---|---|
| `-j load:proc:save` rule (upscaler falls back to its `1:2:2` default) | 7.710 | +166% |
| extraction `-compression_level 0` (ffmpeg default compression) | 18.832 | +9.0% |
| single upscaler invocation (spawned once per encoder block instead) | 19.305 | +6.3% |
| batched shard/hash hardlinks (one `ln` fork per frame instead) | 20.346 | +0.9% |

At the default `-j 1:2:2` a single upscaler process runs 2 save threads; the four-process
baseline ran 8. Measured `cpu_util_mean` in that row: 7.4%, against 22.5% for the final
configuration. OBS.

## 19. Rejected configurations from the optimisation loop — measured reasons

Recorded as observations, not judgements. All regime A unless stated. TRIAL.

| configuration | measurement |
|---|---|
| `-t 640` (one tile per frame instead of the VRAM auto-heuristic's ~12) | **+58.1% fps, FAILED the pixel gate** — decoded frames differ from baseline |
| background extraction overlapped with first chunk | +0.9% A, +0.2% B — inside both noise floors |
| parallel tail encode, 2 concurrent x264 | +0.0% |
| encoder block size 250 instead of 500 | −1.5% |
| final block capped at CHUNK/4 | −1.2% |
| final block sized to the handoff slack | +0.2% |
| 1× frames staged in `/dev/shm` | +1.6% A, **+0.0% B** |
| tmpfs mounted over the work directory | not possible; `mount` rc=32, no `CAP_SYS_ADMIN` |
| encode direct from the upscaler output dir, no per-block staging | +0.0%, ~4000 fewer filesystem ops per trial |

Contradiction with §13 recorded rather than resolved: §13 lists "tile size tuning — no
measurable change at any setting" (RTX 5070 Ti / RX 5700 XT). On `rental-C` (GTX 1660
SUPER, 6 GB) forcing a single tile changed throughput by +58.1% **and changed the output
pixels**. Both measurements stand; the machines and VRAM budgets differ.
