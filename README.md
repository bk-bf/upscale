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
upscale collect      drive a remote GPU that cannot reach back into this network
upscale --status     live progress, phase, upload, host utilisation
upscale --pause      hold after the current chunk
upscale --resume     release a pause
upscale --stop       finish the current episode, then exit
upscale --list       show what is outstanding
upscale --profile N  run under a named profile
upscale --profiles   list available profiles
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
| `REMOTE` | `ubuntu` | ssh alias of the media server; `local` if the library is on this host |
| `LIB` | `/mnt/media/tv/Bleach` | library root on the server |
| `ARCHIVE` | `$LIB/.upscale-originals` | where originals go once replaced |
| `MODEL` / `MODEL_DIR` | `animejanai-suc` | ncnn model name and directory |
| `WORKERS` | `8` | concurrent upscaler processes (measured optimum) |
| `CHUNK` | `2000` | frames per resumable chunk |
| `CRF` / `PRESET` | `18` / `veryfast` | x264 encode settings |
| `WORK` | `/mnt/scratch/upscale` | scratch dir (needs ~12 GB free) |
| `LIMIT` | `0` | stop after N episodes (`0` = unlimited) |
| `EPISODES` | `any` | which episodes this machine owns (see below) |

## Splitting the season across machines

`EPISODES` decides which episodes a queue will take, from the `SxxEyy` number in the
filename. Two machines pointed at the same library will never collide as long as their
sets do not overlap:

```
EPISODES=even    even | odd            one half each
EPISODES=9-12    3,7,9-12              numbers, ranges, and lists of both
EPISODES=20-     -9                    open-ended in either direction
EPISODES=odd,1-10                      terms compose: odd AND within 1–10
```

A **profile** is that plus any other settings, saved under a name in
`$STATE/profiles/<name>.conf`, so switching a machine between halves is one flag
instead of remembering which variables to export:

```bash
upscale --profiles                 # list them
upscale --profile even run         # this box takes the even half
upscale --profile catchup --status # ...and status describes that half, not the season
```

```
# ~/.upscale-queue/profiles/even.conf
EPISODES=even
WORKERS=8
```

`all`, `backwards`, `even` and `odd` are created on first run. `--profile` may precede
any command, including `--status` and `--list`, so every view agrees about which episodes
are "yours". A service can select one with `Environment=PROFILE=all`.

**Prefer converging queues to an even/odd split.** `ORDER=forward|reverse` decides which
end a queue starts from, so two machines can both take `EPISODES=any` and meet in the
middle:

```bash
upscale --profile all       run      # machine A: lowest outstanding first
upscale --profile backwards run      # machine B: highest first
```

Even/odd looks like the obvious split and is worse for anyone watching as the episodes
arrive: each machine owns half the sequence, so the viewer advances only as fast as the
*slower* machine, and if one stalls its half becomes a permanent hole. Converging queues
degrade gracefully — the forward machine always produces the next episode to watch, and
a stall on the other side only slows the meeting point.

They will eventually collide on one episode where they meet; both would upscale it, and
the second to finish overwrites the first with an identical file. Watch the outstanding
count and stop one side as it approaches zero.

## Driving a GPU that cannot reach back

A rented GPU box usually has no route into a home network — CGNAT, no inbound ports,
not on the tailnet — so it can neither fetch its own work nor deliver results. Instead
of granting it access, the **server** runs the same tool in `collect` mode and initiates
every step: push source, poll, pull result, verify, publish.

```bash
GPUHOST='-p 48726 root@1.2.3.4' upscale --profile odd collect
```

| var | default | meaning |
|---|---|---|
| `GPUHOST` | — | ssh destination of the worker (required) |
| `RWORK` | `/root/upscale-work` | working directory on that box |
| `RSCRIPT` | `/root/bench/upscale-ep-rental.sh` | per-episode script to invoke there |
| `POLL` | `60` | seconds between progress polls |

`RSCRIPT` is [`libexec/upscale-worker-remote`](libexec/upscale-worker-remote) — the same
model and encode settings as the local worker, so both halves of a season come out
identical, but tuned for a rented box: 4 workers rather than 8, because the instance was
capped at 9.6 CPUs by its cgroup quota while `nproc` reported 20 (measured there:
4 → 43.7 fps, 5 → 42.6, 6 → 21.9, 8 → 14.0 — past 5 workers CFS throttling collapses
throughput). Copy it to the box once; `collect` invokes it per episode.

`upscale --status` shows the collector's view — which episode is on the box, its chunk
and frame progress, its GPU, and the push/pull transfer. The same command works **on the
box itself**: install `upscale` there and record `RWORK` in `~/.upscale-queue/config`.
Both views come from one probe function, shipped to the box over ssh when the server is
the one asking, so they cannot drift apart.

It uses the same `discover | select_todo` selection as `run`, so a collector and a local
queue can share one library. The remote job is started detached, so a dropped ssh session
cannot kill a 15-minute episode, and the result is frame-counted and checked for an audio
stream before it is published.

## Watchdog

`upscale-watch` answers one question: **has a number changed since last time?**

```bash
upscale-watch                 # sample both pipelines, print the health file
systemctl --user enable --now upscale-watch.timer   # every 5 min, with --fix
cat ~/.upscale-queue/health   # last verdict
```

```
checked   2026-08-10 19:40:18
collector OK           upscaling   up=0 out=0 src=178318050 part=0
local     OK           working     up=1998 seg=15 out=0
delivered 11 episodes
```

Liveness is worthless here. Both real stalls this pipeline has had looked
perfectly healthy: the service was `active`, ssh answered, and a log line was
written every 70 seconds — while in one case the collector tailed a log from an
episode that had finished twelve minutes earlier, and in the other a process sat
in `do_wait` forever. Nothing was down; nothing was moving either. So the watchdog
compares frames upscaled, bytes in flight and episodes delivered against the
previous sample, and calls a stall when the whole tuple stops changing.

One shape is caught immediately rather than after three samples: a source sitting
on the box with no worker running is a stall by definition, not a slow patch.

**Alerts.** If `NTFY_PUBLISH_URL` is set (read from an env file, default
`/run/bws/mediaserver.env`), the watchdog publishes to that ntfy topic — from the
server, so it does not depend on any session being open. Prove the path with
`upscale-watch --test-notify`.

It is deliberately narrow about what earns a banner: only states a **human** can do
something about. A stalled collector restarts itself and says nothing; a box that has
vanished needs a new instance and a new `GPUHOST`, and no amount of retrying produces
one. One notification per incident, not one per check — an hour of five-minute checks
against a box that died at 02:00 is twelve identical banners and a lesson to ignore
them. Recovery sends exactly one quiet line.

`--fix` restarts a stalled **collector** only, because restarting it is provably
safe — it adopts a job already running on the box instead of starting a second
one. It never touches the remote worker or the local queue's episode: killing work
in progress to clear a stall is a worse outcome than the stall.

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

## Notes

[BUGS.md](BUGS.md) documents every failure hit while building this and how each was
fixed — decoder corruption that passes frame-count checks, `set -e` exit-status traps,
concurrency mistakes, and the measurement errors that made several of them hard to see.

## Licence

MIT. The upscaling models are third-party — see [tools/](tools/) for provenance.
