#!/usr/bin/env python3
"""upscale-ui — one page to drive the upscale pipeline.

Runs on the machine that HOLDS THE LIBRARIES (ubuntuserver). It never upscales
anything: it reads the libraries locally, and drives GPU hosts over ssh.

Two rules shape everything here:

  * Truth is derived, never stored. The pipeline itself keeps no work list -
    it asks "which source has no matching .mkv?" every cycle - and this server
    keeps no job database for the same reason. What is outstanding comes from
    `upscale --list`; what a host is doing comes from that host. A restart of
    this service therefore cannot disagree with reality.

  * Nothing is fabricated when a host is unreachable. An ssh failure is
    reported as an error on that host, not as "idle" - an idle-looking panel
    for a box that is actually grinding is worse than an error.

Stdlib only, so there is nothing to install and nothing to keep up to date.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from fnmatch import fnmatch
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
DIST = HERE / "web" / "dist"
CONFIG_PATH = Path(os.environ.get("UPSCALE_UI_CONFIG", HERE / "config.json"))

VIDEO_EXT = {".mkv", ".avi", ".mp4", ".m4v", ".ts"}
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]


def load_config() -> dict:
    """Read config.json on every use.

    Deliberately not cached: editing the file and reloading the page should be
    enough to add a host, without restarting a service that may be driving a
    24-hour run.
    """
    try:
        return json.loads(CONFIG_PATH.read_text())
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        return {"_error": f"{CONFIG_PATH.name}: {e}"}


def run(cmd: list[str], timeout: int = 30, env: dict | None = None) -> tuple[int, str, str]:
    e = dict(os.environ)
    if env:
        e.update(env)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=e)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"
    except FileNotFoundError as exc:
        return 127, "", str(exc)


def ssh_to(host_ssh: str, remote_cmd: str, timeout: int = 30) -> tuple[int, str, str]:
    """`ssh` field may carry options ('-p 48726 root@1.2.3.4'), so split it."""
    return run(SSH + shlex.split(host_ssh) + [remote_cmd], timeout=timeout)


# --------------------------------------------------------------- libraries ---
def libraries(cfg: dict) -> list[dict]:
    root = Path(cfg.get("media_root", "/mnt/media/tv"))
    out = []
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        n = sum(1 for p in d.rglob("*") if p.suffix.lower() in VIDEO_EXT and ".upscale-originals" not in p.parts)
        arch = d / ".upscale-originals"
        done = sum(1 for p in arch.rglob("*") if p.suffix.lower() in VIDEO_EXT) if arch.is_dir() else 0
        if n or done:
            out.append({"name": d.name, "path": str(d), "files": n, "archived": done,
                        "src_ext": src_ext_for(cfg, d.name)})
    return out


def src_ext_for(cfg: dict, lib_name: str) -> str:
    m = cfg.get("src_ext", {}) or {}
    return m.get(lib_name) or m.get("default") or "avi"


# ------------------------------------------------------------------- queue ---
# The ONLY thing stored on disk: which episodes the user has held back. That is
# intent, and nothing else can derive it. Everything else on this page - what is
# outstanding, what is done, what is running - is still asked for fresh, so the
# stored file can never disagree with the library.
STATE_FILE = HERE / "queue-state.json"
_state_lock = threading.Lock()


def _state() -> dict:
    """What the user has decided. Three things, none of them derivable:

      imports  libraries pulled into the queue, in the order they were added
      held     episodes to skip for now (pause)
      removed  episodes taken out of the queue entirely (delete)

    Episodes themselves are NOT stored. Sonarr renames delivered files, so a
    stored episode list would rot; the numbers above survive a rename because
    they are episode identities, not filenames.
    """
    try:
        s = json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        s = {}
    s.setdefault("imports", [])
    s.setdefault("held", {})
    s.setdefault("removed", {})
    s.setdefault("assigned", {})     # file path -> host id
    s.setdefault("active", {})       # host id -> scratch, while its queue should run
    return s


def _save_state(s: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1))
    tmp.replace(STATE_FILE)          # atomic: a torn state file would lose holds


def _set_for(key: str, lib: str) -> set[int]:
    return {int(n) for n in (_state().get(key, {}).get(lib) or [])}


def held_for(_bucket: str = "*") -> set[str]:
    """Held FILE PATHS. Keyed by path, not episode number: the queue holds
    files now, and two shows both have an episode 3."""
    return set(_state().get("held", {}).get("*") or [])


def removed_for(_bucket: str = "*") -> set[str]:
    return set(_state().get("removed", {}).get("*") or [])


def mutate_set(key: str, paths: list[str], add: bool) -> set[str]:
    with _state_lock:
        s = _state()
        cur = set(s.setdefault(key, {}).get("*") or [])
        cur = (cur | set(paths)) if add else (cur - set(paths))
        s[key]["*"] = sorted(cur)
        _save_state(s)
        return cur


DEVDIR = Path(os.environ.get("UPSCALE_DEVDIR", Path.home() / ".upscale" / "devices"))
UPSCALE_BIN = os.environ.get("UPSCALE_BIN", str(Path.home() / ".local" / "bin" / "upscale"))


def all_hosts(cfg: dict) -> dict:
    """Every device, read from the ONE file per device that defines it.

    This used to merge config.json with a second list of machines onboarded
    through the UI, and neither knew what the CLI was actually running. A
    machine's parity, direction, library and worker path could be stated in
    three places at once and disagree - which is how a finished episode came to
    be displayed as running on a machine that never owned it.

    `upscale device <name> ...` writes these files and is the only writer. This
    reads them and is not one. Nothing here invents an answer the CLI would
    disagree with, because there is only one answer.
    """
    out: dict = {}
    if not DEVDIR.is_dir():
        return out
    for f in sorted(DEVDIR.glob("*.conf")):
        d: dict = {}
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            try:                       # values are written with printf %q
                v = " ".join(shlex.split(v))
            except ValueError:
                v = v.strip("'\"")
            d[k.strip().lower()] = v
        name = d.get("name") or f.stem
        mode = d.get("mode") or "box"
        out[name] = {"ssh": d.get("ssh", ""), "label": name,
                     "episodes": d.get("range", "any"), "order": d.get("order", "forward"),
                     "mode": mode, "push": mode == "box",
                     "worker": d.get("worker") or "upscale-worker",
                     "rwork": d.get("rwork") or "/root/work",
                     "src": d.get("src", ""), "dst": d.get("dst", ""),
                     "ext": d.get("ext", "mkv")}
    return out


def upscale_cmd(*args: str) -> dict:
    """Run the CLI. The UI does not reimplement start/stop, it calls them.

    Every control path in this file used to be a second implementation of
    something the CLI already did - selecting, claiming, killing - and each one
    drifted from the others. The button now does exactly what the command does,
    because it IS the command.
    """
    rc, out, err = run([UPSCALE_BIN, *args], timeout=120)
    ok = rc == 0
    return {"ok": ok, "note": (out or "").strip()[-400:],
            **({} if ok else {"error": ((err or out) or f"exit {rc}").strip()[-400:]})}


def probe_host(ssh_target: str) -> dict:
    """Look before saving. An entry that cannot be reached is worse than no
    entry: it shows up as a broken panel forever and nobody remembers why."""
    script = (
        "echo HOST=$(hostname); "
        "echo WORKER=$( (command -v upscale-worker || ls ~/.local/libexec/upscale-worker) 2>/dev/null | head -1); "
        "echo UPSCALE=$( (command -v upscale || ls ~/.local/bin/upscale) 2>/dev/null | head -1); "
        "echo GPU=$(lspci 2>/dev/null | grep -iE 'vga|3d controller' | head -1 | cut -d: -f3- | sed 's/^ //'); "
        "echo CPUS=$(nproc); "
        "for d in $HOME/upscale-scratch /mnt/scratch/upscale-ep /mnt/scratch /tmp; do "
        "  [ -d \"$d\" ] && echo SCRATCH=$d:$(df -Pk \"$d\" | awk 'NR==2{print int($4/1048576)}'); done"
    )
    rc, out, err = ssh_to(ssh_target, script, timeout=25)
    if rc != 0:
        return {"ok": False, "error": (err or f"ssh exited {rc}").strip()[:400]}
    info, scratch = {}, {}
    for line in out.splitlines():
        k, _, v = line.partition("=")
        if k == "SCRATCH" and ":" in v:
            d, _, free = v.rpartition(":")
            scratch[Path(d).name if d != "/tmp" else "tmp"] = {"path": d, "free_gb": int(free or 0)}
        elif k:
            info[k.lower()] = v.strip()
    return {"ok": True, **info, "scratch": scratch,
            "ready": bool(info.get("worker")),
            "note": "" if info.get("worker") else
                    "upscale-worker not found on that machine - install it there first "
                    "(./install.sh in the upscale repo), or this host cannot run anything."}


def save_host(host_id: str, entry: dict) -> dict:
    with _state_lock:
        st = _state()
        st.setdefault("hosts", {})[host_id] = entry
        _save_state(st)
        return st["hosts"]


def forget_host(host_id: str) -> dict:
    with _state_lock:
        st = _state()
        st.setdefault("hosts", {}).pop(host_id, None)
        _save_state(st)
        return st["hosts"]


def assignments() -> dict:
    return dict(_state().get("assigned") or {})


def assign(paths: list[str], host: str) -> dict:
    """Queue episodes INTO a machine.

    An assignment is a claim, which is what makes two machines safe on one
    queue: a driver only ever runs what is assigned to it, and claims an
    unassigned episode before starting it, so two drivers cannot take the same
    file.
    """
    with _state_lock:
        st = _state()
        a = st.setdefault("assigned", {})
        for p in paths:
            if host:
                a[p] = host
            else:
                a.pop(p, None)
        _save_state(st)
        return dict(a)


def imports() -> list[str]:
    return list(_state().get("imports") or [])


def add_imports(paths: list[str]) -> list[str]:
    with _state_lock:
        s = _state()
        for p in paths:
            if p not in s["imports"]:
                s["imports"].append(p)
        _save_state(s)
        return list(s["imports"])


def drop_imports(paths: list[str]) -> list[str]:
    """Remove entries and forget any hold or deletion attached to them, so a
    re-import does not silently reinstate a decision made who knows when."""
    with _state_lock:
        s = _state()
        keep = set(paths)
        s["imports"] = [p for p in s["imports"] if p not in keep]
        for k in ("held", "removed"):
            s[k]["*"] = sorted(set(s.get(k, {}).get("*") or []) - keep)
        _save_state(s)
        return list(s["imports"])


def count_videos(d: Path, budget: int = 4000) -> int:
    """Video files under d, walking at most `budget` entries.

    Bounded on purpose: this runs for every row of an autocomplete, and an
    unbounded rglob over something like /mnt/media would make each keystroke
    walk the whole library.
    """
    n = seen = 0
    stack = [d]
    while stack and seen < budget:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    seen += 1
                    if seen >= budget:
                        break
                    if e.is_dir(follow_symlinks=False):
                        if e.name.startswith(".") and e.name != ".upscale-originals":
                            continue
                        stack.append(Path(e.path))
                    elif os.path.splitext(e.name)[1].lower() in VIDEO_EXT:
                        n += 1
        except (PermissionError, FileNotFoundError, NotADirectoryError):
            continue
    return n


def browse_roots(cfg: dict) -> list[str]:
    r = cfg.get("browse_roots")
    if r:
        return [str(Path(p)) for p in r]
    return [str(Path(cfg.get("media_root", "/mnt/media/tv")).parent)]


def under_roots(cfg: dict, p: Path) -> bool:
    return any(str(p) == r or str(p).startswith(r.rstrip("/") + "/") for r in browse_roots(cfg))


def browse(cfg: dict, q: str) -> dict:
    """Directory completion for the import box — ANY directory, not one root.

    Two modes, chosen by whether the query looks like a path:
      "/mnt/media/an"  -> complete the last segment inside /mnt/media
      "gin"            -> match directory names one level under each root
    A directory with no video files is still listed so it can be walked into;
    it just cannot be imported.
    """
    q = (q or "").strip()
    already = set(imports())
    roots = browse_roots(cfg)
    cands: list[Path] = []
    base_shown = ""

    if q.startswith("/"):
        if q.endswith("/"):
            base, frag = Path(q), ""
        else:
            base, frag = Path(q).parent, Path(q).name.lower()
        base_shown = str(base)
        try:
            cands = sorted((d for d in base.iterdir()
                            if d.is_dir() and not d.name.startswith(".")
                            and d.name.lower().startswith(frag)),
                           key=lambda d: d.name.lower())
        except (PermissionError, FileNotFoundError, NotADirectoryError):
            cands = []
    else:
        low = q.lower()
        for r in roots:
            try:
                cands += [d for d in Path(r).iterdir()
                          if d.is_dir() and not d.name.startswith(".")
                          and (not low or low in d.name.lower())]
            except (PermissionError, FileNotFoundError):
                continue
        cands.sort(key=lambda d: d.name.lower())

    out = []
    for d in cands[:60]:
        n = count_videos(d)
        out.append({"kind": "dir", "name": d.name, "path": str(d), "files": n,
                    "importable": False, "imported": False})

    # Video files in the directory being browsed. These are what actually get
    # imported - a queue entry is one episode, not a folder.
    vids = []
    if q.startswith("/"):
        d = Path(q) if q.endswith("/") else Path(q).parent
        frag = "" if q.endswith("/") else Path(q).name.lower()
        try:
            for f in sorted(d.iterdir(), key=lambda x: x.name.lower()):
                if (f.is_file() and f.suffix.lower() in VIDEO_EXT
                        and not f.name.startswith(".")
                        and f.name.lower().startswith(frag)):
                    vids.append({"kind": "file", "name": f.name, "path": str(f),
                                 "size": f.stat().st_size,
                                 "importable": under_roots(cfg, f),
                                 "imported": str(f) in already})
        except (PermissionError, FileNotFoundError, NotADirectoryError):
            pass
    return {"results": out + vids[:400], "base": base_shown, "roots": roots}


# A queue entry is a FILE, so the library root has to be inferred from it: the
# worker needs LIB to work out where the output goes and where the original is
# archived. "Season 1"/"Specials" are containers, not the show.
SEASONISH = __import__("re").compile(r"(?i)^(season\b|specials$|s\d+$)")


def library_root(f: Path) -> Path:
    d = f.parent
    return d.parent if SEASONISH.match(d.name) else d


EP_RE = __import__("re").compile(r"[Ss](\d+)[Ee](\d+)")


def ep_num(name: str) -> int | None:
    m = EP_RE.search(name)
    return int(m.group(2)) if m else None


def absolute_numbers(paths: list[str]) -> dict:
    """Continuous episode numbers across seasons, per library.

    `n` was the episode number WITHIN its season, so every season restarted at
    1: seven different rows all displayed as "E9", and sorting by the column
    interleaved all seven seasons. Harmless while one season was imported, a
    real mess at 315 rows.

    A season's offset is the sum of the HIGHEST episode number in each earlier
    season of the same library - taken from episode numbers rather than file
    counts, so a season with a missing file does not shift every number after
    it. Libraries are kept apart so Bleach and Gintama cannot collide.
    """
    per: dict = {}
    maxep: dict = {}
    for path in paths:
        pp = Path(path)
        m = EP_RE.search(pp.name)
        if not m:
            continue
        key = (str(library_root(pp)), int(m.group(1)))
        ep = int(m.group(2))
        per[path] = (key, ep)
        if ep > maxep.get(key, 0):
            maxep[key] = ep
    offset = {k: sum(v for k2, v in maxep.items() if k2[0] == k[0] and k2[1] < k[1])
              for k in maxep}
    return {path: offset[key] + ep for path, (key, ep) in per.items()}


def selects(h: dict) -> tuple[str, str]:
    """A host's own selection rule: (episodes, order), same words the CLI uses."""
    return ((h.get("episodes") or "any").lower(), (h.get("order") or "forward").lower())


def ep_in_set(n: int, spec: str) -> bool:
    """Mirror of the CLI's ep_in_set: any | odd | even | 3 | 9-12 | 20- | -9, comma-mixed.

    The UI understood only odd/even/any, so a numeric range fell through and
    matched EVERYTHING - a device restricted to 232-316 still showed as owning
    half the show. Both sides must read a range identically or the column is
    fiction.
    """
    spec = (spec or "any").strip().lower()
    if not spec or spec == "any":
        return True
    kind_ok, range_seen, range_hit = True, False, False
    for term in spec.split(","):
        term = term.strip()
        if not term or term == "any":
            continue
        if term == "even":
            if n % 2 != 0:
                kind_ok = False
        elif term == "odd":
            if n % 2 == 0:
                kind_ok = False
        elif "-" in term:
            lo, _, hi = term.partition("-")
            range_seen = True
            try:
                if int(lo or 0) <= n <= int(hi or 999999):
                    range_hit = True
            except ValueError:
                pass
        elif term.isdigit():
            range_seen = True
            if int(term) == n:
                range_hit = True
    return kind_ok and (range_hit or not range_seen)


_running_cache: dict = {"at": 0.0, "ids": set()}


def running_devices() -> set:
    """Which devices are actually running, per the CLI. Cached briefly.

    Ownership must follow reality: a stopped machine still "owning" a hundred
    episodes tells you they are covered when nothing will touch them. The queue
    is polled every few seconds, so the answer is cached for 10s rather than
    forking the CLI on every request.
    """
    now = time.time()
    if now - _running_cache["at"] < 10:
        return _running_cache["ids"]
    ids = set()
    rc, out, _ = run([UPSCALE_BIN, "device"], timeout=30)
    if rc == 0:
        for line in (out or "").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[-1] in ("running", "stopped"):
                if parts[-1] == "running":
                    ids.add(parts[0])
    _running_cache.update(at=now, ids=ids)
    return ids


def device_map(cfg: dict, ns: list[int]) -> dict:
    """Which machine owns each episode number, DERIVED from host config.

    The device column used to be a stored map that had to be populated by hand,
    and `_next_path` refused anything assigned elsewhere - so a stale entry did
    not merely mislabel a row, it stopped the episode being run at all. The rule
    the machines actually follow already lives in their config: a parity and a
    direction. Deriving the column from that means it is right the moment a
    machine is configured, and there is nothing to assign retroactively.

    Where several machines match a parity, forward ones take the front of the
    outstanding work and reverse ones the back; the boundary is the midpoint of
    what is still outstanding, recomputed on every refresh rather than stored.
    """
    hosts = all_hosts(cfg)
    if not hosts or not ns:
        return {}
    # Stopped devices own nothing. If every device is stopped, fall back to the
    # configured answer rather than showing a queue belonging to nobody.
    live = running_devices()
    if live:
        hosts = {k: v for k, v in hosts.items() if k in live} or hosts
    lo, hi = min(ns), max(ns)
    mid = (lo + hi) / 2.0
    out = {}
    for n in ns:
        cand = []
        for hid, h in hosts.items():
            eps, order = selects(h)
            if not ep_in_set(n, eps):
                continue
            cand.append((hid, order))
        if not cand:
            continue
        if len(cand) > 1:
            want = "reverse" if n > mid else "forward"
            narrowed = [c for c in cand if c[1] == want]
            # A parity-specific rule beats a catch-all: "even" names this episode,
            # "any" merely fails to exclude it.
            if not narrowed:
                # A device naming a specific set beats one that merely fails to
                # exclude this episode.
                narrowed = [c for c in cand
                            if selects(hosts[c[0]])[0] not in ("", "any")] or cand
            cand = narrowed
        out[n] = cand[0][0]
    return out


_rate_hist: dict[tuple, list] = {}


def phase_eta(host: str, phase: str, done: int, total: int) -> tuple[int, float]:
    """-> (eta_seconds, bytes_per_second). Zero when there is not enough history."""
    if not phase or total <= 0 or done <= 0:
        _rate_hist.pop((host, phase), None)
        return 0, 0.0
    now = time.time()
    hist = _rate_hist.setdefault((host, phase), [])
    if hist and done < hist[-1][1]:
        hist.clear()                      # went backwards: a new file, not progress
    hist.append((now, done))
    del hist[:-40]                        # ~2 min at a 3 s poll
    if len(hist) < 2:
        return 0, 0.0
    dt, dd = now - hist[0][0], done - hist[0][1]
    if dt < 3 or dd <= 0:
        return 0, 0.0
    rate = dd / dt
    return int((total - done) / rate), rate


# path -> size of the finished file, recorded when a background delivery starts.
# The server can see the .part arriving but not what it is aiming at.


def outstanding(cfg: dict, lib_path: str) -> dict:
    """What the pipeline itself says is left. One source of truth, not two."""
    up = cfg.get("upscale_bin") or "upscale"
    name = Path(lib_path).name
    env = {"SRC_EXT": src_ext_for(cfg, name), "LIB": lib_path,
           "ARCHIVE": f"{lib_path}/.upscale-originals", "REMOTE": "local", "EPISODES": "any"}
    rc, out, err = run([up, "--list"], timeout=120, env=env)
    if rc != 0 and not out.strip():
        return {"error": (err or "upscale --list failed").strip()[:400], "episodes": []}
    eps = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        # `--list` numbers its rows: "  12\t/path/to/file"
        parts = line.split("\t", 1)
        p = parts[1] if len(parts) == 2 else line.split(None, 1)[-1]
        eps.append({"path": p, "name": Path(p).name})
    return {"episodes": eps, "count": len(eps)}


# ------------------------------------------------------------------- hosts ---


def host_status(name: str, h: dict) -> dict:
    """Live state of one GPU host. An unreachable host says so.

    A PUSH host (a rented box with no route home) runs the same worker and
    reports the same JSON, so progress, fps and ETA need nothing special. The
    one thing it cannot know is which episode it is working on: the server
    pushed the source in under a fixed name, so the worker honestly reports
    "src.avi". The real title is in <rwork>/episode, written by whatever pushed
    it, and is read here in the SAME ssh round trip - a second one per poll,
    per host, would be paid on every refresh for one string.
    """
    worker = h.get("worker") or "upscale-worker"
    cmd = f"{shlex.quote(worker)} status --json"
    if h.get("push"):
        rwork = h.get("rwork") or "/root/work"
        cmd += f"; echo '---EPISODE---'; cat {shlex.quote(rwork)}/episode 2>/dev/null || true"
    rc, out, err = ssh_to(h.get("ssh", name), cmd, timeout=15)
    episode_override = ""
    if "---EPISODE---" in out:
        out, _, episode_override = out.partition("---EPISODE---")
        episode_override = episode_override.strip()
    # `id` is the CONFIG key and is what actions are addressed to; the worker
    # also reports a `host`, which is the machine's own hostname. Merging the
    # worker's payload over a dict keyed "host" silently replaced the former
    # with the latter, and every control button then addressed a host that is
    # not in the config.
    base = {"id": name, "label": h.get("label", name)}
    if rc != 0:
        return {**base, "reachable": False, "error": (err or f"ssh exited {rc}").strip()[:300]}
    try:
        data = json.loads(out.strip() or "{}")
    except json.JSONDecodeError:
        return {**base, "reachable": True, "error": "worker did not return JSON", "raw": out[:300]}
    if episode_override:
        data["episode"] = episode_override
    return {**base, "reachable": True, "push": bool(h.get("push")), **data}


_DEVICE_TABLE_TTL = 3.0
_device_table_lock = threading.Lock()
_device_table: tuple[float, dict] = (0.0, {})


def device_table() -> dict:
    """Every device's running flag, from one `upscale device` call.

    This is memoised for a few seconds because the command is not cheap: to
    answer "is it running" for a local-mode device it ssh's to that device. It
    used to be run once PER HOST, so N devices cost N ssh round trips per host
    and N*N per poll - with four devices, one of them a rented box that had
    been destroyed and could only answer by timing out, that was most of the
    23s it took to draw the page.
    """
    global _device_table
    now = time.monotonic()
    with _device_table_lock:
        ts, table = _device_table
        if table and now - ts < _DEVICE_TABLE_TTL:
            return table
    rc, out, _ = run([UPSCALE_BIN, "device"], timeout=30)
    table = {}
    if rc == 0:
        for line in (out or "").splitlines():
            # continuation lines are indented and describe the line above
            if not line[:1].strip():
                continue
            parts = line.split()
            if parts:
                table[parts[0]] = parts[-1] == "running"
    with _device_table_lock:
        _device_table = (now, table)
    return table


def driver_state(host: str) -> dict:
    """Is this device running, according to the CLI that starts and stops it?

    This used to report on an in-process Driver thread. There is no such thread
    any more - `upscale start <device>` owns that - so asking anything in this
    process would be inventing a second answer, which is the exact habit that
    made a finished episode display as running on a machine that never owned it.
    """
    return {"queue_running": device_table().get(host, False), "queue_note": ""}


def host_states(cfg: dict) -> list[dict]:
    """Live state of every device, polled concurrently.

    These are independent ssh round trips to different machines. Done in a loop
    the page waits for their sum, and one unreachable box puts its whole
    connect timeout in front of every host after it.
    """
    hosts = all_hosts(cfg)
    if not hosts:
        return []
    device_table()          # warm the memo once, not once per worker thread

    def one(item):
        name, h = item
        st = host_status(name, h)
        st.update(driver_state(name))
        st["scratch"] = h.get("scratch", {})
        st["default_scratch"] = h.get("default_scratch")
        return st

    with ThreadPoolExecutor(max_workers=min(8, len(hosts))) as ex:
        return list(ex.map(one, hosts.items()))


_ignore_lock = threading.Lock()
_ignore_cache: dict = {}


def ignore_patterns(lib: Path) -> list[str]:
    """`<library>/.upscaleignore`, the same file the CLI's discovery reads.

    Without this the page lists things the pipeline will never touch: the
    set-aside directories hold files that still carry an SxxEyy and still sit
    inside the library, so they read as episodes to anything that only looks at
    names. Both sides must honour the same file or the page describes work that
    is not going to happen.
    """
    f = lib / ".upscaleignore"
    try:
        mtime = f.stat().st_mtime
    except OSError:
        return []
    key = str(f)
    with _ignore_lock:
        hit = _ignore_cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
    pats = []
    try:
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                pats.append(line)
    except OSError:
        pats = []
    with _ignore_lock:
        _ignore_cache[key] = (mtime, pats)
    return pats


def _ignored(lib: Path, p: Path, pats: list[str]) -> bool:
    # matched against the basename AND the library-relative path, as the CLI does
    try:
        rel = str(p.relative_to(lib))
    except ValueError:
        return False
    return any(fnmatch(p.name, pat) or fnmatch(rel, pat) for pat in pats)


def tracked_paths(cfg: dict) -> list[str]:
    """Every episode the configured devices are responsible for.

    One row per episode FILE in a device's library that falls inside that
    device's range. A delivered episode is included because its master occupies
    the source's own path - the archive is what says it is finished, and the
    status rules below already read it.
    """
    seen: set = set()
    out: list = []
    # Numbering is built from the WHOLE library, never from the tracked subset:
    # a season's offset is the sum of earlier seasons' highest episode, so if a
    # device's range happens to exclude the last episode of season 1, season 2
    # and everything after it shifts down by one - and the row then names the
    # wrong release file as its source.
    numbering: dict = {}
    for _name, h in all_hosts(cfg).items():
        lib = Path(h.get("src") or "")
        if not lib.is_dir():
            continue
        ext = (h.get("ext") or "mkv").lower().lstrip(".")
        spec = (h.get("episodes") or "any").lower()
        pats = ignore_patterns(lib)
        arc = lib / ".upscale-originals"
        every, live = [], []
        for p in lib.rglob(f"*.{ext}"):
            if _ignored(lib, p, pats):
                continue
            every.append(str(p))                 # numbering sees the archive too,
            if arc not in p.parents:             # so offsets match the CLI's
                live.append(p)
        absn = absolute_numbers(every)
        for p in live:
            n = absn.get(str(p))
            if n is None or not ep_in_set(n, spec):
                continue
            if str(p) not in seen:
                seen.add(str(p))
                out.append(str(p))
        numbering.update(absn)
    return out, numbering


_prov_lock = threading.Lock()
_prov_cache: dict = {}

# "[BlueLobster] Gintama - 042 [480p].mkv" -> 42
# "[BlueLobster] Gintama - 001+002 [480p].mkv" -> 1 and 2, for a library that
# keeps a two-parter as one file.
_REL_RE = __import__("re").compile(r"-\s(\d{3}(?:\+\d{3})*)\s\[")


def source_index(lib: Path) -> dict:
    """Absolute episode number -> the release file it was built from.

    Read from the staging directory rather than from a manifest: the file that
    fed an episode is still sitting there under its own release name, so this
    is derived from the work and cannot fall out of date the way a
    hand-maintained list does. Re-read when the directory changes.
    """
    d = lib / ".splice-incoming" / "raw"
    try:
        mtime = d.stat().st_mtime
    except OSError:
        return {}
    key = str(d)
    with _prov_lock:
        hit = _prov_cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
    table: dict = {}
    try:
        for f in d.iterdir():
            m = _REL_RE.search(f.name)
            if not m:
                continue
            for part in m.group(1).split("+"):
                table[int(part)] = f.name
    except OSError:
        table = {}
    with _prov_lock:
        _prov_cache[key] = (mtime, table)
    return table


def queue_rows(cfg: dict, host_states: list[dict]) -> dict:
    """One row per imported FILE.

    Status is derived, not stored:
      running  the file's name is what a host says it is working on
      done     the source is gone from its path and sits in the archive - which
               is exactly what this pipeline means by finished
      held     the user paused it
      queued   everything else

    A missing file that is NOT in the archive is reported as `missing` rather
    than quietly dropped: it usually means it was renamed or moved, and hiding
    that would make the queue silently shorter than the user's intent.
    """
    # WHICH EPISODE IS RUNNING IS THE SEQUENCER'S ANSWER, NOT THE WORKER'S.
    #
    # The worker names the episode from its job file, and that file belongs to
    # the PREVIOUS episode until the new one finishes counting frames - minutes,
    # on a long episode. For all of that time the row the user just started read
    # `queued` while the card was demonstrably working on it, and the episode
    # There is no in-process driver to ask any more: devices are started by
    # `upscale start <name>` and a host's own status is the only live signal.
    # A claim is trusted solely from the machine the episode belongs to, which
    # is what stops a worker that still names its PREVIOUS episode from
    # attaching its phase to a row it does not own.
    driving: dict = {}
    claimed = {x.get("id") for x in driving.values()}

    running = {}
    for h in host_states:
        if h.get("id") in claimed:
            continue                      # already spoken for, by name not guess
        # The fallback name is scraped off a remote command line, so it arrives
        # wrapped in the shell quoting the command was sent with - "...mkv'"
        # never matched any file and the row stayed queued.
        epname = (h.get("episode") or "").strip().strip("'\"")
        if epname and h.get("state") in ("running", "working", "paused", "stopping"):
            running[epname] = h

    held, gone = held_for("*"), removed_for("*")
    assigned = assignments()
    host_labels = {h.get("id", ""): h.get("label", "") for h in host_states}
    rows = []
    _paths, _numbering = tracked_paths(cfg)
    absn = _numbering
    owner = device_map(cfg, sorted(set(absn.values())))
    for path in _paths:
        p = Path(path)
        n = absn.get(path) or ep_num(p.name)
        if path in gone:
            continue
        # Match on the stem as well as the filename. A PUSH host reports the
        # episode STEM - the server pushed the source in under a fixed name, so
        # what identifies it is the title without its extension - while this
        # lookup used the filename WITH `.mkv`. Nothing ever matched, so the row
        # read `queued` and carried no device while the box was demonstrably
        # upscaling it. p.stem is used rather than splitting the reported name,
        # because a title containing a full stop ("Mr. X") would lose its tail.
        h = driving.get(path) or running.get(p.name) or running.get(p.stem)
        # ASSIGNMENT AND CLAIM ARE ONE THING, not two. A host reports the episode
        # from its job file, and that file still names the PREVIOUS episode until
        # the next one finishes counting frames - so a machine that was killed
        # mid-episode last night goes on claiming it for ever. E09 was finished
        # and archived, yet showed as DELIVERING on the desktop, which had since
        # moved to E28 and never owned E09 in the first place.
        #
        # So a claim only counts from the machine the episode BELONGS to. Where
        # an episode is assigned, that assignment decides; an unassigned episode
        # still trusts whoever claims it, which is what lets a queue adopt work
        # nobody has spoken for.
        _own = assigned.get(path) or owner.get(n or -1, "")
        if h and _own and h.get("id") != _own:
            h = None
        lib = library_root(p)
        arch = lib / ".upscale-originals"
        archived = arch.is_dir() and any(x.name == p.name for x in arch.rglob("*"))
        # A background delivery is a state of its own now that phases overlap:
        # the GPU has moved to the next episode while this one still uploads.
        # Detected here rather than asked of the host, because the .part is
        # arriving on THIS machine - and if it is missed, the episode reads as
        # queued and the queue starts it a second time.
        # A .part is only a delivery IN PROGRESS while it is being written.
        # rsync leaves one behind after a failed or killed transfer as a resume
        # point, and treating that as "delivering" for ever made the episode
        # invisible to the queue: not outstanding, so never picked up, while
        # nothing was actually uploading it.
        part = next((x for x in p.parent.glob(f".{p.stem}*.part*")), None)
        if part and (time.time() - part.stat().st_mtime) > 120:
            part = None
        if h:
            status = "paused" if h.get("state") == "paused" else "running"
        elif part and not archived:
            status = "delivering"
        elif archived:
            # THE ARCHIVE IS THE DEFINITION OF DONE, not the path disappearing.
            # With .mkv sources the delivered file takes the source's own path,
            # so "the file is gone" is never true for them and every finished
            # episode read as still queued.
            status = "done"
        elif not p.exists():
            status = "missing"
        elif path in held:
            status = "held"
        else:
            status = "queued"
        assigned_to = assigned.get(path, "")
        row = {"n": n, "name": p.name, "path": path, "status": status,
               "library": str(lib), "library_name": lib.name,
               # What this was built FROM. Empty is a normal answer: it means no
               # release file for that episode is staged, not that anything is wrong.
               "source_name": source_index(lib).get(n, ""),
               "size": p.stat().st_size if p.exists() else 0,
               "assigned": assigned_to,
               # The device column answers "where will this run?", not only
               # "where is it running?" - an assigned episode has an answer long
               # before a GPU touches it.
               "device": ""}   # filled in below, from the derived rule
        if status == "delivering" and not h:
            # A background delivery, measured entirely on this side: the .part
            # arriving here, against the size recorded when it was launched.
            done = part.stat().st_size if part else 0
            # The driver used to record the expected size when it launched the
            # upload. Nothing launches uploads in this process now, so the bar
            # is sized from the source: a 2x master is reliably several times it,
            # and an approximate bar beats a missing one.
            total = 0
            eta, rate = phase_eta("deliver:" + path, "delivering", done, total)
            row.update({"phase": "delivering",
                        "phase_percent": min(100, int(done * 100 / total)) if total else -1,
                        "phase_done": done, "phase_total": total, "phase_unit": "bytes",
                        "phase_eta_s": eta, "phase_rate": rate})
        if h:
            # The two transfer phases can only be measured HERE: the other end
            # of each is a file on this machine. The worker reports how many
            # bytes it has; the server knows what the total should be.
            pd, pt = h.get("phase_done", 0), h.get("phase_total", 0)
            ph = h.get("phase", "")
            if ph == "delivering":
                # The total is the finished file's size, which only the worker
                # knows; the bytes delivered so far are in the .part, which only
                # this machine can see. Neither side can draw this bar alone.
                if part:
                    pd = part.stat().st_size
                pt = h.get("out_bytes") or pt
            elif ph == "fetching":
                pd, pt = h.get("fetch_bytes", 0), (p.stat().st_size if p.exists() else 0)
            pp = h.get("phase_percent", -1)
            peta, prate = h.get("phase_eta_s", 0), 0.0
            if pt and pd:
                pp = min(100, int(pd * 100 / pt))
                # Derived here for the phases the worker cannot time itself.
                e, r = phase_eta(h.get("id", ""), ph, pd, pt)
                if e:
                    peta, prate = e, r
            row.update({"percent": h.get("percent", 0), "fps": h.get("fps", 0),
                        "eta_s": h.get("eta_s", 0), "phase": ph,
                        "phase_percent": pp, "phase_done": pd, "phase_total": pt,
                        "phase_unit": h.get("phase_unit", ""),
                        "phase_eta_s": peta, "phase_rate": prate,
                        "phase_elapsed_s": h.get("phase_elapsed_s", 0),
                        "elapsed_s": h.get("elapsed_s", 0),
                        "frames_done": h.get("frames_done", 0),
                        "frames_total": h.get("frames_total", 0),
                        "host": h.get("id", "")})
        rows.append(row)
    rows.sort(key=lambda r: (r["library_name"].lower(), r["n"] is None,
                             r["n"] if r["n"] is not None else 0, r["name"]))
    # Derive the device column from what each machine is configured to take.
    for r in rows:
        hid = assigned.get(r["path"]) or owner.get(r.get("n") or -1, "")
        r["assigned"] = assigned.get(r["path"], "")
        r["owner"] = hid
        r["device"] = host_labels.get(hid, "") or hid or ""
    return {"rows": rows,
            "counts": {st: sum(1 for r in rows if r["status"] == st)
                       for st in ("done", "running", "paused", "held", "queued", "missing")}}


def control(cfg: dict, host: str, action: str) -> dict:
    h = all_hosts(cfg).get(host)
    if not h:
        return {"ok": False, "error": f"unknown host {host!r}"}
    if action not in ("pause", "resume", "stop"):
        return {"ok": False, "error": f"unknown action {action!r}"}
    up = h.get("upscale") or "upscale"
    rc, out, err = ssh_to(h.get("ssh", host), f"{shlex.quote(up)} --{action}", timeout=20)
    if rc != 0:
        return {"ok": False, "error": (err or f"ssh exited {rc}").strip()[:300]}
    return {"ok": True, "action": action, "host": host, "said": out.strip()}


# ------------------------------------------------------------------ server ---
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet; journald gets the important lines
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if ctype.startswith("application/json") else "no-cache")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _static(self, path: str):
        rel = path.lstrip("/") or "index.html"
        target = (DIST / rel).resolve()
        # never serve outside dist, whatever the path claims
        if not str(target).startswith(str(DIST.resolve())) or not target.is_file():
            target = DIST / "index.html"          # SPA deep links
        if not target.is_file():
            return self._send(503, b"UI not built. Run: cd web && pnpm install && pnpm build\n", "text/plain")
        ctype = {
            ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
            ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png",
            ".ico": "image/x-icon", ".woff2": "font/woff2",
        }.get(target.suffix, "application/octet-stream")
        self._send(200, target.read_bytes(), ctype)

    def do_GET(self):
        cfg = load_config()
        path = urlparse(self.path).path
        q = parse_qs(urlparse(self.path).query)
        if path == "/api/libraries":
            return self._json({"libraries": libraries(cfg), "media_root": cfg.get("media_root")})
        if path == "/api/outstanding":
            lib = (q.get("lib") or [""])[0]
            if not lib:
                return self._json({"error": "lib is required"}, 400)
            return self._json(outstanding(cfg, lib))
        if path == "/api/hosts":
            snap = read_snapshot()
            return self._json({"hosts": snap.get("hosts", []),
                               "ts": snap.get("ts", int(time.time())),
                               "age": snap.get("age", 0)})
        if path == "/api/browse":
            return self._json(browse(cfg, (q.get("q") or [""])[0]))
        if path == "/api/queue":
            return self._json(read_snapshot())
        if path == "/api/health":
            return self._json({"ok": True, "config": str(CONFIG_PATH),
                               "error": cfg.get("_error")})
        return self._static(path)

    def do_POST(self):
        try:
            return self._do_post()
        finally:
            _snap_wake.set()

    def _do_post(self):
        cfg = load_config()
        path = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json({"ok": False, "error": "bad JSON body"}, 400)
        if path == "/api/start":
            # One path: the button runs the same command a shell would.
            return self._json(upscale_cmd("start", (body.get("host") or "").strip()))
        if path == "/api/hosts/probe":
            return self._json(probe_host((body.get("ssh") or "").strip()))
        if path == "/api/hosts/add":
            ssh_t = (body.get("ssh") or "").strip()
            hid = (body.get("id") or "").strip() or ssh_t.split("@")[-1].split()[0]
            if not ssh_t or not hid:
                return self._json({"ok": False, "error": "ssh target is required"}, 400)
            pr = probe_host(ssh_t)
            if not pr.get("ok"):
                return self._json({"ok": False, "error": pr.get("error")}, 400)
            scratch = body.get("scratch") or {k: v["path"] for k, v in (pr.get("scratch") or {}).items()}
            entry = {"ssh": ssh_t,
                     "label": (body.get("label") or "").strip() or f"{pr.get('host', hid)}",
                     "scratch": scratch,
                     "default_scratch": body.get("default_scratch") or next(iter(scratch), ""),
                     "worker": pr.get("worker") or "upscale-worker",
                     "upscale": pr.get("upscale") or "upscale"}
            return self._json({"ok": True, "id": hid, "hosts": save_host(hid, entry),
                               "probe": pr})
        if path == "/api/hosts/remove":
            # Devices live in ~/.upscale/devices; the CLI owns that directory.
            return self._json(upscale_cmd("device", "--clear", (body.get("id") or "").strip()))
        if path == "/api/hosts/remove-legacy":
            return self._json({"ok": True, "hosts": forget_host((body.get("id") or "").strip())})
        if path == "/api/hold":
            paths = body.get("paths") or []
            hold = bool(body.get("hold", True))
            if not paths:
                return self._json({"ok": False, "error": "paths are required"}, 400)
            held = mutate_set("held", paths, hold)
            skipped = []
            if hold:
                # Holding the episode a machine is ON means "move to the next
                # one", so the episode has to actually stop. The driver would
                # notice within a poll, but a host with no driver would sit there
                # finishing an episode the user just paused.
                #
                # Asking the WORKER which episode it is on is not good enough:
                # its answer comes from a job file belonging to the previous
                # episode until the new one has finished counting frames, and
                # comes back wrapped in shell quotes when it is scraped off a
                # command line. Hold then matched nothing and quietly did not
                # stop the episode the user had just held. The driver knows the
                # path it launched, so ask it first.
                for name, h in all_hosts(cfg).items():
                    cur = (host_status(name, h).get("episode") or "").strip().strip("'\"")
                    hit = bool(cur) and any(
                        Path(p).name == cur or Path(p).stem == cur for p in paths)
                    if hit:
                        upscale_cmd("skip", name)   # one path: the CLI kills it
                        skipped.append(name)
            return self._json({"ok": True, "held": sorted(held), "skipped": skipped})
        if path in ("/api/pause", "/api/resume"):
            return self._json(control(cfg, body.get("host", ""), path.rsplit("/", 1)[1]))
        if path == "/api/stop":
            # Same: `upscale stop <device>` is the only way anything stops.
            host = (body.get("host") or "").strip()
            if host:
                return self._json(upscale_cmd("stop", host))
            res = [upscale_cmd("stop", h) for h in all_hosts(cfg)]
            return self._json({"ok": all(r.get("ok") for r in res),
                               "note": "; ".join(r.get("note", "") for r in res)})
        if path == "/api/import":
            paths = [str(Path(x)) for x in (body.get("paths") or []) if x]
            good, bad = [], []
            for x in paths:
                px = Path(x)
                if px.is_file() and px.suffix.lower() in VIDEO_EXT and under_roots(cfg, px):
                    good.append(x)
                else:
                    bad.append(x)
            if not good:
                return self._json({"ok": False,
                                   "error": f"no importable video files ({len(bad)} rejected)"}, 400)
            return self._json({"ok": True, "imports": add_imports(good),
                               "added": len(good), "rejected": bad[:5]})
        if path == "/api/unimport":
            return self._json({"ok": True, "imports": drop_imports(body.get("paths") or [])})
        if path == "/api/remove":
            paths = body.get("paths") or []
            if not paths:
                return self._json({"ok": False, "error": "paths are required"}, 400)
            # Delete removes from the QUEUE, never from disk. Nothing in this
            # service deletes media; the source stays exactly where it is.
            drop_imports(paths)
            return self._json({"ok": True, "imports": imports()})
        return self._json({"error": "not found"}, 404)


SNAPSHOT_INTERVAL = 5.0
_snap_lock = threading.Lock()
_snapshot: dict = {}
_snap_wake = threading.Event()
_snap_ready = threading.Event()


def collect_snapshot() -> dict:
    """Everything the page draws, derived once."""
    cfg = load_config()
    states = host_states(cfg)
    qr = queue_rows(cfg, states)
    libs: dict = {}
    for r in qr["rows"]:
        libs.setdefault(r["library"], {"path": r["library"],
                                       "name": r["library_name"], "n": 0})
        libs[r["library"]]["n"] += 1
    return {**qr, "libraries": list(libs.values()), "hosts": states,
            "ts": int(time.time())}


def snapshot_loop():
    """One collector, however many clients.

    Truth is still derived and nothing here is a job database - this is the
    same derivation the request used to do inline, moved off the request so
    that N polling browsers cause one ssh sweep instead of N, and so a sweep
    that takes longer than the poll interval cannot pile up on itself.
    """
    while True:
        try:
            snap = collect_snapshot()
            with _snap_lock:
                global _snapshot
                _snapshot = snap
            _snap_ready.set()
        except Exception as exc:                      # never kill the collector
            print(f"snapshot failed: {type(exc).__name__}: {exc}",
                  file=sys.stderr, flush=True)
            _snap_ready.set()
        _snap_wake.wait(SNAPSHOT_INTERVAL)
        _snap_wake.clear()


def read_snapshot(wait: float = 20.0) -> dict:
    """The last completed collection. Blocks only for the very first one."""
    if not _snap_ready.is_set():
        _snap_ready.wait(wait)
    with _snap_lock:
        snap = dict(_snapshot)
    if snap:
        snap["age"] = max(0, int(time.time()) - snap.get("ts", 0))
    return snap


def main():
    cfg = load_config()
    port = int(os.environ.get("UPSCALE_UI_PORT") or cfg.get("port") or 8790)
    bind = os.environ.get("UPSCALE_UI_BIND") or cfg.get("bind") or "127.0.0.1"
    srv = ThreadingHTTPServer((bind, port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=snapshot_loop, name="snapshot",
                     daemon=True).start()
    print(f"upscale-ui on http://{bind}:{port}  (config: {CONFIG_PATH})", flush=True)
    if cfg.get("_error"):
        print(f"WARNING {cfg['_error']}", file=sys.stderr, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
