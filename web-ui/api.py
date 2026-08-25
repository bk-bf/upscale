#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

HOME = Path(os.environ.get("UPSCALE_UI_DIR") or Path(__file__).resolve().parent)
STATE = Path(os.environ.get("UPSCALE_STATE") or Path.home() / ".upscale")
WORKER = os.environ.get("UPSCALE_WORKER", ".local/libexec/upscale-worker")

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
       "-o", "ControlPath=none"]
SNAPSHOT_INTERVAL = 5.0


def configure(home: Path | None = None, state: Path | None = None) -> None:
    global HOME, STATE
    if home is not None:
        HOME = Path(home)
    if state is not None:
        STATE = Path(state)


def run_dir() -> Path:
    return STATE / "run"


def state_json() -> Path:
    return run_dir() / "state.json"


def run_log() -> Path:
    return run_dir() / "run.log"


def devices_path() -> Path:
    return HOME / "devices.json"


def read_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def run(cmd, timeout=30):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 124, "", str(exc)


def ssh_to(spec: str, cmd: str, timeout=20):
    return run(SSH + shlex.split(spec) + [cmd], timeout=timeout)


def spawn(argv, log_path: Path, cwd: Path):
    log = open(log_path, "ab", buffering=0)
    return subprocess.Popen(argv, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                            start_new_session=True, cwd=str(cwd))


def config() -> dict:
    return read_json(HOME / "config.json", {})


def browse_roots() -> list:
    return [Path(p) for p in config().get("browse_roots", ["/mnt/media"])]


def allowed(p: Path) -> bool:
    try:
        r = p.resolve()
    except OSError:
        return False
    return any(r == root or root in r.parents for root in browse_roots())


def video_count(d: Path) -> int:
    try:
        return sum(1 for f in d.iterdir() if f.is_file() and not f.name.startswith("."))
    except OSError:
        return 0


def dir_count(d: Path) -> int:
    try:
        return sum(1 for f in d.iterdir() if f.is_dir() and not f.name.startswith("."))
    except OSError:
        return 0


# ------------------------------------------------------------------ routing ---
@dataclass(frozen=True)
class Route:
    method: str
    path: str
    fn: Callable
    accepts: frozenset


ROUTES: dict[tuple, Route] = {}


def route(method: str, path: str, accepts=()):
    def deco(fn):
        ROUTES[(method, path)] = Route(method, path, fn, frozenset(accepts))
        return fn
    return deco


def dispatch(method: str, path: str, body: dict | None = None,
             query: dict | None = None) -> tuple[int, dict]:
    r = ROUTES.get((method.upper(), path))
    if r is None:
        return 404, {"ok": False, "error": f"no route: {method.upper()} {path}"}
    unknown = sorted(set(body or {}) - r.accepts)
    if unknown:
        return 400, {"ok": False,
                     "error": f"{path} does not read {', '.join(unknown)}"}
    try:
        return r.fn(body or {}, query or {})
    except Exception as exc:
        return 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# ------------------------------------------------------------------- browse ---
@route("GET", "/api/browse")
def api_browse(body, query):
    q = (query.get("q") or "").strip()
    if not q:
        return 200, {"ok": True, "base": "", "results": [
            {"kind": "dir", "name": str(r), "path": str(r), "files": video_count(r),
             "dirs": dir_count(r)} for r in browse_roots()]}
    p = Path(q)
    base, frag = (p, "") if q.endswith("/") else (p.parent, p.name.lower())
    if not allowed(base) or not base.is_dir():
        return 200, {"ok": True, "base": str(base), "results": []}
    out = []
    try:
        for e in sorted(base.iterdir()):
            if e.name.startswith(".") or (frag and not e.name.lower().startswith(frag)):
                continue
            if e.is_dir():
                out.append({"kind": "dir", "name": e.name, "path": str(e),
                            "files": video_count(e), "dirs": dir_count(e)})
            elif e.is_file():
                out.append({"kind": "file", "name": e.name, "path": str(e),
                            "size": e.stat().st_size})
    except OSError as exc:
        return 200, {"ok": False, "error": str(exc), "base": str(base), "results": []}
    return 200, {"ok": True, "base": str(base), "results": out[:400]}


# ------------------------------------------------------------------ devices ---
def device_book() -> dict:
    return read_json(devices_path(), {})


def save_book(book: dict) -> None:
    devices_path().parent.mkdir(parents=True, exist_ok=True)
    tmp = devices_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(book, indent=2))
    tmp.replace(devices_path())


def device_list(book: dict | None = None) -> list:
    book = device_book() if book is None else book
    return [{"name": n, "ssh": m.get("ssh", ""), "scratch": m.get("scratch", ""),
             "workers": m.get("workers") or ""} for n, m in sorted(book.items())]


VALID_NAME = "._-"


@route("GET", "/api/devices")
def api_devices(body, query):
    return 200, {"ok": True, "devices": device_list()}


@route("POST", "/api/devices/probe", accepts=("ssh",))
def api_probe(body, query):
    spec = (body.get("ssh") or "").strip()
    if not spec:
        return 400, {"ok": False, "error": "ssh destination is required"}
    out = {"ok": True, "ssh": spec, "reachable": False, "host": "", "gpu": "",
           "cores": 0, "free_gb": 0, "worker": False, "mkvmerge": False,
           "warnings": [], "error": ""}
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
        return 200, out
    raw = {}
    for line in o.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            raw[k.strip()] = v.strip()
    out["reachable"] = True
    out["host"] = raw.get("host", "")
    out["gpu"] = raw.get("gpu", "")
    out["worker"] = raw.get("worker") == "yes"
    out["mkvmerge"] = raw.get("mkvmerge") == "yes"
    if (raw.get("free") or "").isdigit():
        out["free_gb"] = int(raw["free"])
    quota, _, period = (raw.get("cpumax") or "").partition(" ")
    if quota.isdigit() and period.isdigit() and int(period):
        out["cores"] = round(int(quota) / int(period), 1)
    elif (raw.get("nproc") or "").isdigit():
        out["cores"] = int(raw["nproc"])
    if not out["worker"]:
        out["warnings"].append("upscale-worker is not installed on it")
    if not out["mkvmerge"]:
        out["warnings"].append("mkvtoolnix is missing — results will fail in Jellyfin")
    return 200, out


@route("POST", "/api/devices/add", accepts=("name", "ssh", "scratch", "workers"))
def api_device_add(body, query):
    name = (body.get("name") or "").strip()
    spec = (body.get("ssh") or "").strip()
    if not name or not spec:
        return 400, {"ok": False, "error": "name and ssh destination are required"}
    if not all(c.isalnum() or c in VALID_NAME for c in name):
        return 400, {"ok": False,
                     "error": "name may use letters, digits, dot, dash, underscore"}
    book = device_book()
    book[name] = {"ssh": spec, "scratch": (body.get("scratch") or "").strip(),
                  "workers": body.get("workers") or ""}
    save_book(book)
    invalidate()
    return 200, {"ok": True, "added": name, "devices": device_list(book)}


@route("POST", "/api/devices/remove", accepts=("name",))
def api_device_remove(body, query):
    name = (body.get("name") or "").strip()
    if not name:
        return 400, {"ok": False, "error": "name is required"}
    book = device_book()
    if name not in book:
        return 404, {"ok": False, "error": f"no device named {name}",
                     "devices": device_list(book)}
    if name in running_device_names():
        return 409, {"ok": False, "error": f"{name} is in the running run — stop it first",
                     "devices": device_list(book)}
    del book[name]
    save_book(book)
    invalidate()
    return 200, {"ok": True, "removed": name, "devices": device_list(book)}


# --------------------------------------------------------------------- work ---
def pending() -> dict:
    return read_json(run_dir() / "pending.json", {})


def set_pending(d: dict) -> None:
    run_dir().mkdir(parents=True, exist_ok=True)
    (run_dir() / "pending.json").write_text(json.dumps({**pending(), **d}, indent=2))


@route("POST", "/api/import", accepts=("paths",))
def api_import(body, query):
    paths = [p for p in (body.get("paths") or []) if p]
    if not paths:
        return 400, {"ok": False, "error": "nothing picked"}
    dirs = {str(Path(p) if Path(p).is_dir() else Path(p).parent) for p in paths}
    if len(dirs) > 1:
        return 400, {"ok": False, "error": "pick inside one directory: a run has one source"}
    src = dirs.pop()
    if not allowed(Path(src)):
        return 400, {"ok": False, "error": f"not reachable: {src}"}
    set_pending({"source": src})
    n = video_count(Path(src))
    return 200, {"ok": True, "source": src, "files": n,
                 "note": f"source is {src} ({n} files)"}


@route("POST", "/api/pending", accepts=("source", "target", "archive", "delete"))
def api_pending(body, query):
    keep = {}
    for k in ("source", "target", "archive"):
        if k in body:
            v = (body.get(k) or "").strip()
            if v and not allowed(Path(v)):
                return 400, {"ok": False, "error": f"not reachable: {v}"}
            keep[k] = v
    if "delete" in body:
        keep["delete"] = bool(body.get("delete"))
    set_pending(keep)
    return 200, {"ok": True, "pending": pending()}


def state_snapshot() -> dict:
    return read_json(state_json(), {})


def running_device_names() -> list:
    return [d.get("device", "") for d in state_snapshot().get("devices", []) if d.get("device")]


def running_device_specs() -> list:
    return [(d.get("device", ""), d.get("ssh") or d.get("device", ""))
            for d in state_snapshot().get("devices", [])]


def build_start_argv(body: dict) -> tuple[list, str]:
    pend = pending()
    src = (body.get("source") or pend.get("source") or "").strip()
    tgt = (body.get("target") or pend.get("target") or "").strip()
    book = device_book()
    wanted = body.get("devices") or []
    if isinstance(wanted, str):
        wanted = [wanted]
    devs = []
    for d in wanted:
        d = (d or "").strip()
        if d:
            devs.append(f"{d}={book[d]['ssh']}" if d in book else d)
    if not src or not tgt:
        return [], "source and target are required"
    if not devs:
        return [], "at least one device is required"
    for d in (src, tgt):
        if not allowed(Path(d)):
            return [], f"not reachable: {d}"
    argv = [config().get("upscale_bin") or "upscale", "--source", src, "--target", tgt]
    arch = (body.get("archive") or pend.get("archive") or "").strip()
    delete = body.get("delete") if "delete" in body else pend.get("delete")
    if delete:
        argv.append("--delete")
    elif arch:
        if not allowed(Path(arch)):
            return [], f"not reachable: {arch}"
        argv += ["--archive", arch]
    else:
        return [], ("choose an archive directory or delete: "
                    "a finished file has to stop being a source")
    for k, flag in (("size", "--size"), ("workers", "--workers"),
                    ("scratch", "--scratch"), ("model", "--model")):
        v = str(body.get(k) or "").strip()
        if v:
            argv += [flag, v]
    for d in devs:
        argv += ["--device", d]
    return argv, ""


@route("POST", "/api/start", accepts=("devices", "source", "target", "archive",
                                      "delete", "size", "workers", "scratch",
                                      "model", "dry_run"))
def api_start(body, query):
    if state_snapshot():
        return 409, {"ok": False, "error": "a run is already going"}
    argv, why = build_start_argv(body)
    if why:
        return 400, {"ok": False, "error": why}
    line = " ".join(shlex.quote(a) for a in argv)
    if body.get("dry_run"):
        return 200, {"ok": True, "command": line, "note": f"would run: {line}"}
    run_dir().mkdir(parents=True, exist_ok=True)
    stop = run_dir() / "stop"
    if stop.exists():
        stop.unlink()
    try:
        spawn(argv, run_log(), Path.home())
    except OSError as exc:
        return 500, {"ok": False, "error": str(exc)}
    invalidate()
    return 200, {"ok": True, "command": line, "note": f"started: {line}"}


@route("POST", "/api/stop")
def api_stop(body, query):
    if not state_snapshot():
        return 409, {"ok": False, "error": "nothing is running"}
    run_dir().mkdir(parents=True, exist_ok=True)
    (run_dir() / "stop").touch()
    invalidate()
    return 200, {"ok": True, "note": "stopping after the current file"}


@route("POST", "/api/pause")
def api_pause(body, query):
    return worker_signal("pause")


@route("POST", "/api/resume")
def api_resume(body, query):
    return worker_signal("resume")


def worker_signal(action: str):
    devs = running_device_specs()
    if not devs:
        return 409, {"ok": False, "error": "nothing is running"}
    bad = []
    for name, spec in devs:
        rc, _, err = ssh_to(spec, f"$HOME/{WORKER} {action}", timeout=20)
        if rc != 0:
            bad.append(f"{name}: {(err or 'ssh failed').strip()[:80]}")
    if bad:
        return 502, {"ok": False, "error": "; ".join(bad)}
    invalidate()
    return 200, {"ok": True, "note": f"{action}d {len(devs)} device(s)"}


@route("GET", "/api/log")
def api_log(body, query):
    try:
        return 200, {"ok": True, "log": run_log().read_text(errors="replace")[-8000:]}
    except OSError:
        return 200, {"ok": True, "log": ""}


@route("GET", "/api/health")
def api_health(body, query):
    return 200, {"ok": True, "state": str(state_json()), "running": bool(state_snapshot()),
                 "routes": sorted(f"{m} {p}" for m, p in ROUTES)}


# -------------------------------------------------------------------- queue ---
def device_status(spec: str, expect: str) -> dict:
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
    d.update(j)
    d["reachable"] = True
    d["file"] = expect
    d["paused"] = j.get("state") == "paused"
    return d


EP_RE = re.compile(r"[Ss](\d+)[Ee](\d+)")


def episode_key(name: str):
    m = EP_RE.search(name)
    return (int(m.group(1)), int(m.group(2))) if m else None


def rows(st: dict, devices: list) -> list:
    lanes = [(d.get("device", ""), d.get("source", ""), d.get("target", ""))
             for d in devices if d.get("source")]
    if not lanes:
        lanes = [("", st.get("source", ""), st.get("target", ""))]
    busy = {d["file"]: d for d in devices if d.get("file")}
    working = {d.get("episode"): d for d in devices if d.get("episode")}
    out, n = [], 0

    def entry(path: Path, status: str, lsrc: str = ""):
        nonlocal n
        n += 1
        ek = episode_key(path.name)
        d = busy.get(path.name)
        w = working.get(path.name)
        pct = 0
        if w and not d:
            d = w
        if d and w is None and d.get("phase") not in ("sending", "retrieving", "waiting"):
            d = {**d, "phase": "waiting", "done": 0, "total": 0, "percent": 0,
                 "fps": 0, "eta_s": 0, "rate": "", "eta": ""}
        if d:
            pct = d.get("percent") or (int(d["done"] * 100 / d["total"]) if d.get("total") else 0)
        base = dict(d) if d else {}
        for k in ("device", "ssh", "file"):
            base.pop(k, None)
        return {**base,
                "n": ek[1] if ek else n,
                "_sort": ek or (99, 9999 + n),
                "name": path.name, "path": str(path),
                "library_name": Path(lsrc).name if lsrc else "",
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
                    r = entry(f, status, lsrc)
                    r["target_dir"] = ltgt
                    if status == "queued" and not r.get("device"):
                        r["device"] = owner
                    out.append(r)
    out.sort(key=lambda r: (r["_sort"], r["name"]))
    for r in out:
        r.pop("_sort", None)
    return out


def parse_rsync(line: str) -> dict:
    fields = (line or "").split()
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
    if not fields:
        return {}
    return {"phase_percent": pct, "phase_done": done,
            "phase_total": int(done * 100 / pct) if pct else 0,
            "phase_unit": "bytes", "phase_elapsed_s": elapsed, "percent": pct}


def collect() -> dict:
    book = device_book()
    st = state_snapshot()
    entries = st.get("devices", [])
    named = [(e.get("device", ""), e.get("ssh") or e.get("device", "")) for e in entries]
    lanes = {e.get("device", ""): (e.get("source", ""), e.get("target", "")) for e in entries}
    expect = {e.get("device", ""): e.get("file", "") for e in entries}
    driver = {e.get("device", ""): e.get("phase", "") for e in entries}
    xfer = {e.get("device", ""): e.get("xfer", "") for e in entries}
    stopping = (run_dir() / "stop").exists()
    devs = []
    if named:
        with ThreadPoolExecutor(max_workers=min(8, len(named))) as ex:
            devs = list(ex.map(
                lambda nx: {**device_status(nx[1], expect.get(nx[0], "")),
                            "device": nx[0], "ssh": nx[1],
                            "source": lanes.get(nx[0], ("", ""))[0],
                            "target": lanes.get(nx[0], ("", ""))[1],
                            "queue_running": True,
                            "queue_stopping": stopping,
                            "queue_note": driver.get(nx[0], "")}, named))
        for dv in devs:
            dph = driver.get(dv["device"], "")
            if dph in ("sending", "retrieving") or not dv.get("phase"):
                dv["phase"] = dph or dv.get("phase", "")
            dv.update(parse_rsync(xfer.get(dv["device"]) or ""))
    known = {d["device"] for d in devs}
    for name, meta in sorted(book.items()):
        if name in known:
            continue
        devs.append({"device": name, "ssh": meta.get("ssh", ""),
                     "reachable": None, "phase": "", "state": "", "file": "",
                     "percent": 0, "phase_percent": -1,
                     "queue_running": False, "queue_stopping": False,
                     "queue_note": "", "error": "", "source": "", "target": "",
                     "done": 0, "total": 0, "unit": "", "fps": 0, "eta_s": 0})
    r = rows(st, devs)
    for dv in devs:
        meta = book.get(dv["device"], {})
        dv["id"] = dv["device"]
        dv["name"] = dv["device"]
        dv["label"] = dv["device"]
        dv["scratch"] = meta.get("scratch", "")
        dv["known"] = dv["device"] in book
    return {"ok": True,
            "source": st.get("source", ""), "target": st.get("target", ""),
            "pending": pending(), "size": st.get("size", 0), "running": bool(st),
            "stopping": stopping,
            "devices": devs, "rows": r,
            "paused": any(x.get("paused") for x in devs),
            "counts": {s: sum(1 for x in r if x["status"] == s)
                       for s in ("done", "running", "paused", "queued")},
            "ts": int(time.time())}


_lock = threading.Lock()
_snapshot: dict = {}
_ready = threading.Event()
_wake = threading.Event()


def invalidate() -> None:
    _wake.set()


def snapshot_loop(log=print):
    global _snapshot
    while True:
        try:
            s = collect()
            with _lock:
                _snapshot = s
        except Exception as exc:
            log(f"snapshot failed: {exc}")
        _ready.set()
        _wake.wait(SNAPSHOT_INTERVAL)
        _wake.clear()


@route("GET", "/api/queue")
def api_queue(body, query):
    if not _ready.is_set():
        if not _ready.wait(20.0):
            return 200, collect()
    with _lock:
        s = dict(_snapshot)
    if s:
        s["age"] = max(0, int(time.time()) - s.get("ts", 0))
    return 200, s or collect()
