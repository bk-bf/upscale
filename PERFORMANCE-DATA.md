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
