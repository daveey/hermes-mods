"""search_cap (agent-321e1c0705): a GitHub read that prints a count of exactly 1000 is GitHub's search cap, not a total.

2026-11-14 a hub agent audited "PRs merged into main since 11-11 without the ship status" with a hand-written
`gh api graphql --paginate` over `search(type: ISSUE)`. GitHub search returns at most 1000 nodes and `--paginate`
stops there quietly, so the script printed `merged_into_main_since_11-11=1000 ... count=0` and the agent told David
no merge had skipped ship. An uncapped re-check found two (#1489, #3087). The 1000 was in the tool result; nothing
said it was the cap. The coach now attaches one hint to that result.
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rules = load("coach_rules_test_search_cap", ROOT / "agent_rules.py")
core = load("coach_core_test_search_cap", ROOT / "core.py")


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def coach(tmp_path):
    return rules.AgentCoach(None, signals=core.Store(tmp_path / "coach"), clock=Clock())


# Replay of the incident call (hub state.db msg 1295550) and its result (msg 1295552).
INCIDENT_CMD = ("ls -la --time-style=+%TZ ~/.hermes/cache/scratch/hubchat8d8534-shipaudit.tsv 2>/dev/null; "
                "token-broker exec --scope user/github --reason \"hub chat 8d8534: read-only audit of main merges "
                "lacking the ship status, plus PR states #3102/#3105\" -- bash -c 'bash "
                "~/.hermes/cache/scratch/hubchat8d8534-shipaudit.sh; for n in 3102 3105; do gh pr view $n "
                "-R softmax-universe/daveey-hermes --json state,headRefOid,mergedAt; done'")
INCIDENT_OUT = json.dumps({"output": (
    "read_at=2026-11-14T22:51:38Z\nmerged_into_main_since_11-11=1000 with_ship_status=993\n"
    "first_merge_with_ship_status=2026-11-12T15:59:33Z\n"
    "== merged after that without the ship status (sync/upstream-* excluded):\ncount=0 upstream_syncs=7\n"
    "done_at=2026-11-14T22:52:01Z\n#3102 OPEN 6f1ebdf93270 merged=<no value>\n"
    "#3105 MERGED e60e455c0a32 merged=2026-11-14T22:49:30Z\n22:52:02Z"), "exit_code": 0, "error": None})


def test_incident_replay_names_the_cap():
    hit = rules.search_cap("terminal", {"command": INCIDENT_CMD}, INCIDENT_OUT)
    assert hit and "1000" in hit


def test_incident_hint_lands_on_the_result(tmp_path):
    c = coach(tmp_path)
    c.check_search_cap("s1", "terminal", {"command": INCIDENT_CMD}, INCIDENT_OUT)
    hint = c.take("s1")
    assert hint and "1000" in hint and "merged_since.py" in hint and "issueCount" in hint
    # once per session
    c.check_search_cap("s1", "terminal", {"command": INCIDENT_CMD}, INCIDENT_OUT)
    assert c.take("s1") is None


def test_observe_queues_it(tmp_path):
    c = coach(tmp_path)
    c.observe("s2", "terminal", {"command": INCIDENT_CMD}, INCIDENT_OUT)
    hint = c.take("s2")
    assert hint and "GitHub" in hint and "1000" in hint


def test_other_capped_shapes():
    for cmd, out in [
        ("gh api graphql --paginate -F query=@q.graphql --jq '.data.search.nodes[]' | wc -l", "1000\n"),
        ("gh search prs --repo o/r --merged --limit 1000 --json number | jq length", "1000"),
        ("gh run list -R o/r --limit 1000 --json databaseId | jq length", "runs: 1000"),
        ("gh api 'search/issues?q=repo:o/r+is:pr' --paginate --jq '.items[].number' | wc -l", "  1,000\n"),
    ]:
        assert rules.search_cap("terminal", {"command": cmd}, json.dumps({"output": out, "exit_code": 0})), cmd
    code = "import subprocess\nout = subprocess.run(['gh', 'api', 'graphql', '--paginate'])\nprint('total', n)"
    assert rules.search_cap("execute_code", {"code": code}, json.dumps({"status": "success", "output": "total 1000"}))


def test_no_hint_without_github_or_without_a_capped_count():
    gh = "gh api graphql --paginate -F query=@q.graphql | wc -l"
    for cmd, out in [
        ("sqlite3 kanban.db 'select priority from tasks'", "1000\n1000"),           # not a GitHub read
        (gh, "999\n"),                                                               # under the cap
        (gh, "1001\n"),                                                              # a real total over it
        (gh, "10000\n"),
        (gh, "run 21000 started at 2026-11-14T10:00:00Z, id=431000"),               # 1000 inside other numbers
        (gh, "elapsed 1000.5s"),                                                     # not a count
        ("cat notes.md  # mentions github", "the search cap is 1000 results"),       # prose about the cap: no count
    ]:
        assert rules.search_cap("terminal", {"command": cmd}, json.dumps({"output": out, "exit_code": 0})) is None, (cmd, out)
    # a failed read is another problem
    assert rules.search_cap("terminal", {"command": gh}, json.dumps({"output": "1000", "exit_code": 1})) is None
