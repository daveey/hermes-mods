"""status_plain (self-heal agent-48e909978c): a person asks about progress in a long chat whose replies describe
internal coordination ("queued to my subagent", "hub owns any ... repair") instead of the outcome of their ask.

User turns 3/10/15/18 and the reply before turn 18 are the real ones from the 2026-10-25..11-02 issue-repair chat
(9 days, 126 automated notices; product name scrubbed): the asks went unanswered by any outcome, and two of the four phrasings ("how goes?",
"what's the deal, can we unblock ...") slipped past every progress-ask pattern the coach had.
"""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rc = _mod("t_status_plain_rc", "reply_check.py")
rr = _mod("t_status_plain_rr", "relay_repeat.py")

TURN_3 = "whats going on"
TURN_10 = "how goes?"
TURN_15 = "whats going on here, waht are the cards blocked on"
TURN_18 = "what's the deal, can we unblock app issues?"
ASKS = (TURN_3, TURN_10, TURN_15, TURN_18)
# user_turns[18].prev: the whole visible reply before turn 18
PREV_18 = ("Clarification queued to my subagent: read-only regressions only; hub owns any source or association "
           "repair. The detector remains OPEN")

NOTICES = (
    "[chat-closeout] This chat has open work. Say what you are still waiting on, or finish it.",
    "\u2714 [default] @default Kanban t_01a19f6eb64ca98b done \u2014 what's the deal with the queue: fixed",
    "\u23f8 [default] @default Kanban t_01a18e0135bfb3da blocked: still waiting on review; how goes the deploy",
    "# Kanban task t_01a1c0f7026331f3: what's the deal, can we unblock app issues?\n\n## Body\nhow goes?",
    "[coach] status_only: the user asked about progress",
)
CASUAL = ("thanks", "great, now add a dark mode toggle", "resume",
          "the deal is that we ship the widget tomorrow")


def test_progress_ask_matches_his_real_wording():
    for t in ASKS + ("how's it going?", "hows it going with the deploy", "can we get the sync working",
                     "can we fix the builder?", "what is it blocked on"):
        assert rc.PROGRESS_ASK_RE.search(t), t
    for t in CASUAL:
        assert not rc.PROGRESS_ASK_RE.search(t), t


def test_repeat_ask_matches_his_real_wording():
    for t in ASKS:
        assert rr.repeat_ask(t), t


def test_subagent_and_hub_handoffs_are_owner_relays():
    m = next(rc.OWNER_HANDOFF_RE.finditer(PREV_18), None)
    assert m, PREV_18
    assert rr.relay_owner(PREV_18), PREV_18
    for t in ("I sent that to the worker; the coordinator has it.", "Handed it to my subagent.",
              "The hub owns the association repair.", "the worker has it"):
        assert rc.OWNER_HANDOFF_RE.search(t), t
    # a quoted phrase is not a handoff (postmortems quote it)
    assert rc.unverified_owner('The old reply said "queued to my subagent" and stopped.', []) is None
    for t in ("I fixed the hub; it owns no state now.", "Fixed and deployed: 276 issues repaired."):
        assert not rc.OWNER_HANDOFF_RE.search(t), t


def test_relay_repeat_fires_on_turn_18():
    st = {"last_reply": PREV_18}
    hit = rr.assess(st, 18, TURN_18, card_status=lambda c: "running")
    assert hit and "yourself this turn" in hit["hint"]


def test_status_plain_hint_on_real_asks_only():
    for t in ASKS:
        h = rc.status_plain_hint(t)
        assert h and "plain words" in h and "first paragraph" in h, t
    for t in NOTICES + CASUAL:
        assert rc.status_plain_hint(t) is None, t
    # orders and failed-fix reports are progress asks for status_only, but not requests for a status
    for t in ("still not working", "finish it", "deploy this", "can you just merge it"):
        assert rc.PROGRESS_ASK_RE.search(t) and rc.status_plain_hint(t) is None, t
    # a live probe stands in for a person; it is not a Hermes-written row
    assert rc.status_plain_hint("[selfheal-probe] how goes?")
    for platform in ("cron", "kanban", "subagent"):
        assert rc.status_plain_hint(TURN_18, platform=platform) is None
    assert rc.status_plain_hint(TURN_18, parent_session_id="p1") is None


def test_status_plain_off_switch(monkeypatch):
    monkeypatch.setenv("COACH_STATUS_PLAIN", "off")
    assert rc.status_plain_hint(TURN_18) is None
    monkeypatch.setenv("COACH_STATUS_PLAIN", "on")
    assert rc.status_plain_hint(TURN_18)


def test_hooks_end_to_end_once_per_turn_and_logged(tmp_path, monkeypatch):
    mod = _mod("coach_init_sp", "__init__.py")
    core = mod._load("core")
    flm = mod._load("fix_loop")
    monkeypatch.setattr(flm, "aux_judge", lambda s, p: json.dumps({"p_failed_fix": 0.1, "issue": ""}))
    monkeypatch.setattr(mod._load("relay_repeat"), "board_card_status", lambda c: "running")
    store = core.Store(root=tmp_path / "coach")
    coach, hooks = mod.build(store=store, checkout=None)
    hooks["pre_llm_call"](session_id="s1", user_message="fix the issue backfill")
    hooks["post_llm_call"](session_id="s1", assistant_response=PREV_18)
    out = hooks["pre_llm_call"](session_id="s1", user_message=TURN_18)
    assert out and "plain words" in out["context"]
    assert out["context"].count("plain words") == 1
    hooks["post_llm_call"](session_id="s1", assistant_response="276 queued, 0 repaired: the hub import is down.")
    out = hooks["pre_llm_call"](session_id="s1", user_message=NOTICES[0])
    assert not out or "plain words" not in out["context"]
    logged = [json.loads(l) for l in (tmp_path / "coach" / "log.jsonl").read_text().splitlines()]
    hits = [e for e in logged if e.get("event") == "agent_hint" and e.get("rule") == "status_plain"]
    assert len(hits) == 1 and hits[0]["session_id"] == "s1" and TURN_18[:40] in hits[0]["evidence"]
