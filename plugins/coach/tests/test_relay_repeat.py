"""relay_repeat (self-heal agent-1406d53913): the user repeats an ask while the agent keeps relaying an unfinished
owner's progress. Replies and user turns are the real ones from the 2026-11-02 chat (repo/product names scrubbed):
the chat bumped a repair card, then only relayed it for 4 h while David asked again and again."""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


try:
    rr = _mod("t_relay_repeat", "relay_repeat.py")
except FileNotFoundError:  # before the fix: every test below fails on its own, none is skipped
    rr = None
rc = _mod("t_relay_repeat_rc", "reply_check.py")

FIRST_ASK = ("@session:builder/20261101_014435_714d67 -- the problem is that it's not able to access github. let's "
             "figure that out and solve it")
# msg 733598
R_733598 = (
    "Host GitHub access works: I verified access to the private `example/app` repository with read and push "
    "permissions. No new credentials are needed.\n\nThe failure is in the builder's instructions: they point to a host "
    "path instead of `/workspace` and don't explain the host-managed GitHub operations. Earlier tests checked the "
    "sandbox mount, but missed those misleading instructions.\n\nI prioritized the existing repair and started an "
    "independent code trace while its worker waits for capacity. I'm preserving sandbox isolation—not mounting your "
    "credentials or switching it to unrestricted host access.")
# msg 737699
R_737699 = (
    "Yes—the original builder is still blocked. **The repair hasn't been deployed or the failed run retried**, so the "
    "red status is accurate.\n\nThere is concrete progress: the worker committed the fix and reports 17 passing unit "
    "tests, including 9 that fail on the old code. It's now testing the real sandbox workflow before deployment and "
    "recovery.\n\nThe old \"change backend or supply credentials\" request is incorrect; don't act on it. I've directed "
    "recovery to clear that request through the supported retry path, not merely change the indicator.")
# msg 745893
R_745893 = (
    "Resumed and checked the actual test logs:\n\n- The sandbox workflow test passes.\n- All 25 recovery tests pass.\n"
    "- The full regression run still shows errors; I sent that evidence to the active repair worker to resolve before "
    "deployment.\n\nThe original session remains blocked because the fix isn't deployed yet. Recovery is still in "
    "progress; no credential or approval action is needed from you.")
# msg 750819 (tail)
R_750819 = (
    "**This is our runtime integration failing, not your GitHub credentials.** The repair has also grown beyond "
    "authentication: it must fix the builder instructions, its review/publication handoff, and safely retry the "
    "failed task. Those changes are still being tested, not deployed.\n\nI've now sent the verified distinction to "
    "both the repair worker and the original session, and made original-session recovery explicit in the handoff. "
    "**You do not need to supply credentials, change backends, or keep telling that sandboxed agent to fix the "
    "host.** The remaining work is ours.")
# msg 751318
R_751318 = (
    "You're right: the other session is still red because recovery is unfinished.\n\nI've now explicitly linked "
    "**both existing work cards to this chat** and verified the links:\n- **Repair:** `t_01a19f6eb64ca98b` — running.\n"
    "- **Deployment/retry:** `t_b49c4888` — waiting on that repair.\n\nI also checked the worker's actual execution, "
    "not just its heartbeat. It is debugging a failing regression test in the candidate-core test harness. That is "
    "currently holding up deployment.")
# msg 755338: the chat verified the recovery itself
R_755338 = (
    "**The blocked builder has now recovered.** I independently verified:\n\n- The runtime is active.\n- Recovery "
    "successfully launched run 7.\n- The task advanced from **blocked → review**.\n- The incorrect credential/backend "
    "request is cleared.\n\nReview and publication verification remain. This confirms recovery of the builder task—not "
    "that the separate planning chat now has direct repository access.")
DONE_NOTICE = ("✔ [default] @default Kanban t_01a19f6eb64ca98b done — run6: correct worker capsule contract and "
               "reviewed same-card recovery\nThe run6 repair is reviewed and published.")


def test_relay_owner_reads_the_real_relays():
    for r in (R_733598, R_737699, R_745893, R_750819, R_751318):
        assert rr.relay_owner(r), r[:80]
    assert rr.relay_owner(R_751318)["cards"] == ["t_01a19f6eb64ca98b", "t_b49c4888"]
    assert rr.relay_owner(R_755338) is None  # the agent fixed and verified it itself
    assert rr.relay_owner("Fixed: the sidebar scrolls now; I reloaded it and checked.") is None


def test_repeat_ask():
    for t in ("session still red", "why cant you fix it, what's going on", "and the other session is still red",
              "look again", "i dont see any scheduled work here"):
        assert rr.repeat_ask(t), t
    for t in ("resume", "thanks", DONE_NOTICE, "great, now add a dark mode toggle",
              # Hermes-written rows the backtest on real chats caught: a blocked/failed card notice, the compaction
              # todo list and an interrupted-turn note all say "still"/"not"/"failed" without a person asking
              "\u23f8 [default] @default Kanban t_01a18e0135bfb3da blocked: still waiting on review",
              "\u2716 [tools] @default Kanban t_01a197166756bd53 \"M5\" failed: error in deploy",
              "[Your active task list was preserved across context compression] - [>] 3. Verify (in_progress)",
              "[System note: Your previous turn was interrupted mid-run; it is still not done]",
              "[coach] Your message ends on a problem you could fix"):
        assert not rr.repeat_ask(t), t


def _seq(state, steps, status=None):
    """steps: (turn, user_text, reply) -> list of hits (None or dict) in order."""
    out = []
    for turn, user, reply in steps:
        out.append(rr.assess(state, turn, user, card_status=status or (lambda c: "running")))
        if reply is not None:
            state["last_reply"] = reply
    return out


def test_real_session_fires_on_repeats_only():
    st = {}
    hits = _seq(st, [(1, FIRST_ASK, R_733598), (2, "session still red", R_737699), (3, "resume", R_745893),
                     (4, "why cant you fix it, what's going on", R_750819),
                     (5, "and the other session is still red", R_751318)])
    assert hits[0] is None  # the first ask
    assert hits[1] and hits[1]["n"] == 1  # "session still red" after 733598
    assert hits[2] is None  # "resume" is not a repeat ask
    assert hits[3] and hits[3]["n"] == 2  # "why cant you fix it" after 745893
    assert hits[4] and hits[4]["n"] == 3
    h = hits[3]["hint"]
    assert "asked again (2 times)" in h and "yourself this turn" in h and "delegate_task" in h
    assert "kanban_origin_adopt" in h


def test_no_fire_after_agent_fixed_it_itself():
    st = {"last_reply": R_755338}
    assert rr.assess(st, 9, "look again", card_status=lambda c: "running") is None


def test_no_fire_after_owner_completed():
    # the named card is done on the board
    st = {"last_reply": R_751318}
    assert rr.assess(st, 7, "session still red",
                     card_status=lambda c: "done" if c == "t_01a19f6eb64ca98b" else "todo") is None
    # or its completion notice arrived since the relay (the board copy may lag)
    st = {"last_reply": R_751318}
    assert rr.assess(st, 7, DONE_NOTICE, card_status=lambda c: "running") is None
    assert rr.assess(st, 8, "session still red", card_status=lambda c: "running") is None


def test_once_per_turn_and_off_switch(monkeypatch):
    st = {"last_reply": R_737699}
    assert rr.assess(st, 3, "session still red", card_status=lambda c: None)
    assert rr.assess(st, 3, "session still red", card_status=lambda c: None) is None
    monkeypatch.setenv("COACH_RELAY_REPEAT", "off")
    assert not rr.enabled()
    monkeypatch.setenv("COACH_RELAY_REPEAT", "1")
    assert rr.enabled()


def test_hooks_end_to_end(tmp_path, monkeypatch):
    mod = _mod("coach_init_rr", "__init__.py")
    core = mod._load("core")
    flm = mod._load("fix_loop")
    monkeypatch.setattr(flm, "aux_judge", lambda s, p: json.dumps({"p_failed_fix": 0.1, "issue": ""}))
    monkeypatch.setattr(mod._load("relay_repeat"), "board_card_status", lambda c: "running")
    store = core.Store(root=tmp_path / "coach")
    coach, hooks = mod.build(store=store, checkout=None)
    assert hooks["pre_llm_call"](session_id="s1", user_message=FIRST_ASK) is None
    hooks["post_llm_call"](session_id="s1", assistant_response=R_733598)
    out = hooks["pre_llm_call"](session_id="s1", user_message="session still red")
    assert out and "asked again (1 time)" in out["context"]
    assert coach.loop_state("s1")["relay_repeat_turn"] == coach.turn_number("s1")
    hooks["post_llm_call"](session_id="s1", assistant_response=R_755338)
    out = hooks["pre_llm_call"](session_id="s1", user_message="look again")
    assert not out or "asked again" not in out["context"]
    logged = [json.loads(l) for l in (tmp_path / "coach" / "log.jsonl").read_text().splitlines()]
    assert [e["n"] for e in logged if e.get("event") == "agent_hint" and e.get("rule") == "relay_repeat"] == [1]


def test_detection_guard_forces_second_opinion_and_audits():
    """report_only_scored gets relay_repeat: true and the second opinion runs even when the screen scores low."""
    events, confirmed = [], []

    def llm(system, prompt):
        return json.dumps({"report_only": 0.12, "unverified": 0, "evidence": "repair underway"})

    def confirm():
        confirmed.append(1)
        return (0.9, "relayed the owner again after a repeat ask")

    hit = rc.check(R_745893, "why cant you fix it, what's going on", [], llm, threshold=0.8,
                   only=["report_only"], confirm=confirm, confirm_floor=0.5, audit=events.append,
                   thresholds={"report_only": 0.85}, relay_repeat=True)
    assert confirmed == [1]
    scored = [e for e in events if e["event"] == "report_only_scored"]
    assert scored and scored[0]["relay_repeat"] is True and scored[0]["confirm_attempted"] is True
    assert hit and hit["rule"] == "report_only"
    # without the flag, the low screen p skips the second opinion (the old behaviour, unchanged)
    events.clear(); confirmed.clear()
    rc.check(R_745893, "why cant you fix it, what's going on", [], llm, threshold=0.8, only=["report_only"],
             confirm=confirm, confirm_floor=0.5, audit=events.append, thresholds={"report_only": 0.85})
    assert confirmed == []
