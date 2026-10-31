"""Real git histories for the strict traceback fallback, including unchanged raise guards."""
import os
import subprocess

import pytest

from test_self_heal_lane import sh, defect, NOW, Runner, HUB, OPEN, ev, store, _real_adoption_env

MESSAGE = "Managed subscription pins cannot override credentials or endpoint"
BEFORE = f'def resolve():\n    if old_endpoint:\n        raise ModelRoutingError("{MESSAGE}")\n    return old_value\n'


def history(tmp_path, after, path="hermes_cli/runtime_provider.py", subject="fix(routing): handle endpoint", at=NOW - 600, before=BEFORE):
    repo = tmp_path / "fork"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
           "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com"}
    def git(*args, ts=at):
        return subprocess.run(["git", "-C", str(repo), *args], env={**env,
            "GIT_AUTHOR_DATE": f"{int(ts)} +0000", "GIT_COMMITTER_DATE": f"{int(ts)} +0000"},
            check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q", "-b", "main")
    for name in ("hermes_cli/runtime_provider.py", "other/runtime_provider.py"):
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(before)
    git("add", ".")
    git("commit", "-qm", "initial", ts=NOW - 100000)
    (repo / path).write_text(after)
    git("add", ".")
    git("commit", "-qm", subject)
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    return repo, git("rev-parse", "HEAD")


def signal(**kw):
    return defect(component="agent", session_id="", source="signal", at=NOW - 60,
                  symptom=f"ModelRoutingError: {MESSAGE}", details=(
                      'Traceback (most recent call last):\n  File "/install/agent/run.py", line 1, in run\n'
                      '  File "/install/hermes_cli/runtime_provider.py", line 3, in resolve\n'
                      f'    raise ModelRoutingError("{MESSAGE}")\nModelRoutingError: {MESSAGE}'), **kw)


@pytest.mark.parametrize("after", [BEFORE.replace(MESSAGE, "Endpoint refused"),
                                     BEFORE.replace("old_endpoint", "foreign_endpoint")])
@pytest.mark.parametrize("sid", ["", "missing-digest"])
def test_no_session_or_digest_adopts_raise_or_its_guard(store, tmp_path, monkeypatch, after, sid):
    _real_adoption_env(monkeypatch, tmp_path)
    repo, sha = history(tmp_path, after)
    monkeypatch.setenv("COACH_FORK_REPO", str(repo))
    d = signal()
    d["session_id"] = sid
    r = Runner()
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out["adopted"] and out["commit"] == sha and not r.creates()
    assert sh.LaneState(store.root).get(d["fingerprint"])["status"] == "fixed"
    assert ev(store, "selfheal_adopted_owner")[0]["via"] == "commit_no_session"


@pytest.mark.parametrize("after,path,subject,at", [
    (BEFORE.replace(MESSAGE, "new"), "other/runtime_provider.py", "fix: other file", NOW - 600),
    (BEFORE.replace("old_value", "new_value"), "hermes_cli/runtime_provider.py", "fix: unrelated line", NOW - 600),
    (BEFORE.replace(MESSAGE, "new"), "hermes_cli/runtime_provider.py", "feat: not a fix", NOW - 600),
    (BEFORE.replace(MESSAGE, "new"), "hermes_cli/runtime_provider.py", "fix: too old", NOW - 90000),
    (BEFORE.replace(MESSAGE, "new"), "hermes_cli/runtime_provider.py", "fix: future", NOW + 600),
])
def test_fallback_rejects_nearby_unrelated_or_out_of_window_commit(tmp_path, after, path, subject, at):
    repo, _ = history(tmp_path, after, path, subject, at)
    assert sh.session_commit_fix(signal(), NOW - 60, data=tmp_path / "data", repo=repo, now=NOW) is None


def test_readable_session_digest_preserves_old_window(tmp_path):
    repo, _ = history(tmp_path, BEFORE.replace(MESSAGE, "new"))
    d = signal()
    d["session_id"] = "has-digest"
    p = tmp_path / "data" / "macbook" / "default" / "has-digest.json"
    p.parent.mkdir(parents=True)
    p.write_text('{"last_activity_at": 1}')
    assert sh.session_commit_fix(d, NOW - 60, data=tmp_path / "data", repo=repo, now=NOW) is None


@pytest.mark.parametrize("contents", ["{broken", "[]"])
def test_unreadable_or_non_dict_digest_uses_fallback(tmp_path, contents):
    repo, sha = history(tmp_path, BEFORE.replace(MESSAGE, "new"))
    d = signal()
    d["session_id"] = "broken-digest"
    p = tmp_path / "data" / "macbook" / "default" / "broken-digest.json"
    p.parent.mkdir(parents=True)
    p.write_text(contents)
    assert sh.session_commit_fix(d, d["at"], data=tmp_path / "data", repo=repo, now=NOW)["sha"] == sha


def test_same_message_in_different_function_is_not_the_raise_site(tmp_path):
    before = BEFORE + BEFORE.replace("def resolve", "def unrelated")
    after = BEFORE + BEFORE.replace("def resolve", "def unrelated").replace(MESSAGE, "new")
    repo, _ = history(tmp_path, after, before=before)
    assert sh.session_commit_fix(signal(), NOW - 60, data=tmp_path / "data", repo=repo, now=NOW) is None
