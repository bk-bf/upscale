#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api

PAGE = Path(__file__).resolve().parent / "web" / "src" / "routes" / "+page.svelte"


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "ui"
        self.state = self.root / "state"
        self.media = self.root / "media"
        for p in (self.home, self.state, self.media):
            p.mkdir(parents=True)
        (self.home / "config.json").write_text(json.dumps({
            "browse_roots": [str(self.media)],
            "upscale_bin": "/usr/bin/true",
        }))
        api.configure(home=self.home, state=self.state)
        self.spawned = []
        self.ssh_calls = []
        self._real_spawn, self._real_ssh = api.spawn, api.ssh_to
        api.spawn = lambda argv, log, cwd: self.spawned.append(list(argv))
        api.ssh_to = self.fake_ssh
        self.ssh_reply = (0, "", "")
        self.addCleanup(self.restore)

    def restore(self):
        api.spawn, api.ssh_to = self._real_spawn, self._real_ssh
        self.tmp.cleanup()

    def fake_ssh(self, spec, cmd, timeout=20):
        self.ssh_calls.append((spec, cmd))
        return self.ssh_reply

    def call(self, method, path, body=None, query=None):
        return api.dispatch(method, path, body, query)

    def set_state(self, obj):
        (self.state / "run").mkdir(parents=True, exist_ok=True)
        (self.state / "run" / "state.json").write_text(json.dumps(obj))

    def library(self, name, files=()):
        d = self.media / name
        d.mkdir(parents=True, exist_ok=True)
        for f in files:
            (d / f).write_bytes(b"x")
        return d


class Devices(Sandbox):
    def add(self, name="rental", ssh="-p 31174 root@1.2.3.4"):
        return self.call("POST", "/api/devices/add", {"name": name, "ssh": ssh})

    def test_add_then_remove_leaves_the_book_empty(self):
        code, d = self.add()
        self.assertEqual(code, 200)
        self.assertEqual([x["name"] for x in d["devices"]], ["rental"])

        code, d = self.call("POST", "/api/devices/remove", {"name": "rental"})
        self.assertEqual(code, 200)
        self.assertEqual(d["removed"], "rental")
        self.assertEqual(d["devices"], [])

        self.assertEqual(self.call("GET", "/api/devices")[1]["devices"], [])
        self.assertEqual(json.loads((self.home / "devices.json").read_text()), {})

    def test_removing_an_unknown_name_is_a_404_not_a_silent_ok(self):
        self.add()
        code, d = self.call("POST", "/api/devices/remove", {"name": "typo"})
        self.assertEqual(code, 404)
        self.assertFalse(d["ok"])
        self.assertEqual([x["name"] for x in d["devices"]], ["rental"])

    def test_removing_with_the_wrong_key_is_rejected(self):
        self.add()
        code, d = self.call("POST", "/api/devices/remove", {"id": "rental"})
        self.assertEqual(code, 400)
        self.assertIn("id", d["error"])
        self.assertEqual([x["name"] for x in self.call("GET", "/api/devices")[1]["devices"]],
                         ["rental"])

    def test_a_device_in_the_running_run_cannot_be_removed(self):
        self.add()
        self.set_state({"devices": [{"device": "rental", "ssh": "x"}]})
        code, d = self.call("POST", "/api/devices/remove", {"name": "rental"})
        self.assertEqual(code, 409)
        self.assertIn("stop it first", d["error"])

    def test_removal_shows_up_in_the_queue_snapshot(self):
        self.add()
        self.assertEqual([x["name"] for x in api.collect()["devices"]], ["rental"])
        self.call("POST", "/api/devices/remove", {"name": "rental"})
        self.assertEqual(api.collect()["devices"], [])

    def test_add_rejects_a_name_that_would_break_the_command_line(self):
        code, d = self.call("POST", "/api/devices/add", {"name": "a b", "ssh": "host"})
        self.assertEqual(code, 400)
        code, d = self.call("POST", "/api/devices/add", {"name": "", "ssh": "host"})
        self.assertEqual(code, 400)

    def test_probe_reports_a_flat_shape(self):
        self.ssh_reply = (0, "host=box\nworker=yes\nmkvmerge=no\ngpu=RTX 4090\n"
                             "nproc=32\ncpumax=800000 100000\nfree=412\n", "")
        code, d = self.call("POST", "/api/devices/probe", {"ssh": "root@1.2.3.4"})
        self.assertEqual(code, 200)
        self.assertTrue(d["reachable"])
        self.assertEqual(d["host"], "box")
        self.assertEqual(d["gpu"], "RTX 4090")
        self.assertEqual(d["cores"], 8.0)
        self.assertEqual(d["free_gb"], 412)
        self.assertTrue(d["worker"])
        self.assertFalse(d["mkvmerge"])
        self.assertEqual(len(d["warnings"]), 1)

    def test_probe_failure_is_reported_not_invented(self):
        self.ssh_reply = (255, "", "Permission denied (publickey).")
        code, d = self.call("POST", "/api/devices/probe", {"ssh": "root@nope"})
        self.assertFalse(d["reachable"])
        self.assertIn("Permission denied", d["error"])


class Start(Sandbox):
    def setUp(self):
        super().setUp()
        self.src = self.library("show", ["S01E01.mkv", "S01E02.mkv"])
        self.tgt = self.library("show-4k")
        self.arc = self.library("archive")
        self.call("POST", "/api/devices/add", {"name": "desktop", "ssh": "desktop"})

    def body(self, **kw):
        b = {"devices": ["desktop"], "source": str(self.src), "target": str(self.tgt),
             "archive": str(self.arc)}
        b.update(kw)
        return b

    def test_start_builds_the_command_and_runs_it(self):
        code, d = self.call("POST", "/api/start", self.body())
        self.assertEqual(code, 200)
        self.assertEqual(self.spawned, [[
            "/usr/bin/true", "--source", str(self.src), "--target", str(self.tgt),
            "--archive", str(self.arc), "--device", "desktop=desktop"]])

    def test_dry_run_returns_the_command_without_running_it(self):
        code, d = self.call("POST", "/api/start", self.body(dry_run=True))
        self.assertEqual(code, 200)
        self.assertIn("--archive", d["command"])
        self.assertEqual(self.spawned, [])

    def test_delete_replaces_archive(self):
        _, d = self.call("POST", "/api/start", self.body(delete=True, dry_run=True))
        self.assertIn("--delete", d["command"])
        self.assertNotIn("--archive", d["command"])

    def test_neither_archive_nor_delete_is_refused(self):
        code, d = self.call("POST", "/api/start", self.body(archive="", dry_run=True))
        self.assertEqual(code, 400)
        self.assertIn("stop being a source", d["error"])

    def test_a_path_outside_the_roots_is_refused(self):
        code, d = self.call("POST", "/api/start", self.body(target="/etc", dry_run=True))
        self.assertEqual(code, 400)
        self.assertIn("not reachable", d["error"])

    def test_no_device_is_refused(self):
        code, d = self.call("POST", "/api/start", self.body(devices=[], dry_run=True))
        self.assertEqual(code, 400)
        self.assertIn("device", d["error"])

    def test_import_then_start_needs_no_source_in_the_body(self):
        self.call("POST", "/api/import", {"paths": [str(self.src / "S01E01.mkv")]})
        _, d = self.call("POST", "/api/start",
                         {"devices": ["desktop"], "target": str(self.tgt),
                          "archive": str(self.arc), "dry_run": True})
        self.assertIn(f"--source {self.src}", d["command"])

    def test_start_is_refused_while_a_run_exists(self):
        self.set_state({"source": "x", "devices": []})
        code, d = self.call("POST", "/api/start", self.body())
        self.assertEqual(code, 409)

    def test_start_clears_a_leftover_stop_flag(self):
        (self.state / "run").mkdir(parents=True, exist_ok=True)
        (self.state / "run" / "stop").touch()
        self.call("POST", "/api/start", self.body())
        self.assertFalse((self.state / "run" / "stop").exists())

    def test_stop_without_a_run_says_so(self):
        code, d = self.call("POST", "/api/stop")
        self.assertEqual(code, 409)

    def test_stop_writes_the_flag_and_the_snapshot_shows_it(self):
        self.set_state({"source": "x", "devices": []})
        code, d = self.call("POST", "/api/stop")
        self.assertEqual(code, 200)
        self.assertTrue(api.collect()["stopping"])


class Browse(Sandbox):
    def test_a_trailing_slash_lists_the_directory(self):
        self.library("show", ["S01E01.mkv"])
        _, d = self.call("GET", "/api/browse", query={"q": f"{self.media}/"})
        self.assertEqual([r["name"] for r in d["results"]], ["show"])

    def test_a_fragment_filters_by_prefix(self):
        self.library("alpha")
        self.library("beta")
        _, d = self.call("GET", "/api/browse", query={"q": f"{self.media}/al"})
        self.assertEqual([r["name"] for r in d["results"]], ["alpha"])

    def test_outside_the_roots_returns_nothing(self):
        _, d = self.call("GET", "/api/browse", query={"q": "/etc/"})
        self.assertEqual(d["results"], [])

    def test_import_of_two_directories_is_refused(self):
        a = self.library("a", ["x.mkv"])
        b = self.library("b", ["y.mkv"])
        code, d = self.call("POST", "/api/import",
                            {"paths": [str(a / "x.mkv"), str(b / "y.mkv")]})
        self.assertEqual(code, 400)
        self.assertIn("one source", d["error"])


class Queue(Sandbox):
    def test_an_unreachable_device_is_never_reported_as_idle(self):
        self.call("POST", "/api/devices/add", {"name": "rental", "ssh": "root@1.2.3.4"})
        src = self.library("show", ["S01E01.mkv"])
        self.set_state({"source": str(src), "target": str(self.library("out")),
                        "devices": [{"device": "rental", "ssh": "root@1.2.3.4",
                                     "source": str(src), "target": str(self.media / "out")}]})
        self.ssh_reply = (255, "", "connection timed out")
        d = api.collect()
        dev = d["devices"][0]
        self.assertFalse(dev["reachable"])
        self.assertIn("timed out", dev["error"])

    def test_rows_are_numbered_by_episode_not_by_position(self):
        src = self.library("show", ["Show - S01E07.mkv", "Show - S01E02.mkv"])
        self.set_state({"source": str(src), "target": str(self.library("out")), "devices": []})
        r = api.collect()["rows"]
        self.assertEqual([x["n"] for x in r], [2, 7])

    def test_a_delivered_file_keeps_its_number(self):
        src = self.library("show", ["Show - S01E02.mkv"])
        out = self.library("out", ["Show - S01E01.mkv"])
        self.set_state({"source": str(src), "target": str(out), "devices": []})
        r = api.collect()["rows"]
        self.assertEqual([(x["n"], x["status"]) for x in r], [(1, "done"), (2, "queued")])

    def test_rsync_progress_is_reported_in_the_workers_vocabulary(self):
        p = api.parse_rsync("  199,185,400  50%   19.16MB/s    0:00:09")
        self.assertEqual(p["phase_percent"], 50)
        self.assertEqual(p["phase_done"], 199185400)
        self.assertEqual(p["phase_unit"], "bytes")
        self.assertEqual(p["phase_elapsed_s"], 9)


class Dispatch(Sandbox):
    def test_an_unknown_route_is_a_404(self):
        code, d = self.call("POST", "/api/nope")
        self.assertEqual(code, 404)

    def test_a_handler_that_raises_becomes_a_500_not_a_dead_socket(self):
        api.ROUTES[("POST", "/api/boom")] = api.Route(
            "POST", "/api/boom", lambda b, q: 1 / 0, frozenset())
        self.addCleanup(api.ROUTES.pop, ("POST", "/api/boom"))
        code, d = self.call("POST", "/api/boom")
        self.assertEqual(code, 500)
        self.assertIn("ZeroDivisionError", d["error"])


CALL = re.compile(r'\b(?:api|act)\(\s*"(GET|POST)"\s*,\s*[`"](/api/[^`"?]*)')


def keys_at_depth_one(src: str, start: int) -> set:
    depth, i, out, n = 0, start, set(), len(src)
    expect_key = False
    while i < n:
        c = src[i]
        if c in "{[(":
            depth += 1
            expect_key = depth == 1 and c == "{"
        elif c in "}])":
            depth -= 1
            if depth == 0:
                return out
            expect_key = False
        elif depth == 1 and c == ",":
            expect_key = True
        elif depth == 1 and expect_key and not c.isspace():
            if c.isalpha() or c in "_$":
                j = i
                while j < n and (src[j].isalnum() or src[j] in "_$"):
                    j += 1
                out.add(src[i:j])
                i = j
            expect_key = False
            continue
        i += 1
    return out


def helper_keys(src: str, name: str) -> set:
    m = re.search(rf"function\s+{re.escape(name)}\s*\(", src)
    if not m:
        return set()
    body = src[m.end():]
    r = body.find("return")
    brace = body.find("{", r)
    return keys_at_depth_one(body, brace) if r >= 0 and brace >= 0 else set()


def page_calls() -> list:
    src = PAGE.read_text()
    found = []
    for m in CALL.finditer(src):
        method, path = m.group(1), m.group(2)
        rest = src[m.end():].lstrip("`\"")
        brace, close = rest.find("{"), rest.find(")")
        body = set()
        if method == "POST" and 0 <= brace < close:
            body = keys_at_depth_one(rest, brace)
        elif method == "POST":
            helper = re.match(r'\s*,\s*([A-Za-z_$][\w$]*)\s*\(', rest)
            if helper:
                body = helper_keys(src, helper.group(1))
        found.append((method, path, body, src[:m.start()].count("\n") + 1))
    return found


class FrontendContract(unittest.TestCase):
    def test_the_page_has_calls_to_check(self):
        self.assertGreater(len(page_calls()), 5)

    def test_every_path_the_page_calls_exists(self):
        for method, path, _, line in page_calls():
            with self.subTest(call=f"{method} {path}", line=line):
                self.assertIn((method, path), api.ROUTES,
                              f"{PAGE.name}:{line} calls {method} {path}, which no route serves")

    def test_every_key_the_page_posts_is_one_the_handler_reads(self):
        for method, path, keys, line in page_calls():
            r = api.ROUTES.get((method, path))
            if r is None:
                continue
            unknown = keys - r.accepts
            with self.subTest(call=f"{method} {path}", line=line):
                self.assertEqual(unknown, set(),
                                 f"{PAGE.name}:{line} posts {sorted(unknown)} to {path}, "
                                 f"which reads {sorted(r.accepts)}")

    def test_the_page_makes_no_bare_fetch_calls(self):
        bare = [i + 1 for i, ln in enumerate(PAGE.read_text().splitlines())
                if "fetch(" in ln and "async function api" not in ln]
        self.assertLessEqual(len(bare), 1,
                             f"{PAGE.name} bypasses the api() helper at lines {bare}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
