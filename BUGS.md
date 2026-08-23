# BUGS.md

**Most of what follows can no longer happen.** The 2026-08 rewrite removed the
conditions rather than the symptoms, and each entry below is kept because the
reasoning is worth reading, not because the failure is live.

Gone by construction:

- Anything about telling a source from a delivered output (`SRC_EXT`,
  `archived_for`, "re-upscaled its own output", "archived the master as the
  original") — sources and results are now in different directories.
- Anything about identifying a returned episode (the PSNR check, "wrong
  episode?", the 20 dB floor) — the file keeps its name the whole way.
- Anything about ranges, parities or absolute episode numbers disagreeing
  between machines — there are no ranges.
- Anything about two collectors clobbering `src.avi` — files are claimed
  atomically and travel under their own names.
- Anything about `upscale --status` disagreeing with the queue — that renderer
  is gone; the web UI reads the run's own snapshot.

Still live: everything about the worker — chunking, resume, scratch, decoder
behaviour, container timestamps.

---

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

**And it drops the subtitles and the fonts.** The audio fix mapped video and audio and
stopped there, so everything delivered afterwards had its `ass` track and its `ttf`
attachment silently discarded. This survived a whole series unnoticed because Bleach's
sources are XviD `.avi` carrying neither — the bug could not express itself. The first
subtitled show, Gintama, delivered an episode that was simply unwatchable: Japanese
audio, no subtitles, from a source that had both.

Cost: one delivered episode (Gintama S01E03), and the assumption that everything
delivered before it was complete. Not a re-upscale — the video was correct — only a
remux of the delivered file against its archived original.
→ Map every stream class the source can carry: `-map 0:v:0 -map "1:a?" -map "1:s?"
-map "1:t?" -c copy`. `1:t?` is the attachment specifier; without it the subtitles
arrive with no font to render them in. Keep every `?`: a source with none of these
must still succeed.

> Lesson: *a map list is a whitelist, and the streams you leave out do not warn you.*
> A library whose sources all happen to lack a stream class will never test it.

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

## `set -e` and exit status — seven separate outages

`var=$(cmd)` **adopts cmd's exit status**. Under `set -euo pipefail` that kills the
script, with no message. This caused seven different failures before the pattern was
recognised:

- the profiler died the moment nothing matched a `pgrep`
- the per-episode failure counter corrupted, marking a good episode failed 3×
- `--status` printed nothing at all
- the parity filter exited the queue instantly
- `GPUBUSY=$(ls /sys/class/drm/card*/...)` killed the entire script, before it
  printed a single character, on any machine with no GPU. Which is exactly the
  machine `collect` mode is meant to run on: the server.
- `ep=$(sed ... "$QLOG" | tail -1)` did the same thing on the GPU box, where
  there is no collector log to read. Exit 2, no output, nothing in any log.
  `2>/dev/null` hides the message but not the status.
- **`se=$(… | grep -oiE 'S[0-9]+E[0-9]+' | head -1)` in `discover()`'s `emit`**
  made the whole library report **nothing outstanding**. One archived file —
  `Specials/[Lunar] Bleach S1-S2 [640x480][AF803142].avi` — carries no `SxxEyy`,
  so grep exits 1, the assignment adopts it, and the discovery subshell dies
  before emitting a single candidate. The queue then sits idle looking healthy,
  and the `else` branch written specifically to handle specials by exact name is
  unreachable. `epkey()` and `epnum()` had `|| se=''`; `emit` did not.

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
- **`~/.local/bin` is not on the PATH of a non-interactive ssh session.** The run loop
  called the worker as a bare `upscale-ep`, so `upscale run` died with
  `line 1306: upscale-ep: command not found` the moment it was started over ssh rather
  than from a login shell. The same trap hit `ssh ubuntu wake-desktop` and `ssh laptop
  osd` in unrelated tooling — assume no PATH beyond the system default.
  **Worse than failing:** `install.sh` installs the worker as `libexec/upscale-worker`,
  but the bare name `upscale-ep` is what the pre-rename installs left on `$PATH`. On a
  machine carrying both, `run` silently executed the *old* worker — the one with no
  decoder cross-check and no PSNR delivery check, i.e. exactly the version whose missing
  guards are the subject of this file. Resolve the worker by path (`$WORKER`), never by
  name.

## The one that nearly shipped: the wrong episode, correctly counted

A delivered file was rejected for having 33435 frames against a source with 33437. It
was not a short encode. The file was **episode S01E02's video muxed with S02E19's
audio**, presented as S02E19.

The watchdog restarted the collector mid-episode — which its own comment calls safe,
"because it adopts a job already running on the box". The fresh instance re-ran
`discover | select_todo` from scratch and legitimately picked a *different* episode than
the one in flight. Then, in order:

1. it pushed its source over `$RWORK/src.avi` — **the running job's input** — because the
   push came before any check of what the box was doing;
2. it "adopted" that job, because `rworking()` only asked *is a worker running*, never
   *which episode*.

The worker had already extracted its frames, so the video stayed the old episode while
the final mux drew audio from the new `src.avi`. Every count-based assertion passed: the
worker compared against the total it computed at its own job start.

It was caught by two frames. S01E18, S02E02, S02E05 and S02E15 all decode to exactly
33435 — had the collision been with any of those, a wrong episode would have been
verified, published, and archived over its own original.

**Fixes**
- Ask the box what it is working on (`$RWORK/episode`) **before** pushing anything.
- Never overwrite the source of a running job.
- Adoption requires identity: an `out.mkv` belongs to `$RWORK/episode`, never to whatever
  the current instance happened to select. On a mismatch, switch to the box's episode or
  wait — never clobber.

**The general lesson.** Counting is not identity. Frame counts, byte sizes and durations
all answer "is this the right shape", never "is this the right thing" — and a pipeline
that only ever asks the first question will eventually ship something that is the right
shape. The check that saved this was a coincidence, not a design.

## Advertised but not implemented

`collect` mode reached the header comment and the argument dispatch — `collect)
MODE=run; DOCOLLECT=1 ;;` — while its body never made it into the file. Nothing read
`DOCOLLECT`, so `upscale collect` silently ran the **local** pipeline instead of
driving the remote GPU, on a machine that has no GPU at all.

The cause was a patch script whose insertion was guarded (`if 'cmd_collect' not in t`)
while its documentation and dispatch edits were unconditional. When the anchor for the
body failed to match, two of the three edits still landed and the script reported
success.

Lessons that generalise:

- **A guarded edit and an unguarded edit in the same patch can disagree.** Either all
  the edits assert, or none of them do.
- **Grep for the callee, not the flag.** `grep -c DOCOLLECT` was `2` and looked fine;
  `grep -c cmd_collect` was `0`.
- **Exercise every advertised command once on every machine it claims to support.**
  Both of this section's bugs — and the `set -e` GPU one above — would have been caught
  by running `upscale collect-once` on the server a single time.

A related shape: the usage text was printed with `sed -n '2,12p'`, a fixed line range.
Adding three lines to the header pushed `--profile` and `--profiles` off the end, so two
working commands became invisible. Slicing documentation by line number rots silently;
match its shape instead.

## Prefetching the wrong episode

The queue prefetched `TODO[1]` — "the second thing outstanding" — on the assumption that
it was processing `TODO[0]`. But the selection loop skips candidates that are already
processed and merely awaiting upload, so `TODO[1]` was frequently *the very episode being
processed*: the prefetch re-downloaded a file that was already local, and the genuinely
next episode was never fetched ahead of time.

Cost: the GPU sat idle for a full source download (~90 s at 2.2 MB/s, longer when a
400 MB upload was competing for the link) at the start of nearly every episode. The
prefetch's stderr was also being discarded, so its failures were invisible — for the one
background job whose silent failure directly wastes GPU time.

Fix: prefetch the next candidate *after the one actually chosen*, from the same
`runnable` list the run loop takes from, and log its output.

## The one that produced a file every tool could play except the one that matters

**The concat muxer starts the video track 20 ms after the container's zero, and
Fladder's libmpv backend then renders no video at all.**

Audio plays, subtitles load, the first frame sits frozen on screen for the whole
episode. Every other consumer is fine: `ffprobe` reports nothing wrong, `ffmpeg`
decodes every frame, `mpv` plays it start to finish over sftp, Jellyfin
direct-plays it without transcoding, and Fladder's *mdk* backend plays it. Only
libmpv-inside-Flutter fails, and its log says why:

```
media_kit: TextureGL: Resize: (1920, 1440)
GrBackendTextureImageGenerator: Trying to use texture on two GrContexts!
video_output_dispose -> video_output_new -> ...
```

The video output is torn down and rebuilt in a loop, so no frame is ever
presented.

How it was cornered, in order:

| test | result |
|---|---|
| the delivered file, decoded frame by frame | clean, correct content |
| the same file in mpv over sftp | plays fully |
| Jellyfin's stored metadata, codec, profile, level, pixfmt | identical to a working episode |
| Matroska top-level elements, cluster count, attachments | identical |
| every field `ffprobe -show_streams -show_format` prints | **identical** |
| container timestamps | **video starts at 0.020 s, working episode starts at 0** |

The working episode had been remuxed from a finished file for an unrelated
reason, which is the only thing that gave it a zero start - so the difference
was an accident, and without it this would have looked like a client bug with no
cause.

**ffmpeg cannot fix it with a stream copy.** `-output_ts_offset -0.020`,
`-itsoffset -0.020` and a plain `-c copy` remux all leave it at 20 ms: the
Matroska muxer re-derives cluster timecodes from the first packet. `mkvmerge -o
out in` rebases them, and the result matches the working file exactly (video
0.000, audio 0.020, subs 0.000).

**Fix:** run `mkvmerge` over the muxed file before delivering. Container rewrite,
no re-encode, ~30 s on a 1 GB episode, frame count re-asserted afterwards. If
mkvmerge is missing the worker says so loudly rather than shipping a file that
looks fine everywhere except in front of the person watching it.

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

## The one that ate its own source

**Publishing an `.mkv` master destroyed the original it was made from, then
re-upscaled the episode from its own output.**

The collector's publish, in order:

```bash
mv -f "$dest/.$base.mkv.part" "$dest/$base.mkv"   # master into place
...
mv -n "$src" "$ARCHIVE/${src#"$LIB"/}"            # then archive the original
```

With `SRC_EXT=avi` those are two different paths and the order is deliberate: the
source is the only copy until its replacement is verified in place, so it is
archived last.

With `SRC_EXT=mkv` they are **the same path**. `$src` *is* `$dest/$base.mkv`. So:

1. the master overwrote the original — the only copy, gone;
2. the archive step then moved the **master** into `.upscale-originals/`;
3. the library slot was left empty, so `discover` saw the episode as outstanding;
4. the collector selected it again and pushed its own 1440p master back to the
   box as a source — `pushing source (1002 MB)`, against a real source of ~130 MB.

Twenty seconds separated `delivered S07E51 (34525 frames, 45.12 dB, 1003M)` from
`=== S07E51 ===` selecting it a second time.

Every guard the pipeline has was satisfied: frame counts matched, streams were
present, and the PSNR identity check passed at 45.12 dB — because the master
genuinely *is* that episode. Nothing detects that a file is already finished
work, only that it is the right episode.

It destroyed the sources of **S01E10 and S07E51** before it was caught. Both were
recoverable only because the complete-series torrent was still seeding. Nothing
in the pipeline would have recovered them.

**Fix:** archive the original *before* moving the master into place. Verification
— frames, streams, PSNR identity — has already passed by that point, so deferring
the archive protects nothing and risks the source.

**The general shape:** an invariant that holds only because two paths differ by
extension. `SRC_EXT` was added so `.mkv` sources work; it silently turned an
ordering that was merely careful into one that was destructive. When a format
assumption changes, re-check every place that relied on paths being distinct.
