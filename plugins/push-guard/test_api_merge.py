"""Merges through GitHub's API get the same gates as `gh pr merge`.

`gh api -X PUT repos/<o>/<r>/pulls/<n>/merge` (and curl / Python HTTP PUTs to that endpoint) are held to the exact-head
check and the landing-overlap check; GraphQL mergePullRequest / enablePullRequestAutoMerge calls on a guarded repo
are refused; `gh pr merge --auto` on a require_check repo is refused (the gates run now, GitHub merges later).
Why: agents landed daveey/hermes PRs with `gh api -X PUT .../pulls/158/merge -f sha=...` on 2026-10-28, a path the
guard never saw. Replays PR #475 / #681 from fixtures/landing_overlap_pr475_pr681.json (slug acme/core).
Run: python3 -m pytest -q -p no:cacheprovider test_api_merge.py
"""
import subprocess

import pytest

from tests_guard import CHECK, FakeGH, block, core, guard, run  # noqa: F401  (core is a fixture)
from test_landing_overlap import OVERLAP_475, PR475, PR681, RecordedGH

PUT_SPELLINGS = ["-X PUT", "-XPUT", "--method PUT", "--method=PUT", "-X put"]


def _rest(n, sha, method="-X PUT"):
    return f"gh api {method} repos/acme/core/pulls/{n}/merge -f merge_method=squash -f sha={sha}"


def _assert_475_refused(got):
    assert got and got["action"] == "block", got
    msg = got["message"]
    for path in OVERLAP_475:
        assert path in msg
    assert "41b7bad881c3" in msg and "250" in msg and CHECK in msg
    assert "REST API merge of PR #475" in msg
    assert "kanban_reader_lifecycle.py" not in msg


def test_rest_merge_pr475_terminal_is_refused_naming_both_files(core, monkeypatch):
    work, _ = core
    gh = RecordedGH(PR475, 475, "fix/session-open-stalls")
    monkeypatch.setattr(guard, "_gh_json", gh)
    _assert_475_refused(block("terminal", {"command": _rest(475, PR475["head"]), "workdir": str(work)}))


@pytest.mark.parametrize("form", ["string", "argv"])
def test_rest_merge_pr475_execute_code_is_refused_naming_both_files(core, monkeypatch, form):
    work, _ = core
    gh = RecordedGH(PR475, 475, "fix/session-open-stalls")
    monkeypatch.setattr(guard, "_gh_json", gh)
    if form == "string":
        code = f"terminal({_rest(475, PR475['head'])!r})"
    else:
        code = ('import subprocess\nsubprocess.run(["gh", "api", "-X", "PUT", "repos/acme/core/pulls/475/merge", '
                f'"-f", "merge_method=squash", "-f", "sha={PR475["head"]}"], check=True)\n')
    _assert_475_refused(block("execute_code", {"code": code, "workdir": str(work)}))


@pytest.mark.parametrize("entry", ["terminal", "execute_code"])
def test_rest_merge_pr681_is_admitted(core, monkeypatch, entry):
    work, _ = core
    gh = RecordedGH(PR681, 681, "wt/mirror-singleton")
    monkeypatch.setattr(guard, "_gh_json", gh)
    cmd = _rest(681, PR681["head"])
    args = {"command": cmd, "workdir": str(work)} if entry == "terminal" else {"code": f"terminal({cmd!r})"}
    assert block(entry, args) is None
    assert any("/check-runs" in c[1] for c in gh.calls if c[0] == "api")  # it was checked, not skipped


@pytest.mark.parametrize("method", PUT_SPELLINGS)
def test_every_put_spelling_is_a_merge(core, monkeypatch, method):
    work, _ = core
    gh = RecordedGH(PR475, 475, "fix/session-open-stalls")
    monkeypatch.setattr(guard, "_gh_json", gh)
    _assert_475_refused(block("terminal", {"command": _rest(475, PR475["head"], method), "workdir": str(work)}))


def test_get_of_the_merge_endpoint_is_not_a_merge(core):
    work, gh = core
    assert block("terminal", {"command": "gh api repos/acme/core/pulls/475/merge", "workdir": str(work)}) is None
    assert block("terminal", {"command": "gh api -X GET repos/acme/core/pulls/475/merge", "workdir": str(work)}) is None
    assert not gh.calls


def test_rest_merge_checks_the_sha_field_else_the_pr_head(core):
    work, gh = core
    head, pinned = "a" * 40, "b" * 40
    gh.prs["42"] = {"headRefOid": head, "baseRefName": "main", "number": 42}
    gh.runs[head] = [run("completed", "success")]
    gh.runs[pinned] = [run("in_progress")]
    got = block("terminal", {"command": _rest(42, pinned), "workdir": str(work)})
    assert got and pinned[:12] in got["message"] and "in_progress" in got["message"]
    assert block("terminal", {"command": "gh api -X PUT repos/acme/core/pulls/42/merge -f merge_method=squash",
                              "workdir": str(work)}) is None
    gh.runs[head] = [run("completed", "failure")]
    got = block("terminal", {"command": "gh api --method PUT /repos/acme/core/pulls/42/merge", "workdir": str(work)})
    assert got and head[:12] in got["message"] and "failure" in got["message"]


def test_rest_merge_owner_repo_placeholder_resolves_to_the_cwd_repo(core):
    work, gh = core
    gh.prs["42"] = {"headRefOid": "c" * 40, "baseRefName": "main", "number": 42}
    gh.runs["c" * 40] = [run("queued")]
    got = block("terminal", {"command": "gh api -X PUT 'repos/{owner}/{repo}/pulls/42/merge'", "workdir": str(work)})
    assert got and "queued" in got["message"]


def test_rest_merge_wrapped_in_token_broker_is_seen(core):
    work, gh = core
    gh.prs["42"] = {"headRefOid": "c" * 40, "baseRefName": "main", "number": 42}
    gh.runs["c" * 40] = [run("queued")]
    cmd = 'token-broker exec --scope user/github --reason "land PR" -- gh api -X PUT repos/acme/core/pulls/42/merge ' \
          '-f sha=' + "c" * 40
    assert block("terminal", {"command": cmd, "workdir": str(work)})


@pytest.mark.parametrize("wrapper", ["$B", "${B}", '"$B"'])
def test_variable_wrapper_as_recorded_on_pr158_is_seen(core, wrapper):
    """The 2026-10-28 landing step was `$B gh api -X PUT repos/daveey/hermes/pulls/158/merge ... -f sha=...`, with B
    holding `token-broker exec ... --`: the first word is the variable, not gh."""
    work, gh = core
    gh.prs["158"] = {"headRefOid": "c" * 40, "baseRefName": "main", "number": 158}
    gh.runs["c" * 40] = [run("in_progress")]
    cmd = f"{wrapper} gh api -X PUT repos/acme/core/pulls/158/merge -f merge_method=squash -f sha={'c' * 40}"
    got = block("terminal", {"command": cmd, "workdir": str(work)})
    assert got and "in_progress" in got["message"] and "PR #158" in got["message"]
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work, capture_output=True, text=True).stdout.strip()
    gh.runs[sha] = [run("in_progress")]
    assert block("terminal", {"command": f"{wrapper} git push origin HEAD:main", "workdir": str(work)})


def test_rest_merge_override_and_other_repos_skip(core, monkeypatch):
    work, _ = core
    gh = RecordedGH(PR475, 475, "fix/session-open-stalls")
    monkeypatch.setattr(guard, "_gh_json", gh)
    assert block("terminal", {"command": "PUSH_GUARD=off " + _rest(475, PR475["head"]), "workdir": str(work)}) is None
    assert block("execute_code", {"code": f"terminal({('PUSH_GUARD=off ' + _rest(475, PR475['head']))!r})"}) is None
    other = "gh api -X PUT repos/someone/else/pulls/475/merge -f sha=" + PR475["head"]
    assert block("terminal", {"command": other, "workdir": str(work)}) is None
    assert not gh.calls


def test_curl_put_to_the_merge_endpoint_is_gated(core):
    work, gh = core
    gh.prs["42"] = {"headRefOid": "d" * 40, "baseRefName": "main", "number": 42}
    gh.runs["d" * 40] = [run("completed", "cancelled")]
    cmd = ('curl -sS -X PUT -H "Authorization: Bearer $GH_TOKEN" '
           'https://api.github.com/repos/acme/core/pulls/42/merge -d \'{"merge_method":"squash"}\'')
    got = block("terminal", {"command": cmd, "workdir": str(work)})
    assert got and "cancelled" in got["message"]


def test_python_http_put_to_the_merge_endpoint_is_gated(core):
    work, gh = core
    gh.prs["26"] = {"headRefOid": "e" * 40, "baseRefName": "main", "number": 26}
    gh.runs["e" * 40] = [run("in_progress")]
    code = ("import json, urllib.request\n"
            "req = urllib.request.Request('https://api.github.com/repos/acme/core/pulls/26/merge', method='PUT',\n"
            "    data=json.dumps({'merge_method': 'squash', 'sha': '" + "e" * 40 + "'}).encode())\n"
            "urllib.request.urlopen(req)\n")
    got = block("execute_code", {"code": code, "workdir": str(work)})
    assert got and "e" * 12 in got["message"] and "in_progress" in got["message"]


# --- GraphQL ---------------------------------------------------------------------------------------------------

class GraphGH(FakeGH):
    """FakeGH that also answers the PR-node lookup."""

    def __init__(self, nodes):
        super().__init__()
        self.nodes = nodes

    def __call__(self, args, cwd=None):
        if args[:2] == ["api", "graphql"]:
            self.calls.append(list(args))
            for node, repo in self.nodes.items():
                if node in args[-1]:
                    return {"data": {"node": {"repository": {"nameWithOwner": repo}}}}, None
            return {"data": {"node": None}}, None
        return super().__call__(args, cwd)


MUTATION = ("gh api graphql -f query='mutation {\n  mergePullRequest(input: {pullRequestId: \"PR_kwDOguarded1\", "
            "mergeMethod: SQUASH}) { pullRequest { merged } }\n}'")


@pytest.fixture
def graph(core, monkeypatch):
    work, _ = core
    gh = GraphGH({"PR_kwDOguarded1": "acme/core", "PR_kwDOelsewhere": "someone/else"})
    monkeypatch.setattr(guard, "_gh_json", gh)
    return work, gh


def test_graphql_merge_pull_request_on_a_guarded_repo_is_refused(graph):
    work, gh = graph
    got = block("terminal", {"command": MUTATION, "workdir": str(work)})
    assert got and got["action"] == "block"
    assert "mergePullRequest" in got["message"] and "acme/core main" in got["message"]
    assert "gh pr merge" in got["message"] and "--match-head-commit" in got["message"]
    assert block("terminal", {"command": "PUSH_GUARD=off " + MUTATION, "workdir": str(work)}) is None


def test_graphql_merge_through_execute_code_and_auto_merge_mutation(graph):
    work, _ = graph
    code = ('import subprocess\nq = """mutation { enablePullRequestAutoMerge(input: {pullRequestId: '
            '"PR_kwDOguarded1"}) { clientMutationId } }"""\n'
            'subprocess.run(["gh", "api", "graphql", "-f", "query=" + q])\n')
    got = block("execute_code", {"code": code, "workdir": str(work)})
    assert got and "enablePullRequestAutoMerge" in got["message"]
    assert block("execute_code", {"code": "PUSH_GUARD=off\n" + code}) is None


def test_graphql_merge_on_an_unguarded_repo_is_allowed(graph):
    work, _ = graph
    assert block("terminal", {"command": MUTATION.replace("PR_kwDOguarded1", "PR_kwDOelsewhere"),
                              "workdir": str(work)}) is None


def test_graphql_merge_whose_repo_cannot_be_told_is_refused(graph):
    work, _ = graph
    cmd = "gh api graphql -F id=\"$PR_ID\" -f query='mutation($id: ID!) { mergePullRequest(input: {pullRequestId: $id}) { clientMutationId } }'"
    got = block("terminal", {"command": cmd, "workdir": str(work)})
    assert got and "cannot identify" in got["message"]


def test_naming_the_mutation_without_calling_graphql_is_not_a_merge(graph):
    work, gh = graph
    assert block("terminal", {"command": "grep -rn mergePullRequest ~/.hermes/skills", "workdir": str(work)}) is None
    assert block("execute_code", {"code": "print('mergePullRequest' in open('notes.md').read())"}) is None
    assert not gh.calls


# --- gh pr merge --auto ------------------------------------------------------------------------------------------

def test_auto_merge_on_a_require_check_repo_is_refused(core):
    work, gh = core
    gh.prs["329"] = {"headRefOid": "f" * 40, "baseRefName": "main", "number": 329}
    gh.runs["f" * 40] = [run("completed", "success")]  # green now; GitHub would merge later
    got = block("terminal", {"command": "gh pr merge 329 -R acme/core --auto --rebase --match-head-commit " + "f" * 40,
                             "workdir": str(work)})
    assert got and "--auto" in got["message"] and "merge now" in got["message"]
    assert block("terminal", {"command": "gh pr merge 329 -R acme/core --rebase --match-head-commit " + "f" * 40,
                              "workdir": str(work)}) is None
    gh.prs["329"]["baseRefName"] = "release"
    assert block("terminal", {"command": "gh pr merge 329 -R acme/core --auto", "workdir": str(work)}) is None


def test_auto_merge_on_a_merge_train_repo_is_left_alone(core, tmp_path):
    _, gh = core
    assert block("terminal", {"command": "gh pr merge 1189 -R owner/repo --squash --auto",
                              "workdir": str(tmp_path)}) is None
    assert not gh.calls
