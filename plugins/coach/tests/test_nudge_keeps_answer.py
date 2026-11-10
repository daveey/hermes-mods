"""Self-heal agent-e868df5c84: a coach nudge made the agent drop its direct "No." answer to the user's question.

Nov 2 13:49 PT, desktop chat (retention work): David asked "are you done". The held draft opened "**No. Three-day
retention is not yet deployed and verified end to end.**"; report_only nudged it (fix it, card it or prove the owner)
and the revision opened "The work is already started: I verified t_01a19e1fc70f3521 is executing..." with no yes or
no, so he had to ask again. These tests pin, on the real (scrubbed) drafts from that and two other dropped answers:
1. reply_check.direct_lead() finds a draft's direct answer, and every rule's nudge then asks to keep it first;
   a draft with no direct lead gets the nudge unchanged;
2. the live hooks log reply_nudge_answer_dropped when the revision no longer opens with a direct answer, and
   nothing when it still does;
3. signals.collect_coach reports the dropped answer as a coach regression, and self_heal.nudge_counts counts it;
4. "are you done" / "is it done" / "done yet" are progress asks.
"""
import importlib.util
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rc = load("coach_reply_check_keeps_answer", ROOT / "reply_check.py")
sg = load("coach_signals_keeps_answer", ROOT / "signals.py")
sh = load("coach_self_heal_keeps_answer", ROOT / "self_heal.py")

# The incident (coach/drafts.jsonl d_e4b8ada3e3cf4f91 and its reply_nudge_outcome n_8d152b162d534cb2, hermes-hub).
ASK = "are you done"
DRAFT = ("**No. Three-day retention is not yet deployed and verified end to end.** Final integration and privacy fixes "
         "are still underway, followed by production migration and activation.\n\nThe completion notices were for "
         "individual subtasks, not your whole request. I should have made that clearer and spared you the repeated "
         "internal updates.")
REVISED = ("The work is already started: I verified **`t_01a19e1fc70f3521` is executing on hermes-hub in run 28228**, "
           "not merely queued. It owns final integration, tests, and independent review; **`t_01a194b9de51e620`** owns "
           "the subsequent deployment and production verification.")
REVISED_KEPT = ("**No. Three-day retention is not yet deployed and verified end to end.** The work is already started: "
                "I verified `t_01a19e1fc70f3521` is executing on hermes-hub in run 28228, not merely queued.")
# Two of the other five (n_8c73c2052a8e4a9b, a Kanban notice reply; n_ac7981bbb1714c1d, a cross-session reply).
OTHER = [
    ("**No delivery worker is running at this instant.** It was parked on the persistence incident; that "
     "incident now explicitly clears it to resume.",
     "The preventive fix already has an implementation card: **`t_01a1a5a82366d7e6`**, queued at priority 7.",
     "No delivery worker is running at this instant."),
    ("Yes—the hub worker owns the association change. Please keep your work read-only and independently verify the "
     "landing and installation evidence.",
     "The fix is already executing: I verified the hub worker is running on the existing reconciliation.",
     "Yes—the hub worker owns the association change."),
]
NO_LEAD = "I reopened the recovery card and verified it is ready; the owner t_01a18a95223f7036 has the fix."


# ── 1. direct_lead + the keep-answer clause in every nudge ─────────────────────────────────────────────────────

def test_direct_lead_finds_the_incident_answer_and_the_other_drops():
    lead = rc.direct_lead(DRAFT)
    assert lead.startswith("No. Three-day retention is not yet deployed"), lead
    assert "**" not in lead
    for draft, _rev, want in OTHER:
        assert rc.direct_lead(draft) == want
    assert rc.direct_lead(NO_LEAD) == ""
    assert rc.direct_lead(REVISED) == "" and rc.direct_lead(OTHER[0][1]) == ""
    assert rc.direct_lead(REVISED_KEPT).startswith("No.")
    assert rc.direct_lead("") == ""


def test_every_rule_nudge_asks_to_keep_the_direct_answer():
    for rule in rc.NUDGES:
        text = rc.nudge_for(rule, "evidence", draft=DRAFT)
        assert "Keep your direct answer to the user's question as the first line" in text, rule
        assert "'No. Three-day retention is not yet deployed" in text, rule
    # card_closed / kanban_down report_only variants too
    assert "Keep your direct answer" in rc.nudge_for("report_only", "e", card_closed=True, draft=DRAFT)
    assert "Keep your direct answer" in rc.nudge_for("report_only", "e", kanban_down=True, draft=DRAFT)


def test_draft_without_a_direct_lead_gets_the_nudge_unchanged():
    for rule in rc.NUDGES:
        assert rc.nudge_for(rule, "evidence", draft=NO_LEAD) == rc.nudge_for(rule, "evidence")


def test_keep_answer_is_idempotent_for_nudges_built_outside_nudge_for():
    once = rc.keep_answer(rc.STATUS_ONLY_NUDGE.format(evidence="x"), DRAFT)
    assert once.count("Keep your direct answer") == 1
    assert rc.keep_answer(once, DRAFT) == once


def test_answer_dropped_compares_draft_and_revision():
    lead = rc.direct_lead(DRAFT)
    assert rc.answer_dropped(lead, REVISED) is True
    assert rc.answer_dropped(lead, REVISED_KEPT) is False
    assert rc.answer_dropped("", REVISED) is False      # the draft had no direct answer to drop
    assert rc.answer_dropped(lead, "") is False         # an empty reply is another failure, not this one
    for draft, rev, _ in OTHER:
        assert rc.answer_dropped(rc.direct_lead(draft), rev) is True


# ── 2. the live hooks: nudge carries the clause, the outcome logs the drop ───────────────────────────────────

CONFIRM_RO = json.dumps({"outcome": "report_only", "agent_fixable": True,
                         "quote": "not yet deployed and verified end to end"})
CONFIRM_STARTED = json.dumps({"outcome": "started", "agent_fixable": True,
                              "quote": "t_01a19e1fc70f3521 is executing"})


def _hooks(tmp_path, monkeypatch):
    mod = load("coach_init_keeps_answer", ROOT / "__init__.py")
    c = mod._load("core")
    rcm = mod._load("reply_check")
    z = {k: 0 for k in rcm.RULES}
    asked = []
    monkeypatch.setattr(rcm, "aux_judge", lambda s, p: json.dumps(
        {**z, "report_only": 0.9, "evidence": "Three-day retention is not yet deployed and verified end to end."}))

    def aux_confirm(system, prompt):
        asked.append(prompt)
        return [CONFIRM_RO, CONFIRM_STARTED][min(len(asked), 2) - 1]
    monkeypatch.setattr(rcm, "aux_confirm", aux_confirm)
    monkeypatch.setenv("COACH_SYSTEM_ONE", "off")
    monkeypatch.setenv("COACH_STALLED_GOAL", "0")
    monkeypatch.setenv("COACH_REPLY_RULES", "report_only")
    monkeypatch.delenv("COACH_REPORT_ONLY_CONFIRM_CAP", raising=False)
    root = tmp_path / "coach"
    _, hooks = mod.build(store=c.Store(root=root), checkout=None)
    rows = lambda: [json.loads(l) for l in (root / "log.jsonl").read_text().splitlines()]  # noqa: E731
    return hooks, rows


def _turn(hooks, sid, revised):
    hooks["pre_llm_call"](session_id=sid, user_message=ASK, platform="desktop")
    hooks["post_tool_call"](tool_name="kanban_show", args={"task_id": "t_01a19e1fc70f3521"}, result="{}",
                            session_id=sid)
    out = hooks["pre_verify"](session_id=sid, platform="desktop", attempt=0, final_response=DRAFT)
    assert out and out["action"] == "continue", out
    hooks["post_tool_call"](tool_name="kanban_comment", args={"task_id": "t_01a19e1fc70f3521", "body": "x"},
                            result="{}", session_id=sid)
    assert hooks["pre_verify"](session_id=sid, platform="desktop", attempt=1, final_response=revised) is None
    return out["message"]


def test_live_nudge_keeps_the_answer_and_the_incident_revision_logs_answer_dropped(tmp_path, monkeypatch):
    hooks, rows = _hooks(tmp_path, monkeypatch)
    sid = "20261031_064316_d54356"
    msg = _turn(hooks, sid, REVISED)
    assert "Keep your direct answer to the user's question as the first line" in msg
    assert "'No. Three-day retention is not yet deployed" in msg
    nudge = [r for r in rows() if r["event"] == "reply_nudge"]
    assert len(nudge) == 1 and nudge[0]["lead"].startswith("No. Three-day retention")
    dropped = [r for r in rows() if r["event"] == "reply_nudge_answer_dropped"]
    assert len(dropped) == 1, [r["event"] for r in rows()]
    d = dropped[0]
    assert d["session_id"] == sid and d["nudge_id"] == nudge[0]["nudge_id"] and d["rule"] == "report_only"
    assert d["lead"].startswith("No. Three-day retention") and d["reply"].startswith("The work is already started")


def test_live_revision_that_keeps_the_answer_logs_nothing(tmp_path, monkeypatch):
    hooks, rows = _hooks(tmp_path, monkeypatch)
    _turn(hooks, "s_kept", REVISED_KEPT)
    assert [r for r in rows() if r["event"] == "reply_nudge_answer_dropped"] == []
    assert any(r["event"] == "reply_nudge_outcome" for r in rows())


# ── 3. detection: the collector reports it, status counts it ───────────────────────────────────────────────────

NOW = 1793656300.0
DROP = {"ts": NOW - 90, "event": "reply_nudge_answer_dropped", "session_id": "20261031_064316_d54356",
        "rule": "report_only", "nudge_id": "n_8d152b162d534cb2", "draft_id": "d_e4b8ada3e3cf4f91",
        "lead": "No. Three-day retention is not yet deployed and verified end to end.",
        "reply": REVISED[:300]}


def _home(tmp_path, rows):
    h = tmp_path / "home"
    (h / "coach").mkdir(parents=True)
    (h / "coach" / "log.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return h


def test_collector_reports_a_dropped_answer_as_a_coach_regression(tmp_path):
    h = _home(tmp_path, [DROP, {**DROP, "ts": NOW - 30, "session_id": "s2", "nudge_id": "n_2"}])
    sigs = [s for s in sg.collect_coach(h, {}, NOW) if "answer" in s.get("symptom", "")]
    assert len(sigs) == 2
    s = sigs[0]
    assert s["kind"] == "coach" and s["component"] == "plugin"
    assert "dropped" in s["symptom"] and "No. Three-day retention" in s["details"]
    assert "The work is already started" in s["details"] and s["session_id"] == DROP["session_id"]
    assert sigs[0]["fp"] == sigs[1]["fp"]  # one card per rule; later drops comment on it
    assert s.get("comment_open_card") is True


def test_nudge_counts_counts_answer_dropped(tmp_path):
    now = NOW
    rows = [{"ts": now - 200, "event": "reply_nudge", "rule": "report_only", "session_id": DROP["session_id"],
             "nudge_id": "n_8d152b162d534cb2", "outcome": True},
            {"ts": now - 100, "event": "reply_nudge_outcome", "rule": "report_only",
             "session_id": DROP["session_id"], "nudge_id": "n_8d152b162d534cb2", "resolved": True},
            DROP]
    (tmp_path / "log.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    c = sh.nudge_counts(tmp_path, now=now)
    assert c["report_only"]["resolved"] == 1 and c["report_only"]["answer_dropped"] == 1


# ── 4. the ask itself is a progress ask ───────────────────────────────────────────────────────────────────────

def test_are_you_done_is_a_progress_ask():
    for ask in ("are you done", "Are you done?", "is it done?", "done yet?", "is this done", "you done yet"):
        assert rc.PROGRESS_ASK_RE.search(ask), ask
        assert rc.PLAIN_STATUS_ASK_RE.search(ask), ask
    assert not rc.PROGRESS_ASK_RE.search("I'm done with the review, merge it")
