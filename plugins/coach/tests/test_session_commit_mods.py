"""session_commit_fix finds a fix the origin session shipped in hermes-mods (defect desktop-9077b82c2d).

The session answered "figure out why it's not showing the block" with hermes-mods f986de8c
(`fix(session-panel): ...`, desktop-plugins/ only). The lane missed it three ways: it searched only the fork, it read
the sha from the coach digest whose last_reply was clipped before the sha, and the window path wanted "desktop" in
the subject. It then filed and investigated an already-fixed defect.
"""
import json
import os
import sqlite3
import subprocess

from test_self_heal_lane import sh, defect, NOW, Runner, HUB, OPEN, ev, store, _real_adoption_env  # noqa: F401

SYMPTOM = "Blocked card prompt not visible in session panel"
COMPLAINT = NOW - 3600


def _repo(tmp_path, name, subject, path, at=NOW - 3400):
    repo = tmp_path / name
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.com"}

    def git(*a, ts=at):
        return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True,
                              env={**env, "GIT_AUTHOR_DATE": f"{int(ts)} +0000",
                                   "GIT_COMMITTER_DATE": f"{int(ts)} +0000"}).stdout.strip()
    git("init", "-q", "-b", "main")
    (repo / "README").write_text("x")
    git("add", ".")
    git("commit", "-qm", "initial", ts=NOW - 100000)
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("changed")
    git("add", ".")
    git("commit", "-qm", subject)
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    return repo, git("rev-parse", "HEAD")


def _env(monkeypatch, tmp_path, fork=None, mods=None):
    _real_adoption_env(monkeypatch, tmp_path)
    monkeypatch.setenv("COACH_FORK_REPO", str(fork or tmp_path / "no-fork"))
    monkeypatch.setenv("COACH_MODS_REPO", str(mods or tmp_path / "no-mods"))


def _defect():
    return defect(at=NOW - 60, host="hermes-hub", component="desktop", symptom=SYMPTOM, complaint_at=COMPLAINT,
                  source="backstop", session_id="20261102_165350_624f5b")


def _digest(tmp_path, d, last_reply):
    p = tmp_path / "data" / "hermes-hub" / "default" / f"{d['session_id']}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"id": d["session_id"], "host": "hermes-hub", "last_reply": last_reply,
                             "last_activity_at": NOW - 3000,
                             "user_turns": [{"t": COMPLAINT, "text": "figure out why it's not showing the block",
                                             "prev": "ok"}]}))


def _transcript(tmp_path, sid, rows):
    """A local state.db (HERMES_HOME/state.db) with the session's messages: [(role, ts, content)]."""
    db = sqlite3.connect(tmp_path / "home" / "state.db")
    db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT)")
    db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, "
               "content TEXT, timestamp REAL)")
    db.execute("INSERT INTO sessions VALUES (?, 'desktop')", (sid,))
    db.executemany("INSERT INTO messages (session_id, role, timestamp, content) VALUES (?, ?, ?, ?)",
                   [(sid, r, t, c) for r, t, c in rows])
    db.commit()
    db.close()


PANEL_FIX = "fix(session-panel): collapsing never hides what needs you"


def test_mods_sha_named_in_the_complaint_reply_is_adopted_when_the_digest_is_clipped(store, tmp_path, monkeypatch):
    mods, sha = _repo(tmp_path, "mods", PANEL_FIX, "desktop-plugins/session-panel/plugin.js")
    _env(monkeypatch, tmp_path, mods=mods)
    d = _defect()
    _digest(tmp_path, d, "The last follow-up is merged ... Nothing is waiting on you:\n- The se …[clipped")
    _transcript(tmp_path, d["session_id"], [
        ("user", COMPLAINT - 0.4, "figure out why it's not showing the block"),
        ("assistant", COMPLAINT + 240, f"The collapsed panel hid the ask. Fixed in hermes-mods `{sha[:8]}`."),
        ("user", COMPLAINT + 2570, "live chats only"),
        ("assistant", COMPLAINT + 2580, "ok")])
    fix = sh.session_commit_fix(d, COMPLAINT, data=tmp_path / "data", now=NOW)
    assert fix and fix["sha"] == sha and fix["via"] == "named"
    r = Runner()
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out["adopted"] and out["commit"] == sha and not r.creates()


def test_sha_named_only_in_a_later_turn_is_not_bound_to_the_complaint(store, tmp_path, monkeypatch):
    """The complaint's own reply is the binding: a sha the session names while answering a later message is not."""
    mods, sha = _repo(tmp_path, "mods", PANEL_FIX, "desktop-plugins/session-panel/plugin.js")
    _env(monkeypatch, tmp_path, mods=mods)
    d = _defect()
    _digest(tmp_path, d, "…[clipped")
    _transcript(tmp_path, d["session_id"], [
        ("user", COMPLAINT, "figure out why it's not showing the block"),
        ("assistant", COMPLAINT + 240, "Looking; nothing landed yet."),
        ("user", COMPLAINT + 2570, "now fix the collapse animation"),
        ("assistant", COMPLAINT + 2580, f"Done in `{sha[:8]}`.")])
    assert sh.session_commit_fix(d, COMPLAINT, data=tmp_path / "data", now=NOW) is None


def test_window_fix_touching_desktop_plugins_is_adopted_for_component_desktop(store, tmp_path, monkeypatch):
    mods, sha = _repo(tmp_path, "mods", "fix(session-panel): blocked card prompt visible when collapsed",
                      "desktop-plugins/session-panel/plugin.js")
    _env(monkeypatch, tmp_path, mods=mods)
    d = _defect()
    _digest(tmp_path, d, "ok")
    fix = sh.session_commit_fix(d, COMPLAINT, data=tmp_path / "data", now=NOW)
    assert fix and fix["sha"] == sha and fix["via"] == "window"


def test_window_fix_desktop_subject_touching_no_desktop_path_is_not_adopted(store, tmp_path, monkeypatch):
    """`fix(desktop): ...` that changes only gateway code is not a desktop fix, however its subject reads."""
    fork, sha = _repo(tmp_path, "fork", "fix(desktop): blocked card prompt visible when collapsed",
                      "gateway/run.py")
    _env(monkeypatch, tmp_path, fork=fork)
    d = _defect()
    _digest(tmp_path, d, "ok")
    assert sh.session_commit_fix(d, COMPLAINT, data=tmp_path / "data", now=NOW) is None


def test_fixed_event_citing_a_commit_older_than_the_card_logs_late_adopt(store, tmp_path, monkeypatch):
    """Detection: a filed card whose fix predates it shows as selfheal_late_adopt and is counted in status."""
    mods, sha = _repo(tmp_path, "mods", PANEL_FIX, "desktop-plugins/session-panel/plugin.js", at=NOW - 3400)
    _env(monkeypatch, tmp_path, mods=mods)
    d = _defect()
    lane = sh.LaneState(store.root)
    lane.update(d["fingerprint"], status="approved", card="t_0000aaaa1111", filed_at=NOW - 1000,
                component="desktop", symptom=SYMPTOM, session_id=d["session_id"])
    sh.record_event("fixed", d["fingerprint"], note=f"already fixed by {sha[:8]}", store=store, now=NOW)
    (e,) = ev(store, "selfheal_late_adopt")
    assert e["commit"].startswith(sha[:8]) and e["card"] == "t_0000aaaa1111" and e["commit_at"] < e["filed_at"]
    assert sh.late_adopt_counts(store.root, now=NOW)["late_adopt"] == 1
    # a fix committed after the card was filed is the card's own work, not a late adopt
    lane.update(d["fingerprint"], filed_at=NOW - 5000)
    sh.record_event("fixed", d["fingerprint"], note=f"fixed in {sha[:8]}", store=store, now=NOW + 10)
    assert len(ev(store, "selfheal_late_adopt")) == 1
