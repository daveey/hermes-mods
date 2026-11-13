"""report_only vs a close-out that names its owners by PR number only (self-heal agent-b9a5074a0c).

Hub, 2026-11-13 13:03 UTC, an automatic close-out turn: "Two items are still in progress, and both have a worker on
them now (checked 13:03 UTC)", about PR #2220 and PR #1934. Both were true: card t_01a1d4d1885c21b1 (the #2220 landing)
was claimed by worker-2 and card t_01a1d36ddcea59fe (#1934) by worker-6 at 12:59. The turn read them with a raw
sqlite query that wrote no `<- OK` line. The coach held the draft and its revision at p 0.9 with
owner_source="none": it looks owners up only by `t_…` id (reply, ask, or "the card" wording), and this reply named
only PRs. Now a reply ending that names PRs and no card id resolves them to cards (pr_owner_card_ids): card ids in
this turn's calls that name the PR or read a board db, plus open cards whose title or body names the PR, so the
judges get `<- BOARD` lines. report_only_scored logs owner_pr_ids / owner_pr_resolved.

The first tests' reply is reconstructed from the incident report; their tool-log line goes through the real
AgentCoach._log_call. Round 2 (the replay tests at the bottom): the 0b4ec3eb fix still missed the real turn. The
turn's read call named "PRs 2220 and 1934" in its first 140 chars, but the card ids and board paths came later, past
_log_call's TURN_LOG_ARG_CAP, and its result was never logged, so the lookup saw no ids and fell to the board search,
which matched an unrelated scheduled card whose body mentioned #1934. fixtures/pr_owner_closeout_turn.json holds the
real calls, results, ask and revised reply (state.db msgs 1269165-1269201, repo slug and login scrubbed).
"""
import importlib.util
import json
import sqlite3
from collections import deque
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "pr_owner_closeout_turn.json"
# The ro URI flag inside the recorded agent commands below. Spelled in halves: those are fixture strings, not opens,
# and tests/test_sqlite_ro_guard.py would otherwise flag them as bare ro opens with no fallback.
RO = "mode=" + "ro"


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
def _tool_log(calls):
    """The turn log exactly as the coach writes it: each (tool, args, result) through AgentCoach._log_call."""
    coach = rules.AgentCoach(signals=None)
    s = {"turn_log": deque(maxlen=200), "turn_rich": deque(maxlen=200)}
    for tool, args, result in calls:
        coach._log_call(s, tool, args, result, None)
    return list(s["turn_log"])


REAL = json.loads(FIXTURE.read_text())


def _real_log(calls=None):
    """The real turn's log (both calls), built inside each test once its boards are set: _log_call's owner-line
    helpers read the boards, and the coach suite refuses a read of this host's real board."""
    return _tool_log([(c["tool"], c["args"], c["result"]) for c in (calls or REAL["calls"])])


def _old_log():
    """The incident's owner read alone (msgs 1269188/1269194): raw sqlite through shell variables, no `<- OK`."""
    return _real_log(REAL["calls"][1:])

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
    lines = rc.named_card_facts(REPLY, _old_log(), now=NOW)
    got = {cid for cid in (C2220, C1934) for line in lines if f"<- BOARD task={cid} status=running" in line}
    assert got == {C2220, C1934}, lines
    assert rc.owner_source(REPLY, _old_log()) != "none"


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
    assert rc.tail_card_ids(reply, _old_log()) == ([C2220], False)
    assert rc.pr_owner_card_ids(reply[-1500:], _old_log()) == []


def test_pr_lookup_caps_at_named_card_max(tmp_path, monkeypatch):
    extra = [(f"t_01a1d4d1885c2{i:03x}", f"Sub-step {i} of PR #2220", "", "ready", None) for i in range(8)]
    _incident_board(tmp_path, monkeypatch, extra=extra)
    assert len(rc.pr_owner_card_ids("PR #2220 is landing.", [])) <= rc.NAMED_CARD_MAX


# ── detection: report_only_scored says which PRs it saw and how many owners it found ───────────────────────────
def test_report_only_scored_logs_pr_telemetry(tmp_path, monkeypatch):
    _incident_board(tmp_path, monkeypatch)
    assert rc.owner_pr_telemetry(REPLY, _old_log()) == {"owner_pr_ids": ["2220", "1934"], "owner_pr_resolved": 2}
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
    rc.check(REPLY, "[Kanban notice] close-out", _old_log(), lambda s, p: verdict,
             thresholds={"report_only": 0.7}, confirm=lambda: (0.2, "x"), confirm_floor=0.5,
             audit=rows.append, card_status=lambda c: "running", only=["report_only"])
    scored = [r for r in rows if r.get("event") == "report_only_scored"]
    assert scored, rows
    assert scored[0]["owner_pr_ids"] == ["2220", "1934"] and scored[0]["owner_pr_resolved"] == 2, scored
    assert scored[0]["owner_source"] == "board", scored


# ── round 2: the real turn, replayed through _log_call (state.db msgs 1269165-1269201) ──────────────────────────
DECOY = "t_01a1d3df5e5d0757"          # open, scheduled; its body mentions #1934 in passing
T_REPLY = 1794575036.0                # 13:03:56Z, the revised reply


def _real_boards(tmp_path, monkeypatch):
    """The hub board (#1934's owner, claimed by worker-6 at 12:59:19, plus the decoy) and its sync-shadow replica of
    the default board (#2220's owner, claimed by worker-2 at 13:03:34). Neither owner's title or body names its PR."""
    db = _board(tmp_path / "kanban.db", [
        (C1934, "Rework the scheduler follow-up after review", "Address the review comments.", "running",
         T_REPLY - 277),
        (DECOY, "Weekly sync-health digest", "Context: the sync work in #1934 and #1882.", "scheduled", None)])
    shadow = _board(tmp_path / "default.db", [
        (C2220, "Land the lease-refusal comment cleanup", "Review and merge once CI is green.", "running",
         T_REPLY - 22)])
    monkeypatch.setenv("COACH_BOARD_DBS", db)
    monkeypatch.setenv("COACH_SHADOW_DBS", shadow)


def test_real_turn_log_carries_the_card_ids_past_the_arg_cap(tmp_path, monkeypatch):
    _real_boards(tmp_path, monkeypatch)
    log = _real_log()
    reads = [l for l in log if l.lstrip().startswith("<- CARDS read:")]
    assert len(reads) == 1, log
    assert C2220 in reads[0] and C1934 in reads[0], reads
    call = next(l for l in log if "PRs 2220 and 1934" in l)
    assert C2220 not in call  # the `->` line itself is still capped
    assert not any(l.lstrip().startswith("<- CARDS") for l in log[:log.index(call)])  # PR-only first read


def test_real_closeout_resolves_exactly_the_two_owners(tmp_path, monkeypatch):
    _real_boards(tmp_path, monkeypatch)
    log = _real_log()
    reply, ask = REAL["reply"], REAL["ask"]
    ids, fallback = rc.tail_card_ids(reply, log, ask)
    assert set(ids) == {C2220, C1934} and DECOY not in ids and fallback is True, ids
    lines = rc.named_card_facts(reply, log, now=T_REPLY, user_ask=ask)
    got = {cid for cid in (C2220, C1934, DECOY) for l in lines if f"<- BOARD task={cid} " in l}
    assert got == {C2220, C1934}, lines
    assert all("status=running" in l for l in lines), lines
    assert rc.owner_source(reply, log, user_ask=ask) == "replica"
    assert rc.owner_pr_telemetry(reply, log)["owner_pr_resolved"] == 2


def test_board_search_skips_prs_the_reply_lists_as_merged_or_closed(tmp_path, monkeypatch):
    db = _board(tmp_path / "kanban.db", [
        ("t_01a1d3df5e5d07aa", "Follow-up on PR #1698", "", "ready", None),
        ("t_01a1d3df5e5d07ab", "Follow-up on PR #2174", "", "ready", None),
        (C1934, "Rework PR #1934", "", "running", T_REPLY - 277)])
    monkeypatch.setenv("COACH_BOARD_DBS", db)
    monkeypatch.setenv("COACH_SHADOW_DBS", "")
    assert rc.pr_owner_card_ids(REAL["reply"][-1500:], []) == [C1934]


def test_board_search_prefers_a_title_naming_the_pr_over_a_body_mention(tmp_path, monkeypatch):
    extra = [(f"t_01a1d3df5e5d0{i:03x}", f"Unrelated chore {i}", "mentions PR #2220 in passing", "ready", None)
             for i in range(6)]
    db = _board(tmp_path / "kanban.db", [*extra, (C2220, "Land PR #2220", "", "running", T_REPLY - 22)])
    monkeypatch.setenv("COACH_BOARD_DBS", db)
    monkeypatch.setenv("COACH_SHADOW_DBS", "")
    assert rc.pr_owner_card_ids("Still in progress: PR #2220 is landing.", [])[0] == C2220


def test_card_read_line_only_for_board_reads_or_card_ids():
    log = _tool_log([("terminal", {"command": "ls -la " + "x" * 200}, '{"output": "t_01a1d3df5e5d0aaa"}'),
                     ("terminal", {"command": "true; " + "y" * 200 + " sqlite3 ~/.hermes/kanban.db 'select id'"},
                      '{"output": "t_01a1d3df5e5d0bbb", "exit_code": 0}'),
                     ("terminal", {"command": "z" * 200 + f" hermes kanban show {C2220}"},
                      '{"error": "boom", "exit_code": 1}')])
    cards = [l for l in log if l.lstrip().startswith("<- CARDS")]
    assert len(cards) == 2, log
    assert "t_01a1d3df5e5d0bbb" in cards[0] and "kanban.db" in cards[0]
    assert C2220 in cards[1]
    # status lines still sit right after their call (restart_deferral / card_closed read line i+1)
    i = next(i for i, l in enumerate(log) if l.startswith("-> terminal(zzz"))
    assert log[i + 1].lstrip().startswith("<- ERROR"), log


# ── agent-276aaa2c41: the ask names a different card; the reply's ending names the PR / "the build card" ────────────
# Hub session 20261112_002413_39be4c, 00:04Z 2026-11-13. The ask was the done-notice of review card t_01a1d22c3f7ee8d5;
# the reply's ending talked about PR 1714 and "the build card" (t_01a1cc39958aa6f9, scheduled, its wake_check names
# .../pull/1714), which the turn had read with a raw read-only sqlite query. tail_card_ids returned the ask's review
# card alone, so the judges saw only a done card and held a true reply at p 0.9 (hub coach/log.jsonl line 73022).
REVIEW = "t_01a1d22c3f7ee8d5"
BUILD = "t_01a1cc39958aa6f9"
ASK_276 = (f"[Kanban notice] Task {REVIEW} (Independent review: worker enrollment fix) is done.\n"
           "Summary: approved at exact head.")
REPLY_276 = (
    "The review is done and approved.\n\n"
    "Autoscaling is unchanged: PR 1714 (the enrollment fix) is still open, and the build card is parked on its merge. "
    "Your autoscaling chat, which owns that merge, last wrote at 21:57Z and is waiting on your MacBook coming back "
    "online.")
# CMD_276 is a recorded tool-log command string (never executed here): its read-only sqlite3 URIs open nothing, so
# they need no query_only fallback (tests/test_sqlite_ro_guard.py reads this file).
CMD_276 = ("cd ~; D=~/.hermes/kanban.db; date -u +%T; for n in 1779 1714; do echo \"$n $(token-broker exec --scope "
           "user/github --reason 'read PR state for close-out' -- gh pr view $n -R acme/hermes --json state,mergedAt,"
           "headRefOid,title 2>&1 | tail -1 | cut -c1-260)\"; done; for f in $D ~/.hermes/kanban/sync-shadow/default.db;"
           f" do sqlite3 -separator ' | ' \"file:$f?{RO}\" \"select id,status,coalesce(lease_holder,'-'),created_on,"
           "substr(title,1,100) from tasks where status not in ('archived','done') and (title like '%1779%' or title "
           f"like '%pre-rebase head%' or id='{BUILD}')\"; sqlite3 \"file:$f?{RO}\" \"select substr(coalesce(summary,"
           f"''),1,900) from task_runs where task_id='{REVIEW}' order by id desc limit 1\"; done | sort -u")
OUT_276 = (
    "00:03:12\n1779 {\"headRefOid\":\"c11e71f25cec5320e240694fb4510d4bddc936ef\",\"mergedAt\":\"2026-11-13T00:02:26Z\","
    "\"state\":\"MERGED\",\"title\":\"core-wt-land: fetch an absent rejected pre-rebase head before classifying the "
    "rejection\"}\n1714 {\"headRefOid\":\"557e763cf342e3cc38f53dc492ba9e29ac5cc10b\",\"mergedAt\":null,\"state\":\"OPEN\","
    "\"title\":\"fix(workers): enrollment ownership and blocked Pending:Wait cleanup\"}\nI approved PR #1779 at exact "
    "head c11e71f25c and posted a COMMENT review at that commit (read back). Not merged; t_01a1d15e7a7c779f owns "
    f"landing.\n{BUILD} | scheduled | - | hub | Build multi-worker autoscaling: up to 3 on-demand autoscaled workers "
    "with a free-slot target\nt_01a1d15e7a7c779f | running | worker-3 | worker-3 | core-wt rejection classification: "
    "fetch missing pre-rebase head before patch comparison")
WAKE_1714 = "gh pr view https://github.com/acme/hermes/pull/1714 --json state -q .state | grep -qx MERGED"


def _log_276():
    """The turn's real board read (state.db msgs 1257171/1257172, repo slug scrubbed, result trimmed) through the real
    _log_call: the build card's id lies past TURN_LOG_ARG_CAP."""
    return _tool_log([("terminal", {"command": CMD_276},
                       json.dumps({"output": OUT_276, "exit_code": 0, "error": None}))])


def _board_276(tmp_path, monkeypatch, wake=WAKE_1714):
    db = _board(tmp_path / "kanban.db", [
        (REVIEW, "Independent review: worker enrollment fix", "Review the enrollment change.", "done", None),
        (BUILD, "Build: worker autoscaling", "Scale the pool from queue depth.", "scheduled", None)])
    c = sqlite3.connect(db)
    c.execute("insert into task_events (task_id, kind, payload, created_at) values (?,?,?,?)",
              (BUILD, "scheduled", json.dumps({"reason": "SCHEDULED_UNTIL=2026-11-13T06:00:00Z waiting on the merge",
                                               "wake_check": wake}), NOW - 1800))
    c.commit()
    c.close()
    monkeypatch.setenv("COACH_BOARD_DBS", db)
    monkeypatch.setenv("COACH_SHADOW_DBS", "")
    return db


def test_ask_named_card_does_not_hide_the_pr_owner_the_reply_refers_to(tmp_path, monkeypatch):
    _board_276(tmp_path, monkeypatch)
    log = _log_276()
    lines = rc.named_card_facts(REPLY_276, log, now=NOW, user_ask=ASK_276)
    assert any(f"<- BOARD task={BUILD} status=scheduled" in l for l in lines), lines
    ids, fallback = rc.tail_card_ids(REPLY_276, log, ASK_276)
    assert ids[0] == REVIEW and BUILD in ids and fallback is True, ids


def test_pr_without_hash_resolves_through_the_wake_check_alone(tmp_path, monkeypatch):
    _board_276(tmp_path, monkeypatch)
    assert rc.tail_pr_ids(REPLY_276) == ["1714"]
    assert rc.pr_owner_card_ids(REPLY_276, []) == [BUILD]


def test_wake_check_by_core_wt_probe_number_resolves(tmp_path, monkeypatch):
    _board_276(tmp_path, monkeypatch, wake="hermes-core-wt-land --review-ci-probe --repo x/y --number 1714 --sha ab")
    assert rc.pr_owner_card_ids("PR #1714 is still open.", []) == [BUILD]


def test_wake_check_of_a_closed_card_is_not_an_owner(tmp_path, monkeypatch):
    db = _board_276(tmp_path, monkeypatch)
    c = sqlite3.connect(db)
    c.execute("update tasks set status='done' where id=?", (BUILD,))
    c.commit()
    c.close()
    assert rc.pr_owner_card_ids("PR 1714 is still open.", []) == []


def test_raw_readonly_sqlite_read_counts_as_a_turn_touched_card(tmp_path, monkeypatch):
    _board_276(tmp_path, monkeypatch)
    assert BUILD in rc.turn_card_ids(_log_276())
    ec = _tool_log([("execute_code", {"code": "import sqlite3\nc = sqlite3.connect('file:/h/.hermes/kanban/sync-shadow/"
                                               f"mac.db?{RO}', uri=True)\nprint(c.execute(\"select status from "
                                               f"tasks where id='{BUILD}'\").fetchall())"}, '{"output": "scheduled"}')])
    assert rc.turn_card_ids(ec) == [BUILD]
    assert rc.turn_card_ids(_tool_log([("terminal", {"command": f"grep {BUILD} notes.txt"}, '{"output": ""}')])) == []


def test_ask_only_behaviour_kept_when_the_reply_names_no_pr_and_no_card(tmp_path, monkeypatch):
    _board_276(tmp_path, monkeypatch)
    reply = "The review is done and approved. Nothing else needs you."
    assert rc.tail_card_ids(reply, _log_276(), ASK_276) == ([REVIEW], True)


def test_reply_naming_a_card_id_ignores_the_ask_and_the_pr(tmp_path, monkeypatch):
    _board_276(tmp_path, monkeypatch)
    reply = f"PR 1714 is still open; {BUILD} is parked on its merge."
    assert rc.tail_card_ids(reply, _log_276(), ASK_276) == ([BUILD], False)


def test_ask_plus_referred_cards_cap_at_named_card_max(tmp_path, monkeypatch):
    _board_276(tmp_path, monkeypatch)
    ask = "Done: " + " ".join(f"t_01a1d22c3f7ee8{i:02x}" for i in range(rc.NAMED_CARD_MAX))
    ids, _ = rc.tail_card_ids(REPLY_276, _log_276(), ask)
    assert len(ids) <= rc.NAMED_CARD_MAX
