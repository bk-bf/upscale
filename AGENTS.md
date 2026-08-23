# Working on upscale

Two files do the work:

- `upscale` — runs where the media is. Picks files out of `--source`, sends each
  to a device, writes the result to `--target`, archives or deletes the source.
- `libexec/upscale-worker` — runs on a GPU. Given a source path and an output
  path, it extracts frames, shards them across N upscaler processes, encodes in
  chunks, and resumes by verifying the chunks it already has.

That split is the whole architecture. `upscale` moves files and decides nothing
else; the worker scales one file and knows nothing about where it came from.

## The rule that keeps it small

**A source is a file in the source directory.** Not a file with a known
extension, not a file without a matching output, not a file whose identity is
absent from an archive.

Before this, sources and results shared a tree, and roughly 2000 of 2600 lines
existed to undo that: `SRC_EXT` guessing which files were sources, an archive
lookup distinguishing a delivered master from its own source, absolute episode
numbers and parities selecting a subset, `.upscaleignore` re-excluding what
those wrongly included, and a picture-content identity check because the
transfer renamed everything to `src.avi`.

Before adding a mechanism, check whether it is compensating for two things
sharing a location or a name. If it is, separate them instead.

## Things that are true and worth keeping true

- **The file keeps its name end to end.** This is load-bearing. It is why a
  returned result needs no identification, and why a device's report of what it
  is working on can be matched to a row directly.
- **The server moves files; devices never reach back.** A rented box holds no
  key to the machine with the media on it.
- **One check on a result: frame count against the source.** A truncated
  transfer is what actually goes wrong. Anything cleverer has already cost more
  than it caught — a 20 dB PSNR floor false-rejected a correct master at
  19.64 dB because a malformed subtitle line inflated the container duration.
- **Resumability lives in the worker, not in a work list.** There is no saved
  queue and no progress pointer: what is left is read from the source directory
  every cycle.
- **`upscale` writes `~/.upscale/run/state.json`; the UI reads it.** One writer.
  If the UI ever needs to know something new, `upscale` should write it, not
  work it out again.

## Testing

Run it. A source directory with one file, a real device, `--archive` to a
scratch directory. The failure modes that matter — a truncated push, a device
that dies mid-chunk, a name with spaces in it — do not show up in a dry run.
