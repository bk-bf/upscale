# Bugs

Everything that went wrong building this, and what fixed it. Written down because
most of these were silent — the pipeline reported success while producing damaged
output — and because several classes of them will bite anyone doing the same thing.

## The one that mattered: a decoder that lies

**`ffmpeg 8.1.2` silently mis-decodes MPEG-4 ASP (XviD/DivX).** Byte-identical input,
visibly corrupt output, and — critically — the **correct frame count**.

Two episodes shipped with wrecked openings before anyone noticed, because every check
in the pipeline was a *count* check. Extraction, upscale, encode and concat all
asserted frame counts, and all of them passed.

How it was cornered, in order:

| test | result |
|---|---|
| source frame, decoded on the server (6.1.1) | clean |
| same frame through the model, directly | clean and sharp |
| a different episode, same model and settings | clean |
| the delivered file | destroyed |
| the *segment* before concat | already destroyed |
| the fetched source, md5 vs server | **identical** |
| that source decoded on the desktop (8.1.2) | **corrupt** |

Same bytes, same command, different ffmpeg. Not hwaccel, not threading — a decoder
regression. A static 7.0.2 build produced output byte-identical to 6.1.1.

Damage is content-dependent, not uniform. Measured against a known-good decoder:
`mean diff 19.5 / max 237` on an opening frame, and **exactly 0** — bit-identical —
from frame ~2500 onward. That is why only the openings looked wrong.

**Fixes**
- Pin a known-good decoder for extraction (`FFDEC`); the system ffmpeg is fine for encoding.
- **Cross-check two decoders before upscaling anything.** Five sampled frames, ~5 s per
  episode, aborts on disagreement. This is the only guard that catches content corruption;
  no count-based assertion can.

> Lesson: *frame counts prove nothing about content.* If a stage can produce wrong
> pixels, something must compare pixels.

## Silent output corruption of another kind

**Rebuilding video from frames drops the audio.** Episodes shipped silent. The frame
counts were, again, perfect.
→ Mux the original back at concat: `-map 0:v:0 -map "1:a?" -c copy`. No re-encode, so it
costs nothing. `1:a?` is optional so a source without audio still succeeds.

## ffmpeg invocation traps

**Apostrophes break concat lists.** The concat demuxer quotes each path in single
quotes, so `A Shinigami's Work` truncated at the apostrophe and the episode failed.
A third of this series' titles contain one.
→ Escape as `'\''`.

**`-bsf:v mpeg4_unpack_bframes` cannot apply to a PNG output stream.** It's an *output*
bitstream filter; pointing it at an image sequence fails every time. It sat in the
extraction path silently falling back for hours.
→ Removed. The decoder handles packed bitstream on its own — and unpacking first is
actively harmful: it dropped 14 frames of 33,438.

**ffmpeg can't infer a container from `.mkv.part`.** Writing to a temp name needs
`-f matroska` stated explicitly.

**`-ss` seeks to the keyframe at or before the timestamp**, and a value a hair under a
keyframe's PTS lands on the *previous* one. Cutting at `83.416666` when the keyframe is
at `83.417000` silently took 42 extra frames.
→ Cut by verified frame count, not computed timestamp, and assert the total afterwards.

## `set -e` and exit status — four separate outages

`var=$(cmd)` **adopts cmd's exit status**. Under `set -euo pipefail` that kills the
script, with no message. This caused four different failures before the pattern was
recognised:

- the profiler died the moment nothing matched a `pgrep`
- the per-episode failure counter corrupted, marking a good episode failed 3×
- `--status` printed nothing at all
- the parity filter exited the queue instantly

Related: **`grep -c` and `pgrep -c` print `0` *and* exit non-zero.** So
`n=$(grep -c x f || echo 0)` yields the string `"0\n0"`, which then blows up any
arithmetic using it.

Also: **`[ test ] && continue` returns non-zero when the test is false**, and `set -e`
treats that as fatal.

**Fixes**
```bash
n=$(grep -c x f) || n=0        # || on the ASSIGNMENT, not inside the substitution
if [ "$x" -eq 0 ]; then continue; fi   # never `[ ] && continue` under set -e
```

## Concurrency

**A bare `wait` reaps every child.** The background encoder was started, then the next
chunk's bare `wait` (intended for the upscale workers) reaped it, so the later
`wait "$enc_pid"` failed with *"pid is not a child of this shell"* and killed the episode.
→ Collect worker PIDs and wait on those specifically.

**x264 grabs every core.** Once the encode ran concurrently with the upscale workers it
fought them directly — load average 25 on a 16-thread box, GPU starved to 15%.
→ `-threads 4`. It only has to keep up with one chunk per ~50 s.

**`pgrep`/`pkill -f` match your own command line.** `pkill -f upscale-profile` issued over
ssh killed the ssh session (exit 255), because the remote shell's argv contained the
pattern. Passing the script via stdin (`ssh host 'bash -s' <<'EOF'`) leaves argv as just
`bash -s` and avoids it.

## Queue logic

- **One failed episode ended the entire run** — `set -e` propagated it up. Now failures
  are isolated, counted, and abandoned after `MAX_FAILS`.
- **The queue re-selected episodes that were still uploading.** They are genuinely
  "outstanding" until the `.mkv` lands, so it kept picking the same one and spinning
  instead of starting the next — destroying the transfer/compute overlap that is the
  whole point of the phase split. Now it skips anything already processed locally.
- **`--pause` did nothing.** The queue wrote the flag to its own state dir; the worker
  polled a different one. Both are written now.
- **Two machines collided.** Splitting a season across a second box needs an explicit
  filter (`PARITY=even|odd`) — otherwise both discover the same work.

## Status display

Every one of these made the pipeline *look* broken or fine when it wasn't:

- read the wrong state directory → "no episode in flight" during an active episode
- captured `render` in `$(...)`, so the subshell discarded the previous CPU/network
  sample and both metrics read `-` forever
- cleared the whole screen before redrawing → flicker; and called `discover` (an ssh
  round-trip) **every frame**, so each redraw waited ~1 s. Cached it and redraw with
  per-line `\033[K` instead: 8 ms/frame.
- counted a finished chunk twice (segment written *and* its frames still on disk),
  overshooting by a whole chunk — which a high-water clamp then made permanent
- reported a phantom **100%** from a job file that outlived its work directory
- `tr '\n' '\x1f'` — GNU `tr` takes octal, not `\xNN`, so the delimiter silently mangled
  episode names
- rsync `--partial` writes `.name.mkv.part.XXXXXX`; a glob without the trailing `*`
  never matched, so upload progress was always empty

## Environment

- **Tailscale grants are one-directional.** `desktop → server` does not imply
  `server → desktop`; the reverse needs its own grant. Symptom: `tailscale ping` succeeds
  (handled by tailscaled) while TCP times out.
- **Tailscale SSH `src` rejects host aliases** — it is identity-based (users, groups,
  tags), not IP-based. An untagged node inherits its owner's identity, so an existing
  `user → autogroup:self` rule already covers machine-to-machine SSH; only the L3 grant
  was missing.
- **Hardlinks cannot cross filesystems.** Staging chunks by hardlink requires the frame
  store and the work dir on one device.
- **ncnn caps at 8 GPUs.** `NCNN_MAX_GPU_COUNT 8` in `gpu.cpp`: it clamps the device
  count, then treats the resulting `VK_INCOMPLETE` (Vulkan error 5) as fatal — so
  `realesrgan-ncnn-vulkan` fails outright on a 10-GPU host. Rebuild with a raised cap.
- **CUDA containers exclude Vulkan.** `NVIDIA_DRIVER_CAPABILITIES` defaults to
  `compute,utility`; without `graphics` the driver's Vulkan ICD is never mounted and the
  GPU is invisible to ncnn.
- **`nproc` lies inside containers.** One rental reported 20 CPUs against a cgroup quota
  of 9.6. Tuning workers on `nproc` caused hard CFS throttling — 2654 of 7294 periods
  throttled — and made 8 workers **three times slower** than 4 on that box. Read
  `cpu.cfs_quota_us`, not `nproc`.

## Measurement mistakes

- **Extrapolating throughput from TFLOPS was wrong every time.** Predicted 2–3 fps for a
  5700 XT that did 9.97; predicted 13.5 fps/card for a 2080 Ti that did 7.66. The
  bottleneck moves between GPU, CPU and per-frame overhead depending on model and worker
  count, so only measurement on the actual machine means anything.
- **Benchmarks that are too small mislead.** 144 frames per GPU could not amortise an
  18.6 s startup, reading as 26 fps where the real figure was 48.7.
- **Measuring one change at a time hid the answer.** More workers alone: +58%. A cheaper
  model alone: **0%**. Together: +3.9×. The model is worthless until the GPU is
  saturated, and the GPU cannot saturate on one worker — testing them separately made
  both look useless.
- **md5 is too sensitive for "is this corrupt?"** Two ffmpeg versions differ on every
  frame from IDCT rounding alone. Use a perceptual difference; the real corruption showed
  `mean 19.5` against `0.0` for benign version skew.
