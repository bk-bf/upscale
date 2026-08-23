#!/usr/bin/env python3
"""upscale-ui — one page showing what the upscaler is doing.

Runs on the machine holding the media. It never upscales anything and it never
decides anything: it reads the run's own snapshot, lists two directories, and
asks each device what it is working on.

Truth is derived, never stored, and there is far less of it to derive than
there used to be. The pipeline no longer selects a subset of a shared library,
so this server no longer mirrors episode numbering, ranges, parities, ignore
globs or an archive lookup to stay in step with it. What is left to do is what
is in the source directory; what is done is what is in the target directory.

Stdlib only.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
DIST = HERE / "web" / "dist"
RUN = Path(os.environ.get("UPSCALE_STATE", Path.home() / ".upscale")) / "run"
STATE_JSON = RUN / "state.json"
WORKER = os.environ.get("UPSCALE_WORKER", ".local/libexec/upscale-worker")
RUN_LOG = RUN / "run.log"

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
       "-o", "ControlPath=none"]
SNAPSHOT_INTERVAL = 5.0


def run(cmd, timeout=30):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 124, "", str(exc)


def ssh_to(spec: str, cmd: str, timeout=20):
    return run(SSH + shlex.split(spec) + [cmd], timeout=timeout)


def config() -> dict:
    try:
        return json.loads((HERE / "config.json").read_text())
    except (OSError, ValueError):
        return {}


# ------------------------------------------------------------------ browse ---
def browse_roots() -> list:
    return [Path(p) for p in config().get("browse_roots", ["/mnt/media"])]


def allowed(p: Path) -> bool:
    try:
        r = p.resolve()
    except OSError:
        return False
    return any(r == root or root in r.parents for root in browse_roots())


def search(q: str) -> dict:
    """Typeahead over paths, in the shape the page already speaks.

    A trailing slash lists a directory; anything else filters that directory by
    prefix. Only the allowed roots are reachable.
    """
    q = q or ""
    if not q:
        return {"base": "", "results": [
            {"kind": "dir", "name": str(r), "path": str(r), "files": 0} for r in browse_roots()]}
    p = Path(q)
    base, frag = (p, "") if q.endswith("/") else (p.parent, p.name.lower())
    if not allowed(base) or not base.is_dir():
        return {"base": str(base), "results": []}
    out = []
    try:
        for e in sorted(base.iterdir()):
            if e.name.startswith(".") or (frag and not e.name.lower().startswith(frag)):
                continue
            if e.is_dir():
                try:
                    n = sum(1 for f in e.iterdir() if f.is_file() and not f.name.startswith("."))
                except OSError:
                    n = 0
                out.append({"kind": "dir", "name": e.name, "path": str(e), "files": n})
            elif e.is_file():
                out.append({"kind": "file", "name": e.name, "path": str(e),
                            "size": e.stat().st_size})
    except OSError:
        pass
    return {"base": str(base), "results": out[:400]}


PENDING = RUN / "pending.json"


def pending() -> dict:
    try:
        return json.loads(PENDING.read_text())
    except (OSError, ValueError):
        return {}


def set_pending(d: dict) -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    PENDING.write_text(json.dumps({**pending(), **d}, indent=2))


def browse(where: str) -> dict:
    """Directories under the allowed roots, with a count of what is in them.

    Nothing outside browse_roots is reachable, and only directories are listed -
    a run takes a directory, not a file.
    """
    if not where:
        return {"path": "", "up": "", "dirs": [
            {"path": str(r), "name": str(r), "files": 0} for r in browse_roots()]}
    p = Path(where)
    if not allowed(p) or not p.is_dir():
        return {"error": f"not reachable: {where}", "dirs": []}
    dirs = []
    try:
        for d in sorted(p.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                try:
                    n = sum(1 for f in d.iterdir() if f.is_file() and not f.name.startswith("."))
                except OSError:
                    n = 0
                dirs.append({"path": str(d), "name": d.name, "files": n})
    except OSError as exc:
        return {"error": str(exc), "dirs": []}
    up = str(p.parent) if allowed(p.parent) else ""
    files = sum(1 for f in p.iterdir() if f.is_file() and not f.name.startswith("."))
    return {"path": str(p), "up": up, "dirs": dirs, "files": files}


# ------------------------------------------------------------------ devices ---
# An ADDRESS BOOK, nothing more. It maps a name to an ssh destination so a
# rented box is not a row of digits. It does not decide what a device does -
# that is decided by the run, in its arguments, every time.
DEVICES_PATH = HERE / "devices.json"


def devices() -> dict:
    try:
        return json.loads(DEVICES_PATH.read_text())
    except (OSError, ValueError):
        return {}


def save_devices(d: dict) -> None:
    tmp = DEVICES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=2))
    tmp.replace(DEVICES_PATH)


def probe(spec: str) -> dict:
    """Can we reach it, and is it ready to be given work?

    Reports what would actually stop a run: no worker installed, no upscaler
    binary, no mkvtoolnix (results play in mpv and fail in Jellyfin without it),
    and how many cores the cgroup really allows - `nproc` lies on a rented box,
    and worker count follows cores, not the card.
    """
    out = {"ssh": spec, "reachable": False}
    rc, o, err = ssh_to(spec, (
        "printf 'host=%s\n' \"$(hostname)\"; "
        "printf 'worker=%s\n' \"$([ -x $HOME/.local/libexec/upscale-worker ] && echo yes || echo no)\"; "
        "printf 'mkvmerge=%s\n' \"$(command -v mkvmerge >/dev/null && echo yes || echo no)\"; "
        "printf 'gpu=%s\n' \"$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)\"; "
        "printf 'nproc=%s\n' \"$(nproc)\"; "
        "printf 'cpumax=%s\n' \"$(cat /sys/fs/cgroup/cpu.max 2>/dev/null)\"; "
        "printf 'free=%s\n' \"$(df -BG --output=avail $HOME 2>/dev/null | tail -1 | tr -d ' G')\""
    ), timeout=25)
    if rc != 0:
        out["error"] = (err or "ssh failed").strip()[:200]
        return out
    out["reachable"] = True
    for line in o.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    quota, period = (out.get("cpumax") or "").split(" ") if " " in (out.get("cpumax") or "") else ("", "")
    if quota.isdigit() and period.isdigit() and int(period):
        out["cores"] = round(int(quota) / int(period), 1)
    elif (out.get("nproc") or "").isdigit():
        out["cores"] = int(out["nproc"])
    warn = []
    if out.get("worker") != "yes":
        warn.append("upscale-worker is not installed on it")
    if out.get("mkvmerge") != "yes":
        warn.append("mkvtoolnix is missing — results will fail in Jellyfin")
    out["warnings"] = warn
    return out


# -------------------------------------------------------------------- start ---
def start_run(body: dict) -> dict:
    """Build an `upscale` command line and run it.

    The page never decides anything the command would decide. It assembles
    arguments and execs the binary - so what runs is what you would have typed,
    and there is one implementation of the rules.
    """
    if state():
        return {"ok": False, "error": "a run is already going"}
    pend = pending()
    src = (body.get("source") or pend.get("source") or "").strip()
    tgt = (body.get("target") or pend.get("target") or "").strip()
    book = devices()
    # A saved name is resolved to NAME=SPEC so the name travels with the
    # command and comes back in the log and the snapshot.
    wanted = body.get("devices") or ([body["host"]] if body.get("host") else [])
    devs = []
    for d in wanted:
        d = (d or "").strip()
        if not d:
            continue
        devs.append(f"{d}={book[d]['ssh']}" if d in book else d)
    devices_ = devs
    if not src or not tgt:
        return {"ok": False, "error": "source and target are required"}
    if not devices_:
        return {"ok": False, "error": "at least one device is required"}
    for d in (src, tgt):
        if not allowed(Path(d)):
            return {"ok": False, "error": f"not reachable: {d}"}
    argv = [config().get("upscale_bin") or "upscale", "--source", src, "--target", tgt]
    arch = (body.get("archive") or pend.get("archive") or "").strip()
    if body.get("delete"):
        argv.append("--delete")
    elif arch:
        if not allowed(Path(arch)):
            return {"ok": False, "error": f"not reachable: {arch}"}
        argv += ["--archive", arch]
    else:
        return {"ok": False, "error": "choose --archive or --delete: a finished file has to stop being a source"}
    for k, flag in (("size", "--size"), ("workers", "--workers"),
                    ("scratch", "--scratch"), ("model", "--model")):
        v = str(body.get(k) or "").strip()
        if v:
            argv += [flag, v]
    for d in devices_:
        argv += ["--device", d]
    RUN.mkdir(parents=True, exist_ok=True)
    try:
        log = open(RUN_LOG, "ab", buffering=0)
        # No shell: every argument - including an ssh spec with spaces in it -
        # is passed through as one word and never re-parsed.
        subprocess.Popen(argv, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                         start_new_session=True, cwd=str(Path.home()))
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "command": " ".join(shlex.quote(a) for a in argv)}


# ------------------------------------------------------------------- state ---
def state() -> dict:
    """The run's own snapshot: source, target, devices, and each device's file.

    Written by `upscale` itself, so there is no second opinion about what is
    running. Absent means nothing is running, which is a normal answer.
    """
    try:
        return json.loads(STATE_JSON.read_text())
    except (OSError, ValueError):
        return {}


def device_status(spec: str, expect: str) -> dict:
    """What this device says about itself, PASSED THROUGH UNCHANGED.

    The worker already emits phase, phase_percent, phase_done, phase_total,
    phase_unit, phase_elapsed_s, fps, eta_s and the rest. Renaming any of that
    on the way past is how the page came to ask for fields nothing produced and
    render a dash: the API answered correctly in a vocabulary only this file
    spoke. There is one shape, and the worker defines it.

    An unreachable device is reported as unreachable, never as idle.
    """
    d = {"device": spec, "ssh": spec, "file": expect, "reachable": False,
         "phase": "", "state": "", "paused": False, "episode": "", "error": "",
         "percent": 0, "phase_percent": -1, "rate": "", "eta": "",
         "queue_running": False, "queue_stopping": False, "queue_note": ""}
    rc, out, err = ssh_to(spec, f"$HOME/{WORKER} status", timeout=20)
    if rc != 0:
        d["error"] = (err or "ssh failed").strip()[:200]
        return d
    try:
        j = json.loads(out.strip() or "{}")
    except ValueError:
        return d
    d.update(j)                      # verbatim - no translation
    d["reachable"] = True
    d["file"] = expect
    d["paused"] = j.get("state") == "paused"
    return d


def rows(st: dict, devices: list) -> list:
    """One row per file: what is in the source directory, then the target.

    No numbering, no ownership rule, no archive probe. A file being worked on is
    the file a device says it has - and it says a name, because the name never
    left it.
    """
    # Each device owns a source directory, so a file's device is simply which
    # directory it is in. Nothing is assigned, guessed or claimed - reassigning
    # an episode is moving it, and the column can never credit the wrong box.
    lanes = [(d.get("device", ""), d.get("source", ""), d.get("target", ""))
             for d in devices if d.get("source")]
    if not lanes:
        lanes = [("", st.get("source", ""), st.get("target", ""))]
    # The driver's claim says which row it is working on. The WORKER says what
    # the GPU is actually on, by name, from its own file. While the driver is
    # waiting for a busy device those are different files, and only the second
    # one may lend a row its progress.
    busy = {d["file"]: d for d in devices if d.get("file")}
    working = {d.get("episode"): d for d in devices if d.get("episode")}
    out, n = [], 0

    def entry(path: Path, status: str, lsrc: str = "", ltgt: str = ""):
        nonlocal n
        n += 1
        d = busy.get(path.name)
        w = working.get(path.name)
        pct = 0
        if w and not d:
            d = w                      # the GPU is on it, whoever claimed it
        if d and w is None and d.get("phase") not in ("sending", "retrieving", "waiting"):
            # claimed, but the device is busy with something else
            d = {**d, "phase": "waiting", "done": 0, "total": 0, "percent": 0,
                 "fps": 0, "eta_s": 0, "rate": "", "eta": ""}
        if d:
            # during a transfer the percentage is the transfer's; during the
            # upscale it is the worker's frame count
            pct = d.get("percent") or (int(d["done"] * 100 / d["total"]) if d.get("total") else 0)
        base = dict(d) if d else {}
        base.pop("device", None); base.pop("ssh", None); base.pop("file", None)
        return {**base,
                "n": n, "name": path.name, "path": str(path),
                "library_name": Path(lsrc).name if lsrc else "",
                # Where it lands. A queued row sits in the source directory, so
                # its own path never shows the destination.
                "target_dir": "",
                "status": ("paused" if d.get("paused") else "running") if d else status,
                "device": d["device"] if d else "",
                "phase": d.get("phase", "") if d else "",
                "percent": pct,
                "size": path.stat().st_size if path.exists() else 0}

    seen = set()
    for owner, lsrc, ltgt in lanes:
        for base, status in ((lsrc, "queued"), (ltgt, "done")):
            if not base or (base, status) in seen:
                continue
            seen.add((base, status))
            p = Path(base)
            if not p.is_dir():
                continue
            for f in sorted(p.iterdir()):
                if f.is_file() and not f.name.startswith("."):
                    r = entry(f, status, lsrc, ltgt)
                    r["target_dir"] = ltgt
                    # a queued file belongs to the device whose directory holds it
                    if status == "queued" and not r.get("device"):
                        r["device"] = owner
                    out.append(r)
    return out


def collect() -> dict:
    book = devices()          # fetched before the local name shadows it
    st = state()
    entries = st.get("devices", [])
    # A device has a NAME and an ADDRESS, and they are not the same string.
    # Asking "rental" over ssh reaches nothing; the worker lives at its ssh spec.
    named = [(e.get("device", ""), e.get("ssh") or e.get("device", "")) for e in entries]
    lanes = {e.get("device", ""): (e.get("source", ""), e.get("target", "")) for e in entries}
    expect = {e.get("device", ""): e.get("file", "") for e in entries}
    driver = {e.get("device", ""): e.get("phase", "") for e in entries}
    xfer = {e.get("device", ""): e.get("xfer", "") for e in entries}
    devs = []
    if named:
        with ThreadPoolExecutor(max_workers=min(8, len(named))) as ex:
            devs = list(ex.map(
                lambda nx: {**device_status(nx[1], expect.get(nx[0], "")),
                            "device": nx[0], "ssh": nx[1],
                            "source": lanes.get(nx[0], ("", ""))[0],
                            "target": lanes.get(nx[0], ("", ""))[1],
                            # It is in the run's own snapshot, and that file
                            # exists only while the driver is alive - so this
                            # device is being worked, whatever the box is doing
                            # at this instant.
                            "queue_running": True,
                            "queue_stopping": (RUN / "stop").exists(),
                            "queue_note": driver.get(nx[0], "")}, named))
        # What the driver is doing wins: it is the one moving the file, and a
        # worker asked during a push still reports its previous phase.
        for dv in devs:
            # The driver only owns the phases it performs itself. While the
            # device is working, the worker knows better - it can say
            # "extracting" where the driver only knows "upscaling".
            dph = driver.get(dv["device"], "")
            if dph in ("sending", "retrieving") or not dv.get("phase"):
                dv["phase"] = dph or dv.get("phase", "")
            # A transfer is a phase like any other, and it is described in the
            # SAME vocabulary the worker uses - phase_percent, phase_done,
            # phase_unit, phase_elapsed_s - so the page needs no special case
            # and no second set of field names.
            #
            # rsync: "  199,185,400 100%   19.16MB/s    0:00:09"
            fields = (xfer.get(dv["device"]) or "").split()
            done = pct = elapsed = 0
            for f in fields:
                if f.replace(",", "").isdigit() and not done:
                    done = int(f.replace(",", ""))
                elif f.endswith("%") and f[:-1].isdigit():
                    pct = int(f[:-1])
                elif f.count(":") == 2:
                    h, m, sec = f.split(":")
                    if h.isdigit():
                        elapsed = int(h) * 3600 + int(m) * 60 + int(sec)
            if fields:
                dv["phase_percent"] = pct
                dv["phase_done"] = done
                dv["phase_total"] = int(done * 100 / pct) if pct else 0
                dv["phase_unit"] = "bytes"
                dv["phase_elapsed_s"] = elapsed
                dv["percent"] = pct
    if not devs:
        # Nothing running: the machines are still worth showing, so the page can
        # manage them and start a run against one.
        devs = [{"device": n, "ssh": m.get("ssh", ""), "id": n, "label": n,
                    "reachable": None, "phase": "", "file": "", "percent": 0,
                    "queue_running": False, "queue_stopping": False,
                    "queue_note": "", "error": "", "source": "", "target": "",
                    "done": 0, "total": 0, "unit": "", "fps": 0, "eta_s": 0,
                    "scratch": m.get("scratch", ""), "default_scratch": m.get("scratch", "")}
                   for n, m in book.items()]
    r = rows(st, devs)
    for dv in devs:
        meta = book.get(dv["device"], {})
        dv["id"] = dv["device"]
        dv["label"] = dv["device"]
        dv["scratch"] = meta.get("scratch", "")
        dv["default_scratch"] = meta.get("scratch", "")
    return {"source": st.get("source", ""), "target": st.get("target", ""),
            "pending": pending(),
            "size": st.get("size", 0), "running": bool(st),
            "devs": devs, "hosts": devs, "rows": r,
            "paused": any(x.get("paused") for x in devs),
            "counts": {s: sum(1 for x in r if x["status"] == s)
                       for s in ("done", "running", "paused", "queued")},
            "ts": int(time.time())}


# One collector, however many browsers. Each device poll is an ssh round trip,
# and the page refreshes every few seconds; done per request, N clients caused
# N sweeps and the sweeps piled up on each other.
_lock = threading.Lock()
_snapshot: dict = {}
_ready = threading.Event()


def snapshot_loop():
    global _snapshot
    while True:
        try:
            s = collect()
            with _lock:
                _snapshot = s
        except Exception as exc:                     # never kill the collector
            print(f"snapshot failed: {exc}", file=sys.stderr, flush=True)
        _ready.set()
        time.sleep(SNAPSHOT_INTERVAL)


def snapshot(wait: float = 20.0) -> dict:
    if not _ready.is_set():
        _ready.wait(wait)
    with _lock:
        s = dict(_snapshot)
    if s:
        s["age"] = max(0, int(time.time()) - s.get("ts", 0))
    return s


# ------------------------------------------------------------------ server ---
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control",
                         "no-store" if ctype.startswith("application/json") else "no-cache")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _static(self, path: str):
        rel = path.lstrip("/") or "index.html"
        target = (DIST / rel).resolve()
        if not str(target).startswith(str(DIST.resolve())) or not target.is_file():
            target = DIST / "index.html"
        if not target.is_file():
            return self._send(503, b"UI not built: cd web && pnpm build\n", "text/plain")
        ctype = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
                 ".json": "application/json", ".svg": "image/svg+xml",
                 ".png": "image/png", ".ico": "image/x-icon",
                 ".woff2": "font/woff2"}.get(target.suffix, "application/octet-stream")
        self._send(200, target.read_bytes(), ctype)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/api/queue", "/api/hosts"):
            return self._json(snapshot())
        if path == "/api/browse":
            from urllib.parse import parse_qs
            q = parse_qs(urlparse(self.path).query)
            if "q" in q:                       # typeahead, the page's own shape
                return self._json(search(q["q"][0]))
            return self._json(browse((q.get("path") or [""])[0]))
        if path == "/api/log":
            try:
                return self._json({"log": RUN_LOG.read_text(errors="replace")[-8000:]})
            except OSError:
                return self._json({"log": ""})
        if path == "/api/devices":
            return self._json({"devices": devices()})
        if path == "/api/health":
            return self._json({"ok": True, "state": str(STATE_JSON),
                               "running": bool(state())})
        return self._static(path)

    def _devices(self):
        return [d.get("device", "") for d in state().get("devices", []) if d.get("device")]

    def do_POST(self):
        path = urlparse(self.path).path
        # Pause and resume reach the worker on each device, which holds after the
        # current CHUNK - so a pause costs at most one chunk, not the episode.
        if path in ("/api/pause", "/api/resume"):
            action = path.rsplit("/", 1)[-1]
            devs = self._devices()
            if not devs:
                return self._json({"ok": False, "error": "nothing is running"}, 409)
            bad = []
            for spec in devs:
                rc, _, err = ssh_to(spec, f"$HOME/{WORKER} {action}", timeout=20)
                if rc != 0:
                    bad.append(f"{spec}: {(err or 'ssh failed').strip()[:80]}")
            if bad:
                return self._json({"ok": False, "error": "; ".join(bad)}, 502)
            return self._json({"ok": True, "action": action, "devices": len(devs)})
        if path in ("/api/hosts/probe", "/api/hosts/add", "/api/hosts/remove"):
            path = path.replace("/api/hosts/", "/api/devices/")
        if path in ("/api/devices/probe", "/api/devices/add", "/api/devices/remove"):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, OSError) as exc:
                return self._json({"ok": False, "error": str(exc)}, 400)
            if path.endswith("probe"):
                spec = (body.get("ssh") or "").strip()
                if not spec:
                    return self._json({"ok": False, "error": "ssh destination is required"}, 400)
                return self._json({"ok": True, "probe": probe(spec)})
            book = devices()
            if path.endswith("remove"):
                book.pop((body.get("name") or "").strip(), None)
                save_devices(book)
                return self._json({"ok": True, "devices": book})
            name = (body.get("name") or body.get("label") or "").strip()
            spec = (body.get("ssh") or "").strip()
            if not name or not spec:
                return self._json({"ok": False, "error": "name and ssh destination are required"}, 400)
            if not all(c.isalnum() or c in "._-" for c in name):
                return self._json({"ok": False, "error": "name may use letters, digits, dot, dash, underscore"}, 400)
            book[name] = {"ssh": spec, "scratch": (body.get("scratch") or "").strip(),
                          "workers": body.get("workers") or ""}
            save_devices(book)
            return self._json({"ok": True, "devices": book})
        if path in ("/api/import", "/api/remove", "/api/hold"):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, OSError) as exc:
                return self._json({"ok": False, "error": str(exc)}, 400)
            paths = [p for p in (body.get("paths") or []) if p]
            if path == "/api/import":
                # Importing now means: this is where the work is. A directory is
                # taken as-is; files are taken as the directory holding them,
                # because a run reads a directory, not a list.
                if not paths:
                    return self._json({"ok": False, "error": "nothing picked"}, 400)
                dirs = {str(Path(p) if Path(p).is_dir() else Path(p).parent) for p in paths}
                if len(dirs) > 1:
                    return self._json({"ok": False,
                                       "error": "pick inside one directory: a run has one source"}, 400)
                src = dirs.pop()
                if not allowed(Path(src)):
                    return self._json({"ok": False, "error": f"not reachable: {src}"}, 400)
                set_pending({"source": src})
                n_files = sum(1 for f in Path(src).iterdir()
                              if f.is_file() and not f.name.startswith("."))
                return self._json({"ok": True, "added": n_files, "source": src,
                                   "note": f"source is {src} ({n_files} files)"})
            # Hold and remove managed a stored work list. There is no list: what
            # is queued is what is in the source directory, so the honest answer
            # is to say so rather than pretend.
            return self._json({"ok": False, "error":
                               "there is no work list to edit — move files in or out of the "
                               "source directory instead"}, 409)
        if path == "/api/start":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, OSError) as exc:
                return self._json({"ok": False, "error": str(exc)}, 400)
            r = start_run(body)
            return self._json(r, 200 if r.get("ok") else 400)
        if path == "/api/stop":
            try:
                (RUN / "stop").touch()
            except OSError as exc:
                return self._json({"ok": False, "error": str(exc)}, 500)
            return self._json({"ok": True, "note": "stopping after the current file"})
        return self._json({"error": "not found"}, 404)


def main():
    cfg = config()
    port = int(os.environ.get("UPSCALE_UI_PORT") or cfg.get("port") or 8790)
    bind = os.environ.get("UPSCALE_UI_BIND") or cfg.get("bind") or "127.0.0.1"
    srv = ThreadingHTTPServer((bind, port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=snapshot_loop, name="snapshot", daemon=True).start()
    print(f"upscale-ui on http://{bind}:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
