"""Tests for job_state.py (stdlib unittest; run: python3 -m unittest discover -s <skill>/tests)."""
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import job_state as js  # noqa: E402


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = js.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["JOB_STATE_DIR"] = self.tmp.name
        self.path = os.path.join(self.tmp.name, "demo.md")

    def tearDown(self):
        os.environ.pop("JOB_STATE_DIR", None)
        self.tmp.cleanup()

    def text(self):
        with open(self.path) as f:
            return f.read()


class ShowRecord(Base):
    def test_show_creates_all_sections(self):
        rc, out, _ = run("show", "demo")
        self.assertEqual(rc, 0)
        for s in js.SECTIONS:
            self.assertIn(f"## {s}", out)
        self.assertTrue(os.path.exists(self.path))

    def test_record_roundtrip_keeps_sections_and_order(self):
        run("record", "demo", "--last-run", "first", "--tried", "a", "--stamp", "2026-11-01T00:00Z")
        run("record", "demo", "--last-run", "second", "--tried", "b", "--watch", "flaky thing",
            "--lesson", "[use-ro-db] open kanban.db read-only", "--stamp", "2026-11-02T00:00Z")
        st = js.load(self.path)
        self.assertEqual(st.sections["Last run"], ["2026-11-02T00:00Z second"])
        self.assertEqual(st.sections["Tried"], ["- 2026-11-01T00:00Z a", "- 2026-11-02T00:00Z b"])
        self.assertEqual(st.sections["Watch list"], ["- flaky thing"])
        self.assertEqual(st.sections["Lessons"], ["- [use-ro-db] (x1, last 2026-11-02) open kanban.db read-only"])
        # a re-parse renders byte-identically
        self.assertEqual(js.State.parse(self.text(), "demo").render(), self.text())

    def test_unwatch_and_duplicate_watch(self):
        run("record", "demo", "--watch", "pr 12 rebase", "--watch", "pr 12 rebase", "--watch", "disk on hub")
        self.assertEqual(js.load(self.path).sections["Watch list"], ["- pr 12 rebase", "- disk on hub"])
        run("record", "demo", "--unwatch", "pr 12")
        self.assertEqual(js.load(self.path).sections["Watch list"], ["- disk on hub"])

    def test_lesson_without_slug_is_refused(self):
        rc, _, err = run("record", "demo", "--lesson", "no slug here")
        self.assertEqual(rc, 2)
        self.assertIn("[slug]", err)

    def test_hand_edits_and_unknown_sections_survive(self):
        with open(self.path, "w") as f:
            f.write("# demo state\nnote by hand\n\n## Last run\nold\n\n## Tried\n- x\n  continued\n\n"
                    "## Notes\nkeep me\n")
        run("record", "demo", "--tried", "y", "--stamp", "2026-11-03T00:00Z")
        t = self.text()
        self.assertIn("note by hand", t)
        self.assertIn("- x\n  continued\n- 2026-11-03T00:00Z y", t)
        self.assertIn("## Notes\nkeep me", t)


class Promotion(Base):
    def test_promote_at_three_then_mark(self):
        for i in range(2):
            _, out, _ = run("record", "demo", "--lesson", "[gh-rate] wait for the gh rate limit reset")
            self.assertNotIn("PROMOTE", out)
        _, out, _ = run("record", "demo", "--lesson", "[gh-rate] check gh api rate_limit first")
        self.assertIn("PROMOTE [gh-rate] x3: check gh api rate_limit first", out)
        rc, out, _ = run("lessons", "demo")
        self.assertIn("[gh-rate] x3", out)
        _, out, _ = run("record", "demo", "--promoted", "gh-rate")
        self.assertNotIn("PROMOTE", out)
        self.assertIn(", promoted)", self.text())
        # a promoted lesson that recurs again is not re-promoted
        _, out, _ = run("record", "demo", "--lesson", "[gh-rate] again")
        self.assertNotIn("PROMOTE", out)
        self.assertIn("(x4, last", self.text())


class Prune(Base):
    def test_record_caps_at_150_dropping_oldest_tried_first(self):
        run("record", "demo", "--lesson", "[keep-me] lessons survive", "--watch", "watch survives")
        for i in range(200):
            run("record", "demo", "--tried", f"attempt {i:03d}", "--stamp", "2026-11-03T00:00Z")
        st = js.load(self.path)
        self.assertLessEqual(st.line_count(), 150)
        self.assertEqual(len(self.text().splitlines()), st.line_count())
        tried = st.sections["Tried"]
        self.assertTrue(tried[-1].endswith("attempt 199"))
        self.assertFalse(any(t.endswith("attempt 000") for t in tried))
        # contiguous newest block kept
        nums = [int(t.rsplit(" ", 1)[1]) for t in tried]
        self.assertEqual(nums, list(range(200 - len(nums), 200)))
        self.assertIn("[keep-me]", self.text())
        self.assertIn("watch survives", self.text())

    def test_prune_command_on_oversize_file_and_promoted_lessons_go_next(self):
        st = js.State.new("demo")
        for i in range(100):
            st.add_tried(f"t{i}", "2026-11-01T00:00Z")
        for i in range(160):
            st.add_lesson(f"[l{i}] lesson {i}", f"2026-10-{(i % 28) + 1:02d}")
            if i < 60:
                st.mark_promoted(f"l{i}")
        js.save(self.path, st)
        rc, out, _ = run("prune", "demo")
        self.assertEqual(rc, 0, out)
        st = js.load(self.path)
        self.assertEqual(st.sections["Tried"], [])
        self.assertLessEqual(st.line_count(), 150)
        # some promoted lessons went; unpromoted lessons are never pruned
        self.assertLess(sum(1 for e in st.sections["Lessons"] if "promoted" in e), 60)
        self.assertEqual(sum(1 for e in st.sections["Lessons"] if "promoted" not in e), 100)

    def test_prune_exit_3_when_unprunable(self):
        st = js.State.new("demo")
        for i in range(160):
            st.add_watch(f"w{i}")
        js.save(self.path, st)
        rc, _, err = run("prune", "demo")
        self.assertEqual(rc, 3)
        self.assertIn("over the 150-line cap", err)

    def test_max_lines_option(self):
        for i in range(30):
            run("record", "demo", "--tried", f"t{i}", "--max-lines", "20")
        self.assertLessEqual(js.load(self.path).line_count(), 20)


class Slice(Base):
    def test_slice_has_newest_tried_only(self):
        for i in range(15):
            run("record", "demo", "--tried", f"t{i:02d}")
        run("record", "demo", "--lesson", "[a] b")
        _, out, _ = run("show", "demo", "--slice", "--tried", "3")
        self.assertIn("## Tried (newest 3 of 15)", out)
        self.assertIn("t14", out)
        self.assertNotIn("t11", out)
        self.assertIn("[a] (x1", out)
        self.assertIn("## Watch list\n(none)", out)


class Harvest(Base):
    def make_db(self):
        db = os.path.join(self.tmp.name, "kanban.db")
        con = sqlite3.connect(db)
        con.execute("create table tasks (id text, idempotency_key text)")
        con.execute("create table task_runs (id integer primary key, task_id text, outcome text, summary text,"
                    " metadata text, ended_at real)")
        con.executemany("insert into tasks values (?,?)", [
            ("t_1", "deflake:tests/a.py::x"), ("t_2", "deflake:tests/b.py::y"), ("t_3", "ci-red:o/r:abc"),
            ("t_4", "deflake_other")])
        meta = {"job_state": {"tried": "pinned the clock", "lessons": ["[fake-clock] use a fake clock"],
                              "watch": ["tests/b.py::y"]}}
        con.executemany("insert into task_runs values (?,?,?,?,?,?)", [
            (1, "t_1", "completed", "fixed a", None, 1793600000),
            (2, "t_2", "completed", "fixed b", json.dumps(meta), 1793600100),
            (3, "t_3", "completed", "other family", None, 1793600200),
            (4, "t_4", "completed", "underscore is literal", None, 1793600250),
            (5, "t_1", None, "still running", None, None),
            (6, "t_1", "continued", "checkpoint only", None, 1793600300)])
        con.commit()
        con.close()
        return db

    def test_late_lower_run_id_is_not_skipped(self):
        db = self.make_db()
        run('harvest', 'demo', '--db', db, '--key-prefix', 'deflake:')
        with sqlite3.connect(db) as con:
            con.execute("update task_runs set outcome='completed', summary='late finish', ended_at=1793600400 where id=5")
        run('harvest', 'demo', '--db', db, '--key-prefix', 'deflake:')
        self.assertIn('late finish', self.text())

    def test_crash_after_state_commit_does_not_count_lessons_twice(self):
        from unittest.mock import patch
        db = self.make_db()
        original = js.save
        def committed_then_crash(path, st):
            original(path, st)
            raise RuntimeError('interrupted after atomic state replace')
        with patch.object(js, 'save', side_effect=committed_then_crash):
            with self.assertRaises(RuntimeError):
                run('harvest', 'demo', '--db', db, '--key-prefix', 'deflake:')
        run('harvest', 'demo', '--db', db, '--key-prefix', 'deflake:')
        self.assertIn('[fake-clock] (x1', self.text())
        self.assertNotIn('(x2', self.text())

    def test_legacy_cursor_migration_preserves_state_and_tracks_open_runs(self):
        db = self.make_db()
        run('record', 'demo', '--tried', 'legacy entry')
        with open(os.path.join(self.tmp.name, '.demo.harvest.json'), 'w') as f:
            json.dump({'last_run_id': 6}, f)
        run('harvest', 'demo', '--db', db, '--key-prefix', 'deflake:')
        self.assertNotIn('fixed a', self.text())
        with sqlite3.connect(db) as con:
            con.execute("update task_runs set outcome='completed', summary='late legacy finish', ended_at=1793600400 where id=5")
        run('harvest', 'demo', '--db', db, '--key-prefix', 'deflake:')
        self.assertIn('late legacy finish', self.text())
        self.assertIn('legacy entry', self.text())

    def test_corrupt_legacy_cursor_fails_closed(self):
        db = self.make_db()
        with open(os.path.join(self.tmp.name, '.demo.harvest.json'), 'w') as f:
            f.write('{')
        with self.assertRaises(ValueError):
            run('harvest', 'demo', '--db', db, '--key-prefix', 'deflake:')
        self.assertFalse(os.path.exists(self.path))

    def test_concurrent_harvests_count_each_run_once(self):
        import subprocess
        db = self.make_db()
        cmd = [sys.executable, js.__file__, 'harvest', 'demo', '--db', db, '--key-prefix', 'deflake:']
        procs = [subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        for proc in procs:
            out, err = proc.communicate(timeout=20)
            self.assertEqual(proc.returncode, 0, out + err)
        self.assertEqual(len(js.load(self.path).sections['Tried']), 2)
        self.assertIn('[fake-clock] (x1', self.text())

    def test_source_rebinding_refused_and_record_preserves_cursor(self):
        db = self.make_db()
        run('harvest', 'demo', '--db', db, '--key-prefix', 'deflake:')
        run('record', 'demo', '--watch', 'preserved')
        run('prune', 'demo')
        run('harvest', 'demo', '--db', db, '--key-prefix', 'deflake:')
        self.assertIn('[fake-clock] (x1', self.text())
        with self.assertRaises(ValueError):
            run('harvest', 'demo', '--db', db, '--key-prefix', 'ci-red:')
        _, sliced, _ = run('show', 'demo', '--slice')
        self.assertNotIn('harvest-v2', sliced)

    def test_harvest_is_idempotent_and_scoped_to_prefix(self):
        db = self.make_db()
        rc, out, _ = run("harvest", "demo", "--db", db, "--key-prefix", "deflake:")
        self.assertEqual(rc, 0)
        self.assertIn("2 of 3 new run(s), 1 with metadata.job_state", out)
        st = js.load(self.path)
        self.assertEqual(len(st.sections["Tried"]), 2)
        self.assertIn("t_1 completed: fixed a", st.sections["Tried"][0])
        self.assertIn("t_2 completed: pinned the clock", st.sections["Tried"][1])
        self.assertTrue(st.sections["Last run"][0].endswith("t_2 (deflake:tests/b.py::y) completed"))
        self.assertIn("[fake-clock] (x1", st.sections["Lessons"][0])
        self.assertEqual(st.sections["Watch list"], ["- tests/b.py::y"])
        self.assertNotIn("other family", self.text())
        self.assertNotIn("underscore", self.text())
        self.assertNotIn("checkpoint only", self.text())
        rc, out, _ = run("harvest", "demo", "--db", db, "--key-prefix", "deflake:")
        self.assertIn("nothing new", out)
        self.assertEqual(len(js.load(self.path).sections["Tried"]), 2)


if __name__ == "__main__":
    unittest.main()
