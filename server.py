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
import sys
import threading
import time
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


def all_hosts(cfg: dict) -> dict:
    """Hosts from config.json plus any onboarded through the UI.

    Onboarded ones live in the state file rather than config.json so that
    adding a machine never rewrites a hand-written, commented config.
    """
    merged = dict(cfg.get("hosts") or {})
    merged.update(_state().get("hosts") or {})
    return merged


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
    running = {}
    for h in host_states:
        epname = (h.get("episode") or "")
        if epname and h.get("state") in ("running", "working", "paused", "stopping"):
            running[epname] = h

    held, gone = held_for("*"), removed_for("*")
    assigned = assignments()
    host_labels = {h.get("id", ""): h.get("label", "") for h in host_states}
    rows = []
    for path in imports():
        p = Path(path)
        n = ep_num(p.name)
        if path in gone:
            continue
        h = running.get(p.name)
        lib = library_root(p)
        arch = lib / ".upscale-originals"
        archived = arch.is_dir() and any(x.name == p.name for x in arch.rglob("*"))
        if h:
            status = "paused" if h.get("state") == "paused" else "running"
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
               "size": p.stat().st_size if p.exists() else 0,
               "assigned": assigned_to,
               # The device column answers "where will this run?", not only
               # "where is it running?" - an assigned episode has an answer long
               # before a GPU touches it.
               "device": (h.get("label") if h else host_labels.get(assigned_to, "")) or ""}
        if h:
            # The two transfer phases can only be measured HERE: the other end
            # of each is a file on this machine. The worker reports how many
            # bytes it has; the server knows what the total should be.
            pd, pt = h.get("phase_done", 0), h.get("phase_total", 0)
            ph = h.get("phase", "")
            if ph == "delivering":
                part = next((x for x in p.parent.glob(f".{p.stem}*.part*")), None)
                if part:
                    pd = part.stat().st_size
            elif ph == "fetching":
                pd, pt = h.get("fetch_bytes", 0), (p.stat().st_size if p.exists() else 0)
            pp = h.get("phase_percent", -1)
            if pt and pd:
                pp = min(100, int(pd * 100 / pt))
            row.update({"percent": h.get("percent", 0), "fps": h.get("fps", 0),
                        "eta_s": h.get("eta_s", 0), "phase": ph,
                        "phase_percent": pp, "phase_done": pd, "phase_total": pt,
                        "phase_unit": h.get("phase_unit", ""),
                        "phase_eta_s": h.get("phase_eta_s", 0),
                        "phase_elapsed_s": h.get("phase_elapsed_s", 0),
                        "elapsed_s": h.get("elapsed_s", 0),
                        "frames_done": h.get("frames_done", 0),
                        "frames_total": h.get("frames_total", 0),
                        "host": h.get("id", "")})
        rows.append(row)
    rows.sort(key=lambda r: (r["library_name"].lower(), r["n"] is None,
                             r["n"] if r["n"] is not None else 0, r["name"]))
    return {"rows": rows,
            "counts": {st: sum(1 for r in rows if r["status"] == st)
                       for st in ("done", "running", "paused", "held", "queued", "missing")}}


def kill_episode(cfg: dict, host: str) -> None:
    """Stop the EPISODE, leaving any queue alone.

    Used when the running episode is held: the machine must move on to the next
    one, not stall. Finished chunks stay in scratch, so releasing the hold later
    resumes the episode rather than restarting it - which is what makes this a
    pause and not a discard.
    """
    h = all_hosts(cfg).get(host)
    if not h:
        return
    ssh_to(h.get("ssh", host),
           "pkill -f '[u]pscale-worker' ; pkill -f '[r]ealesrgan-ncnn-vulkan' ; "
           "pkill -f '[r]sync .*upscale' ; true", timeout=20)


def abort_host(cfg: dict, host: str) -> dict:
    """Stop NOW, not after the episode.

    Order matters: kill the queue first, or it starts the next episode the
    moment the worker dies. Then the worker, then the GPU processes it spawned,
    which do not die with their parent.

    The in-flight episode is discarded, not resumed - but nothing is lost that
    was already earned: finished chunks stay in scratch and a later run skips
    them. Delivery is atomic (upload to a hidden .part, rename only after the
    size matches), so an abort mid-upload cannot leave a half-file in the
    library.
    """
    h = all_hosts(cfg).get(host)
    if not h:
        return {"ok": False, "error": f"unknown host {host!r}"}
    remote = ("pkill -f '[u]pscale (ep|run|once)' ; "
              "pkill -f '[u]pscale-worker' ; "
              "pkill -f '[r]ealesrgan-ncnn-vulkan' ; "
              "pkill -f '[r]sync .*upscale' ; "
              "rm -f ~/.upscale-ep/pause ~/.upscale-ep/stop ~/.upscale-queue/pause ~/.upscale-queue/stop ; "
              "echo aborted")
    rc, out, err = ssh_to(h.get("ssh", host), remote, timeout=25)
    # pkill exits 1 when it matched nothing, which is a normal abort of an idle
    # host, not a failure.
    if rc not in (0, 1) and not out.strip():
        return {"ok": False, "error": (err or f"ssh exited {rc}").strip()[:300]}
    return {"ok": True, "action": "abort", "host": host}


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
    """Live state of one GPU host. An unreachable host says so."""
    worker = h.get("worker") or "upscale-worker"
    rc, out, err = ssh_to(h.get("ssh", name), f"{shlex.quote(worker)} status --json", timeout=15)
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
    return {**base, "reachable": True, **data}


def host_running(h: dict, name: str) -> dict:
    """Is a queue actually driving this host, and on what?

    Separate from the worker's own state: the worker reports the EPISODE, this
    reports whether anything will pick up the next one.
    """
    rc, out, _ = ssh_to(h.get("ssh", name),
                        "ps -eo args= | grep -E '[u]pscale (ep|run|once)' | head -3", timeout=15)
    lines = [l.strip() for l in out.splitlines() if l.strip()] if rc == 0 else []
    return {"queue_running": bool(lines), "queue_cmd": lines[0] if lines else ""}


# ----------------------------------------------------------------- actions ---
# ------------------------------------------------------------------ driver ---
# One sequencer per host, so the QUEUE is a queue rather than a fixed list fired
# at a machine and forgotten.
#
# It exists because of what pause has to mean: holding the RUNNING episode has
# to move the machine on to the next one, not stall it. A remote `for` loop
# cannot do that - by the time you want to skip, the loop is already inside the
# episode. So the server starts one episode at a time, watches it, and decides
# what happens next.
#
# The episode itself is still launched detached on the host. If this service
# restarts, the episode keeps going and nothing is lost; only the sequencing
# stops, and pressing Start picks it up again. That is deliberate - a web
# service restarting must never abandon a two-hour render, and must never
# silently start new work either.
_drivers: dict[str, "Driver"] = {}
_drivers_lock = threading.Lock()


class Driver(threading.Thread):
    def __init__(self, cfg: dict, host: str, scratch: str):
        super().__init__(daemon=True)
        self.host, self.scratch = host, scratch
        self.stop_after_current = False
        self.current = ""
        self.note = ""
        self._cfg = cfg

    # --- helpers ----------------------------------------------------------
    def _h(self):
        return all_hosts(load_config()).get(self.host) or {}

    def _next_path(self) -> str:
        """The next episode THIS machine should run.

        Assigned to it, or unassigned - and an unassigned one is claimed before
        it is returned, under the same lock that writes assignments, so two
        machines racing the same queue cannot both take it.
        """
        cfg = load_config()
        rows = queue_rows(cfg, [])["rows"]           # no host states: pure queue view
        held, gone = held_for(), removed_for()
        with _state_lock:
            st = _state()
            a = st.setdefault("assigned", {})
            for r in rows:
                p = r["path"]
                if r["status"] != "queued" or p in held or p in gone:
                    continue
                owner = a.get(p)
                if owner and owner != self.host:
                    continue
                if not owner:
                    a[p] = self.host                 # claim
                    _save_state(st)
                return p
        return ""

    def _launch(self, path: str) -> bool:
        h = self._h()
        work = (h.get("scratch") or {}).get(self.scratch)
        worker = h.get("worker") or "upscale-worker"
        lib = library_root(Path(path))
        env = (f"LIB={shlex.quote(str(lib))} "
               f"ARCHIVE={shlex.quote(str(lib / '.upscale-originals'))} "
               + (f"WORK={shlex.quote(work)} " if work else ""))
        remote = (f"setsid nohup env {env}{shlex.quote(worker)} run {shlex.quote(path)} "
                  f">> ~/upscale-ui.log 2>&1 < /dev/null & echo started")
        rc, _, err = ssh_to(h.get("ssh", self.host), remote, timeout=25)
        if rc != 0:
            self.note = (err or f"ssh exited {rc}").strip()[:200]
            return False
        return True

    def _kill_current(self):
        h = self._h()
        ssh_to(h.get("ssh", self.host),
               "pkill -f '[u]pscale-worker' ; pkill -f '[r]ealesrgan-ncnn-vulkan' ; "
               "pkill -f '[r]sync .*upscale' ; true", timeout=20)

    # --- loop -------------------------------------------------------------
    def _still_queued(self, path: str) -> bool:
        return any(r["path"] == path and r["status"] == "queued"
                   for r in queue_rows(load_config(), [])["rows"])

    def run(self):
        # ADOPT whatever is already running. This service restarting must not
        # lock the queue until the episode in flight ends - the render survives
        # a restart, so the sequencing has to be able to rejoin it.
        st = host_status(self.host, self._h())
        if st.get("state") in ("running", "working", "paused", "stopping"):
            self.current = st.get("episode", "")
            self.note = f"adopted {self.current}"
            quiet = 0
            while quiet < 3:
                time.sleep(4)
                st = host_status(self.host, self._h())
                if not st.get("reachable"):
                    continue
                if st.get("state") in ("running", "working", "paused", "stopping"):
                    quiet = 0
                else:
                    quiet += 1
            self.current = ""

        while True:
            path = self._next_path()
            if not path:
                self.note = "nothing left to run"
                break
            self.current = path
            if not self._launch(path):
                break

            # 1. WAIT FOR IT TO ACTUALLY START. Treating "not busy" as finished
            #    is what turned this loop into a launch storm: a status blip
            #    right after launch read as a completed episode, so the same
            #    file was started again every few seconds - three workers ended
            #    up on one episode, sharing a scratch dir.
            started = False
            for _ in range(30):                       # up to ~2 min
                time.sleep(4)
                st = host_status(self.host, self._h())
                if st.get("state") in ("running", "working", "paused", "stopping"):
                    started = True
                    break
                if path in held_for():
                    break
            if not started and path not in held_for():
                self.note = f"{Path(path).name} never started - stopping rather than retrying"
                self._kill_current()
                break

            # 2. Watch until it is genuinely finished.
            quiet = 0
            while True:
                time.sleep(4)
                st = host_status(self.host, self._h())
                if not st.get("reachable"):
                    continue                          # a blip is not an outcome
                if path in held_for():
                    self.note = f"skipped {Path(path).name}"
                    self._kill_current()
                    break
                if st.get("state") in ("running", "working", "paused", "stopping"):
                    quiet = 0
                    continue
                quiet += 1
                if quiet >= 3:                        # ~12 s of genuine silence
                    break

            # 3. DONE IS DERIVED, NOT ASSUMED. If the worker stopped but the
            #    episode is still queued, it failed - and relaunching it is the
            #    storm again. Stop and say so.
            if path not in held_for() and self._still_queued(path):
                self.note = (f"{Path(path).name} stopped without finishing - "
                             f"queue halted so it cannot loop on it")
                break
            self.current = ""
            if self.stop_after_current:
                self.note = "stopped after the current episode"
                break
        self.current = ""
        with _drivers_lock:
            if _drivers.get(self.host) is self:
                _drivers.pop(self.host, None)


def start_job(cfg: dict, host: str, scratch: str, paths: list[str] | None = None) -> dict:
    h = all_hosts(cfg).get(host)
    if not h:
        return {"ok": False, "error": f"unknown host {host!r}"}
    if paths:
        assign(paths, host)              # queue this selection into this machine
    with _drivers_lock:
        d = _drivers.get(host)
        if d and d.is_alive():
            return {"ok": False, "error": f"{host} already has a queue running"}
        d = Driver(cfg, host, scratch)
        _drivers[host] = d
        d.start()
    return {"ok": True, "host": host, "work": (h.get("scratch") or {}).get(scratch) or "(host default)"}


def driver_state(host: str) -> dict:
    d = _drivers.get(host)
    if not d or not d.is_alive():
        return {"queue_running": False, "queue_note": ""}
    return {"queue_running": True, "queue_note": d.note,
            "queue_stopping": d.stop_after_current,
            "queue_current": Path(d.current).name if d.current else ""}


def stop_driver(host: str, kill: bool = False) -> dict:
    d = _drivers.get(host)
    if not d or not d.is_alive():
        return {"ok": True, "note": "no queue was running"}
    d.stop_after_current = True
    if kill:
        d._kill_current()
    return {"ok": True, "note": "queue will stop after the current episode" if not kill
                                else "queue stopped and the current episode killed"}


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
            hosts = all_hosts(cfg)
            out = []
            for name, h in hosts.items():
                st = host_status(name, h)
                st.update(driver_state(name))
                st["scratch"] = h.get("scratch", {})
                st["default_scratch"] = h.get("default_scratch")
                out.append(st)
            return self._json({"hosts": out, "ts": int(time.time())})
        if path == "/api/browse":
            return self._json(browse(cfg, (q.get("q") or [""])[0]))
        if path == "/api/queue":
            hosts = all_hosts(cfg)
            states = []
            for name, h in hosts.items():
                st = host_status(name, h)
                st.update(driver_state(name))
                st["scratch"] = h.get("scratch", {})
                st["default_scratch"] = h.get("default_scratch")
                states.append(st)
            qr = queue_rows(cfg, states)
            libs = {}
            for r in qr["rows"]:
                libs.setdefault(r["library"], {"path": r["library"], "name": r["library_name"], "n": 0})
                libs[r["library"]]["n"] += 1
            return self._json({**qr, "libraries": list(libs.values()),
                               "hosts": states, "ts": int(time.time())})
        if path == "/api/health":
            return self._json({"ok": True, "config": str(CONFIG_PATH), "error": cfg.get("_error")})
        return self._static(path)

    def do_POST(self):
        cfg = load_config()
        path = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json({"ok": False, "error": "bad JSON body"}, 400)
        if path == "/api/start":
            # No path list: the driver re-reads the queue before every episode,
            # so holds and deletions made WHILE it runs are honoured. Handing it
            # a frozen list would make the buttons decorative again.
            return self._json(start_job(cfg, body.get("host", ""), body.get("scratch", ""),
                                        body.get("paths") or []))
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
        if path == "/api/assign":
            return self._json({"ok": True,
                               "assigned": assign(body.get("paths") or [], body.get("host") or "")})
        if path == "/api/hosts/remove":
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
                for name, h in all_hosts(cfg).items():
                    st = host_status(name, h)
                    cur = st.get("episode") or ""
                    if cur and any(Path(p).name == cur for p in paths):
                        kill_episode(cfg, name)
                        skipped.append(name)
            return self._json({"ok": True, "held": sorted(held), "skipped": skipped})
        if path in ("/api/pause", "/api/resume"):
            return self._json(control(cfg, body.get("host", ""), path.rsplit("/", 1)[1]))
        if path == "/api/stop":
            # Stop is the QUEUE's stop: finish the episode in flight, then stop.
            # It also writes the pipeline's own flag, so an `upscale ep` run
            # started outside this UI stops too.
            host = body.get("host", "")
            control(cfg, host, "stop")
            return self._json({"ok": True, **stop_driver(host)})
        if path == "/api/abort":
            stop_driver(body.get("host", ""), kill=False)   # stop sequencing first
            return self._json(abort_host(cfg, body.get("host", "")))
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


def main():
    cfg = load_config()
    port = int(os.environ.get("UPSCALE_UI_PORT") or cfg.get("port") or 8790)
    bind = os.environ.get("UPSCALE_UI_BIND") or cfg.get("bind") or "127.0.0.1"
    srv = ThreadingHTTPServer((bind, port), Handler)
    srv.daemon_threads = True
    print(f"upscale-ui on http://{bind}:{port}  (config: {CONFIG_PATH})", flush=True)
    if cfg.get("_error"):
        print(f"WARNING {cfg['_error']}", file=sys.stderr, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
