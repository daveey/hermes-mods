"""Regression tests for the shared-scratch half of evidence-clobber-guard (self-heal agent-c97f5ffde4).

A helper subagent inherited its parent's cwd ``~/.hermes/cache/scratch/care-retention-gap`` (shared by 225
sessions), wrote ``resolve.py``/``runv.sh`` there with ``cat >`` and later ran ``rm -f resolve.py runv.sh`` and
``rm -rf w w0 v-*`` without checking whether those names already existed. The guard now refuses truncating
writes and ``rm`` of files under ``$HERMES_HOME/cache/scratch/<dir>/`` that are older than the task's first
tool call. These tests drive the real registered ``pre_tool_call`` hook. Run:
    python3 -m pytest -q -p no:cacheprovider plugins/evidence-clobber-guard/tests
"""

import os
import time

import pytest

from test_guard import _load_hook

# Replays of the incident's tool calls (state.db msgs 1204780 and 1205802; 1205783 for the dir rm).
MSG_1204780 = ("cat > resolve.py <<'EOF'\nimport json, sys\n# don't touch the parent's files\n"
               "print(json.dumps({'ok': True}))\nEOF\npython3 resolve.py")
MSG_1205802 = "rm -f resolve.py runv.sh logs/*.log && ls"
MSG_1205783 = "rm -rf w w0 v-*"


@pytest.fixture
def scr(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    shared = home / "cache" / "scratch" / "care-retention-gap"
    (shared / "logs").mkdir(parents=True)
    (shared / "w").mkdir()
    for rel in ("resolve.py", "runv.sh", "logs/a.log", "w/state.json"):
        (shared / rel).write_text("parent's work\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(home / "cache" / "scratch"))
    monkeypatch.delenv("HERMES_EVIDENCE_CLOBBER_GUARD", raising=False)
    hook = _load_hook()

    def run(command, task="child-1", workdir=shared):
        return hook(tool_name="terminal", args={"command": command, "workdir": str(workdir)}, task_id=task)

    # The helper's first tool call (a read) records its start, after the parent made its files.
    time.sleep(0.05)
    assert run("ls") is None
    time.sleep(0.05)
    return {"shared": shared, "run": run, "home": home}


def assert_refused(res, cmd):
    assert res and res.get("action") == "block", f"not refused: {cmd!r}"
    assert "shared scratch" in res["message"] and "mktemp -d -p $TMPDIR" in res["message"]


def test_incident_cat_heredoc_over_parents_file_is_refused(scr):
    assert_refused(scr["run"](MSG_1204780), MSG_1204780)
    assert (scr["shared"] / "resolve.py").read_text() == "parent's work\n"


def test_incident_rm_of_parents_files_is_refused(scr):
    assert_refused(scr["run"](MSG_1205802), MSG_1205802)
    assert_refused(scr["run"](MSG_1205783), MSG_1205783)


@pytest.mark.parametrize("cmd", [
    "echo x > runv.sh",
    "python3 x.py 2>&1 | tee logs/a.log",
    "rm resolve.py",
    "rm -r w",
    "cd logs && rm -f *.log",
    "bash -c 'rm -f resolve.py'",
    "rm -f $TMPDIR/care-retention-gap/runv.sh",
    "echo x >| $HERMES_HOME/cache/scratch/care-retention-gap/resolve.py",
])
def test_other_shapes_on_older_files_are_refused(scr, cmd):
    assert_refused(scr["run"](cmd), cmd)


def test_own_new_files_appends_and_reads_pass(scr):
    run, shared = scr["run"], scr["shared"]
    for name in ("mine.py", "mine.log"):
        (shared / name).write_text("mine\n")
    # An archive this task unpacked keeps its old mtime, but it is still the task's own file (ctime).
    (shared / "unpacked.txt").write_text("from tar\n")
    os.utime(shared / "unpacked.txt", (0, 0))
    for cmd in ("cat > mine.py <<'EOF'\nx\nEOF", "rm -f mine.py mine.log unpacked.txt", "echo x >> resolve.py",
                "python3 x.py | tee -a runv.sh", "cat resolve.py", "rm -f does-not-exist.txt",
                "echo x > brand-new.txt", "d=$(mktemp -d -p $TMPDIR) && echo x > $d/out.log"):
        assert run(cmd) is None, cmd


def test_another_task_started_before_the_file_may_not_but_its_creator_after_may(scr):
    # A task whose first call came after the file existed is the incident; files outside scratch are untouched.
    assert_refused(scr["run"]("rm -f resolve.py", task="child-2"), "child-2")
    outside = scr["home"] / "work"
    outside.mkdir()
    (outside / "resolve.py").write_text("x")
    assert scr["run"]("rm -f resolve.py && echo x > resolve.py", workdir=outside) is None


def test_start_survives_a_guard_reload_and_scratch_root_files_are_not_shared(scr):
    # A fresh plugin load (gateway restart) keeps child-1's persisted start: the parent's file is still refused.
    hook = _load_hook()
    res = hook(tool_name="terminal", args={"command": "rm runv.sh", "workdir": str(scr["shared"])},
               task_id="child-1")
    assert_refused(res, "reload")
    # A file directly in $TMPDIR (mktemp -p $TMPDIR) is not inside a shared folder.
    root_file = scr["shared"].parent / "tmp.abc"
    root_file.write_text("x")
    assert scr["run"](f"rm -f {root_file}") is None
    # A herestring is not a here-doc: the rest of the command is still checked.
    assert_refused(scr["run"]("cat <<< 'it''s' > resolve.py"), "herestring")


def test_kill_switch_and_log(scr, monkeypatch):
    assert_refused(scr["run"]("rm -f runv.sh"), "log")
    log = (scr["home"] / "logs" / "evidence-clobber-guard.log").read_text()
    assert "runv.sh" in log
    monkeypatch.setenv("HERMES_EVIDENCE_CLOBBER_GUARD", "off")
    assert scr["run"]("rm -f runv.sh") is None
