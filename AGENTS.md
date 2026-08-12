# AGENTS.md — working on `upscale`

Read this before changing anything here. `BUGS.md` is the other required read: it
documents every failure this pipeline has already hit, and most "bugs" a fresh agent
finds are already in it, already fixed.

## What this is

A resumable pipeline that AI-upscales an entire anime series 2×, on GPUs that are
somewhere else, and delivers finished episodes into a media library. It has run to
completion once: 96 episodes, on a rented Vast.ai box driven from the media server.

Two roles, one script:

- **`run`** — the machine WITH the GPU pulls its own work, upscales, delivers.
- **`collect`** — the SERVER drives a GPU box that has no route back into the network
  (CGNAT, no inbound). Every step is server-initiated: push source, poll, pull result,
  verify, publish.

Per-episode work is `libexec/upscale-worker` (local) and `libexec/upscale-worker-remote`
(on the rented box). Around them: `upscale-watch` (progress watchdog),
`upscale-artwork` (Jellyfin thumbnails), `upscale-guard` (runs ON the box, from cron).

## The invariants — do not break these

**There is no saved work list and no progress pointer.** Every cycle asks one question:
*which source has no matching output?* That is what makes a power cut a non-event. Any
change that introduces persistent queue state is almost certainly wrong.

**Discovery matches on episode identity (`SxxEyy`), never on the whole filename.** The
delivered file does not keep the source's name — Sonarr re-detects quality and renames
`... SDTV.mkv` to `... HDTV-720p.mkv`. Exact-name matching made every finished episode
look outstanding again and re-queued 40 of them.

**Counting is not identity.** Frame counts, byte sizes and durations answer *is this the
right shape*, never *is this the right thing*. Four episodes in one season decode to
exactly 33435 frames. A wrong episode was published-but-for-a-two-frame coincidence, so
delivery now also samples 5 frames and requires ≥20 dB PSNR against the source. Do not
loosen that check — it is the only thing that caught it.

**Never touch a job you did not verify the identity of.** Before pushing, read
`$RWORK/episode` and find out what the box is actually working on. A restarted collector
once pushed a different episode's source over a running job's input and then "adopted"
that job, producing one episode's video with another's audio.

**Verification failures are content failures; connection failures are not.** `MAX_FAILS`
blacklists an episode. A transient ssh drop currently counts toward it — that is a known
flaw worth fixing, not a design.

## Shell traps that have actually cost time here

These are not hypothetical. Each one shipped, silently, at least once.

- **`var=$(cmd)` adopts cmd's exit status.** Under `set -euo pipefail` that kills the
  script with no message. Six separate outages. Always `var=$(cmd) || var=default`.
- **`head -1` on a pipeline whose producer is still writing** sends SIGPIPE upstream;
  with `pipefail` the whole pipeline then "fails". Twice. Use
  `mapfile -t arr < <(...)` and take `arr[0]`.
- **A function returns its last command's status.** A trailing `if [ ... ]` that is false
  makes the function return 1 on a perfectly good run. End such functions with `return 0`.
- **`pgrep -f <pattern>` over ssh matches the ssh command line carrying the pattern.**
  Three times. Bracket the first character: `[u]pscale-worker-remote`.
- **`${VAR:-default}` treats empty as unset.** When empty is a meaningful value ("this
  machine is off tonight"), use `${VAR-default}`.
- **Overwriting a running script in place corrupts it** — bash reads scripts
  incrementally. Write to a temp name and `mv -f`. And note the running process keeps
  the OLD inode, so a deployed fix is not active until the service restarts.

## Media traps

- `ffprobe -count_frames` decodes; `nb_frames` reads the container's claim. They differ
  on packed-bitstream MPEG-4 ASP. Say which you used.
- ffmpeg 8.1.2 silently mis-decodes MPEG-4 ASP: byte-identical input, visibly corrupt
  output, and a **correct frame count**. The pinned static ffmpeg 7.0.2 plus the
  two-decoder cross-check in the worker exists solely for this.
- The `psnr` filter reports at info level. `-v error` hides the very line you are
  parsing, and everything silently scores 0.

## Notifications — there is exactly one way to send

**Do not write `curl ... $NTFY_PUBLISH_URL` anywhere.** `~/.local/bin/notify` is the only
thing on the machine that publishes, and nothing in this repo knows the topic or the
credentials any more.

```bash
notify --list                                  # every sender, its owner and state
notify --register <id> <owner-path> <purpose>  # before a new thing can send
notify --retire <id> <reason>                  # a finished job cannot alarm about itself
notify <id> <title> <body> [priority] [tags]
```

An unregistered id is refused (non-zero exit). A retired sender is refused (zero exit —
refusing is the correct outcome, not an error). Cooldown is enforced centrally, so two
senders cannot each dedupe "correctly" and still spam together. Every attempt is logged
to `~/.config/ntfy/sent.log` with its sender id, so the audit trail names the culprit.

This exists because a completed run produced a "finished" banner and then two "it's
broken" alarms about the same event, an hour apart, from a second notifier that had no
idea the first had declared success.

## Before adding a monitor, timer or notifier

Enumerate what already runs — `systemctl --user list-timers --all`, `crontab -l` on
every host, `~/.local/bin`. Two notifiers on one ntfy topic produced three phone alerts
for a single event. Extend the existing thing rather than adding a parallel one, and
teach any monitor the difference between "the work finished" and "the thing died" —
otherwise it pages about success.

## Where the context lives outside this repo

- Universal memory `upscale-pipeline-tool` — what the tool is, where it is installed,
  the two modes, and the invariants, in a form that survives outside this checkout.
- Skill `upscale` (`~/.claude/skills/upscale/SKILL.md`) — how to OPERATE it: status,
  profiles, what to check when it looks stuck, and what not to do.
- `check-what-already-runs` (universal memory) — the monitoring lesson this project
  produced: enumerate existing timers before adding one.

## Performance data

`PERFORMANCE-DATA.md` holds the raw measurements from the completed run — hardware of
each machine used, per-component utilisation, throughput distributions, phase costs,
transfer rates, and the measured results of every configuration that was tried and
rejected. It is deliberately data-only: no analysis, no recommendations, and every row
carries how it was obtained. Add to it rather than restating numbers elsewhere.

## Testing

There is no test suite; the pipeline is verified against real files. When changing
selection or verification logic, prove it with real data before deploying:

```bash
REMOTE=local ORDER=forward EPISODES=any ./upscale --list    # what would be picked
./upscale --profiles                                        # episode sets
```

For the identity check, the honest test is a known-wrong pair: it must score low
(~13 dB) while a correct pair scores high (29–44 dB).

## Deploying

`./install.sh` puts everything under `~/.local`. On a machine with a queue running, do
not install over a live script — copy to a temp name and `mv -f`, then restart the
service so the new inode is actually used.
