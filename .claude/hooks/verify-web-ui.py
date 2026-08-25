#!/usr/bin/env python3
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WEB_UI = REPO / "web-ui"
TESTS = WEB_UI / "test_api.py"
WATCHED = ("api.py", "server.py", "uictl", "test_api.py")
SKIP = ("/dist/", "/node_modules/", "/.svelte-kit/")


def touches_web_ui(path: str) -> bool:
    if not path or any(s in path for s in SKIP):
        return False
    p = Path(path)
    if WEB_UI not in p.parents and p.parent != WEB_UI:
        return False
    return p.name in WATCHED or p.suffix == ".svelte"


def run_tests():
    return subprocess.run([sys.executable, str(TESTS)], cwd=str(WEB_UI),
                          capture_output=True, text=True)


def stale_build():
    dist = WEB_UI / "web" / "dist" / "index.html"
    if not dist.is_file():
        return "web-ui/web/dist is missing"
    cut = dist.stat().st_mtime
    srcs = [WEB_UI / "api.py", WEB_UI / "server.py", *(WEB_UI / "web" / "src").rglob("*")]
    newer = [p for p in srcs if p.is_file() and p.stat().st_mtime > cut]
    return f"web/dist is older than {newer[0].relative_to(REPO)}" if newer else ""


def block(msg):
    print(msg, file=sys.stderr)
    sys.exit(2)


def main():
    try:
        ev = json.load(sys.stdin)
    except (ValueError, OSError):
        return
    tool = ev.get("tool_name", "")
    ti = ev.get("tool_input", {}) or {}

    if tool == "Bash":
        cmd = ti.get("command", "")
        if not re.search(r"(?:^|[;&|]\s*|\n\s*)git\s+(?:commit|push)\b", cmd):
            return
        if str(REPO) not in str(ev.get("cwd", REPO)):
            return
        r = run_tests()
        if r.returncode != 0:
            block("upscale/CLAUDE.md: web-ui/test_api.py fails — not committable.\n\n"
                  + r.stderr[-3000:])
        stale = stale_build()
        if stale:
            block(f"upscale/CLAUDE.md: {stale}.\n"
                  "Run web-ui/check, then node web-ui/shot.mjs and read the PNGs.")
        return

    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        if not touches_web_ui(ti.get("file_path", "")):
            return
        r = run_tests()
        if r.returncode != 0:
            tail = (r.stderr or r.stdout).strip().splitlines()
            block("web-ui/test_api.py fails after this edit:\n\n"
                  + "\n".join(tail[-25:])
                  + "\n\nFinish the change, then run web-ui/check.")


if __name__ == "__main__":
    main()
