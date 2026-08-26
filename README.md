# upscale

Send every file in a directory to a GPU, scale it, write the result somewhere
else.

```
upscale --source /srv/incoming --target /srv/done --archive /srv/originals \
        --device desktop --device "-p 31174 root@87.192.102.26" \
        --size 2 --workers 8
```

Two files. `upscale` runs on the machine holding the media; `upscale-worker`
runs on each GPU. `./install.sh` puts both in `~/.local`.

On a GPU box that has no checkout, that copy is the only thing there and the
default is what you want. On the machine you edit from, `./install.sh --link`
symlinks the two instead, so an edit is live without reinstalling.

## The idea

A source is a file in the source directory. That is the whole model.

- **What is a source?** Whatever is in `--source`. Any extension, any name.
- **Is it finished?** It is in `--target`, or it is not.
- **Which file is this?** It kept its name the entire way.
- **How do I skip one?** Move it out of `--source`.

Each file is taken, scaled, written to `--target` under the same name, then
moved to `--archive` or deleted — so it stops being a source. One of those two
is required, because a finished file left in the source directory would be
processed again on the next run.

Devices run in parallel, one file each, claiming work with an atomic `mkdir` so
two can never take the same file. What is left is re-read from the source
directory each time, so files added mid-run are picked up.

## Devices

A device is an ssh destination — `desktop`, or `-p 31174 root@1.2.3.4`. Nothing
is registered or configured; pass `--device` once per GPU.

Devices never reach back. The server pushes the source in and pulls the result
out, so a rented box holds no key to the machine with the media on it.

`--scratch` needs roughly 40 GB per file in flight (~32 GB of PNGs plus shard
output). Put it on NVMe — a DRAM-less SATA SSD collapses under it, measured
20 fps → 13.5.

`--workers` is upscaler processes per device. Throughput is bound by PNG
encode/decode, not by the card: on identical RTX 5060 Tis, a 5.76-core quota
gave 11.8 fps and a 23-core quota gave 23.

## What was removed

The previous version kept sources and results in one tree, and most of its 2600
lines existed to undo that.

| Mechanism | Existed because | Replaced by |
|---|---|---|
| `SRC_EXT` (`avi\|mkv\|both`) | it had to guess which files were sources | the source directory |
| `archived_for()` | a delivered master and its own source shared a path | a separate target directory |
| PSNR identity check, 20 dB floor | the transfer renamed every file to `src.avi`, so a result could not be matched back by name | the file keeps its name |
| ranges, parities, absolute episode numbers | selecting a subset of a shared library | put the subset in the source directory |
| `.upscaleignore` | re-excluding what the above wrongly included | still supported, no longer load-bearing |
| two transfer implementations | the worker fetched for itself in one mode, the server pushed in another | the server moves files, always |
| `upscale --status` TUI | there was no other view | the web UI, which reads the same state |
| `upscale-watch`, `upscale-guard` | babysitting a remote job the server could not observe | the server holds the session for the whole upscale |

Each had grown its own failures: re-upscaling its own output, archiving a master
as an "original", rejecting a correct result at 19.64 dB, two collectors
swapping `src.avi` under each other's running decoder, one device reading a
range differently from every other machine.

None of it was wrong code. It was correct handling of a problem that did not
need to exist.

## What was kept

The upscale itself. An episode is tens of thousands of frames and a machine will
be interrupted, so `upscale-worker` extracts frames, shards them across N
upscaler processes, encodes in chunks, and on restart verifies and skips the
chunks it already has.

The one check on a returned file is its frame count against the source. A
truncated transfer is what actually goes wrong; *which* file it is was never in
question.

## Requirements

On the machine with the media: `bash`, `rsync`, `ssh`, `ffprobe`.

On each device: `bash`, `ffmpeg`/`ffprobe`, `mkvtoolnix` (container timestamp
normalisation — without it results play in mpv and fail in Jellyfin with a white
screen), and a `realesrgan-ncnn-vulkan` binary with its models.
