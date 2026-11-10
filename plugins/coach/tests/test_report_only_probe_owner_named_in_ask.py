"""report_only vs a probe-parked owner the ASK names but the draft calls only "the existing task" (agent-b22d5f6bb5).

Hub 20261103_030939_2dfec2, 2026-11-04 04:28Z: an async-delegation-done notice whose ask names delivery card
t_01a19fa29d3949d0 (scheduled, wake_check `tailscale ping ... test-key-gen` every 900 s, waiting for the MacBook to
come back online). The draft said the public mirror was published and "the existing task will resume automatically
when it reconnects" without the card id. The coach's own board read (named_card_facts) only looked at card ids in the
reply, so neither judge saw the card's live probe and both scored 0.9 (log.jsonl ts 1793766532). The revision named
the card and failed again (0.9, ts 1793766554); the self-heal collector called it "a different problem" because the
nudged paragraph named no id (nudge_outcome_other_item, ts 1793768176). The card woke 18 min later and finished.

Now, when the reply names no card, the coach reads the cards the user's ask names (as owner_probe_live already did),
and evidence_ids falls back to the ask's card ids when the quoted paragraph names none.
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_askprobe_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rules = _mod("agent_rules")
rc = _mod("reply_check")
sg = _mod("signals")

OWNER = "t_01a19fa29d3949d0"
MIRROR = "t_01a19d60d1b1064a"
T = 1793766532.0  # the draft's report_only_scored
PARKED_AT = 1793730154  # `scheduled` event: SCHEDULED_UNTIL=2026-11-04T18:30Z, waiting for the MacBook to wake
SID = "20261103_030939_2dfec2"

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
PROBE = "tailscale ping -c 1 --timeout 5s test-key-gen >/dev/null 2>&1"


def _board(tmp_path, last_check_ago=300, name="kanban.db"):
    path = tmp_path / name
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.execute("insert into tasks (id, title, status, created_at) values (?,?,?,?)",
              (OWNER, "Finish shipping actionable blocker notices PR744 and verify Desktop rollout", "scheduled",
               1793679596))
    for kind, at in (("claimed", 1793730035), ("commented", 1793730137), ("scheduled", PARKED_AT),
                     ("worker_result", 1793730164), ("wake_episode_started", PARKED_AT + 900),
                     ("commented", 1793766478)):
        c.execute("insert into task_events (task_id, kind, created_at) values (?,?,?)", (OWNER, kind, at))
    c.execute("insert into task_runs (task_id, status, outcome, started_at, ended_at) values (?,?,?,?,?)",
              (OWNER, "scheduled", "scheduled", 1793730035, 1793730164))
    c.execute("insert into task_wake_episodes values (?,?,?,?,?)", (OWNER, PROBE, PARKED_AT + 900, 39, None))
    last = int(T - last_check_ago)
    c.execute("insert into task_wake_checks (task_id, cmd, every, next_at, last_at, last_rc, fails, purpose) "
              "values (?,?,?,?,?,?,?,?)", (OWNER, PROBE, 900, last + 900, last, 1, 0, "wake"))
    c.commit()
    c.close()
    return str(path)


# The ask (drafts.jsonl d_b93126943acc4083, head), the draft and the revision (state.db msg 824842), verbatim.
ASK = ("[ASYNC DELEGATION BATCH COMPLETE \u2014 deleg_7d8cf281]\nA background fan-out unit you dispatched earlier \u2014 "
       "1 subagent(s) \u2014 has finished; its consolidated results are below.\n\n--- \u2713 TASK 1/1: Execute remaining "
       "agent-owned public mirror work for blocker-visibility PR744 on hub now rather than waiting for sleeping "
       f"MacBook. Existing delivery card {OWNER} scheduled; original mirror owner macbook:{MIRROR}. Do not force Mac "
       "wake/restart or claim inaccessible Mac verification done. Record evidence on existing delivery card, don't "
       "close it; parent verifies published artifacts.")
DRAFT = ("The public mirror is published. I fetched GitHub and verified commit "
         "[`e7abb268`](https://github.com/daveey/hermes-mods/commit/e7abb268) is on `main`, including the "
         "blocker-visibility fix as patch 1821.\n\n"
         "The tooling card now has one remaining check: independent MacBook verification. A fresh connection attempt "
         "still timed out; the existing task will resume automatically when it reconnects. No action is needed from "
         "you.")
REVISED = (f"The follow-up is already scheduled on **`{OWNER}`**: it checks MacBook reachability every 15 minutes and "
           "resumes verification when reachable. I updated it with the verified mirror publication and exact "
           "remaining acceptance check. A duplicate card cannot make the offline Mac reachable.")
NUDGED_EV = "The tooling card now has one remaining check: independent MacBook verification."
OUTCOME_EV = "A duplicate card cannot make the offline Mac reachable."

CMD = ("git -C /home/ubuntu/code/hermes-mods fetch origin main --quiet && git -C /home/ubuntu/code/hermes-mods "
       "merge-base --is-ancestor e7abb268 origin/main && git -C /home/ubuntu/code/hermes-mods show "
       "e7abb268:core/mirror-1821-verification.md; tailscale ping -c 1 --timeout 5s test-key-gen")
LOG = [f"-> terminal({CMD[:200]})", "<- OK # Mirror 1821 verification ... timed out",
       f"-> kanban_comment(task_id={OWNER}, body=Parent independently fetched hermes-mods origin/main ...)",
       f'<- OK {{"ok": true, "task_id": "{OWNER}", "comment_id": 42496, "live_run": false}}']


# ── 1. the judges see the probe of the card the ask names when the draft names no card ─────────────────────────
def test_draft_judge_prompts_carry_the_ask_cards_live_probe(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", _board(tmp_path))
    monkeypatch.setattr(rc.time, "time", lambda: T)
    lines = rc.named_card_facts(DRAFT, LOG, now=T, user_ask=ASK)
    assert lines and f"task={OWNER}" in lines[0] and "status=scheduled" in lines[0], lines
    assert "probe=live(" in lines[0] and "every=15m" in lines[0], lines
    for prompt in (rc.judge_prompt(ASK, LOG, DRAFT), rc.confirm_prompt(ASK, LOG, DRAFT)):
        assert f"<- BOARD task={OWNER} status=scheduled" in prompt and "probe=live(" in prompt, prompt[-1500:]


def test_revision_judge_prompts_carry_the_live_probe(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", _board(tmp_path))
    monkeypatch.setattr(rc.time, "time", lambda: T)
    for prompt in (rc.judge_prompt(ASK, LOG, REVISED), rc.confirm_prompt(ASK, LOG, REVISED)):
        assert f"<- BOARD task={OWNER} status=scheduled" in prompt and "probe=live(" in prompt, prompt[-1500:]


def test_reply_naming_its_own_card_does_not_pull_in_the_asks_cards(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", _board(tmp_path))
    lines = rc.named_card_facts("Card t_01a1ffff00000000 owns the rest.", LOG, now=T, user_ask=ASK)
    assert not any(OWNER in l for l in lines), lines


def test_frozen_probe_on_the_ask_card_reads_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", _board(tmp_path, last_check_ago=9000))
    lines = rc.named_card_facts(DRAFT, LOG, now=T, user_ask=ASK)
    assert lines and "probe=stale(" in lines[0] and "probe=live" not in lines[0], lines


# ── 2. detection guard: report_only_scored.owner_probe_live / owner_source on the draft ────────────────────────
def _scored(tmp_path, monkeypatch, reply=DRAFT, **board):
    monkeypatch.setenv("COACH_BOARD_DBS", _board(tmp_path, **board))
    monkeypatch.setattr(rc.time, "time", lambda: T)
    events, prompts = [], []

    def llm(system, prompt):
        prompts.append(prompt)
        return json.dumps({"report_only": 0.9, "unverified": 0, "evidence": NUDGED_EV})

    rc.check(reply, ASK, LOG, llm, threshold=0.8, only=["report_only"],
             confirm=lambda: (0.2, "owner parked on a live probe"), confirm_floor=0.5,
             audit=events.append, thresholds={"report_only": 0.5}, now=T)
    return next(e for e in events if e["event"] == "report_only_scored"), prompts


def test_draft_report_only_scored_logs_owner_probe_live(tmp_path, monkeypatch):
    ev, prompts = _scored(tmp_path, monkeypatch)
    assert ev["owner_probe_live"] is True and ev["owner_source"] == "board", ev
    assert prompts and "probe=live(" in prompts[0], prompts[0][-800:]


def test_frozen_probe_draft_logs_owner_probe_not_live(tmp_path, monkeypatch):
    ev, _ = _scored(tmp_path, monkeypatch, last_check_ago=9000, name="frozen.db")
    assert ev["owner_probe_live"] is False, ev


# ── 3. the collector: the nudged paragraph names no id, the ask does: same item as the revision ────────────────
def test_evidence_ids_fall_back_to_the_asks_cards():
    assert rc.evidence_ids(DRAFT, NUDGED_EV) == [] or OWNER not in rc.evidence_ids(DRAFT, NUDGED_EV)
    got = rc.evidence_ids(DRAFT, NUDGED_EV, user_ask=ASK)
    assert OWNER in got, got
    assert OWNER in rc.evidence_ids(REVISED, OUTCOME_EV, user_ask=ASK)
    # a paragraph that names its own ids keeps only those (plus the evidence's)
    assert rc.evidence_ids("Card t_01a1ffff00000000 owns it.", "owns it", user_ask=ASK) == ["t_01a1ffff00000000"]


def test_1793766532_1793766554_pair_is_one_item(tmp_path):
    nudge = {"ts": 1793766532.12, "event": "reply_nudge", "session_id": SID, "rule": "report_only",
             "nudge_id": "n_a965f33919c54d9c", "p": 0.9, "evidence": NUDGED_EV, "platform": "desktop",
             "draft_id": "d_b93126943acc4083", "outcome": True,
             "item_ids": rc.evidence_ids(DRAFT, NUDGED_EV, user_ask=ASK)}
    out = {"ts": 1793766554.85, "event": "reply_nudge_outcome", "session_id": SID, "rule": "report_only",
           "nudge_id": "n_a965f33919c54d9c", "draft_id": "d_b93126943acc4083", "p": 0.9, "resolved": False,
           "judged": True, "evidence": OUTCOME_EV, "reply": REVISED[:300], "src": "confirm",
           "item_ids": rc.evidence_ids(REVISED, OUTCOME_EV, user_ask=ASK)}
    h = tmp_path / "home"
    (h / "coach").mkdir(parents=True)
    (h / "coach" / "log.jsonl").write_text(json.dumps(nudge) + "\n" + json.dumps(out) + "\n")
    got = [s for s in sg.collect_coach(h, {}, 1793766554.85 + 400) if s.get("component") == "agent"]
    rows = [json.loads(l) for l in (h / "coach" / "log.jsonl").read_text().splitlines()]
    assert not [r for r in rows if r.get("event") == "nudge_outcome_other_item"], rows[-1]
    assert got and "different problem" not in got[0]["symptom"], got


def test_live_hook_passes_the_ask_to_board_reads_and_item_ids():
    """Source check: the pre_verify confirm closure and both item_ids call sites hand the ask over."""
    src = (HERE / "__init__.py").read_text()
    assert "rc.named_card_facts(reply, coach.turn_rich(session_id), user_ask=ask)" in src
    assert src.count("user_ask=ask)") >= 3, "named_card_facts + both evidence_ids calls must pass the ask"
