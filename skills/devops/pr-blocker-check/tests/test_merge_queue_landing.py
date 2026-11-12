"""A merge queue (e.g. Graphite) lands a PR as a squash `... (#N)` on the base branch and closes it:
state CLOSED, mergedAt null. The checker must report that as merged, and never as closed when the
commit lookup fails."""
import importlib.util
import json
import pathlib
import subprocess
from urllib.parse import parse_qs, urlparse

p = pathlib.Path(__file__).parents[1] / "scripts" / "pr_blocker_check.py"
spec = importlib.util.spec_from_file_location("pbc_mq", p)
pbc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pbc)  # type: ignore[union-attr]

REPO, NUM = "example-org/example-repo", "4242"
CLOSED_PR = dict(state="CLOSED", mergedAt=None, closedAt="2026-01-10T19:29:31Z", baseRefName="main",
                 headRefOid="feedface", mergeable="UNKNOWN", mergeStateStatus="UNKNOWN",
                 reviewDecision="APPROVED", statusCheckRollup=[], reviewRequests=[])
SHA = "d105a38c85aabbccddeeff00112233445566778899"


def commit(sha, msg):
    return {"sha": sha, "commit": {"message": msg}}


def fake_gh(monkeypatch, commits_result, calls=None):
    """commits_result: list (served as JSON), a str (raw stdout), or an int (gh exit code)."""
    def run(cmd, **kw):
        if calls is not None:
            calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(cmd, 0, json.dumps(CLOSED_PR), "")
        assert cmd[:2] == ["gh", "api"], cmd
        if isinstance(commits_result, int):
            if kw.get("check"):
                raise subprocess.CalledProcessError(commits_result, cmd, "", "HTTP 502")
            return subprocess.CompletedProcess(cmd, commits_result, "", "HTTP 502")
        out = commits_result if isinstance(commits_result, str) else json.dumps(commits_result)
        return subprocess.CompletedProcess(cmd, 0, out, "")
    monkeypatch.setattr(pbc.subprocess, "run", run)


def verdict(capsys, rc):
    return rc, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_a_queue_squash_on_base_is_merged(monkeypatch, capsys):
    calls = []
    fake_gh(monkeypatch, [commit("1" * 40, "other change (#4241)"),
                          commit(SHA, "fix the thing (#4242)\n\nbody text (#1)")], calls)
    rc, v = verdict(capsys, pbc.main(["x", REPO, NUM]))
    assert rc == 0 and v["verdict"] == "merged"
    assert SHA[:10] in v["detail"] and "merge queue" in v["detail"]
    api = [c for c in calls if c[:2] == ["gh", "api"]][0]
    q = parse_qs(urlparse(api[2]).query)
    assert urlparse(api[2]).path == "repos/%s/commits" % REPO
    assert q["sha"] == ["main"]
    assert q["since"] == ["2026-01-10T17:29:31Z"] and q["until"] == ["2026-01-10T19:44:31Z"]


def test_b_no_matching_commit_stays_closed(monkeypatch, capsys):
    fake_gh(monkeypatch, [commit("1" * 40, "unrelated change"), commit("2" * 40, "another (#7)")])
    rc, v = verdict(capsys, pbc.main(["x", REPO, NUM]))
    assert rc == 50 and v["verdict"] == "closed"


def test_b_empty_window_stays_closed(monkeypatch, capsys):
    fake_gh(monkeypatch, [])
    rc, v = verdict(capsys, pbc.main(["x", REPO, NUM]))
    assert rc == 50 and v["verdict"] == "closed"


def test_c_lookup_failure_diagnoses_never_closed(monkeypatch, capsys):
    for bad in (1, "", "Denied: quota guard", "{not json", json.dumps({"message": "Not Found"})):
        fake_gh(monkeypatch, bad)
        rc, v = verdict(capsys, pbc.main(["x", REPO, NUM]))
        assert rc == 40 and v["action"] == "diagnose" and v["verdict"] != "closed", bad


def test_d_other_pr_number_does_not_count(monkeypatch, capsys):
    fake_gh(monkeypatch, [commit("3" * 40, "near miss (#42420)"), commit("4" * 40, "other (#424)"),
                          commit("5" * 40, "mentions (#4242) but lands (#4243)")])
    rc, v = verdict(capsys, pbc.main(["x", REPO, NUM]))
    assert rc == 50 and v["verdict"] == "closed"


def test_truncated_page_without_match_is_unknown(monkeypatch, capsys):
    fake_gh(monkeypatch, [commit("%040d" % i, "c %d (#%d)" % (i, i)) for i in range(100)])
    rc, v = verdict(capsys, pbc.main(["x", REPO, NUM]))
    assert rc == 40 and v["action"] == "diagnose"


def test_github_merged_state_needs_no_lookup():
    assert pbc.classify(dict(CLOSED_PR, state="MERGED"), lookup=lambda pr: 1 / 0)["verdict"] == "merged"
