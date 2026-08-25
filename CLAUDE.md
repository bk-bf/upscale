# upscale

@AGENTS.md

Two halves, verified differently.

| | What it is | How a change is verified |
|---|---|---|
| `upscale`, `libexec/upscale-worker` | the pipeline | a real run against a real device |
| `web-ui/` | the page and its API | `web-ui/check`, then a screenshot |

Nothing here is finished on the strength of reading the code back.

## Changing anything in `web-ui/`

Run this sequence, in this order, every time. Do not report the work as done
until step 5 has produced a PNG you opened.

```bash
web-ui/check                                    # 1-3: tests, build, restart
mkdir -p /tmp/upscale-ui && node web-ui/shot.mjs # 4: main, machines, start, source
```

1. **`python3 web-ui/test_api.py`** — 33 stdlib tests, no ssh and no GPU.
   `check` runs it first and stops on failure.
2. **`cd web-ui/web && pnpm build`** — the service serves `web/dist`, so an
   unbuilt change is not deployed no matter how correct it is.
3. **`systemctl --user restart upscale-ui`**, then `GET /api/health`.
4. **`node web-ui/shot.mjs`** — writes one PNG per UI state and exits non-zero
   on a page error, console error or failed request.
5. **Read every PNG it printed.** A screenshot you did not open is not a check.
6. **Drive the feature you changed** and assert the effect outside the page —
   the device book on disk, `/api/queue`, `~/.upscale/run/`. `web-ui/uictl`
   does this without a browser:

   ```bash
   web-ui/uictl routes
   web-ui/uictl POST /api/devices/remove name=zz-throwaway
   web-ui/uictl POST /api/start devices=desktop source=... target=... delete=true dry_run=true
   ```

## Adding or changing an endpoint

An endpoint is a function in `web-ui/api.py`, registered with the body keys it
reads:

```python
@route("POST", "/api/devices/remove", accepts=("name",))
def api_device_remove(body, query):
    ...
    return 200, {"ok": True, "removed": name, "devices": device_list(book)}
```

- `dispatch()` returns 400 naming any key the handler does not read. Adding a
  field to a request means adding it to `accepts`.
- The page reaches the API only through its `api()` / `act()` helpers.
  `test_api.py::FrontendContract` parses `+page.svelte`, extracts every call and
  the keys of the object it posts, and fails with the svelte line number when a
  path has no route or a key no handler reads. A rename on one side fails there.
- Put nothing in `server.py` but sockets, static files and the snapshot thread.
  Logic there is logic no test can reach.
- Write the test in the same edit as the handler. A route with no test is a
  route that has never run.

## Changing the pipeline

`upscale` and `libexec/upscale-worker` have no test harness, so a change is
verified by running it: a source directory with one file, a real device,
`--archive` to a scratch directory. Check the output plays in Jellyfin, not
only in mpv. A truncated push, a device that dies mid-chunk and a filename with
spaces in it do not show up any other way.

## The gate

`.claude/hooks/verify-web-ui.py` runs `web-ui/test_api.py` after any edit to
`api.py`, `server.py`, `uictl`, `test_api.py` or a `.svelte` file, and again
before a git commit or push in this repo. A commit is refused while the tests
fail or `web/dist` is older than its sources.

A failure straight after an edit can mean the change is half made. Finish it and
run `web-ui/check`. Do not route around the hook, delete the assertion, or widen
`accepts` to match a wrong request — fix whichever side is wrong.

## Reporting

Say which of the six steps you ran. If one was skipped or blocked, say which and
why, rather than reporting the rest as a pass.
