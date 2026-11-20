"""unblock_card_hold: a blocked card's human_request must carry the generate-unblock-card brief.

Incident: 2026-11-20 session summary showed card t_01a1f8b56d1a69fc ("Reply A or B in this chat. A = authorize one
bounded repair run against the current Mac Desktop (PID 5198 ...)") with no situation, consequences or
recommendation. The skills try-to-unblock / generate-unblock-card hold the reasoning; this rule checks the output.
"""
import json

import pytest
from conftest import llm_returning

INCIDENT = dict(
    action=("Reply A or B in this chat. A = authorize one bounded repair run against the current Mac Desktop "
            "(PID 5198; you start it with a debug inspector, or tell me an inspector already exists). "
            "B = close acceptance with the documented gaps."),
    why_human=("The proven app process is gone and no inspector is available. Getting one means restarting "
               "Desktop or changing its launch settings, which is outside my approved scope."),
    interaction="approval",
    scope="PR1697 Mac delivery acceptance",
    evidence="ps: no process 5198; inspector port 9229 closed",
)

GOOD = dict(
    action="Choose how to finish verifying the fix. Unblock = A (one supervised test run); archive = B.",
    why_human=("Tried: the original test app exited, so I can't attach to it; a Desktop restart with debugging is "
               "outside this card's approval. Only you can: allow a Desktop restart, or accept less proof."),
    interaction="approval",
    scope=("Situation: The fix for the Mac app's idle screen is merged and installed. Three of four checks passed; "
           "the last needs a debug connection to the running app, which has closed.\n"
           "Options:\n"
           "A) One supervised test run — restart Desktop with debugging (~1 min, chats reload). Reversible.\n"
           "B) Accept with the known gap — lowers the acceptance criteria to 3 of 4 checks. Nothing restarts.\n"
           "Recommend: A — one minute of restart buys the only check covering the bug you hit."),
    choices=[{"label": "A: one supervised test run (recommended)", "value": "A"},
             {"label": "B: accept with the known gap", "value": "B"}],
    evidence="ps: no process 5198; inspector port 9229 closed",
)

PHYSICAL = dict(
    action="Plug the ring charger into the MacBook. Unblock = I resume the pairing test. Archive = drop it.",
    why_human="Tried: BLE scan from the Mac and the hub; the ring is asleep. Only you can: physically plug it in.",
    interaction="physical",
    scope=("Situation: The pairing test needs the ring awake and it has been asleep since last night.\n"
           "Recommend: plug it in when you are next at the desk; the test takes two minutes after that."),
    evidence="ble-scan: 0 devices matching ring",
)


@pytest.fixture(autouse=True)
def _brief_on(monkeypatch):
    monkeypatch.setenv("ASK_GATE_UNBLOCK_BRIEF", "1")


def _events(tmp_path, kind):
    p = tmp_path / "logs" / "ask-gate.jsonl"
    return [r for r in map(json.loads, p.read_text().splitlines()) if r.get("kind") == kind] if p.exists() else []


def _block(gate, hr, session="s1", task=None):
    gate.LOG.add(session, "terminal", "evidence check", hr["evidence"])
    gate.LLM = llm_returning([("ok", "a real human decision")] * 4)
    args = {"kind": "needs_input", "reason": "see human_request", "human_request": hr}
    if task:
        args["task_id"] = task
    return gate.pre_tool_call(tool_name="kanban_block", session_id=session, args=args)


def test_incident_is_missing_every_part(gate):
    gaps = gate.unblock_card_quality.missing(INCIDENT)
    assert any("Tried" in g for g in gaps)
    assert any("Situation" in g for g in gaps)
    assert any("Recommend" in g for g in gaps)
    assert any("Options" in g for g in gaps)


def test_good_brief_passes(gate):
    assert gate.unblock_card_quality.missing(GOOD) == []


def test_physical_step_needs_no_options(gate):
    assert gate.unblock_card_quality.missing(PHYSICAL) == []


def test_markdown_labels_are_recognised(gate):
    hr = dict(GOOD, scope=GOOD["scope"].replace("Situation:", "**Situation:**").replace("Recommend:", "- **Recommend**:"))
    assert gate.unblock_card_quality.missing(hr) == []


def test_options_from_choices_when_no_option_lines(gate):
    scope = GOOD["scope"].split("Options:")[0] + "Options: see the buttons.\nRecommend: A — cheapest full proof of the fix."
    assert gate.unblock_card_quality.missing(dict(GOOD, scope=scope)) == []
    assert any("Options" in g for g in gate.unblock_card_quality.missing(dict(GOOD, scope=scope, choices=None)))


@pytest.mark.parametrize("labels,count", [(("A", "B"), 0), (("A (recommended)", "B (recommended)"), 2)])
def test_exactly_one_recommended_choice(gate, labels, count):
    hr = dict(GOOD, choices=[{"label": l, "value": str(i)} for i, l in enumerate(labels)])
    assert any(f"{count} choices" in g for g in gate.unblock_card_quality.missing(hr))


def test_empty_recommend_is_missing(gate):
    hr = dict(GOOD, scope=GOOD["scope"].rsplit("Recommend:", 1)[0] + "Recommend: A")
    assert any("Recommend" in g for g in gate.unblock_card_quality.missing(hr))


def test_incident_block_is_held_before_the_judge(gate, tmp_path):
    r = _block(gate, INCIDENT, task="t_01a1f8b56d1a69fc")
    assert r and r["action"] == "block"
    assert "unblock_card_hold" in r["message"]
    assert "try-to-unblock" in r["message"] and "generate-unblock-card" in r["message"]
    assert gate.LLM.seen == []
    assert _events(tmp_path, "unblock_card_hold")


def test_blocked_create_is_held(gate):
    gate.LOG.add("s1", "terminal", "evidence check", INCIDENT["evidence"])
    gate.LLM = llm_returning([("ok", "fine")])
    r = gate.pre_tool_call(tool_name="kanban_create", session_id="s1", args={
        "title": "PR1697 Mac delivery", "initial_status": "blocked", "human_request": INCIDENT})
    assert r and "unblock_card_hold" in r["message"]


def test_good_brief_is_not_held_by_this_rule(gate, tmp_path):
    r = _block(gate, GOOD, task="t_11111111")
    assert r is None or "unblock_card_hold" not in r.get("message", "")
    assert not _events(tmp_path, "unblock_card_hold")


def test_hold_is_bounded_then_released(gate, tmp_path):
    for _ in range(2):
        r = _block(gate, INCIDENT, task="t_0123456789abcdef")
        assert r and "unblock_card_hold" in r["message"]
    r = _block(gate, INCIDENT, task="t_0123456789abcdef")
    assert r is None or "unblock_card_hold" not in r.get("message", "")
    assert _events(tmp_path, "unblock_card_released")


def test_rule_error_fails_open(gate, tmp_path, monkeypatch):
    monkeypatch.setattr(gate.unblock_card_quality, "hit", lambda r: 1 / 0)
    r = _block(gate, INCIDENT, task="t_00000000")
    assert r is None or "unblock_card_hold" not in r.get("message", "")
    assert _events(tmp_path, "unblock_card_error")


# ── review fixes ────────────────────────────────────────────────────────────────────────────────────────────────
def test_single_line_scope_with_sentence_labels(gate):
    scope = ("Situation: The fix is installed and one check remains open here. Options: A) run once more, 1 min. "
             "B) accept the gap. Recommend: A - cheap and covers the real bug.")
    assert gate.unblock_card_quality.missing(dict(GOOD, scope=scope)) == []


def test_headings_and_numbered_options(gate):
    scope = ("## Situation\nThe app closed so the last check cannot attach anymore.\n## Options\n1. restart once\n"
             "2. accept the gap\n## Recommendation\nOption 1 because it is a one minute restart.")
    assert gate.unblock_card_quality.missing(dict(GOOD, scope=scope)) == []
    assert gate.unblock_card_quality.options("Option A: restart\nOption B: accept") == ["Option A: restart", "Option B: accept"]


def test_eg_and_ranges_are_not_options(gate):
    assert gate.unblock_card_quality.options("E.g. a restart for 2 - 3 min") == []


def test_mid_sentence_word_is_not_a_label(gate):
    parts = gate.unblock_card_quality.sections("Situation: we weighed the options: none are cheap here at all.")
    assert "options" not in parts


def test_open_reply_question_needs_no_options(gate):
    hr = dict(PHYSICAL, interaction="reply", action="What is the guest Wi-Fi SSID? Unblock = I configure it; archive = skip.")
    assert gate.unblock_card_quality.missing(hr) == []
    pick = dict(hr, action="Reply A or B: which network should the hub join?")
    assert any("Options" in g for g in gate.unblock_card_quality.missing(pick))


def test_pr_approval_scope_keeps_its_grammar(gate):
    hr = dict(INCIDENT, scope="acme/widgets#12 head abcdef1", why_human="Tried: the merge queue; review is required.")
    assert gate.unblock_card_quality.contract_scope(hr["scope"])
    assert gate.unblock_card_quality.missing(hr) == []
    assert any("Tried" in g for g in gate.unblock_card_quality.missing(dict(hr, why_human="Needs review.")))


def test_physical_contract_scope_keeps_its_labels(gate):
    hr = dict(PHYSICAL, scope="DEVICE: ring\nHUMAN_STEP: plug the charger in\nOBSERVATION: ble scan sees ring")
    assert gate.unblock_card_quality.missing(hr) == []
    brief = "Situation: The ring has been asleep since last night here.\nRecommend: plug it in at the desk now.\n"
    parts = gate.unblock_card_quality.sections(brief + hr["scope"])
    assert parts["recommend"] == "plug it in at the desk now."


def test_owner_recommendation_hold_runs_first(gate):
    # An in-limits pick with a full brief: owner_recommendation applies it; the brief rule never speaks.
    hr = dict(GOOD, scope=GOOD["scope"].replace("lowers the acceptance criteria to 3 of 4 checks", "marks 3 of 4 done"),
              why_human="Tried: attaching to the closed app. Only you can: decide.")
    r = _block(gate, dict(hr, why_human="no tried line"), task="t_22222222")
    assert r and "owner_recommendation_hold" in r["message"]


def test_reconciled_approval_answers_before_the_brief(gate):
    """An already-approved ask gets 'already approved', not 'rewrite the brief' (rule on)."""
    import test_approval_reconciliation as rec
    dispose = rec.setup(gate, lambda target: rec.snapshot())
    try:
        r = rec.call(gate)
        assert r["action"] == "block" and "already approved" in r["message"]
        assert "unblock_card_hold" not in r["message"]
    finally:
        dispose()


def test_blocked_creates_have_their_own_bound(gate):
    gate.LOG.add("s1", "terminal", "evidence check", INCIDENT["evidence"])
    gate.LLM = llm_returning([("ok", "fine")] * 8)
    def create(title):
        return gate.pre_tool_call(tool_name="kanban_create", session_id="s1",
                                  args={"title": title, "initial_status": "blocked", "human_request": INCIDENT})
    for _ in range(2):
        assert "unblock_card_hold" in create("first card")["message"]
    r = create("second card")
    assert r and "unblock_card_hold" in r["message"], "a second card does not inherit the first card's spent budget"


def test_test_switch_needs_pytest(gate, monkeypatch):
    monkeypatch.setenv("ASK_GATE_UNBLOCK_BRIEF", "0")
    assert gate.unblock_card_quality.hit(INCIDENT) is None
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    assert gate.unblock_card_quality.hit(INCIDENT) is not None


# ── second review ───────────────────────────────────────────────────────────────────────────────────────────────
def test_contract_scope_hold_says_keep_the_scope(gate):
    hr = dict(INCIDENT, scope="acme/widgets#12 head abcdef1", choices=None)
    r = _block(gate, hr, task="t_33333333")
    assert r and r["action"] == "block"
    assert "unblock_card_hold" in r["message"]
    assert "keep scope exactly as it is" in r["message"]
    assert "'Situation:'" not in r["message"]
    msg = gate.unblock_card_quality.hit(hr)["message"]
    assert "keep scope exactly as it is" in msg and "why_human" in msg


def test_exemption_needs_a_pr_number_or_device_lines(gate):
    q = gate.unblock_card_quality
    assert not q.contract_scope("hermes/desktop")
    assert not q.contract_scope("mac-app/idle-fix")
    assert not q.contract_scope(GOOD["scope"] + "\nEVIDENCE: ps output")
    assert q.contract_scope("HUMAN_STEP: plug it in\nOBSERVATION: scan")
    assert any("Situation" in g for g in q.missing(dict(INCIDENT, scope="hermes/desktop", why_human="Tried: x.")))


@pytest.mark.parametrize("text,items", [
    ("A) Use plan B: restart once\nB) accept", ["A) Use plan B: restart once", "B) accept"]),
    ("A) I prefer option A. It is cheap.\nB) Python 3. Then rerun.", ["A) I prefer option A. It is cheap.", "B) Python 3. Then rerun."]),
    ("I prefer option A. It is cheap.", []),
    ("A) run once more, 1 min. B) accept the gap.", ["A) run once more, 1 min.", "B) accept the gap."]),
])
def test_options_are_not_over_split(gate, text, items):
    assert gate.unblock_card_quality.options(text) == items


def test_double_space_and_crlf(gate):
    q = gate.unblock_card_quality
    parts = q.sections("Situation: The fix is installed and one check is open.  Options: A) x. B) y.  Recommend: A - cheap.")
    assert parts["options"] == "A) x. B) y."
    assert q.sections("## Situation\r\nThe app closed so the last check cannot attach.\r\n")["situation"].startswith("The app")
    assert any("Situation" in g for g in q.missing(dict(GOOD, scope="Situation: **short** __x__\nRecommend: A - fine and cheap enough.")))


def test_retitled_creates_are_capped_per_session(gate):
    gate.LOG.add("s9", "terminal", "evidence check", INCIDENT["evidence"])
    gate.LLM = llm_returning([("ok", "fine")] * 12)
    held = 0
    for i in range(8):
        r = gate.pre_tool_call(tool_name="kanban_create", session_id="s9",
                               args={"title": f"retitle {i}", "initial_status": "blocked", "human_request": INCIDENT})
        held += bool(r and "unblock_card_hold" in r.get("message", ""))
    assert held == gate.unblock_card_quality.MAX_CREATE_HOLDS


# ── third review ────────────────────────────────────────────────────────────────────────────────────────────────
def test_label_inside_option_text_is_prose(gate):
    q = gate.unblock_card_quality
    scope = ("Situation: The patch is ready and the vendor signed off on it.\nOptions:\n"
             "A) Apply the patch. Recommended: by vendor.\nB) Wait a week.\nRecommend: A because it fixes it now.")
    parts = q.sections(scope)
    assert q.options(parts["options"]) == ["A) Apply the patch. Recommended: by vendor.", "B) Wait a week."]
    assert parts["recommend"] == "A because it fixes it now."


def test_next_labels_bullet_does_not_leak(gate):
    parts = gate.unblock_card_quality.sections("- Situation: The app closed and the check is open.\n- Options:\n- A) one\n- B) two\n- Recommend: A - it is cheap enough.")
    assert parts["situation"] == "The app closed and the check is open."
    assert gate.unblock_card_quality.options(parts["options"]) == ["A) one", "B) two"]


def test_numbered_substeps_stay_in_their_option(gate):
    assert gate.unblock_card_quality.options("A) restart\n1. stop\n2. start\nB) accept") == ["A) restart 1. stop 2. start", "B) accept"]


@pytest.mark.parametrize("scope", [
    "Situation: The fix is installed and one check is open.\nOptions: A) run once more. B) accept the gap. Recommend: A because it is cheap.",
    "Situation: The fix is installed and one check is open. Options: A) run once more. B) accept the gap.\nRecommend: A because it is cheap.",
])
def test_mixed_layouts_parse(gate, scope):
    q = gate.unblock_card_quality
    parts = q.sections(scope)
    assert q.options(parts["options"]) == ["A) run once more.", "B) accept the gap."]
    assert parts["recommend"] == "A because it is cheap."
