"""Land turn for state merges (t_01a1d6b3538e8e43): ordering, never admitting. Fake gh only; never merges."""
import hashlib
import importlib.util
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("turn_guard", Path(__file__).with_name("guard.py"))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

R = "example/protected"
NOW = 1_794_604_000
BASE = "3" * 40
H2563 = "4559fc21fb586bf9b813010e973ba04a84eac8bf"
H2600 = "b" * 40
# #2563's files at its 4th refusal (2026-11-13 20:53 UTC).
F2563 = ["hermes-sync", "home/skills/hermes-mods-repo-sync-pitfalls/references/core-update-runtime.md",
         "tests/test_core_drain_gateway_chat_pause.py", "tests/test_core_park_ack.py"]


def pr(number, head, files):
    return {"number": number, "state": "OPEN", "baseRefName": "state", "headRefOid": head, "baseRefOid": BASE,
            "url": f"https://github.com/{R}/pull/{number}", "comments": [],
            "reviews": [{"id": f"PRR_{number}", "author": {"login": "you"}, "authorAssociation": "MEMBER",
                         "state": "COMMENTED", "submittedAt": "2026-11-13T15:00:00Z", "commit": {"oid": head},
                         "body": f"Independent exact-head review @ {head}: approve"}],
            "files": [{"path": f} for f in files],
            "statusCheckRollup": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}]}


def delta(files, subject="base change", when=NOW):
    from datetime import datetime, timezone
    date = datetime.fromtimestamp(when, timezone.utc).isoformat()
    return {"status": "ahead", "ahead_by": 1, "behind_by": 0, "total_commits": 1, "_when": when,
            "commits": [{"sha": "c" * 40, "parents": [{"sha": BASE}], "commit": {"message": subject, "committer": {"date": date}}}],
            "files": [{"filename": f, "status": "modified", "previous_filename": None} for f in files]}


IDENTICAL = {"status": "identical", "ahead_by": 0, "behind_by": 0, "total_commits": 0, "commits": [], "files": []}


class FakeGitHub:
    """Just enough of the REST API: PR views, the drift compare, git commits/refs and PR state."""

    def __init__(self, prs, compare=IDENTICAL, pr_state=None, refs_fail=False):
        self.prs, self.compare, self.pr_state, self.refs_fail = prs, compare, pr_state or {}, refs_fail
        self.commits = {h: {"sha": h, "tree": {"sha": "e" * 40}, "message": "x"} for h in (H2563, H2600)}
        self.refs, self.calls = {}, []
        self.activity, self.page_size = None, 100      # push events newest first; None: one push BASE -> compare tip

    def _activity(self, path):
        events = self.activity
        if events is None:
            c = self.compare
            events = ([push_event(BASE, c["commits"][-1]["sha"], c.get("_when", NOW))] if c.get("commits") else [])
        m = re.search(r"[&?]after=cur(\d+)", path)
        start = int(m[1]) if m else 0
        page = events[start:start + self.page_size]
        link = (f'Link: <https://api.github.com/repositories/1/activity?ref=refs%2Fheads%2Fstate&per_page=100'
                f'&after=cur{start + self.page_size}>; rel="next"\r\n'
                if start + self.page_size < len(events) else "")
        return SimpleNamespace(returncode=0, stderr="",
                               stdout=f"HTTP/2.0 200 OK\r\nContent-Type: application/json\r\n{link}\r\n{json.dumps(page)}")

    def run(self, argv, **kw):
        self.calls.append(argv)
        ok = lambda body: SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")
        fail = SimpleNamespace(returncode=1, stdout=json.dumps({"message": "Not Found"}), stderr="")
        if argv[:3] == ["gh", "pr", "view"]:
            n = int(argv[3])
            data = dict(self.prs[n])
            data["state"] = self.pr_state.get(n, data["state"]).upper()
            return ok(data)
        args = argv[2:]
        method = "GET"
        if args[0] == "-X":
            method, args = args[1], args[2:]
        path = args[0]
        body = json.loads(kw["input"]) if "--input" in args else None
        if "/activity?" in path:
            assert "-i" in args and "page=" not in path.replace("per_page=", ""), argv
            return self._activity(path)
        if body is not None and method == "GET":
            method = "POST"
        if path == "graphql":
            pairs = re.findall(r'(t\d+): object\(oid: "([0-9a-f]+)"\)', body["query"])
            return ok({"data": {"repository": {alias: self.commits.get(sha) for alias, sha in pairs}}})
        if "/compare/" in path:
            tail = path.rsplit("/compare/", 1)[1]
            edge = getattr(self, "edge_compare", {}).get(tail)       # immutable `<first-parent>...<merge>` edges
            return ok(edge) if edge is not None else ok(self.compare)
        if "/commits/" in path and "/git/" not in path:
            sha = path.rsplit("/", 1)[1]
            return ok(self.commits[sha]) if sha in self.commits else fail
        if "/commits?" in path:
            return fail
        if path.endswith("/git/matching-refs/land-turn/pr-"):
            if self.refs_fail:
                return fail
            rows = [{"ref": r, "object": {"sha": s}} for r, s in sorted(self.refs.items())]
            return ok([rows[:4], rows[4:]] if "--slurp" in args else rows)
        if path.endswith("/git/commits") and method == "POST":
            sha = hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()
            self.commits[sha] = {"sha": sha, "tree": {"sha": body["tree"]}, "message": body["message"],
                                 "parents": body["parents"]}
            return ok(self.commits[sha])
        if "/git/commits/" in path:
            c = self.commits.get(path.rsplit("/", 1)[1])
            return ok(c) if c else fail
        if "/git/ref/" in path:
            ref = "refs/" + path.split("/git/ref/", 1)[1]
            return ok({"ref": ref, "object": {"sha": self.refs[ref]}}) if ref in self.refs else fail
        if path.endswith("/git/refs") and method == "POST":
            if body["ref"] in self.refs:
                return fail
            self.refs[body["ref"]] = body["sha"]
            return ok({"ref": body["ref"]})
        if "/git/refs/" in path:
            ref = "refs/" + path.split("/git/refs/", 1)[1]
            if method == "DELETE":
                self.refs.pop(ref, None)
                return ok({})
            if method == "PATCH":
                self.refs[ref] = body["sha"]
                return ok({"ref": ref})
        if "/pulls/" in path:
            return ok({"state": self.pr_state.get(int(path.rsplit("/", 1)[1]), "open")})
        raise AssertionError(f"unexpected gh call {argv}")

    def turn(self, number):
        sha = self.refs.get(f"refs/land-turn/pr-{number}")
        return g.parse_turn(self.commits[sha]["message"]) if sha else None


@pytest.fixture
def gh(monkeypatch):
    holder = {}

    def make(*a, **kw):
        fake = FakeGitHub(*a, **kw)
        monkeypatch.setattr(g.subprocess, "run", fake.run)
        holder["fake"] = fake
        return fake
    monkeypatch.setattr(g, "_now", lambda: NOW)
    return make


def merge(number, head):
    # Same entry the adapter and `--gh-args` use: the canonical aggregate deadline (g.PREFLIGHT_TIMEOUT).
    return g.bounded_preflight(
        g.check_argv, ["gh", "pr", "merge", str(number), "--repo", R, "--rebase", "--match-head-commit", head],
        repos={R}, env={"GH_REPO": R})


def seed_turn(fake, number, acquired, files=None):
    rec = {"pr": number, "head": H2563, "acquired": acquired,
           "expires": acquired + g._TURN_TTL, "files": files or ["hermes-sync"]}
    sha = hashlib.sha1(json.dumps(rec).encode()).hexdigest()
    fake.commits[sha] = {"sha": sha, "message": f"land-turn pr={number}\n\n{json.dumps(rec)}"}
    fake.refs[f"refs/land-turn/pr-{number}"] = sha


def test_three_overlapping_turns_have_one_oldest_winner(gh):
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in (2563, 2579, 2555)})
    for n, age in ((2563, 120), (2579, 60), (2555, 30)):
        seed_turn(fake, n, NOW - age)
    assert merge(2563, H2563) is None
    for n in (2579, 2555):
        assert "PR #2563 holds" in merge(n, H2563)


def test_eight_refs_do_not_hide_oldest_holder(gh):
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in range(1, 10)})
    for n in range(1, 9):
        seed_turn(fake, n, NOW - 100 + n)
    assert "PR #1 holds" in merge(9, H2563)


def test_large_paginated_snapshot_keeps_oldest(gh):
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in range(1, 103)})
    for n in range(1, 102):
        seed_turn(fake, n, NOW - 200 + n)
    assert "PR #1 holds" in merge(102, H2563)
    assert len([c for c in fake.calls if c[1:3] == ["api", "graphql"]]) == 2


@pytest.mark.parametrize("object_value", [{"message": "broken"}, {}, None])
def test_malformed_record_names_owner_and_recovers_without_peer_writes(gh, object_value):
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in (1, 2)})
    seed_turn(fake, 1, NOW - 60)
    fake.commits[fake.refs["refs/land-turn/pr-1"]] = object_value
    assert "refs/land-turn/pr-1: malformed record" in merge(2, H2563)
    before = dict(fake.refs)
    assert merge(1, H2563) is None
    assert fake.refs == before
    assert "UTC" in g._turn_acquire(1, fake.prs[1], R, None, {})
    assert fake.turn(1)["pr"] == 1
    fake.commits[fake.refs["refs/land-turn/pr-1"]]["message"] = "broken"
    fake.prs[1]["state"] = "CLOSED"
    before = dict(fake.refs)
    assert merge(2, H2563) is None
    assert fake.refs == before


def test_cleanup_is_bounded(gh):
    fake = gh({200: pr(200, H2563, ["hermes-sync"])})
    for n in range(1, 51):
        seed_turn(fake, n, NOW - 3 * g._TURN_TTL)
    assert merge(200, H2563) is None
    assert not [c for c in fake.calls if "DELETE" in c]  # speculative reader never writes
    assert g._turn_hold_refusal(200, fake.prs[200], R, None, {}) is None
    assert len([c for c in fake.calls if "DELETE" in c]) == 2


def test_acquire_checks_older_overlap_itself(gh):
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in (2563, 2579)})
    seed_turn(fake, 2563, NOW - 60)
    why = g._turn_acquire(2579, fake.prs[2579], R, None, {})
    assert "PR #2563 holds" in why
    assert fake.turn(2579) is None


def test_hold_also_reports_drift(gh):
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in (2563, 2579)}, compare=delta(["hermes-sync"]))
    seed_turn(fake, 2563, NOW - 60)
    why = merge(2579, H2563)
    assert "PR #2563 holds" in why and "update-branch" in why and "state commit(s) landed since then" in why


def test_exact_head_rejection_yields_without_deleting_ticket(gh):
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in (2563, 2579)})
    seed_turn(fake, 2563, NOW - 60)
    fake.prs[2563]["reviews"][0]["body"] = "Independent exact-head review: request changes"
    assert merge(2579, H2563) is None
    assert fake.turn(2563) is not None
    fake.prs[2563]["reviews"][0]["body"] = "Independent exact-head review: approve"
    assert "PR #2563 holds" in merge(2579, H2563)


def test_unrefreshed_drifted_holder_yields_after_grace(gh, monkeypatch):
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in (2563, 2579)}, compare=delta(["hermes-sync"]))
    seed_turn(fake, 2563, NOW - 60)
    assert "PR #2563 holds" in merge(2579, H2563)
    monkeypatch.setattr(g, "_now", lambda: NOW + 901)
    assert "PR #2563 holds" not in merge(2579, H2563)


def test_old_ticket_is_not_expired_by_age_alone_refresh_keeps_it(gh, monkeypatch):
    """A refreshed old ticket keeps priority; later drift starts its own 900 s refresh grace."""
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in (1, 2)})      # compare=IDENTICAL: nothing landed since base
    seed_turn(fake, 1, NOW - 2 * g._TURN_REFRESH_GRACE)
    fake.prs[1]["headRefOid"] = H2600
    fake.prs[1]["reviews"] = []
    monkeypatch.setattr(g, "_carried_approval", lambda *a: (True, ""))
    assert "PR #1 holds" in merge(2, H2563)           # refreshed, undrifted: keeps priority despite its age
    fake.compare = delta(["hermes-sync"])             # state moved again under the old ticket
    assert "PR #1 holds" in (merge(2, H2563) or "")   # NEW drift: a full refresh window, despite ticket age
    monkeypatch.setattr(g, "_now", lambda: NOW + 899)
    assert "PR #1 holds" in merge(2, H2563)
    monkeypatch.setattr(g, "_now", lambda: NOW + 900)
    assert "PR #1 holds" not in merge(2, H2563)


@pytest.mark.parametrize("date", [None, "invalid", "2099-01-01T00:00:00Z"])
def test_drift_timestamp_unreadable_never_yields(gh, date):
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in (1, 2)}, compare=delta(["hermes-sync"]))
    seed_turn(fake, 1, NOW - 1800)
    fake.activity = [{**push_event(BASE, "c" * 40, NOW - 1000), "timestamp": date}]
    why = merge(2, H2563)
    assert why and "now holds" not in why
    assert fake.turn(2) is None


def test_initial_stale_base_grace_starts_at_acquisition(gh, monkeypatch):
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in (1, 2)},
              compare=delta(["hermes-sync"], when=NOW - 5000))
    seed_turn(fake, 1, NOW - 899)
    assert "PR #1 holds" in merge(2, H2563)
    monkeypatch.setattr(g, "_now", lambda: NOW + 1)
    assert "PR #1 holds" not in merge(2, H2563)


# ---- push-activity arrival attribution (v7: server-recorded before/after chain; no commit-clock heuristic) ----
# Clock contract under test: the GitHub activity event timestamp (when the server recorded the ref update).

def sha(n):
    return f"{n:040x}"


def S(x):
    return x if isinstance(x, str) else sha(x)


def iso(when):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(when, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def push_event(before, after, when, kind="push"):
    """Shape of GET repos/{repo}/activity?ref=refs/heads/state (generic actor)."""
    return {"id": 1, "node_id": "PSH_x", "before": S(before), "after": S(after), "ref": "refs/heads/state",
            "timestamp": iso(when), "activity_type": kind, "actor": {"login": "someone", "id": 1, "type": "User"}}


def node(n, parents, date=NOW - 10**6):
    """A compare commit. Its committer date is deliberately irrelevant (and usually wrong) to the guard."""
    return {"sha": sha(n), "parents": [{"sha": S(p)} for p in parents],
            "commit": {"message": f"n{n}", "committer": {"date": iso(date)}}}


def fl(*names, prev=None):
    return [{"filename": f, "status": "modified", "previous_filename": prev} for f in names]


def world(gh, pushes, ticket_age=1800, newer=(), commits=None, page_size=100):
    """`pushes` oldest-first (before, after, when, files); each is one server push event and one immutable edge.
    The compare lists `commits` (default: one commit per push) in a deliberately unordered way."""
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in (1, 2)})
    seed_turn(fake, 1, NOW - ticket_age)
    commits = commits if commits is not None else [node(a if not isinstance(a, str) else 0, [b]) for b, a, _, _ in pushes]
    agg = [f for *_, files in pushes for f in files]
    fake.compare = {"status": "ahead", "ahead_by": len(commits), "behind_by": 0, "total_commits": len(commits),
                    "commits": list(reversed(commits)), "files": agg}
    fake.edge_compare = {f"{S(b)}...{S(a)}": {"status": "ahead", "behind_by": 0, "files": files}
                         for b, a, _, files in pushes}
    # Default histories have no cancellations; cumulative prefixes retain earlier changes.
    for i, (_, a, _, _) in enumerate(pushes):
        fake.edge_compare[f"{BASE}...{S(a)}"] = {"status": "ahead", "behind_by": 0,
            "files": [f for *_, files in pushes[:i + 1] for f in files]}
    fake.activity = list(newer) + [push_event(b, a, w) for b, a, w, _ in reversed(pushes)]
    fake.page_size = page_size
    return fake


def chain_pushes(n, overlap_at, overlap_when, spacing=1):
    """BASE -> 1 -> 2 ... -> n, one push each; only push `overlap_at` touches the PR's file."""
    out = []
    for i in range(1, n + 1):
        files = fl("hermes-sync") if i == overlap_at else fl("home/skills/unrelated/SKILL.md")
        out.append((BASE if i == 1 else i - 1, i, overlap_when if i == overlap_at else NOW - 3000 + i * spacing, files))
    return out


def is_edge(argv):
    return any("/compare/" in a and "..." + "0" * 20 in a for a in argv)


def activity_calls(fake):
    return [c for c in fake.calls if any("/activity?" in a for a in c)]


def drift_world(gh, pushes, **kw):
    return world(gh, pushes, **kw)


@pytest.mark.parametrize("last_file", ["a.py", "b.py"])
def test_reverted_overlap_restarts_full_grace(gh, monkeypatch, last_file):
    fake = world(gh, [(BASE, 1, NOW - 2001, fl("a.py")),
                      (1, 2, NOW - 2000, fl("a.py")),
                      (2, 3, NOW - 100, fl(last_file))], ticket_age=3000)
    for data in fake.prs.values():
        data["files"] = [{"path": "a.py"}, {"path": "b.py"}]
    fake.compare["files"] = fl(last_file)
    seed_turn(fake, 1, NOW - 3000, ["a.py", "b.py"])
    fake.edge_compare[f"{BASE}...{sha(2)}"] = {"status": "ahead", "behind_by": 0, "files": []}
    assert "PR #1 holds" in merge(2, H2563)
    monkeypatch.setattr(g, "_now", lambda: NOW + 799)
    assert "PR #1 holds" in merge(2, H2563)
    monkeypatch.setattr(g, "_now", lambda: NOW + 800)
    assert "PR #1 holds" not in merge(2, H2563)


def test_arrival_time_is_the_server_event_not_the_committer_date(gh, monkeypatch):
    """A delayed push of an old commit: committer date 2 h old, but it reached the server 10 s ago."""
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in (1, 2)}, compare=delta(["hermes-sync"], when=NOW - 7200))
    seed_turn(fake, 1, NOW - 1800)
    fake.activity = [push_event(BASE, "c" * 40, NOW - 10)]
    assert "PR #1 holds" in merge(2, H2563)                              # not an early yield from the old date
    monkeypatch.setattr(g, "_now", lambda: NOW + 889)
    assert "PR #1 holds" in merge(2, H2563)
    monkeypatch.setattr(g, "_now", lambda: NOW + 890)
    assert "PR #1 holds" not in merge(2, H2563)


def test_sync_merge_with_reversed_parents_uses_push_chain_only(gh, monkeypatch):
    """The 'foxtrot' shape: tip M = merge(host-local L, previous server tip D). No parent order is consulted."""
    d = node(1, [BASE])
    local = node(7, [BASE])
    m = node(2, [7, 1])
    fake = world(gh, [(BASE, 1, NOW - 3000, fl("home/skills/unrelated/SKILL.md")), (1, 2, NOW - 100, fl("hermes-sync"))],
                 commits=[d, local, m])
    assert "PR #1 holds" in merge(2, H2563)
    monkeypatch.setattr(g, "_now", lambda: NOW + 800)
    assert "PR #1 holds" not in merge(2, H2563)
    assert not [c for c in fake.calls if any(f"repos/{R}/commits/" in a for a in c)]       # no commit clocks read


def test_events_newer_than_the_frozen_tip_are_ignored(gh, monkeypatch):
    newer = [push_event(2, 9, NOW - 5), push_event(9, 10, NOW - 3)]
    world(gh, [(BASE, 1, NOW - 3000, fl("home/skills/unrelated/SKILL.md")), (1, 2, NOW - 1000, fl("hermes-sync"))],
          newer=newer)
    assert "PR #1 holds" not in merge(2, H2563)                          # started = 1000 s ago, not the newer pushes


def test_cursor_pagination_reaches_the_base(gh):
    fake = world(gh, chain_pushes(12, 1, NOW - 3001), page_size=5)
    assert "PR #1 holds" not in merge(2, H2563)
    calls = activity_calls(fake)
    assert len(calls) == 3 and all(c[-1] == "-i" for c in calls)
    assert not any("after=" in c[2] for c in calls[:1]) and "after=cur5" in calls[1][2] and "after=cur10" in calls[2][2]
    assert not any(re.search(r"[&?]page=", c[2]) for c in calls)         # `page=` is ignored by the API: never used


@pytest.mark.parametrize("case", ["gap", "retention", "too-many-pages", "duplicate-after", "branch-create",
                                  "decreasing-timestamps", "future", "wrong-ref", "http-error", "bad-cursor",
                                  "no-events"])
def test_broken_or_missing_activity_refuses_never_yields(gh, monkeypatch, case):
    pushes = chain_pushes(6, 3, NOW - 2000)
    fake = world(gh, pushes)
    ev = fake.activity                                                   # newest first
    if case == "gap":
        del ev[2]
    elif case == "retention":
        del ev[-1]
    elif case == "too-many-pages":
        fake.page_size = 1                                               # base is reachable only on page 6 > bound 5
    elif case == "duplicate-after":
        ev.insert(1, push_event(77, 6, NOW - 60))
    elif case == "branch-create":
        ev[-1]["before"] = "0" * 40
    elif case == "decreasing-timestamps":
        ev[0]["timestamp"] = iso(NOW - 3500)                             # newest push claims to predate older ones
    elif case == "future":
        ev[2]["timestamp"] = iso(NOW + 7200)
    elif case == "wrong-ref":
        ev[1]["ref"] = "refs/heads/other"
    elif case == "bad-cursor":
        fake.page_size = 1
        real = fake._activity
        fake._activity = lambda path: (lambda r: SimpleNamespace(returncode=0, stderr="", stdout=r.stdout.replace("after=cur", "after=%24(x)")))(real(path))
    elif case == "no-events":
        fake.activity = []
    if case == "http-error":
        inner = fake.run
        monkeypatch.setattr(g.subprocess, "run", lambda argv, **kw: (
            SimpleNamespace(returncode=1, stdout="", stderr="HTTP 403")
            if any("/activity?" in a for a in argv) else inner(argv, **kw)))
    why = merge(2, H2563)
    assert why and "snapshot unreadable" in why and "PR #1 holds" not in why and "now holds" not in why, (case, why)
    assert fake.turn(2) is None
    assert len(activity_calls(fake)) <= g._ACTIVITY_PAGES


def test_push_straddling_the_ci_base_is_clipped_to_base(gh, monkeypatch):
    """The CI base sits inside one pushed range: only base...after counts (older edits in that push do not)."""
    commits = [node(1, [BASE]), node(2, [1]), node(3, [2])]
    fake = world(gh, [(901, 2, NOW - 1000, fl("hermes-sync")), (2, 3, NOW - 50, fl("home/skills/unrelated/SKILL.md"))],
                 commits=commits)
    fake.edge_compare[f"{BASE}...{sha(2)}"] = fake.edge_compare.pop(f"{sha(901)}...{sha(2)}")
    assert "PR #1 holds" not in merge(2, H2563)
    assert f"repos/{R}/compare/{BASE}...{sha(2)}" in [a for c in fake.calls for a in c]
    assert not any(sha(901) in a for c in fake.calls for a in c if "/compare/" in a)


def test_earlier_unreadable_edge_blocks_later_overlap_decision(gh):
    fake = world(gh, [(BASE, 1, NOW - 3000, fl("home/skills/unrelated/SKILL.md")), (1, 2, NOW - 2000, fl("hermes-sync"))])
    fake.edge_compare[f"{BASE}...{sha(1)}"] = {"status": "diverged", "behind_by": 3, "files": []}   # earliest unreadable
    why = merge(2, H2563)
    assert "PR #1 holds" not in (why or "") and why and "now holds" not in why


def _wide(gh, monkeypatch, last_when, per_call=1.7, n=49):
    fake = world(gh, chain_pushes(n, n, last_when))
    return fake, SimClock(monkeypatch, fake, per_call)


def test_49_edges_with_late_first_overlap_fit_the_30s_eligibility(gh, monkeypatch):
    fake, sim = _wide(gh, monkeypatch, NOW - 901, per_call=0.5)
    why = merge(2, H2563)
    assert "PR #1 holds" not in why and "now holds the state land turn" in why, why
    assert 1 <= len([c for c in fake.calls if is_edge(c)]) <= 48
    assert not any(f"{BASE}...{sha(49)}" in a for c in fake.calls for a in c)  # tip reused
    assert len(activity_calls(fake)) == 1
    assert sim.monotonic() <= 30, sim.monotonic()                        # serial: 49 x 1.7 s = 83 s
    print("49-edge simulated finish", sim.monotonic())


def test_edge_reads_are_concurrent_but_never_more_than_eight(gh, monkeypatch):
    import threading
    import time as real_time
    fake, _ = _wide(gh, monkeypatch, NOW - 901, per_call=0)   # SimClock only for its shims; real delay below
    inner, lock, state = g.subprocess.run, threading.Lock(), {"now": 0, "max": 0}

    def slow(argv, **kw):
        if not is_edge(argv):
            return inner(argv, **kw)
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        real_time.sleep(0.02)
        try:
            return inner(argv, **kw)
        finally:
            with lock:
                state["now"] -= 1
    monkeypatch.setattr(g.subprocess, "run", slow)
    assert "PR #1 holds" not in merge(2, H2563)
    assert 2 <= state["max"] <= g._EDGE_WORKERS, state


def test_exact_900s_boundary_from_arrival_time(gh, monkeypatch):
    fake, sim = _wide(gh, monkeypatch, NOW - 900)
    assert "PR #1 holds" not in merge(2, H2563)                          # now - 900 >= 900 -> yields
    fake2, sim2 = _wide(gh, monkeypatch, NOW - 899)
    assert "PR #1 holds" in merge(2, H2563)


def test_slow_edge_reads_refuse_at_the_shared_deadline_without_late_allow(gh, monkeypatch):
    fake, sim = _wide(gh, monkeypatch, NOW - 2000, per_call=9.0)
    why = merge(2, H2563)
    assert isinstance(why, str) and "now holds" not in why and "PR #1 holds" not in why, why
    assert fake.turn(2) is None
    assert all(t < 30 + 1e-9 for t, a, _ in sim.starts if is_edge(a)), max(t for t, *_ in sim.starts)


def test_rename_and_truncated_edges(gh):
    world(gh, [(BASE, 1, NOW - 3000, fl("home/skills/unrelated/SKILL.md")),
               (1, 2, NOW - 1000, fl("somewhere/else.txt", prev="hermes-sync"))])
    assert "PR #1 holds" not in merge(2, H2563)                          # rename source counts; 1000 s >= 900
    big = fl(*[f"x/{i}.txt" for i in range(g._COMPARE_FILES_CAP)])
    fake = world(gh, [(BASE, 1, NOW - 3000, big), (1, 2, NOW - 1000, fl("hermes-sync"))])
    why = merge(2, H2563)
    assert why and "PR #1 holds" not in why and "now holds" not in why, why   # truncated edge: unknown, refuse
    assert fake.turn(2) is None


def test_future_arrival_time_never_yields(gh):
    fake = world(gh, [(BASE, 1, NOW - 3000, fl("home/skills/unrelated/SKILL.md")), (1, 2, NOW + 7200, fl("hermes-sync"))])
    why = merge(2, H2563)
    assert why and "PR #1 holds" not in why and "now holds" not in why
    assert fake.turn(2) is None


def test_only_overlapping_pushes_start_grace(gh, monkeypatch):
    fake = world(gh, [(BASE, 1, NOW - 1600, fl("home/skills/unrelated/SKILL.md")), (1, 2, NOW - 1, fl("hermes-sync"))])
    assert "PR #1 holds" in merge(2, H2563)
    monkeypatch.setattr(g, "_now", lambda: NOW + 899)
    assert "PR #1 holds" not in merge(2, H2563)


def test_standalone_scope_has_shared_canonical_deadline(monkeypatch):
    assert g._PREFLIGHT_DEADLINE.get() is None
    with g._scope():
        deadline = g._PREFLIGHT_DEADLINE.get()
        assert 17 < g._left() <= 18
        with g._scope():
            assert g._PREFLIGHT_DEADLINE.get() == deadline
    assert g._PREFLIGHT_DEADLINE.get() is None


def test_2563_drift_refusal_takes_the_turn(gh):
    # 4th refusal: b36b5421a9 changed tests/state_guard_rules.py (shared ~ tests/test_core_park_ack.py).
    fake = gh({2563: pr(2563, H2563, F2563)}, compare=delta(["tests/state_guard_rules.py"]))
    why = merge(2563, H2563)
    assert why and "update-branch" in why and "now holds the state land turn" in why
    rec = fake.turn(2563)
    assert rec == {"pr": 2563, "head": H2563, "acquired": NOW, "expires": NOW + g._TURN_TTL, "files": F2563}
    assert g._TURN_TTL >= 3600


def test_overlapping_merge_waits_for_the_holder(gh):
    fake = gh({2563: pr(2563, H2563, F2563), 2600: pr(2600, H2600, ["hermes-sync", "tests/test_x.py"])},
              compare=delta(["tests/state_guard_rules.py"]))
    merge(2563, H2563)
    fake.compare = IDENTICAL            # #2600's own CI base is current: only the turn holds it
    why = merge(2600, H2600)
    assert why and "PR #2563 holds the state land turn" in why and "hermes-sync ~ hermes-sync" in why
    # A PR whose files cannot drift the holder lands as before.
    fake.prs[2601] = pr(2601, H2600, ["hosts/hermes-hub/bb-tuner/x.py"])
    fake.prs[2601]["number"] = 2601
    assert merge(2601, H2600) is None
    # The holder itself is never held by its own turn; with a fresh base it merges.
    assert merge(2563, H2563) is None


def test_turn_covers_ci_files_too(gh):
    fake = gh({2563: pr(2563, H2563, F2563), 2600: pr(2600, H2600, [".github/workflows/x.yml"])},
              compare=delta(["hermes-sync"]))
    merge(2563, H2563)
    fake.compare = IDENTICAL
    assert "holds the state land turn" in merge(2600, H2600)


def test_merged_holder_releases_without_manual_ref_cleanup(gh):
    fake = gh({2563: pr(2563, H2563, F2563), 2600: pr(2600, H2600, ["hermes-sync"])},
              compare=delta(["hermes-sync"]))
    merge(2563, H2563)
    fake.compare, fake.pr_state[2563] = IDENTICAL, "closed"
    assert merge(2600, H2600) is None
    # Merge preflight is read-only while approval runs; non-speculative readers
    # retain cleanup, rather than deleting a ref observed before approval.
    assert "refs/land-turn/pr-2563" in fake.refs
    assert g._turn_hold_refusal("2600", pr(2600, H2600, ["hermes-sync"]), R, None, {}) is None
    assert "refs/land-turn/pr-2563" not in fake.refs


def test_expired_turn_is_ignored(gh, monkeypatch):
    fake = gh({2563: pr(2563, H2563, F2563), 2600: pr(2600, H2600, ["hermes-sync"])},
              compare=delta(["hermes-sync"]))
    merge(2563, H2563)
    fake.compare = IDENTICAL
    monkeypatch.setattr(g, "_now", lambda: NOW + g._TURN_TTL + 1)
    assert merge(2600, H2600) is None


def test_unreadable_turns_fail_closed(gh):
    # A partial snapshot cannot prove there is no older holder.
    gh({2600: pr(2600, H2600, ["hermes-sync"])}, refs_fail=True)
    assert "snapshot unreadable" in merge(2600, H2600)


def test_live_turn_is_not_extended(gh, monkeypatch):
    fake = gh({2563: pr(2563, H2563, F2563)}, compare=delta(["hermes-sync"]))
    merge(2563, H2563)
    first = fake.refs["refs/land-turn/pr-2563"]
    monkeypatch.setattr(g, "_now", lambda: NOW + 600)
    why = merge(2563, H2563)
    assert fake.refs["refs/land-turn/pr-2563"] == first and "now holds the state land turn" in why
    # After expiry the PR cools down for another TTL (others in its area get a window) ...
    monkeypatch.setattr(g, "_now", lambda: NOW + g._TURN_TTL + 5)
    why = merge(2563, H2563)
    assert fake.refs["refs/land-turn/pr-2563"] == first and "now holds" not in why
    # ... then a new refusal records a new turn (PATCH of the existing ref).
    monkeypatch.setattr(g, "_now", lambda: NOW + 2 * g._TURN_TTL + 5)
    merge(2563, H2563)
    assert fake.turn(2563)["expires"] == NOW + 3 * g._TURN_TTL + 5


def test_readers_delete_cooled_turns(gh, monkeypatch):
    fake = gh({2563: pr(2563, H2563, F2563), 2600: pr(2600, H2600, ["hosts/x/y.py"])},
              compare=delta(["hermes-sync"]))
    merge(2563, H2563)

    fake.compare = IDENTICAL
    monkeypatch.setattr(g, "_now", lambda: NOW + g._TURN_TTL + 5)   # cooling: kept
    assert merge(2600, H2600) is None
    assert "refs/land-turn/pr-2563" in fake.refs
    g._live_turns(R, None, {})
    assert set(fake.refs) == {"refs/land-turn/pr-2563"}
    monkeypatch.setattr(g, "_now", lambda: NOW + 2 * g._TURN_TTL + 5)
    assert merge(2600, H2600) is None
    g._live_turns(R, None, {})
    # Merge speculation is read-only; standalone readers retain bounded cleanup.
    assert g._turn_hold_refusal(2600, fake.prs[2600], R, None, {}) is None
    assert not fake.refs


class SimClock:
    """Simulated latency for the guard's own subprocess calls (module-local shims; no global patching).

    Each gh/git call costs `per_call` seconds on the calling thread's clock. Worker threads start at the main
    thread's time and `monotonic()` is the latest of all, so a concurrent batch costs one call, not N."""

    def __init__(self, monkeypatch, fake, per_call=2.0):
        import threading
        import time as real_time
        import subprocess as real_subprocess
        self.per_call, self.main, self.lock = per_call, threading.current_thread(), threading.Lock()
        self.clock, self.starts = {self.main: 0.0}, []
        original = fake.run

        def run(argv, **kw):
            me = threading.current_thread()
            with self.lock:
                t = self.clock.setdefault(me, self.clock[self.main])
                self.starts.append((t, list(argv), me))
                self.clock[me] = t + self.per_call
            return original(argv, **kw)
        monkeypatch.setattr(g, "subprocess", SimpleNamespace(**{**vars(real_subprocess), "run": run}))
        monkeypatch.setattr(g, "time", SimpleNamespace(**{**vars(real_time), "monotonic": self.monotonic}))

    def monotonic(self):
        import threading
        with self.lock:
            me = threading.current_thread()
            if me == self.main:
                self.clock[me] = max(self.clock.values())   # join: the main thread has waited for its workers
            return self.clock.setdefault(me, self.clock[self.main])

    def writes(self):
        return [(t, a) for t, a, _ in self.starts if "-X" in a or "--input" in a and "graphql" not in a]


def test_slow_calls_do_not_truncate_oldest_turn(gh, monkeypatch):
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in range(1, 10)})
    for n in range(1, 9):
        seed_turn(fake, n, NOW - 100 + n)
    SimClock(monkeypatch, fake)
    assert "PR #1 holds" in merge(9, H2563)
    assert len([c for c in fake.calls if c[1:3] == ["api", "graphql"]]) == 1


def _crowded_drift_world(gh, monkeypatch, per_call):
    """Six overlapping holders that yielded (exact-head rejection) and a drifted newcomer: the round-2 B2 case."""
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in range(1, 8)}, compare=delta(["hermes-sync"]))
    for n in range(1, 7):
        seed_turn(fake, n, NOW - 300 + n)
        fake.prs[n]["reviews"][0]["body"] = "Independent exact-head review: request changes"
    return fake, SimClock(monkeypatch, fake, per_call)


def test_drift_acquire_path_fits_the_pre_tool_call_budget(gh, monkeypatch):
    fake, sim = _crowded_drift_world(gh, monkeypatch, 0.5)
    why = merge(7, H2563)
    assert "now holds the state land turn" in why, why
    assert sim.monotonic() <= 18, sim.monotonic()                       # cost bound (call count), inside the canonical 18 s budget
    lists = [c for c in fake.calls if any("matching-refs" in w for w in c)]
    assert len(lists) == 2                                               # preflight snapshot + post-write check only
    assert len([c for c in fake.calls if c[1:3] == ["api", "graphql"]]) == 1   # records are read once, never re-read
    assert len([c for c in fake.calls if c[:3] == ["gh", "pr", "view"]]) == 7  # each PR once, holders concurrently
    assert fake.turn(7) is not None


@pytest.mark.parametrize("per_call", [3.0, 5.0, 9.0])
def test_budget_exhaustion_fails_closed_without_late_writes(gh, monkeypatch, per_call):
    # Exercise the canonical deadline under slow calls; never enlarge the budget.
    budget = 18.0
    monkeypatch.setattr(g, "PREFLIGHT_TIMEOUT", budget)
    fake, sim = _crowded_drift_world(gh, monkeypatch, per_call)
    why = merge(7, H2563)
    assert isinstance(why, str) and ("ship-only-guard" in why), why   # never allowed (refusal, or the canonical expiry text)
    assert all(t <= budget - g._WRITE_RESERVE for t, _ in sim.writes()), sim.writes()
    # No call starts after the deadline either: the abandoned worker cannot publish after the hook timed out.
    assert all(t < budget for t, _, _ in sim.starts)
    if per_call >= 5:
        assert "nothing was approved" in why or "snapshot unreadable" in why or "exhausted" in why or "now holds" not in why


def test_carry_eligible_refresh_keeps_the_ticket(gh, monkeypatch):
    """Round-2 B1: update-branch with a carried approval must not drop the holder mid-CI."""
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in (1, 2)})
    seed_turn(fake, 1, NOW - 120)
    fake.prs[1]["headRefOid"] = H2600
    fake.prs[1]["reviews"] = []                       # refreshed head, no exact-head verdict
    monkeypatch.setattr(g, "_carried_approval", lambda *a: (True, ""))
    assert "PR #1 holds" in merge(2, H2563)           # the existing carry decision admits #1, so #1 keeps priority
    monkeypatch.setattr(g, "_carried_approval", lambda *a: (False, "patch changed"))
    assert merge(2, H2563) is None                    # unreviewed content change still yields
    monkeypatch.setattr(g, "_carried_approval", lambda *a: (True, ""))
    fake.prs[1]["reviews"] = [{**pr(1, H2600, ["x"])["reviews"][0], "body": "Independent exact-head review: request changes"}]
    assert merge(2, H2563) is None                    # a block on the new head always yields


def test_turn_errors_refuse(gh, monkeypatch):
    gh({2600: pr(2600, H2600, ["hermes-sync"])})

    def boom(*a):
        raise OverflowError("x")
    monkeypatch.setattr(g, "_live_turns", boom)
    assert "snapshot unreadable" in merge(2600, H2600)


@pytest.mark.parametrize("fault", ["batch-error", "partial", "missing", "junk"])
def test_incomplete_snapshots_never_admit_or_acquire(gh, monkeypatch, fault):
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in (1, 2)})
    seed_turn(fake, 1, NOW - 60)
    if fault == "junk":
        fake.commits[fake.refs["refs/land-turn/pr-1"]]["message"] = "not a turn"

    original = g._gh_call
    def broken(args, *a, **kw):
        if args == ["graphql"]:
            if fault == "batch-error":
                return -1, None
            if fault == "partial":
                return 0, {"data": {"repository": {}}, "errors": [{"message": "timeout"}]}
            if fault == "missing":
                return 0, {"data": {"repository": {}}}
        return original(args, *a, **kw)
    monkeypatch.setattr(g, "_gh_call", broken)
    assert "snapshot unreadable" in merge(2, H2563)
    before = dict(fake.refs)
    assert "snapshot unreadable" in g._turn_acquire(2, fake.prs[2], R, None, {})
    assert fake.refs == before


def test_pending_or_unreadable_holder_does_not_yield(gh):
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in (1, 2)})
    seed_turn(fake, 1, NOW - 60)
    fake.prs[1]["reviews"] = []
    assert "PR #1 holds" in merge(2, H2563)
    fake.prs[1]["reviews"] = None
    assert "snapshot unreadable" in merge(2, H2563)


def test_legacy_record_and_equal_time_have_stable_order(gh):
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in (1, 2, 3)})
    for n in (2, 1):
        seed_turn(fake, n, NOW - 60)
        obj = fake.commits[fake.refs[f"refs/land-turn/pr-{n}"]]
        rec = g.parse_turn(obj["message"])
        del rec["acquired"]
        obj["message"] = f"land-turn pr={n}\n\n{json.dumps(rec)}"
    assert merge(1, H2563) is None
    assert "PR #1 holds" in merge(2, H2563)
    assert "PR #1 holds" in merge(3, H2563)


def test_refreshed_head_requires_approval_to_regain_ticket(gh):
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in (1, 2)}, compare=delta(["hermes-sync"], when=NOW - 1000))
    seed_turn(fake, 1, NOW - 1000)
    seed_turn(fake, 2, NOW - 500)
    assert g._turn_hold_refusal(2, fake.prs[2], R, None, {}) is None
    fake.prs[1]["headRefOid"] = H2600
    fake.prs[1]["reviews"] = []
    assert g._turn_hold_refusal(2, fake.prs[2], R, None, {}) is None
    fake.prs[1]["reviews"] = pr(1, H2600, ["hermes-sync"])["reviews"]
    fake.compare = IDENTICAL
    assert "PR #1 holds" in g._turn_hold_refusal(2, fake.prs[2], R, None, {})


def test_unrelated_unreadable_holder_does_not_block(gh):
    fake = gh({1: pr(1, H2563, ["area/a.py"]), 2: pr(2, H2600, ["other/zzz/b.py"])})
    seed_turn(fake, 1, NOW - 60, ["area/a.py"])
    fake.prs[1]["comments"] = [{}] * 100
    assert merge(2, H2600) is None


def test_yielded_holder_does_not_claim_it_still_holds(gh):
    fake = gh({1: pr(1, H2563, ["hermes-sync"])}, compare=delta(["hermes-sync"], when=NOW - 1000))
    seed_turn(fake, 1, NOW - 1000)
    assert "now holds" not in merge(1, H2563)


def test_nontransitive_overlap_does_not_reserve_waiters_extra_area(gh):
    fake = gh({1: pr(1, H2563, ["area/a.py"]), 2: pr(2, H2563, ["area/a.py", "other/b.py"]),
               3: pr(3, H2563, ["other/b.py"])})
    for n in (1, 2, 3):
        seed_turn(fake, n, NOW - 100 + n, [f["path"] for f in fake.prs[n]["files"]])
    assert merge(1, H2563) is None
    assert "PR #1 holds" in merge(2, H2563)
    assert merge(3, H2563) is None


def test_other_refusals_take_no_turn(gh):
    data = pr(2563, H2563, F2563)
    data["statusCheckRollup"][0]["conclusion"] = "FAILURE"
    fake = gh({2563: data}, compare=delta(["hermes-sync"]))
    assert "not green" in merge(2563, H2563)
    assert not fake.refs


@pytest.mark.parametrize("msg", [
    "", "other subject\n\n{\"pr\": 1, \"expires\": 1, \"files\": [\"a\"]}",
    "land-turn pr=1\n\n{\"pr\": true, \"expires\": 1, \"files\": [\"a\"]}",
    "land-turn pr=1\n\n{\"pr\": 1, \"expires\": \"x\", \"files\": [\"a\"]}",
    "land-turn pr=1\n\n{\"pr\": 1, \"expires\": Infinity, \"files\": [\"a\"]}",
    "land-turn pr=1\n\n{\"pr\": 1, \"expires\": NaN, \"files\": [\"a\"]}",
    "land-turn pr=1\n\n{\"pr\": 1, \"expires\": 1, \"files\": [\"\"]}", "land-turn pr=1\n\n{not json"])
def test_parse_turn_rejects_junk(msg):
    assert g.parse_turn(msg) is None


@pytest.mark.parametrize("pr_file,base_file,held", [
    # Skill prose against skill prose never refuses (#2563 vs sync 5fbd15ce4f, same reference file) ...
    ("home/skills/a/references/x.md", "home/skills/a/references/x.md", False),
    ("home/skills/a/references/x.md", "home/skills/a/SKILL.md", False),
    ("skills/devops/a/SKILL.md", "skills/devops/a/references/y.md", False),
    # ... but prose beside code, non-skill markdown and CI files still do.
    ("home/skills/a/scripts/test_ex.py", "home/skills/a/SKILL.md", True),
    ("home/skills/a/SKILL.md", "home/skills/a/scripts/tool.py", True),
    ("hosts/hermes-hub/x/README.md", "hosts/hermes-hub/x/notes.md", True),
    ("home/skills/a/SKILL.md", ".github/workflows/ci.yml", True),
])
def test_skill_prose_rule(pr_file, base_file, held):
    assert bool(g._overlap(pr_file, base_file)) is held
