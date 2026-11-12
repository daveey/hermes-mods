"""An admission's fix usually lands BEFORE the admission (defect plugin-aafb20159a).

The MacBook session 20261026_235722_bef6c6 shipped hermes-mods 2c0772cc ("follow-through: a subscription made after
the card completed is not a missed notice", body names the session) at 11:47:54 PST and reported it at 11:55:10. The
detector turned that report into admission plugin-aafb20159a with complaint_at = the report. session_commit_fix only
searched commits AFTER complaint_at, and the commit's subject shares just one stemmed word with the symptom, so the
lane filed an investigation card that rediscovered the fix.

Now: for an admission the search starts ADMISSION_FIX_LOOKBACK_S before complaint_at, and a commit whose message
names the origin session id is defect-bound when its subject and body share max(2, a third) of the symptom's
distinctive words. An unrelated fix from the same session (names the session, shares one word: "task") is still not
adopted, and a complaint (not a post-fix report) still searches only after complaint_at.
"""
import json
import os
import subprocess

from test_self_heal_lane import sh, defect, NOW, Runner, HUB, OPEN, ev, store, _real_adoption_env  # noqa: F401

SID = "20261026_235722_bef6c6"
SYMPTOM = "False completion reminders for subscriptions created after task completion"
COMPLAINT = NOW - 3600
FIX_SUBJECT = "follow-through: a subscription made after the card completed is not a missed notice"
FIX_BODY = (f"A chat that subscribes to a card only after it completed starts past the completion event.\n"
            f"Seen live: chat {SID} commented on the done card 62 min after it completed.")


def _repo(tmp_path, commits):
    """A repo whose origin/main holds `commits`: [(subject, body, ts)], oldest first. -> (repo, [sha...])."""
    repo = tmp_path / "mods"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.com"}

    def git(*a, ts=NOW - 100000):
        return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True,
                              env={**env, "GIT_AUTHOR_DATE": f"{int(ts)} +0000",
                                   "GIT_COMMITTER_DATE": f"{int(ts)} +0000"}).stdout.strip()
    git("init", "-q", "-b", "main")
    (repo / "README").write_text("x")
    git("add", ".")
    git("commit", "-qm", "initial")
    shas = []
    for i, (subject, body, ts) in enumerate(commits):
        p = repo / "plugins" / "follow-through" / f"f{i}.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(i))
        git("add", ".")
        git("commit", "-qm", subject, "-m", body, ts=ts)
        shas.append(git("rev-parse", "HEAD"))
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    return repo, shas


def _env(monkeypatch, tmp_path, mods):
    _real_adoption_env(monkeypatch, tmp_path)
    monkeypatch.setenv("COACH_FORK_REPO", str(tmp_path / "no-fork"))
    monkeypatch.setenv("COACH_MODS_REPO", str(mods))


def _defect(source="admission"):
    return defect(at=NOW - 60, host="macbook", component="plugin", symptom=SYMPTOM, complaint_at=COMPLAINT,
                  source=source, session_id=SID, quote="the missed flag now requires the subscription to predate it")


def _digest(tmp_path, turn_prev="ok"):
    p = tmp_path / "data" / "macbook" / "default" / f"{SID}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"id": SID, "host": "macbook", "last_reply": "unrelated later work",
                             "last_activity_at": NOW - 600,
                             "user_turns": [{"t": COMPLAINT + 900, "text": "next notice", "prev": turn_prev}]}))


def test_admission_fix_committed_minutes_before_the_report_is_adopted(store, tmp_path, monkeypatch):
    """The real shape: the fix is 8 min before complaint_at and the digest's reply names its sha."""
    mods, (sha,) = _repo(tmp_path, [(FIX_SUBJECT, FIX_BODY, COMPLAINT - 436)])
    _env(monkeypatch, tmp_path, mods)
    d = _defect()
    _digest(tmp_path, turn_prev=f"Fixed it (hermes-mods {sha[:8]}). Two new tests failed before the change.")
    fix = sh.session_commit_fix(d, COMPLAINT, data=tmp_path / "data", now=NOW)
    assert fix and fix["sha"] == sha and fix["via"] == "named"
    r = Runner()
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out["adopted"] and out["commit"] == sha and not r.creates()


def test_admission_fix_naming_the_session_is_adopted_without_a_named_sha(store, tmp_path, monkeypatch):
    """The commit message naming the origin session is the binding even when no reply names the sha."""
    mods, (sha,) = _repo(tmp_path, [(FIX_SUBJECT, FIX_BODY, COMPLAINT - 436)])
    _env(monkeypatch, tmp_path, mods)
    _digest(tmp_path)
    fix = sh.session_commit_fix(_defect(), COMPLAINT, data=tmp_path / "data", now=NOW)
    assert fix and fix["sha"] == sha and fix["via"] == "session_named"


def test_unrelated_same_session_fix_before_the_admission_is_not_adopted(store, tmp_path, monkeypatch):
    """Naming the session is provenance, not subject: a nearby fix for another defect it reported stays unbound."""
    mods, (sha,) = _repo(tmp_path, [("fix(board): strip a refused task column and resend",
                                     f"Seen in chat {SID}.", COMPLAINT - 300)])
    _env(monkeypatch, tmp_path, mods)
    _digest(tmp_path, turn_prev=f"Landed {sha[:8]}.")
    d = _defect()
    assert sh.session_commit_fix(d, COMPLAINT, data=tmp_path / "data", now=NOW) is None
    assert not sh._commit_binds_defect(mods, sha, "fix(board): strip a refused task column and resend", d)


def test_one_shared_word_without_the_session_id_is_not_binding(store, tmp_path, monkeypatch):
    """The f83c4186 subject rule is unchanged: one shared word from another chat's commit binds nothing."""
    body = "A chat that subscribes late is not missed. Seen live in chat 20261101_101010_aaaaaa."
    mods, (sha,) = _repo(tmp_path, [(FIX_SUBJECT, body, COMPLAINT - 436)])
    _env(monkeypatch, tmp_path, mods)
    _digest(tmp_path, turn_prev=f"Landed {sha[:8]}.")
    assert sh.session_commit_fix(_defect(), COMPLAINT, data=tmp_path / "data", now=NOW) is None


def test_session_named_commit_long_after_the_admission_is_not_adopted(store, tmp_path, monkeypatch):
    """Live sweep: kanban-a3ba4c03a8 (same session, "Stale completion notices delivered as fresh turns") shares two
    words with 2c0772cc, which landed 17 h after that admission: later work for another defect, not its fix."""
    mods, (sha,) = _repo(tmp_path, [(FIX_SUBJECT, FIX_BODY, COMPLAINT + 17 * 3600)])
    _env(monkeypatch, tmp_path, mods)
    _digest(tmp_path)
    d = defect(at=NOW - 60, host="macbook", component="kanban", symptom="Stale completion notices delivered as fresh "
               "turns", complaint_at=COMPLAINT, source="admission", session_id=SID)
    assert sh.session_commit_fix(d, COMPLAINT, data=tmp_path / "data", now=NOW + 20 * 3600) is None


def test_lookback_is_bounded(store, tmp_path, monkeypatch):
    mods, (sha,) = _repo(tmp_path, [(FIX_SUBJECT, FIX_BODY, COMPLAINT - sh.ADMISSION_FIX_LOOKBACK_S - 60)])
    _env(monkeypatch, tmp_path, mods)
    _digest(tmp_path, turn_prev=f"Fixed it (hermes-mods {sha[:8]}).")
    assert sh.session_commit_fix(_defect(), COMPLAINT, data=tmp_path / "data", now=NOW) is None


def test_user_complaint_does_not_look_before_the_complaint(store, tmp_path, monkeypatch):
    """A user complaint raised after a commit says that commit did not fix it: no lookback for complaints."""
    mods, (sha,) = _repo(tmp_path, [(FIX_SUBJECT, FIX_BODY, COMPLAINT - 436)])
    _env(monkeypatch, tmp_path, mods)
    _digest(tmp_path, turn_prev=f"Fixed it (hermes-mods {sha[:8]}).")
    assert sh.session_commit_fix(_defect("complaint"), COMPLAINT, data=tmp_path / "data", now=NOW) is None
