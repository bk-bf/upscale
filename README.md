# upscale

A resumable pipeline for AI-upscaling an entire anime series, on one consumer GPU,
faster than you can watch it.

Sources live on a media server; the GPU lives somewhere else. `upscale` pulls each
episode over ssh, upscales it 2×, delivers the result back into the library, and moves
the original into a hidden archive — so the library only ever shows finished episodes.
Then it does the next one, forever, until there is nothing left.

It is built to survive a power cut. There is **no saved work list and no progress
pointer**: every cycle it asks one question — *which source has no matching `.mkv`?* —
so after any crash or reboot of either machine, whatever is unfinished is still
unfinished, and whatever finished is visible and skipped. A half-done episode resumes
from its already-encoded chunks.

```
upscale run          process everything outstanding, then idle for more
upscale once         process outstanding work, then exit
upscale --status     live progress, phase, upload, host utilisation
upscale --pause      hold after the current chunk
upscale --resume     release a pause
upscale --stop       finish the current episode, then exit
upscale --list       show what is outstanding
```

```
Bleach - S01E04 - And Your Bird Can Speak SDTV
[█████████░░░░░░░░░░░░░░░░░░░░░░░░░]  29%   10000/33527 frames
chunk 6/17   38.4 fps   elapsed 4m   eta 10m
upload  [████████████░░░░░░░░]  61%   287/464 MB   Bleach - S01E03
state   processing + uploading   |   queue 93 outstanding
next    Bleach - S01E05 - Beat the Invisible Enemy! SDTV Proper
cpu  99%   ram 3.4/15.5G   gpu  67%  vram 1.0/8.0G   net 1.7 v 15.2 ^ Mbit/s
```

## Requirements

- A GPU machine with **Vulkan** (any vendor — this was built on an AMD RX 5700 XT),
  `ffmpeg`, and [realesrgan-ncnn-vulkan](https://github.com/xinntao/Real-ESRGAN)
- A media server reachable over ssh with key auth, as an ssh alias (default `ubuntu`)
- An ncnn-format 2× model (see [tools/](tools/) to convert one)

## Install

```bash
./install.sh                                      # only `upscale` lands on $PATH
loginctl enable-linger "$USER"                    # start without logging in
systemctl --user enable --now upscale.service     # survives reboots
```

## Configuration

Everything is an environment variable:

| var | default | meaning |
|---|---|---|
| `REMOTE` | `ubuntu` | ssh alias of the media server |
| `LIB` | `/mnt/media/tv/Bleach` | library root on the server |
| `ARCHIVE` | `$LIB/.upscale-originals` | where originals go once replaced |
| `MODEL` / `MODEL_DIR` | `animejanai-suc` | ncnn model name and directory |
| `WORKERS` | `8` | concurrent upscaler processes (measured optimum) |
| `CHUNK` | `2000` | frames per resumable chunk |
| `CRF` / `PRESET` | `18` / `veryfast` | x264 encode settings |
| `WORK` | `/mnt/scratch/upscale` | scratch dir (needs ~12 GB free) |
| `LIMIT` | `0` | stop after N episodes (`0` = unlimited) |

## How it works

**Phases overlap across episodes.** While episode N is on the GPU, N+1 is downloading
and N−1 is uploading. On a slow link this matters enormously — overlapped, transfer is
free; serial, it adds minutes per episode. Only one upload runs at a time, so two
transfers never split the link between them.

**Chunked and verified.** Each chunk is staged by hardlink (no copying), upscaled by
`WORKERS` processes writing into one directory, encoded, and frame-counted. Frame counts
are asserted at every stage — extraction, upscale, encode, and final concat — so a
dropped frame aborts the run instead of silently shortening the episode.

**Delivery is atomic.** Uploads land as a hidden `.part` file and are renamed only after
byte-size verification, so a media scanner never sees a half-written episode. The
original is archived only after the replacement is confirmed, with `mv -n` so a rerun
can never clobber it. Network calls retry with backoff, so a server reboot pauses the
pipeline rather than failing it.

## What actually made it fast

Measured on an RX 5700 XT upscaling 640×480 anime 2× to 1280×960. Almost everything
that sounded promising did nothing:

| change | effect |
|---|---|
| tile size tuning | **0%** — flat at every setting |
| lighter model, 1 process | **0%** — model compute wasn't the bottleneck |
| `waifu2x` upconv_7 | +10%, worse quality |
| `waifu2x` cunet | −50% |
| perceptual frame dedup | 45–53% fewer frames, but visibly judders |
| **concurrent workers** | **+58%**, then +17% again once the model got cheaper |
| **a smaller model** | **+118%**, but only once the GPU was saturated |
| **overlapping encode + delete** | GPU idle 62% → 33% |

The order matters more than the list. A single worker leaves the GPU at ~65% busy with
per-frame overhead dominating, so a model with 12× fewer parameters ran at *identical*
speed. Add workers and the GPU saturates; only *then* does a cheaper model pay off.
Tested separately, both look worthless.

Then profiling showed the reverse problem. With the cheap model the card was idle 62% of
the time — 26% of wall clock in phases where it did nothing at all (x264 encoding each
chunk, and deleting 2000 upscaled PNGs), plus starvation inside the upscale phase itself.
Handing finished frames to a background encoder by `mv` and deleting asynchronously took
GPU idle from 62% to 33%.

Worker counts, 1200 frames per run:

| model | 4 workers | 8 | 12 |
|---|---|---|---|
| `2x_AnimeJaNai_V2_SuperUltraCompact` | 33.1 fps | **38.7** | 36.4 |
| `realesr-animevideov3` | 16.0 fps | 16.2 | 16.2 |

The heavy model is flat — genuinely GPU-bound, so extra workers just queue behind the
card. The light one was starved, gains 17% at 8 workers, and loses ground at 12 as they
contend. Net: **9.97 → 38.7 fps, about 3.9×**, or ~15 min for a 23-minute episode.

Frame dedup deserves a note. Anime is animated on 2s/3s, so ~50% of frames are visually
duplicate and reusing their upscales is nearly free. It was rejected here because it
judders on slow pans: the frames aren't bit-identical (compression noise differs), so the
match has to be perceptual, and a perceptual match that's wrong freezes real motion.
`tools/dedup/` keeps the implementation for anyone whose source tolerates it.

## Licence

MIT. The upscaling models are third-party — see [tools/](tools/) for provenance.
