"""report_only vs a close-out that names its owners by PR number only (self-heal agent-b9a5074a0c).

Hub, 2026-11-13 13:03 UTC, an automatic close-out turn: "Two items are still in progress, and both have a worker on
them now (checked 13:03 UTC)", about PR #2220 and PR #1934. Both were true: card t_01a1d4d1885c21b1 (the #2220 landing)
was claimed by worker-2 and card t_01a1d36ddcea59fe (#1934) by worker-6 at 12:59. The turn read them with a raw
sqlite query that wrote no `<- OK` line. The coach held the draft and its revision at p 0.9 with
owner_source="none": it looks owners up only by `t_…` id (reply, ask, or "the card" wording), and this reply named
only PRs. Now a reply ending that names PRs and no card id resolves them to cards (pr_owner_card_ids): card ids in
this turn's calls that name the PR or read a board db, plus open cards whose title or body names the PR, so the
judges get `<- BOARD` lines. report_only_scored logs owner_pr_ids / owner_pr_resolved.

The tool-log line and reply below are reconstructed from the incident report (the hub transcript is not readable
from a worker node); the card ids, PR numbers, statuses and times are the incident's.
"""
import importlib.util
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_prowner_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rules = _mod("agent_rules")
rc = _mod("reply_check")

C2220 = "t_01a1d4d1885c21b1"
C1934 = "t_01a1d36ddcea59fe"
NOW = 1794575006.0 + 15     # 2026-11-13T13:03:41Z, just after the agent's read
CLAIM_2220 = NOW - 7        # claimed by worker-2 at 13:03:34
CLAIM_1934 = NOW - 281      # claimed by worker-6 at 12:59

REPLY = (
    "Done for this round: the review verdicts are posted and the stale cards are archived.\n\n"
    "Two items are still in progress, and both have a worker on them now (checked 13:03 UTC): PR #2220 (the "
    "lease-refusal fix) is landing on worker-2, and PR #1934 (the scheduler follow-up) is being reworked on "
    "worker-6. Nothing else needs you.")
TOOL_LOG = [
    f"-> terminal(sqlite3 -readonly ~/.hermes/kanban.db \"select id,status,claim_lock,datetime(claim_expires,"
    f"'unixepoch') from tasks where id in ('{C2220}','{C1934}')\")",
]

SCHEMA = """
create table tasks (id text primary key, title text, body text, status text, created_at integer,
                    started_at integer, completed_at integer, current_run_id integer, created_on text);
create table task_events (id integer primary key autoincrement, task_id text, run_id integer, kind text,
                          payload text, created_at integer);
create table task_runs (id integer primary key autoincrement, task_id text, status text, outcome text,
                        started_at integer, ended_at integer);
create table task_links (parent_id text, child_id text, primary key (parent_id, child_id));
"""


def _board(path, cards):
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    for cid, title, body, status, claimed in cards:
        c.execute("insert into tasks (id, title, body, status, created_at) values (?,?,?,?,?)",
                  (cid, title, body, status, int(NOW - 3600)))
        c.execute("insert into task_events (task_id, kind, created_at) values (?,?,?)", (cid, "created", NOW - 3600))
        if claimed:
            c.execute("insert into task_events (task_id, kind, created_at) values (?,?,?)", (cid, "claimed", claimed))
            c.execute("insert into task_runs (task_id, status, started_at) values (?,?,?)",
                      (cid, "running", claimed))
    c.commit()
    c.close()
    return str(path)


def _incident_board(tmp_path, monkeypatch, extra=()):
    db = _board(tmp_path / "kanban.db", [
        (C2220, "Land core PR #2220 (remote lease CLI refusal)", "Merge https://github.com/x/y/pull/2220 once CI "
         "is green.", "running", CLAIM_2220),
        (C1934, "Rework PR 1934 after review", "Address the review comments on the scheduler PR.", "running",
         CLAIM_1934),
        *extra])
    monkeypatch.setenv("COACH_BOARD_DBS", db)
    monkeypatch.setenv("COACH_SHADOW_DBS", "")
    return db


# ── the incident: PR numbers resolve to their live cards ────────────────────────────────────────────────────────
def test_pr_only_closeout_gets_board_lines_for_both_owner_cards(tmp_path, monkeypatch):
    _incident_board(tmp_path, monkeypatch)
    lines = rc.named_card_facts(REPLY, TOOL_LOG, now=NOW)
    got = {cid for cid in (C2220, C1934) for line in lines if f"<- BOARD task={cid} status=running" in line}
    assert got == {C2220, C1934}, lines
    assert rc.owner_source(REPLY, TOOL_LOG) != "none"


def test_pr_ids_and_resolution_found_from_titles_alone(tmp_path, monkeypatch):
    _incident_board(tmp_path, monkeypatch)
    assert rc.tail_pr_ids(REPLY) == ["2220", "1934"]
    ids = rc.pr_owner_card_ids((REPLY or "")[-1500:], [])
    assert set(ids) == {C2220, C1934}, ids
    assert rc.tail_card_ids(REPLY, [])[1] is True  # a fallback, like the other no-id paths


def test_turn_call_naming_the_pr_resolves_a_card_whose_title_does_not(tmp_path, monkeypatch):
    db = _board(tmp_path / "kanban.db", [(C2220, "Land the lease fix", "", "running", CLAIM_2220)])
    monkeypatch.setenv("COACH_BOARD_DBS", db)
    monkeypatch.setenv("COACH_SHADOW_DBS", "")
    log = [f"-> terminal(gh pr view 2220 --json state; hermes kanban show {C2220})"]
    reply = "PR #2220 is landing now on worker-2."
    assert rc.pr_owner_card_ids(reply, log) == [C2220]


# ── negative cases: behaviour unchanged ─────────────────────────────────────────────────────────────────────────
def test_pr_with_no_card_adds_nothing(tmp_path, monkeypatch):
    _incident_board(tmp_path, monkeypatch)
    reply = "One item is still in progress: PR #9999 is landing on worker-2."
    assert rc.named_card_facts(reply, [], now=NOW) == []
    assert rc.owner_source(reply, []) == "none"


def test_done_card_naming_the_pr_is_not_an_owner(tmp_path, monkeypatch):
    db = _board(tmp_path / "kanban.db", [
        ("t_01a1d4d1885c21aa", "Review PR #2220", "", "done", None),
        ("t_01a1d4d1885c21ab", "Old PR #2220 attempt", "", "archived", None)])
    monkeypatch.setenv("COACH_BOARD_DBS", db)
    monkeypatch.setenv("COACH_SHADOW_DBS", "")
    reply = "PR #2220 is landing now on worker-2."
    log = ["-> terminal(sqlite3 -readonly ~/.hermes/kanban.db \"select status from tasks where id="
           "'t_01a1d4d1885c21aa'\")"]
    assert rc.pr_owner_card_ids(reply, log) == []
    assert rc.named_card_facts(reply, log, now=NOW) == []


def test_reply_naming_a_card_id_is_unchanged(tmp_path, monkeypatch):
    _incident_board(tmp_path, monkeypatch)
    reply = f"PR #2220 is landing on worker-2; card {C2220} owns it."
    assert rc.tail_card_ids(reply, TOOL_LOG) == ([C2220], False)
    assert rc.pr_owner_card_ids(reply[-1500:], TOOL_LOG) == []


def test_pr_lookup_caps_at_named_card_max(tmp_path, monkeypatch):
    extra = [(f"t_01a1d4d1885c2{i:03x}", f"Sub-step {i} of PR #2220", "", "ready", None) for i in range(8)]
    _incident_board(tmp_path, monkeypatch, extra=extra)
    assert len(rc.pr_owner_card_ids("PR #2220 is landing.", [])) <= rc.NAMED_CARD_MAX


# ── detection: report_only_scored says which PRs it saw and how many owners it found ───────────────────────────
def test_report_only_scored_logs_pr_telemetry(tmp_path, monkeypatch):
    _incident_board(tmp_path, monkeypatch)
    assert rc.owner_pr_telemetry(REPLY, TOOL_LOG) == {"owner_pr_ids": ["2220", "1934"], "owner_pr_resolved": 2}
    assert rc.owner_pr_telemetry("PR #9999 is landing.", []) == {"owner_pr_ids": ["9999"], "owner_pr_resolved": 0}
    assert rc.owner_pr_telemetry(f"Card {C2220} owns PR #2220.", []) == {}
    assert rc.owner_pr_telemetry("Nothing left.", []) == {}


def test_report_only_scored_row_carries_pr_fields_and_board_owner(tmp_path, monkeypatch):
    import json
    _incident_board(tmp_path, monkeypatch)
    monkeypatch.setattr(rc.time, "time", lambda: NOW)
    monkeypatch.setattr(rules.time, "time", lambda: NOW)
    rows = []
    verdict = json.dumps({"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": 0.9,
                          "evidence": "no owner shown"})
    rc.check(REPLY, "[Kanban notice] close-out", TOOL_LOG, lambda s, p: verdict,
             thresholds={"report_only": 0.7}, confirm=lambda: (0.2, "x"), confirm_floor=0.5,
             audit=rows.append, card_status=lambda c: "running", only=["report_only"])
    scored = [r for r in rows if r.get("event") == "report_only_scored"]
    assert scored, rows
    assert scored[0]["owner_pr_ids"] == ["2220", "1934"] and scored[0]["owner_pr_resolved"] == 2, scored
    assert scored[0]["owner_source"] == "board", scored
