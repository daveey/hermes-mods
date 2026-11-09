"""report_only vs an owner card parked on a live wake probe (self-heal agent-61b50f34ae).

Hub 20261102_165350_624f5b, 2026-11-06 22:54Z: a Kanban completion notice. The reply's one open item was the merged
Desktop recovery fix (PR #784, 9ba5754) not yet installed on the MacBook. Its owner t_01a19d53771620db was scheduled
with `wake_check=rollout-probe.py` every 30 min (77 probe runs since 2026-11-05 07:48Z); the MacBook's updater
installs the staged build on the next quit. The turn read the owner (`hermes kanban show ... --json | python3`),
but the `<- OK` line only showed `last=wake_episode_started (1d ago)`: the coach's board read never looked at the
probe, so the card looked like a stalled owner and both judges scored the reply 0.9 (log.jsonl:39389, 39393).
The self-heal collector then called the paraphrased same item "a different problem" (39404).

Now a scheduled card's board facts carry `probe=live(...)` / `probe=stale(...)` from task_wake_checks, the
rubrics count a live probe waiting on a non-agent producer as a started owner, `report_only_scored` logs
`owner_probe_live`, and the collector treats two verdicts naming the same card / PR / commit as one item.
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_wakeprobe_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rules = _mod("agent_rules")
rc = _mod("reply_check")
sg = _mod("signals")

OWNER = "t_01a19d53771620db"
T = 1794005652.9  # the nudge's report_only_scored (2026-11-06T22:54:12Z)
EPISODE_AT = 1793864880  # wake_episode_started, 2026-11-05T07:48Z

SCHEMA = """
create table tasks (id text primary key, title text, status text, created_at integer, started_at integer,
                    completed_at integer, current_run_id integer);
create table task_events (id integer primary key autoincrement, task_id text, run_id integer, kind text,
                          payload text, created_at integer);
create table task_runs (id integer primary key autoincrement, task_id text, status text, outcome text,
                        started_at integer, ended_at integer);
create table task_links (parent_id text, child_id text, primary key (parent_id, child_id));
create table task_wake_episodes (task_id text primary key, cmd text not null, first_at integer not null,
                                 checks integer not null, last_stale_at integer);
create table task_wake_checks (task_id text primary key, cmd text not null, every integer not null,
                               next_at integer not null, last_at integer, last_rc integer, last_out text,
                               fails integer not null default 0, generation text,
                               purpose text not null default 'wake');
"""
PROBE = f"python3 /home/ubuntu/.hermes/kanban/workspaces/{OWNER}/rollout-probe.py"


def _board(tmp_path, last_check_ago=600, probe=True, rc_=1, name="kanban.db"):
    path = tmp_path / name
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.execute("insert into tasks (id, status, created_at) values (?,?,?)", (OWNER, "scheduled", 1793640855))
    for kind, at in (("heartbeat", 1793782984), ("commented", 1793782995), ("scheduled", 1793783017),
                     ("worker_result", 1793783025), ("wake_episode_started", EPISODE_AT)):
        c.execute("insert into task_events (task_id, kind, created_at) values (?,?,?)", (OWNER, kind, at))
    c.execute("insert into task_runs (task_id, status, outcome, started_at) values (?,?,?,?)",
              (OWNER, "scheduled", "scheduled", 1793782905))
    if probe:
        c.execute("insert into task_wake_episodes values (?,?,?,?,?)", (OWNER, PROBE, EPISODE_AT, 77, None))
        last = int(T - last_check_ago)
        c.execute("insert into task_wake_checks (task_id, cmd, every, next_at, last_at, last_rc, fails, purpose) "
                  "values (?,?,?,?,?,?,?,?)", (OWNER, PROBE, 1800, last + 1800, last, rc_, 0, "wake"))
    c.commit()
    c.close()
    return str(path)


# The turn's owner read, verbatim (state.db msgs 971748/971749).
CMD = ("hermes kanban show t_01a19d53771620db --json 2>/dev/null | python3 -c \"\nimport json,sys,time;d=json.load("
       "sys.stdin);t=d.get('task',d);r=d.get('runs',[])\nprint(t.get('status'));x=r[-1] if r else {}\nprint('last "
       "run',x.get('status'),x.get('outcome'),str(x.get('summary') or x.get('error') or '')[:700])\nev=[e for e in "
       "d.get('events',[]) if e.get('kind') in ('scheduled','parked')][-1:]\nprint(json.dumps(ev)[:900])\"")
OUT = ("scheduled\nlast run scheduled scheduled SCHEDULED_UNTIL=2026-11-11T09:05:00Z \u2014 MacBook is offline at "
       "2026-11-04 09:02Z; rollout probe exits 1 with no installed-bundle/process evidence. PR #784 implementation, "
       "exact-head review and merge already finished. Await normal MacBook updater installing landed 9ba5754 and a "
       "newly started Desktop; wake early on existing rollout probe, then perform live primary-close + pinned-read "
       "proof without forced restart.\n[{\"kind\": \"scheduled\"}]")

# The nudged draft (drafts.jsonl d_bddd7e6aa3dd4459) and the revised reply (state.db msg 971842), verbatim.
DRAFT = (
    "The last live check passed. On your MacBook, quitting the app installed the new version on its own; the "
    "version stamp changed from `cd592994bc` to `4dfb4cc1f0`. The app-update path works end to end now.\n\n"
    "Only one card is still open: the fix that lets the app recover when its connection to the gateway breaks "
    "(`t_01a19d53771620db`). Its code is merged (PR #784, commit `9ba5754`). It isn't running on your Mac yet, "
    "because the version you just installed was built before that fix landed. This needs nothing from you:\n\n"
    "- Your MacBook's code checkout (`62c90b6d51`) already contains the fix. The app version that includes it was "
    "ready to install as of 17:53 today.\n"
    "- The updater is set to install it the next time you quit the app. The app has been open since Nov 3.\n"
    "- The card checks every 30 minutes. Once the new version is installed, it tests recovery live on your Mac. It "
    "never quits the app itself.\n\n"
    "If you never quit the app, the card stays parked until Nov 11 and then reports that the fix still isn't "
    "installed.")
REVISED = (
    "The last live check passed. Quitting the app on your MacBook installed the new version on its own: the version "
    "stamp changed from `cd592994bc` to `4dfb4cc1f0`. The app-update path now works end to end.\n\n"
    "One fix is still waiting to land on your Mac: app recovery when its connection to the gateway breaks "
    "(`t_01a19d53771620db`, PR #784, commit `9ba5754`). The version that just installed was built before that fix "
    "merged. Getting it installed is already moving, and you don't need to do anything:\n\n"
    "- Your MacBook's checkout (`62c90b6d51`) already contains the fix.\n"
    "- At 17:53 today, hermes-sync copied over an app build that includes it, ready to install.\n"
    "- The updater is set to install that build the next time you quit the app. The app has been open since Nov 3.\n"
    "- The card checks every 30 minutes. Once the new build is in, it tests recovery live on your Mac. It never "
    "quits the app itself.\n\n"
    "If you want it sooner, quit the app whenever it's convenient. The update installs and the app reopens in about "
    "30 seconds. I won't quit it for you, because that would cut off any chats that are mid-reply. If you never quit "
    "it, the card stays parked until Nov 11 and then reports that the fix still isn't installed.")
NUDGED_EV = "It isn't running on your Mac yet, because the version you just installed was built before that fix landed."
OUTCOME_EV = "One fix is still waiting to land on your Mac: app recovery when its connection to the gateway breaks"


def _res(out):
    return json.dumps({"output": out, "exit_code": 0, "error": None})


def _owner_line(monkeypatch, db):
    monkeypatch.setenv("COACH_BOARD_DBS", db)
    return rules.owner_result_line("terminal", {"command": CMD}, _res(OUT), now=T)


# ── 1. the turn's own owner read now shows the live probe ───────────────────────────────────────────────────────
def test_owner_read_of_probe_parked_card_shows_live_probe(tmp_path, monkeypatch):
    line = _owner_line(monkeypatch, _board(tmp_path))
    assert line and f"task={OWNER}" in line and "status=scheduled" in line, line
    assert "probe=live(" in line and "rc=1" in line and "last=10m" in line and "every=30m" in line, line
    assert "checks=77" in line, line
    assert len(line) <= rules.OK_LINE_MAX, line


def test_frozen_probe_reads_stale(tmp_path, monkeypatch):
    """The same fixture with the probe frozen for 2.5 h (every=30m): not a live wait."""
    line = _owner_line(monkeypatch, _board(tmp_path, last_check_ago=9000))
    assert "probe=stale(" in line and "probe=live" not in line, line


def test_broken_probe_reads_stale(tmp_path, monkeypatch):
    """A probe exiting 2+ (broken) is not a live wait even when it just ran."""
    line = _owner_line(monkeypatch, _board(tmp_path, rc_=2))
    assert "probe=stale(" in line, line


def test_probe_less_park_adds_no_probe_fact(tmp_path, monkeypatch):
    line = _owner_line(monkeypatch, _board(tmp_path, probe=False))
    assert line and "probe=" not in line, line


def test_board_line_and_kanban_show_summary_carry_the_probe(tmp_path, monkeypatch):
    db = _board(tmp_path)
    facts = rules.board_card_facts(OWNER, now=T, dbs=[db])
    assert facts and facts["probe"]["live"] is True and facts["probe"]["checks"] == 77, facts
    assert "probe=live(" in rules.board_fact_line(facts, now=T)
    # kanban_show tool result (no terminal pipe): the summary line gets the probe from the board too
    monkeypatch.setenv("COACH_BOARD_DBS", db)
    show = {"task": {"id": OWNER, "status": "scheduled"},
            "runs": [{"id": 1, "status": "scheduled", "outcome": "scheduled", "started_at": 1793782905}],
            "events": [{"kind": "wake_episode_started", "created_at": EPISODE_AT}]}
    line = rules.owner_result_line("kanban_show", {"task_id": OWNER}, json.dumps(show), now=T)
    assert line and "status=scheduled" in line and "probe=live(" in line, line


# ── 2. detection guard: report_only_scored.owner_probe_live ────────────────────────────────────────────────────
def _scored(tmp_path, monkeypatch, **board):
    log = ["-> terminal(" + CMD[:120] + ")", _owner_line(monkeypatch, _board(tmp_path, **board))]
    events = []

    def llm(system, prompt):
        return json.dumps({"report_only": 0.88, "unverified": 0, "evidence": NUDGED_EV})

    rc.check(DRAFT, "\u2714 [default] @default Kanban t_01a1a09989e69967 done", log, llm, threshold=0.8,
             only=["report_only"], confirm=lambda: (0.2, "owner parked on a live probe"), confirm_floor=0.5,
             audit=events.append, thresholds={"report_only": 0.5})
    return next(e for e in events if e["event"] == "report_only_scored")


def test_report_only_scored_logs_owner_probe_live(tmp_path, monkeypatch):
    assert _scored(tmp_path, monkeypatch)["owner_probe_live"] is True
    assert _scored(tmp_path, monkeypatch, last_check_ago=9000, name="frozen.db")["owner_probe_live"] is False
    assert rc.owner_probe_live(DRAFT, [f"   <- BOARD task={OWNER} status=scheduled probe=live(rc=1)"]) == [OWNER]
    # a live probe on a card the reply does not name is not this reply's owner
    assert rc.owner_probe_live("All done.", [f"   <- OK task={OWNER} status=scheduled probe=live(rc=1)"]) == []


# ── 3. both rubrics say how to read a probe fact ───────────────────────────────────────────────────────────────
def test_rubrics_count_a_live_probe_on_a_named_producer_as_started():
    for rubric in (rc.BOARD_OWNER, rc.JUDGE_SYS, rc.CONFIRM_SYS, rc.REPORT_ONLY_RULE):
        assert "probe=live" in rubric and "probe=stale" in rubric, rubric[:200]
        assert "updater" in rubric and "install" in rubric, rubric[:200]


# ── 4. the collector: a paraphrase naming the same card / PR / commit is the same item ─────────────────────────
def _home(tmp_path, rows):
    h = tmp_path / "home"
    (h / "coach").mkdir(parents=True)
    (h / "coach" / "log.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return h


SID = "20261102_165350_624f5b"
NOW = T + 400


def _pair(nudge_ids=None, out_ids=None):
    nudge = {"ts": T, "event": "reply_nudge", "session_id": SID, "rule": "report_only", "nudge_id": "n_dd92fa928de14087",
             "p": 0.9, "evidence": NUDGED_EV, "platform": "desktop", "draft_id": "d_bddd7e6aa3dd4459", "outcome": True}
    out = {"ts": T + 21, "event": "reply_nudge_outcome", "session_id": SID, "rule": "report_only",
           "nudge_id": "n_dd92fa928de14087", "draft_id": "d_bddd7e6aa3dd4459", "p": 0.9, "resolved": False,
           "judged": True, "evidence": OUTCOME_EV, "reply": REVISED[:300], "src": "confirm"}
    if nudge_ids is not None:
        nudge["item_ids"] = nudge_ids
    if out_ids is not None:
        out["item_ids"] = out_ids
    return nudge, out


def test_evidence_ids_reads_the_paragraph_the_verdict_quotes():
    assert set(rc.evidence_ids(DRAFT, NUDGED_EV)) >= {OWNER, "#784", "9ba5754"}
    assert set(rc.evidence_ids(REVISED, OUTCOME_EV)) >= {OWNER, "#784", "9ba5754"}
    # the ids of the first paragraph (the passed live check) are not this item's
    assert "cd59299" not in rc.evidence_ids(REVISED, OUTCOME_EV)


def test_39390_39393_pair_is_one_item(tmp_path):
    nudge, out = _pair(rc.evidence_ids(DRAFT, NUDGED_EV), rc.evidence_ids(REVISED, OUTCOME_EV))
    h = _home(tmp_path, [nudge, out])
    got = [s for s in sg.collect_coach(h, {}, NOW) if s.get("component") == "agent"]
    rows = [json.loads(l) for l in (h / "coach" / "log.jsonl").read_text().splitlines()]
    assert not [r for r in rows if r.get("event") == "nudge_outcome_other_item"], rows[-1]
    assert got and "different problem" not in got[0]["symptom"], got


def test_same_item_rules():
    assert sg.same_item("PR #784 still not installed", "the #784 build is not on the Mac")
    assert sg.same_item("a", "b", ["t_01a19d53771620db"], ["t_01a19d53771620db", "#9"])
    assert sg.same_item("Still running: main CI for the command-list fix",
                        "main CI for the command-list fix is still running")  # token overlap >= 0.5
    # agent-e5b6dcf428's genuinely different pair stays different
    assert not sg.same_item("Still running: main CI for the command-list fix. I'm watching it and will post the "
                            "result.", "The two 30 s connect timeouts are still unexplained. They now cost at most "
                            "about 2.5 minutes")


def test_nudge_and_outcome_records_carry_item_ids():
    """The live hook logs item_ids on reply_nudge and reply_nudge_outcome (source check: both call evidence_ids)."""
    src = (HERE / "__init__.py").read_text()
    assert src.count("rc.evidence_ids(") >= 2, "reply_nudge and reply_nudge_outcome must both log item_ids"
