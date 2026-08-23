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
    """What this device says about itself.

    An unreachable device is reported as unreachable. It is never rendered as
    idle: an idle-looking panel for a box that is actually grinding is worse
    than an error.
    """
    d = {"device": spec, "file": expect, "reachable": False, "phase": "",
         "done": 0, "total": 0, "unit": "", "fps": 0.0, "eta_s": 0}
    rc, out, _ = ssh_to(spec, f"$HOME/{WORKER} status", timeout=20)
    if rc != 0:
        return d
    d["reachable"] = True
    try:
        j = json.loads(out.strip() or "{}")
    except ValueError:
        return d
    d.update({k: j.get(k, d[k]) for k in
              ("phase", "done", "total", "unit", "fps", "eta_s") if k in j})
    for a, b in (("frames_done", "done"), ("frames_total", "total")):
        if a in j and not d[b]:
            d[b] = j[a]
    return d


def rows(st: dict, devices: list) -> list:
    """One row per file: what is in the source directory, then the target.

    No numbering, no ownership rule, no archive probe. A file being worked on is
    the file a device says it has - and it says a name, because the name never
    left it.
    """
    src, tgt = st.get("source"), st.get("target")
    busy = {d["file"]: d for d in devices if d.get("file")}
    out, n = [], 0

    def entry(path: Path, status: str):
        nonlocal n
        n += 1
        d = busy.get(path.name)
        pct = 0
        if d and d.get("total"):
            pct = int(d["done"] * 100 / d["total"])
        return {"n": n, "name": path.name, "path": str(path),
                "library_name": Path(src).name if src else "",
                "status": "running" if d else status,
                "device": d["device"] if d else "",
                "phase": d.get("phase", "") if d else "",
                "percent": pct,
                "done": d.get("done", 0) if d else 0,
                "total": d.get("total", 0) if d else 0,
                "unit": d.get("unit", "") if d else "",
                "fps": d.get("fps", 0) if d else 0,
                "eta_s": d.get("eta_s", 0) if d else 0,
                "size": path.stat().st_size if path.exists() else 0}

    for base, status in ((src, "queued"), (tgt, "done")):
        if not base:
            continue
        p = Path(base)
        if not p.is_dir():
            continue
        for f in sorted(p.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                out.append(entry(f, status))
    return out


def collect() -> dict:
    st = state()
    specs = [d.get("device", "") for d in st.get("devices", [])]
    expect = {d.get("device", ""): d.get("file", "") for d in st.get("devices", [])}
    devices = []
    if specs:
        with ThreadPoolExecutor(max_workers=min(8, len(specs))) as ex:
            devices = list(ex.map(lambda s: device_status(s, expect.get(s, "")), specs))
    r = rows(st, devices)
    return {"source": st.get("source", ""), "target": st.get("target", ""),
            "size": st.get("size", 0), "running": bool(st),
            "devices": devices, "hosts": devices, "rows": r,
            "counts": {s: sum(1 for x in r if x["status"] == s)
                       for s in ("done", "running", "queued")},
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
        if path == "/api/health":
            return self._json({"ok": True, "state": str(STATE_JSON),
                               "running": bool(state())})
        return self._static(path)

    def do_POST(self):
        path = urlparse(self.path).path
        # The only control worth exposing. Everything else about a run is
        # decided by the command that started it.
        if path == "/api/stop":
            try:
                (RUN / "stop").touch()
            except OSError as exc:
                return self._json({"ok": False, "error": str(exc)}, 500)
            return self._json({"ok": True, "note": "stopping after the current file"})
        return self._json({"error": "not found"}, 404)


def config() -> dict:
    try:
        return json.loads((HERE / "config.json").read_text())
    except (OSError, ValueError):
        return {}


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
