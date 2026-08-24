# upscale-ui

One page to drive [`upscale`](https://github.com/bk-bf/upscale): pick a library
and a GPU host, start a range, watch it, pause it.

Runs on the machine that **holds the libraries** (ubuntuserver). It never
upscales anything itself — it reads the libraries locally and drives GPU hosts
over ssh.

```
https://upscale.callmedaddy.dedyn.io       (through Caddy, owner devices only)
http://100.122.63.6:8790                   (direct on the tailnet)
```

## What it shows

Per host: state, current episode, chunk and frame progress, fps, elapsed, ETA,
which scratch disk it is using, and whether a GPU process is actually turning.
That last one matters — the job file survives a crash, so "running" on its own
can lie.

It also says whether a **queue** is driving the host, separately from the
episode. Without one, the host finishes what it has and stops; that is a normal
state and worth seeing rather than inferring.

## Two rules it is built on

**Truth is derived, never stored.** The pipeline keeps no work list — it asks
"which source has no matching `.mkv`?" every cycle — and this server keeps no
job database for the same reason. Outstanding work comes from `upscale --list`;
host state comes from that host. Restarting this service cannot make it
disagree with reality.

**Nothing is invented when a host is unreachable.** An ssh failure is reported
as an error on that host, never as "idle". An idle-looking panel for a box that
is actually grinding is worse than an error.

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
