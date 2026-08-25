# upscale-ui

One page to drive [`upscale`](https://github.com/bk-bf/upscale): pick a source
directory and a GPU device, start it, watch it, stop it.

Runs on the machine that **holds the libraries** (ubuntuserver). It never
upscales anything itself — it reads the libraries locally and drives GPU hosts
over ssh.

```
https://upscale.callmedaddy.dedyn.io       (through Caddy, owner devices only)
http://100.122.63.6:8790                   (direct on the tailnet)
```

## What it shows

Per device: state, current episode, chunk and frame progress, fps, elapsed, ETA
and whether a GPU process is actually turning. That last one matters — the job
file survives a crash, so "running" on its own can lie.

It also says whether a run is driving the device, separately from the episode.
Without one, the device finishes what it has and stops.

## How it is put together

Three files, split by what each one can be tested without.

| File | What it is | Needs |
|---|---|---|
| `api.py` | every endpoint, as a function in `ROUTES` | nothing — no socket, no browser |
| `server.py` | sockets, static files, the snapshot thread | a port |
| `web/src/routes/+page.svelte` | the page | a build |

`api.dispatch(method, path, body, query) -> (status, dict)` is the only way in.
The HTTP server calls it, `uictl` calls it, and the tests call it, so a feature
that works from one works from all three.

Each route declares the body keys it reads:

```python
@route("POST", "/api/devices/remove", accepts=("name",))
```

`dispatch` rejects a body carrying anything else with a 400 naming the key. A
page that posts `{id}` to a handler reading `name` gets an error instead of a
success it did not earn.

## Driving it without a browser

```bash
./uictl routes                                     # every path and the keys it reads
./uictl GET  /api/devices
./uictl POST /api/devices/remove name=rental
./uictl GET  /api/browse q=/mnt/media/tv/
./uictl POST /api/start devices=desktop \
    source=/mnt/media/tv/show target=/mnt/media/tv-4k/show \
    delete=true dry_run=true                       # prints the command, runs nothing
```

`uictl` imports `api.py` directly — it is the page's own code path with the
browser taken out. `dry_run=true` on `/api/start` returns the exact `upscale`
command line without spawning it.

## Tests

```bash
python3 test_api.py
```

Stdlib `unittest`, no network and no GPU: `api.ssh_to` and `api.spawn` are
replaced with fakes, and `api.configure()` points the device book and the run
directory at a temp directory.

`FrontendContract` reads `+page.svelte`, extracts every `api()`/`act()` call and
the keys of the object it posts, and fails if a path has no route or a key no
handler reads — reporting the svelte line number. That check is why a rename on
one side cannot silently pass on the other.

## Two rules it is built on

**Truth is derived, never stored.** The pipeline keeps no work list — what is
left to do is what is in the source directory — and this server keeps no job
database for the same reason. Restarting it cannot make it disagree with
reality.

**Nothing is invented when a host is unreachable.** An ssh failure is reported
as an error on that device, never as "idle". An idle-looking panel for a box
that is actually grinding is worse than an error.

## Install

```bash
./install.sh --with-units
loginctl enable-linger "$USER"
systemctl --user enable --now upscale-ui
```

`config.json` (copied from `config.example.json` on first install) holds the
port, the media root, and one entry per GPU host with its ssh target and its
scratch directories.

### It binds the tailscale IP, not 127.0.0.1

Caddy runs in a container, so its loopback is not this host's — a service on
`127.0.0.1` is unreachable to it. Native services here bind the tailnet address
for that reason (dashboard `:8443`, copyparty `:3923`). The address is CGNAT and
not routable from the internet.

Reaching it **through Caddy** additionally needs the docker bridge allowed to
that port, which is what the existing services have:

```bash
sudo ufw allow from 172.18.0.0/16 to 100.122.63.6 port 8790 proto tcp
```

Without it the direct tailnet URL works and the HTTPS one returns 502.

## Relationship to the queue daemon

`upscale` deliberately has **no** always-on queue daemon: work is started by
command and exits when done. This service does not change that. It is a web
server, so it is a daemon by necessity, but it starts work only when someone
presses a button — it never picks up work on its own, and it holds no schedule.
