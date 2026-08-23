# upscale — architecture simplification

**The point of this file:** the design review found that most of the pipeline's
complexity is downstream of one decision — sources and finished masters share a
directory and an extension. Nearly every subsystem below exists to undo that.
This is the list for unwinding it.

**Prime directive: delete mechanism, do not add it.** Every item here should
make the code smaller. If a change makes it bigger, the design is still wrong.

Live context: two queues have been running against the current design all week;
Gintama 1–19 is upscaling right now. Nothing here may break a running queue.

---

## The root cause

`SRC_EXT` decides what a source *is* by file extension (`avi | mkv | both`), and
sources live in the same tree as their outputs. Verified consequences in
`upscale`:

- L122 *"Setting this to mkv makes sources and outputs share an extension"*
- L210 *"…tells a delivered output apart from a source when the two share an extension"*
- L222 *"When SRC_EXT includes mkv, a source and a delivered output look identical"*
- `archived_for()` exists solely to answer "is this a source or a finished master?"
- `.upscaleignore` exists to re-exclude things that only look like sources

**None of this is needed if the source directory and the target directory are
different directories.** Then a source is "a file in the source dir", full stop —
any extension, no probing, no archive lookup, no ignore-by-glob.

- [ ] **Move to explicit source/target on the command line.** Proposed shape:
      ```
      upscale --source /path/to/src --target /path/to/out --size 2 \
              --device <ip1> <ip2> [--delete | --archive /path/to/archive]
      ```
  - `--size 2` replaces the implicit 2x.
  - `--delete` or `--archive <dir>` replaces the current in-place archive dance.
  - `.upscaleignore` still works, placed in the source dir — but excluding an
    episode becomes "move it out of the source dir", which needs no feature.
- [ ] **Delete `SRC_EXT` and everything that keys off it**, including
      `archived_for()` and the source-vs-output disambiguation.

## Transfer path — one implementation, not two

- [ ] **Remove the duplicate transfer path.** There are two separate
      implementations of the same movement:
  - box mode: the *server* pushes (`upscale` L1314) and pulls (L1377) over ssh
  - local mode: the *worker* rsyncs for itself (`libexec/upscale-worker`)
      Target: the ubuntuserver is the only mover — it pushes the source in and
      pulls the master out. Devices never reach back.

## Identity — use the name

- [ ] **Stop identifying episodes by picture content.** The PSNR identity check
      (`upscale-worker` L583-611) exists because **the box always receives the
      file as `src.avi`** (`upscale` L841: *"On the box the source is always
      called src.avi"*). The transfer destroys the filename, so the returned
      master cannot be matched back by name and has to be re-identified from
      pixels.
  - The answer to "why was the original filename never an option" is that the
    pipeline throws it away in transit. Keep the name and the whole check —
    plus its 20 dB threshold, its false rejections and its retry logic — is
    deletable.
  - **Trap:** the threshold has already produced a false rejection on a correct
    master (S02E42, 19.64 dB) because a malformed subtitle line inflated the
    container duration and the sampler ran past the end of the video. Whatever
    replaces this must not sample by container duration.

## Remove outright

- [ ] **`upscale-artwork`** — a one-off thumbnail fix that was never meant to be
      permanent code. Remove `libexec/upscale-artwork`,
      `upscale-artwork.service`, `upscale-artwork.timer`, the `install.sh`
      lines, and disable the timer on any host running it.
- [ ] **The TUI status renderer (`cmd_status`)** — deprecated by the web UI,
      which reads the same device files. Two renderers of one state is exactly
      the drift this project keeps paying for.

## Sequencing (so nothing breaks mid-run)

- [ ] Do the removals first (`upscale-artwork`, `cmd_status`) — they touch
      nothing a running queue depends on.
- [ ] Do the source/target change as one commit with the transfer rework; they
      are the same change seen from two ends.
- [ ] Land it only when no queue is mid-episode, and keep the old device files
      readable — the UI reads them and must not break in the same step.
