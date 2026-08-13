#!/usr/bin/env python3
"""Run ONE Claude session for the research loop, and expose it in the web UI.

    session.py --prompt-file P --project D [--title T] [--tag loop] [--model opus]

The division of labour this file exists to enforce:

  * the SCRIPTS own the session — starting it, how long it may live, what
    happens when it ends. That is `loop.sh`.
  * `mon` only EXPOSES it. Its store is the surface the dashboard renders from,
    so writing an incident and a transcript there is enough to make a session
    readable from a phone, tagged as a loop, with the elapsed timer running.

mon used to own this, and could not: it caps every session's wall clock with a
timer started once and fired unconditionally, so an eight-hour loop inside one
mon session was never possible. Nothing here imposes a timeout. The guard ends
the night; a session ends when the work does.

Exit code is the session's own. It never raises on a bad stream line — a
malformed event should cost one transcript entry, not the night.
"""
import argparse, json, os, subprocess, sys, time, uuid
from pathlib import Path

STATE = Path(os.path.expanduser("~/.local/state/mon"))
INCIDENTS, TRANSCRIPTS = STATE / "incidents", STATE / "transcripts"


def turns(ev):
    """A stream event reduced to what is worth watching, in order.

    Every block, not the first that matches: an assistant message routinely
    carries reasoning, a sentence about what it is about to do, then the call
    itself, and returning early throws away whichever came last.
    """
    out, kind = [], ev.get("type")
    if kind in ("assistant", "user"):
        for b in (ev.get("message") or {}).get("content") or []:
            t = b.get("type")
            if t == "thinking" and (b.get("thinking") or "").strip():
                out.append({"role": "thinking", "text": b["thinking"]})
            elif t == "text" and (b.get("text") or "").strip():
                out.append({"role": "claude", "text": b["text"]})
            elif t == "tool_use":
                name, arg = b.get("name") or "", b.get("input") or {}
                detail = str(arg.get("command") or arg.get("file_path")
                             or arg.get("pattern") or "")[:160]
                out.append({"role": "tool", "name": name,
                            "text": f"{name} {detail}".strip()})
            elif t == "tool_result":
                body = b.get("content")
                if isinstance(body, list):
                    body = " ".join(x.get("text", "") for x in body if isinstance(x, dict))
                body = str(body or "").strip()
                out.append({"role": "output", "text": body[:900],
                            "clipped": len(body) > 900,
                            "failed": bool(b.get("is_error"))})
    elif kind == "result":
        out.append({"role": "result", "text": str(ev.get("result") or "")})
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--prompt-file", required=True)
    p.add_argument("--project", required=True)
    p.add_argument("--title", default="research loop")
    p.add_argument("--tag", default="loop")
    p.add_argument("--by", default="kirill")
    p.add_argument("--model", default="opus")
    p.add_argument("--mode", default="acceptEdits")
    a = p.parse_args()

    prompt = Path(a.prompt_file).read_text()
    started = time.time()
    inc_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:12]}"
    INCIDENTS.mkdir(parents=True, exist_ok=True)
    TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
    inc_path = INCIDENTS / f"{inc_id}.json"
    tr_path = TRANSCRIPTS / f"{inc_id}.jsonl"

    # The shape mon writes, because the dashboard reads exactly this. `ts` and
    # `status` are what drive the elapsed timer on the card: it ticks while the
    # status is "diagnosing" and freezes at the last turn once it is not.
    inc = {"id": inc_id, "kind": "session", "ts": started,
           "project": str(Path(a.project).resolve()), "prompt": prompt,
           "mode": a.mode, "by": a.by, "tag": a.tag, "monitor": a.title,
           "source_type": "session", "source_target": a.by,
           "line": a.title, "count": 1, "context": [],
           "status": "diagnosing", "pid": os.getpid()}

    def save():
        tmp = inc_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(inc))
        tmp.replace(inc_path)

    def append(event):
        with tr_path.open("a") as fh:
            fh.write(json.dumps(event) + "\n")

    save()
    append({"role": "you", "ts": started, "text": prompt})
    print(inc_id, flush=True)

    exe = os.path.expanduser("~/.local/bin/claude")
    cmd = [exe, "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--permission-mode", a.mode, "--model", a.model]

    session_id, last_text, rc = None, "", 1
    try:
        proc = subprocess.Popen(cmd, cwd=a.project, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, bufsize=1)
    except OSError as e:
        inc["status"] = "error"
        inc["result"] = {"error": f"cannot execute {exe}: {e}"}
        save()
        return 1

    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            session_id = session_id or ev.get("session_id")
            for t in turns(ev):
                t["ts"] = time.time()
                append(t)
                if t["role"] in ("claude", "result"):
                    last_text = t["text"]
        rc = proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        rc = 130
    finally:
        err = (proc.stderr.read() or "").strip() if proc.stderr else ""
        inc["status"] = "diagnosed" if rc == 0 else "error"
        inc["result"] = {"took_sec": round(time.time() - started, 1),
                         "session_id": session_id,
                         "summary": last_text[:400],
                         "raw": last_text}
        if rc != 0:
            inc["result"]["error"] = (err or f"session exited {rc}")[:400]
        save()
    return rc


if __name__ == "__main__":
    sys.exit(main())
