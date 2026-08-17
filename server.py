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
def start_job(cfg: dict, host: str, lib: str, rng: str, scratch: str) -> dict:
    h = (cfg.get("hosts") or {}).get(host)
    if not h:
        return {"ok": False, "error": f"unknown host {host!r}"}
    libs = {l["path"]: l for l in libraries(cfg)}
    if lib not in libs:
        return {"ok": False, "error": f"unknown library {lib!r}"}
    scratches = h.get("scratch") or {}
    work = scratches.get(scratch)
    if scratch and not work:
        return {"ok": False, "error": f"unknown scratch {scratch!r} for {host}"}

    st = host_status(host, h)
    if st.get("state") in ("running", "paused", "stopping"):
        return {"ok": False, "error": f"{host} is already on {st.get('episode') or 'a job'}"}

    up = h.get("upscale") or "upscale"
    env = f"SRC_EXT={shlex.quote(src_ext_for(cfg, Path(lib).name))}"
    if work:
        env += f" WORK={shlex.quote(work)}"
    # setsid+nohup: this must outlive the ssh connection, the browser, and this
    # service. The pipeline is a 24-hour job; a web request is not its lifetime.
    remote = (f"setsid nohup env {env} {shlex.quote(up)} ep {shlex.quote(rng)} "
              f"ubuntu:{shlex.quote(lib)} >> ~/upscale-ui.log 2>&1 < /dev/null & echo started")
    rc, out, err = ssh_to(h.get("ssh", host), remote, timeout=25)
    if rc != 0:
        return {"ok": False, "error": (err or f"ssh exited {rc}").strip()[:300]}
    return {"ok": True, "started": out.strip(), "host": host, "library": lib,
            "range": rng, "work": work or "(host default)"}


def control(cfg: dict, host: str, action: str) -> dict:
    h = (cfg.get("hosts") or {}).get(host)
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
            hosts = cfg.get("hosts") or {}
            out = []
            for name, h in hosts.items():
                st = host_status(name, h)
                if st.get("reachable"):
                    st.update(host_running(h, name))
                st["scratch"] = h.get("scratch", {})
                st["default_scratch"] = h.get("default_scratch")
                out.append(st)
            return self._json({"hosts": out, "ts": int(time.time())})
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
            return self._json(start_job(cfg, body.get("host", ""), body.get("library", ""),
                                        body.get("range", "any") or "any", body.get("scratch", "")))
        if path in ("/api/pause", "/api/resume", "/api/stop"):
            return self._json(control(cfg, body.get("host", ""), path.rsplit("/", 1)[1]))
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
