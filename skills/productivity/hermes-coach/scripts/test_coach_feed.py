"""Tests for coach_feed.py conflict handling. Run: python3 -B -m unittest test_coach_feed (from scripts/)."""
import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import coach_feed


def g(repo, *a):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *a],
                          capture_output=True, text=True, check=True).stdout


class ConflictTest(unittest.TestCase):
    def make_conflict(self, name="research/state/spend.jsonl"):
        d = Path(tempfile.mkdtemp())
        repo = d / "repo"
        repo.mkdir()
        g(repo, "init", "-q", "-b", "main")
        f = repo / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('{"a":1}\n')
        g(repo, "add", "-A"); g(repo, "commit", "-qm", "base")
        g(repo, "checkout", "-qb", "other")
        f.write_text('{"a":1}\n{"b":2}\n{"c":3}\n')
        g(repo, "commit", "-qam", "other")
        g(repo, "checkout", "-q", "main")
        f.write_text('{"a":1}\n{"c":3}\n{"d":4}\n')
        g(repo, "commit", "-qam", "main")
        subprocess.run(["git", "-C", str(repo), "merge", "other"], capture_output=True)
        return repo, f

    def test_jsonl_union_dedupe(self):
        repo, f = self.make_conflict()
        self.assertEqual(coach_feed.unmerged_paths(repo), ["research/state/spend.jsonl"])
        resolved, unresolved = coach_feed.resolve_unmerged(repo)
        self.assertEqual((resolved, unresolved), (["research/state/spend.jsonl"], []))
        self.assertEqual(coach_feed.unmerged_paths(repo), [])
        self.assertEqual(sorted(f.read_text().splitlines()), sorted(['{"a":1}', '{"b":2}', '{"c":3}', '{"d":4}']))

    def test_non_jsonl_is_unresolved(self):
        repo, _ = self.make_conflict("notes.txt")
        resolved, unresolved = coach_feed.resolve_unmerged(repo)
        self.assertEqual((resolved, unresolved), ([], ["notes.txt"]))

    def run_feed(self, repo):
        home = repo.parent / "home"
        home.mkdir(exist_ok=True)
        return subprocess.run([sys.executable, str(HERE / "coach_feed.py"), "--host", "h", "--repo-dir", str(repo),
                               "--home", str(home), "--no-push"], capture_output=True, text=True)

    def test_main_survives_conflict(self):
        repo, f = self.make_conflict()
        r = self.run_feed(repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        st = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(st["resolved_conflicts"], ["research/state/spend.jsonl"])
        self.assertIn("auto-resolved", r.stderr)

    def test_main_skips_unresolvable(self):
        repo, _ = self.make_conflict("notes.txt")
        r = self.run_feed(repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        st = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertIn("notes.txt", st["skipped"])
        self.assertIn("ERROR", r.stderr)


class ForwardSignalsTest(unittest.TestCase):
    def test_runs_the_forwarder_with_host_and_home(self):
        seen = []

        def runner(cmd, **kw):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "signals: forwarded 3 to you@hub.example.com", "")
        out = coach_feed.forward_signals("macbook", "/tmp/h", runner=runner)
        self.assertIn("forwarded 3", out)
        cmd = seen[0]
        self.assertTrue(cmd[1].endswith("plugins/coach/signals.py"))
        self.assertEqual(cmd[2:], ["--host", "macbook", "--home", "/tmp/h", "forward"])

    def test_failure_is_reported_not_raised(self):
        def boom(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 150)
        self.assertEqual(coach_feed.forward_signals("mini", None, runner=boom), "error: TimeoutExpired")
        bad = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "ssh failed")
        self.assertEqual(coach_feed.forward_signals("mini", None, runner=bad), "error: ssh failed")

    def test_no_hub_configured_says_so_and_ships_nothing(self):
        h = Path(tempfile.mkdtemp())
        out = coach_feed.forward_signals("macbook", str(h))
        self.assertIn("no hub configured", out)
        self.assertEqual(coach_feed.signals_state(out), "no-hub")
        self.assertFalse((h / "coach" / "signals-cursor.json").exists())


class FeedStatusTest(unittest.TestCase):
    """<host>/feed-status.json: the hub's only view of a Mac forwarder that never reached it."""

    def test_states(self):
        s = coach_feed.signals_state
        self.assertEqual(s("signals: heartbeat ok to you@hub.example.com"), "ok")
        self.assertEqual(s("signals: forwarded 4 to you@hub.example.com"), "ok")
        self.assertEqual(s("signals: this host is the hub (its own cron sweeps locally)"), "hub")
        self.assertEqual(s("error: signals forward to you@hub.example.com failed (2 pending): Permission denied"),
                         "error")
        self.assertEqual(s("ok"), "unknown")

    def test_written_only_when_health_changes(self):
        repo = Path(tempfile.mkdtemp())
        w = coach_feed.write_feed_status
        self.assertTrue(w(repo, "mini", "signals: no hub configured (x)", now="t1"))
        self.assertFalse(w(repo, "mini", "signals: no hub configured (x)", now="t2"))
        self.assertTrue(w(repo, "mini", "signals: forwarded 3 to you@hub.example.com", now="t3"))
        self.assertFalse(w(repo, "mini", "signals: heartbeat ok to you@hub.example.com", now="t4"))
        self.assertFalse(w(repo, "mini", "signals: forwarded 9 to you@hub.example.com", now="t5"))
        err = "error: signals forward to you@hub.example.com failed ({} pending): Permission denied"
        self.assertTrue(w(repo, "mini", err.format(2), now="t6"))
        self.assertFalse(w(repo, "mini", err.format(5), now="t7"))  # the pending count alone is not a change
        st = json.loads((repo / "mini" / "feed-status.json").read_text())
        self.assertEqual(st["signals"], "error")
        self.assertEqual(st["since"], "t6")
        self.assertIn("(pending)", st["detail"])

    def test_main_commits_the_status(self):
        d = Path(tempfile.mkdtemp())
        repo, home = d / "repo", d / "home"
        repo.mkdir(); home.mkdir()
        g(repo, "init", "-q", "-b", "main")
        (repo / "README.md").write_text("x\n")
        g(repo, "add", "-A"); g(repo, "commit", "-qm", "base")
        (repo / "h").mkdir()
        r = subprocess.run([sys.executable, str(HERE / "coach_feed.py"), "--host", "h", "--repo-dir", str(repo),
                            "--home", str(home), "--no-push"], capture_output=True, text=True,
                           env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
        self.assertEqual(r.returncode, 0, r.stderr)
        st = json.loads(g(repo, "show", "HEAD:h/feed-status.json"))
        self.assertEqual(st["signals"], "no-hub")

    def test_main_reports_phase_times_and_never_commits_the_digest_cursor(self):
        """cron-c89ed44e95: the feed's creep to its 600 s kill was invisible; the status line now carries
        per-phase elapsed_s, stderr logs each phase as it ends (survives a kill), and the cursor stays local."""
        import sqlite3, time
        d = Path(tempfile.mkdtemp())
        repo, home = d / "repo", d / "home"
        repo.mkdir(); home.mkdir()
        g(repo, "init", "-q", "-b", "main")
        (repo / "README.md").write_text("x\n")
        g(repo, "add", "-A"); g(repo, "commit", "-qm", "base")
        con = sqlite3.connect(home / "state.db")
        con.executescript("create table sessions (id text primary key, source text, model text, parent_session_id text,"
                          " started_at real, ended_at real, end_reason text, message_count int, tool_call_count int,"
                          " api_call_count int, input_tokens int, output_tokens int, cache_read_tokens int,"
                          " actual_cost_usd real, estimated_cost_usd real, title text, last_activity_at real);"
                          "create table messages (id integer primary key, session_id text, role text, content text,"
                          " tool_name text, tool_calls text, timestamp real);")
        con.execute("insert into sessions (id, started_at) values ('s1', ?)", (time.time(),))
        con.execute("insert into messages (session_id, role, content, timestamp) values ('s1','user','hi',?)",
                    (time.time(),))
        con.commit(); con.close()
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        run = lambda: subprocess.run([sys.executable, str(HERE / "coach_feed.py"), "--host", "h", "--repo-dir",
                                      str(repo), "--home", str(home), "--no-push"], capture_output=True, text=True,
                                     env=env)
        r = run()
        self.assertEqual(r.returncode, 0, r.stderr)
        st = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(set(st["elapsed_s"]), {"signals", "pull", "digest", "commit", "push", "total"})
        self.assertEqual(st["digest"]["written"], 1)
        self.assertIn("coach_feed: phase digest", r.stderr)
        self.assertTrue((repo / "h" / ".digest-cursor.json").exists())
        self.assertIn("h/default/s1.json", g(repo, "ls-files"))
        self.assertNotIn(".digest-cursor.json", g(repo, "ls-files"))
        self.assertEqual(g(repo, "status", "--porcelain"), "")
        st2 = json.loads(run().stdout.strip().splitlines()[-1])
        self.assertEqual(st2["digest"]["skipped"], 1)
        self.assertIsNone(st2["commit"])


class SkillOutcomesCopy(unittest.TestCase):
    def test_flushes_then_copies_home_and_profiles_as_a_union(self):
        d = Path(tempfile.mkdtemp())
        home, repo = d / "home", d / "repo"
        (home / "logs").mkdir(parents=True)
        (home / "profiles" / "coach" / "logs").mkdir(parents=True)
        (home / "logs" / "skill-outcomes.jsonl").write_text('{"skill":"a","ts":2}\n')
        (home / "profiles" / "coach" / "logs" / "skill-outcomes.jsonl").write_text('{"skill":"b","ts":3}\n')
        dest = repo / "hub" / "skill-outcomes.jsonl"
        dest.parent.mkdir(parents=True)
        dest.write_text('{"skill":"old","ts":1}\n{"skill":"a","ts":2}\n')  # already collected; local log pruned
        plugin = d / "outcomes.py"
        plugin.write_text("")
        calls = []
        n = coach_feed.collect_skill_outcomes(home, repo, "hub", runner=lambda cmd, **kw: calls.append(cmd),
                                              plugin=plugin)
        self.assertEqual(n, 3)
        self.assertEqual(calls[0][1:], [str(plugin), "flush", "--home", str(home), "--all-profiles"])
        self.assertEqual(dest.read_text().splitlines(), ['{"skill":"old","ts":1}', '{"skill":"a","ts":2}',
                                                         '{"skill":"b","ts":3}'])

    def test_nothing_to_collect(self):
        d = Path(tempfile.mkdtemp())
        self.assertIsNone(coach_feed.collect_skill_outcomes(d / "h", d / "r", "hub", runner=lambda *a, **k: None,
                                                            plugin=d / "missing.py"))
        self.assertFalse((d / "r").exists())

    def test_real_plugin_path_exists(self):
        self.assertTrue(coach_feed.skill_outcomes_plugin().exists())


class SelfhealSeenCopy(unittest.TestCase):
    """<repo>/<host>/selfheal-seen.json: this host's detections (shadow or live) for the hub's ask-gate
    (plugin-03d4509c8a: a MacBook detection was invisible to the hub, so ask-gate re-filed the turn)."""

    def test_copies_home_and_profiles_as_a_union_without_symptom_text(self):
        d = Path(tempfile.mkdtemp())
        home, repo = d / "home", d / "repo"
        (home / "coach").mkdir(parents=True)
        (home / "profiles" / "bb" / "coach").mkdir(parents=True)
        (home / "coach" / "selfheal.json").write_text(json.dumps({
            "agent-a193bf9868": {"first": 10, "last": 20, "count": 2, "sessions": ["s1"], "symptom": "secret-ish"},
            "bad": "not a dict"}))
        (home / "profiles" / "bb" / "coach" / "selfheal.json").write_text(json.dumps({
            "agent-a193bf9868": {"first": 5, "last": 15, "sessions": ["s2"]}}))
        dest = repo / "macbook" / "selfheal-seen.json"
        dest.parent.mkdir(parents=True)
        dest.write_text(json.dumps({"agent-old": {"first": 1, "last": 2, "sessions": ["s0"]}}))  # local pruned
        n = coach_feed.collect_selfheal_seen(home, repo, "macbook")
        self.assertEqual(n, 2)
        st = json.loads(dest.read_text())
        self.assertEqual(st["agent-old"], {"first": 1, "last": 2, "sessions": ["s0"]})
        self.assertEqual(st["agent-a193bf9868"], {"first": 5, "last": 20, "sessions": ["s1", "s2"]})
        self.assertNotIn("secret-ish", dest.read_text())
        mtime = dest.stat().st_mtime_ns
        coach_feed.collect_selfheal_seen(home, repo, "macbook")
        self.assertEqual(dest.stat().st_mtime_ns, mtime)  # unchanged -> not rewritten (no commit churn)

    def test_nothing_to_collect(self):
        d = Path(tempfile.mkdtemp())
        self.assertIsNone(coach_feed.collect_selfheal_seen(d / "h", d / "r", "hub"))
        self.assertFalse((d / "r").exists())

    def test_main_commits_it_and_askgate_reads_it(self):
        d = Path(tempfile.mkdtemp())
        repo, home = d / "repo", d / "home"
        repo.mkdir(); (home / "coach").mkdir(parents=True)
        (home / "coach" / "selfheal.json").write_text(json.dumps({
            "agent-a193bf9868": {"first": 1792600030, "last": 1792600030, "sessions": ["s_fix"]}}))
        g(repo, "init", "-q", "-b", "main")
        (repo / "README.md").write_text("x\n")
        g(repo, "add", "-A"); g(repo, "commit", "-qm", "base")
        r = subprocess.run([sys.executable, str(HERE / "coach_feed.py"), "--host", "macbook", "--repo-dir",
                            str(repo), "--home", str(home), "--no-push"], capture_output=True, text=True,
                           env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("agent-a193bf9868", g(repo, "show", "HEAD:macbook/selfheal-seen.json"))
        import coach_askgate
        hub = d / "hub"
        hub.mkdir()
        seen = coach_askgate.detected_sessions(hub, data=repo)
        self.assertEqual(coach_askgate.detection_for({"session": "s_fix", "t": 1792600000}, seen),
                         "agent-a193bf9868")


if __name__ == "__main__":
    unittest.main()
