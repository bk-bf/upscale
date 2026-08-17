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
    return s


def _save_state(s: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1))
    tmp.replace(STATE_FILE)          # atomic: a torn state file would lose holds


def _set_for(key: str, lib: str) -> set[int]:
    return {int(n) for n in (_state().get(key, {}).get(lib) or [])}


def held_for(lib: str) -> set[int]:
    return _set_for("held", lib)


def removed_for(lib: str) -> set[int]:
    return _set_for("removed", lib)


def mutate_set(key: str, lib: str, episodes: list[int], add: bool) -> set[int]:
    with _state_lock:
        s = _state()
        cur = {int(n) for n in (s.setdefault(key, {}).get(lib) or [])}
        cur = (cur | set(episodes)) if add else (cur - set(episodes))
        s[key][lib] = sorted(cur)
        _save_state(s)
        return cur


def set_held(lib: str, episodes: list[int], hold: bool) -> set[int]:
    return mutate_set("held", lib, episodes, hold)


def imports() -> list[str]:
    return list(_state().get("imports") or [])


def add_import(path: str) -> list[str]:
    with _state_lock:
        s = _state()
        if path not in s["imports"]:
            s["imports"].append(path)
        _save_state(s)
        return list(s["imports"])


def drop_import(path: str) -> list[str]:
    """Remove a library and forget its holds and deletions with it.

    Leaving them behind means re-importing a library silently reinstates
    decisions made who-knows-when, which is worse than starting clean.
    """
    with _state_lock:
        s = _state()
        s["imports"] = [p for p in s["imports"] if p != path]
        s["held"].pop(path, None)
        s["removed"].pop(path, None)
        _save_state(s)
        return list(s["imports"])


def browse(cfg: dict, q: str) -> list[dict]:
    """Folders under media_root matching q, for the import autocomplete."""
    root = Path(cfg.get("media_root", "/mnt/media/tv"))
    q = (q or "").strip().lower()
    already = set(imports())
    out = []
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if q and q not in d.name.lower():
            continue
        n = sum(1 for p in d.rglob("*")
                if p.suffix.lower() in VIDEO_EXT and ".upscale-originals" not in p.parts)
        if not n:
            continue
        out.append({"name": d.name, "path": str(d), "files": n,
                    "imported": str(d) in already})
        if len(out) >= 40:
            break
    return out


EP_RE = __import__("re").compile(r"[Ss](\d+)[Ee](\d+)")


def ep_num(name: str) -> int | None:
    m = EP_RE.search(name)
    return int(m.group(2)) if m else None


def queue_rows(cfg: dict, lib: str, host_states: list[dict]) -> dict:
    """One row per episode: what is done, held, running, or waiting.

    Done is read from the ARCHIVE, because that is what "done" means to this
    pipeline - the original is moved aside once its upscale is published.
    """
    out = outstanding(cfg, lib)
    if out.get("error"):
        return {"error": out["error"], "rows": []}
    held, gone = held_for(lib), removed_for(lib)
    running = {}
    for h in host_states:
        epname = (h.get("episode") or "")
        if epname and h.get("state") in ("running", "working", "paused", "stopping"):
            running[epname] = h

    rows = []
    arch = Path(lib) / ".upscale-originals"
    done_names = {p.name for p in arch.rglob("*") if p.suffix.lower() in VIDEO_EXT} if arch.is_dir() else set()
    for p in sorted(done_names):
        n = ep_num(p)
        rows.append({"n": n, "name": p, "status": "done", "path": ""})
    for e in out["episodes"]:
        n = ep_num(e["name"])
        h = running.get(e["name"])
        # Deleted episodes leave the queue entirely - that is the difference
        # between delete and hold, and why they are separate sets.
        if n is not None and n in gone and not h:
            continue
        if h:
            status = "paused" if h.get("state") == "paused" else "running"
        elif n is not None and n in held:
            status = "held"
        else:
            status = "queued"
        row = {"n": n, "name": e["name"], "path": e["path"], "status": status}
        if h:
            row.update({"percent": h.get("percent", 0), "fps": h.get("fps", 0),
                        "eta_s": h.get("eta_s", 0), "phase": h.get("phase", ""),
                        "host": h.get("id", "")})
        rows.append(row)
    rows.sort(key=lambda r: (r["n"] is None, r["n"] if r["n"] is not None else 0, r["name"]))
    return {"rows": rows, "held": sorted(held),
            "counts": {s: sum(1 for r in rows if r["status"] == s)
                       for s in ("done", "running", "paused", "held", "queued")}}


def range_expr(cfg: dict, lib: str) -> str:
    """The episode set to actually run: outstanding MINUS held.

    Passed to `upscale ep` as an explicit comma list rather than `any`, so a
    hold is honoured by the pipeline itself instead of being a UI-only fiction.
    """
    out = outstanding(cfg, lib)
    skip = held_for(lib) | removed_for(lib)
    nums = sorted({n for e in out.get("episodes", [])
                   if (n := ep_num(e["name"])) is not None and n not in skip})
    return ",".join(str(n) for n in nums)


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
    h = (cfg.get("hosts") or {}).get(host)
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
        if path == "/api/browse":
            return self._json({"results": browse(cfg, (q.get("q") or [""])[0])})
        if path == "/api/queue":
            hosts = cfg.get("hosts") or {}
            states = []
            for name, h in hosts.items():
                st = host_status(name, h)
                if st.get("reachable"):
                    st.update(host_running(h, name))
                states.append(st)
            # One table across every imported library, so the queue is the queue
            # rather than a per-library view you have to remember to switch.
            libs, rows, counts = [], [], {}
            for lib in imports():
                qr = queue_rows(cfg, lib, states)
                name = Path(lib).name
                libs.append({"path": lib, "name": name, "error": qr.get("error"),
                             "run_range": range_expr(cfg, lib) if not qr.get("error") else "",
                             "counts": qr.get("counts", {})})
                for r in qr.get("rows", []):
                    rows.append({**r, "library": lib, "library_name": name})
                for k, v in (qr.get("counts") or {}).items():
                    counts[k] = counts.get(k, 0) + v
            return self._json({"libraries": libs, "rows": rows, "counts": counts,
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
            lib = body.get("library", "")
            # An explicit episode list, not "any": that is how a hold reaches the
            # pipeline instead of being a label this page draws on top of it.
            rng = body.get("range") or (range_expr(cfg, lib) if lib else "") or "any"
            return self._json(start_job(cfg, body.get("host", ""), lib, rng, body.get("scratch", "")))
        if path == "/api/hold":
            lib = body.get("library", "")
            eps = [int(n) for n in (body.get("episodes") or []) if str(n).isdigit()]
            hold = bool(body.get("hold", True))
            if not lib or not eps:
                return self._json({"ok": False, "error": "library and episodes are required"}, 400)
            # A RUNNING episode cannot be held - it is already on the GPU. qBit
            # behaves the same way: pausing the active item stops the transfer,
            # which here means pausing its host.
            held = set_held(lib, eps, hold)
            return self._json({"ok": True, "held": sorted(held),
                               "run_range": range_expr(cfg, lib)})
        if path in ("/api/pause", "/api/resume", "/api/stop"):
            return self._json(control(cfg, body.get("host", ""), path.rsplit("/", 1)[1]))
        if path == "/api/abort":
            return self._json(abort_host(cfg, body.get("host", "")))
        if path == "/api/import":
            p = (body.get("path") or "").rstrip("/")
            root = str(Path(cfg.get("media_root", "/mnt/media/tv")))
            # Only inside media_root, and only somewhere that exists: this value
            # reaches `upscale --list` as LIB.
            if not p or not p.startswith(root + "/") or not Path(p).is_dir():
                return self._json({"ok": False, "error": f"not a library under {root}"}, 400)
            return self._json({"ok": True, "imports": add_import(p)})
        if path == "/api/unimport":
            return self._json({"ok": True, "imports": drop_import((body.get("path") or "").rstrip("/"))})
        if path == "/api/remove":
            lib = body.get("library", "")
            eps = [int(n) for n in (body.get("episodes") or []) if str(n).isdigit()]
            if not lib or not eps:
                return self._json({"ok": False, "error": "library and episodes are required"}, 400)
            # Delete removes from the QUEUE, never from disk. Nothing here
            # deletes media; the source stays exactly where it is.
            gone = mutate_set("removed", lib, eps, bool(body.get("remove", True)))
            return self._json({"ok": True, "removed": sorted(gone),
                               "run_range": range_expr(cfg, lib)})
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
