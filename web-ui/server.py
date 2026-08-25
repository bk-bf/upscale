#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import api

DIST = Path(__file__).resolve().parent / "web" / "dist"

CTYPE = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
         ".json": "application/json", ".svg": "image/svg+xml",
         ".png": "image/png", ".ico": "image/x-icon", ".woff2": "font/woff2"}


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

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n) or b"{}")

    def _static(self, path: str):
        rel = path.lstrip("/") or "index.html"
        target = (DIST / rel).resolve()
        if not str(target).startswith(str(DIST.resolve())) or not target.is_file():
            target = DIST / "index.html"
        if not target.is_file():
            return self._send(503, b"UI not built: cd web && pnpm build\n", "text/plain")
        self._send(200, target.read_bytes(),
                   CTYPE.get(target.suffix, "application/octet-stream"))

    def _api(self, method: str, path: str, query: dict, body):
        code, obj = api.dispatch(method, path, body, query)
        self._json(code, obj)

    def do_GET(self):
        u = urlparse(self.path)
        if not u.path.startswith("/api/"):
            return self._static(u.path)
        query = {k: v[0] for k, v in parse_qs(u.query).items()}
        self._api("GET", u.path, query, {})

    def do_POST(self):
        u = urlparse(self.path)
        try:
            body = self._body()
        except (ValueError, OSError) as exc:
            return self._json(400, {"ok": False, "error": f"bad request body: {exc}"})
        if not isinstance(body, dict):
            return self._json(400, {"ok": False, "error": "body must be a JSON object"})
        self._api("POST", u.path, {k: v[0] for k, v in parse_qs(u.query).items()}, body)


def main():
    cfg = api.config()
    port = int(os.environ.get("UPSCALE_UI_PORT") or cfg.get("port") or 8790)
    bind = os.environ.get("UPSCALE_UI_BIND") or cfg.get("bind") or "127.0.0.1"
    srv = ThreadingHTTPServer((bind, port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=api.snapshot_loop, name="snapshot", daemon=True,
                     kwargs={"log": lambda m: print(m, file=sys.stderr, flush=True)}).start()
    print(f"upscale-ui on http://{bind}:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
