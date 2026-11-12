"""Adjacency-window rule for the root-cap guard (v15).

v10-v14 each recognised a post-authorization scope change by a vocabulary (restore words, bound words, trigger-topic
words, a relevance wordlist) and each lost to the next phrasing. The default is now inverted for the conversation
about the decision: the authorizing message and the next `_FOLLOWUP_WINDOW` human messages. Inside it an own,
affirmative, non-question, non-quoted, non-reported sentence is a no-op ONLY if it matches a CLOSED neutral grammar
(courtesies / acknowledgements) or is valid terms, affirmative permanence, the authorization itself or a restriction.
Anything else clears the stored terms and marks the decision temporary, so the write refuses until a fresh valid
`Owner card ...` / `Restore ... to N <trigger>` pair (or an explicit permanent supersession) exists.
Fixture homes only; no config command runs; both callback shapes.
"""
import json

import pytest

from test_root_cap_guard import w, KEY, SET8, SURFACES, TEMP_OWNER, PERM, FAR, QUEUE

OWNER = f"Owner card {TEMP_OWNER}, run 1, this session."
RESTORE = f"Restore root {KEY} to 7 on owner completion."
TEMP_FULL = (f"I authorize a temporary root {KEY} increase from 7 to 8. {OWNER} "
             f"Restore root {KEY} to 7 on owner completion or at {FAR}, whichever is first.")
TEMP_ONE = f"I authorize a temporary root {KEY} increase from 7 to 8. {OWNER} {RESTORE}"

# Phrasings of every kind the earlier rounds enumerated plus ones nobody has listed: none names the key, a deictic,
# a restore/owner word or a trigger-topic word, and none is a courtesy.
UNKNOWN = [
    "Ignore my earlier instructions about when to undo this.", "Disregard what I said earlier about undoing the raise.",
    "Scratch the conditions.", "Remove all conditions.", "Lift the restrictions.", "No conditions apply.",
    "No strings attached.", "The conditions are void.", "The conditions are waived.", "Forget the earlier conditions.",
    "Skip the paperwork.", "Do it without conditions.", "Friday only.", "Through the weekend.", "Hold it for the sprint.",
    "Good for this sprint.", "Valid through Friday.", "Revisit after the launch.", "Review it next week and undo if needed.",
    "Until further notice we revisit it.", "Do whatever is simplest.", "Leave it open.", "Pin it there.",
    "Sunset it by the quarter end.", "Bill it to the platform team.",
    "Let the dispatcher decide.", "Zebra marmalade quantum.", "Make it stick.", "Whatever.",
    "Treat the earlier paragraph as a draft.", "Consider the matter closed.", "Fine, but only mornings.",
]
PERM_ONLY = ["Only while Dana is on call."]  # a bound on top of valid TEMP terms leaves them tracked
COURTESY = ["Thanks.", "Thank you!", "ok", "Okay, thanks.", "Yes, go ahead.", "Go ahead.", "Sounds good.", "Great, thanks!",
            "Got it.", "Please proceed.", "Do it.", "LGTM", "Perfect. Thanks."]


def rows(w):
    return json.loads((w.shared / "root_cap_exceptions.json").read_text())


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("later", UNKNOWN)
def test_unknown_sentence_after_temporary_clears_terms(w, surface, later):
    w.register()
    assert w.blocked(surface, SET8, texts=[TEMP_FULL, later]), later


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("later", UNKNOWN)
def test_unknown_sentence_after_permanent_demands_terms(w, surface, later):
    w.register()
    assert w.blocked(surface, SET8, texts=[PERM, later]), later


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("later", PERM_ONLY)
def test_bound_on_permanent_demands_terms_but_keeps_valid_temp_terms(w, surface, later):
    w.register()
    assert w.blocked(surface, SET8, texts=[PERM, later]), later
    assert not w.blocked(surface, SET8, texts=[TEMP_FULL, later]), later


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("later", COURTESY)
def test_courtesy_changes_nothing(w, surface, later):
    w.register()
    assert not w.blocked(surface, SET8, texts=[TEMP_FULL, later]), later
    assert not w.blocked(surface, SET8, texts=[PERM, later]), later


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("later", UNKNOWN[:12])
def test_valid_replacement_after_unknown_sentence_persists(w, surface, later):
    w.register()
    assert not w.blocked(surface, SET8, texts=[TEMP_FULL, later, f"{OWNER} {RESTORE}"]), later
    assert rows(w), "a valid replacement must still be tracked"


@pytest.mark.parametrize("surface", SURFACES)
def test_permanent_supersession_after_unknown_sentence(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[TEMP_FULL, "Skip the paperwork.", PERM])
    assert not w.blocked(surface, SET8, texts=[PERM, "Friday only.", PERM])


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("extra", ["Skip the paperwork.", "Remove all conditions.", "Friday only.", "No strings attached."])
def test_same_message_unknown_sentence(w, surface, extra):
    w.register()
    assert w.blocked(surface, SET8, texts=[f"{TEMP_FULL} {extra}"]), extra
    assert w.blocked(surface, SET8, texts=[f"{PERM} {extra}"]), extra
    assert not w.blocked(surface, SET8, texts=[f"{TEMP_FULL} Thanks."])


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("noop", [
    "Did I say to skip the paperwork?", "Can we drop all conditions?", "Should I scratch the conditions?",
    'The assistant wrote: "Scratch the conditions."', 'Dana said "no strings attached".',
    "Is the sprint over?", "> Remove all conditions.", "According to the notice, the conditions are void.",
])
def test_question_quoted_reported_are_noops(w, surface, noop):
    w.register()
    assert not w.blocked(surface, SET8, texts=[TEMP_FULL, noop]), noop
    assert not w.blocked(surface, SET8, texts=[PERM, noop]), noop


@pytest.mark.parametrize("surface", SURFACES)
def test_window_is_the_next_three_messages(w, surface):
    w.register()
    ok = ["ok", "thanks", "sure"]
    # inside the window the unknown sentence at position 1..3 clears; the older topic gate still applies beyond it
    for k in range(3):
        texts = [PERM] + ok[:k] + ["Friday only."]
        assert w.blocked(surface, SET8, texts=texts), k
    assert w.blocked(surface, SET8, texts=[PERM] + ok + ["Make that temporary."])


@pytest.mark.parametrize("surface", SURFACES)
def test_original_incident_and_unrelated_config(w, surface):
    w.register()
    assert w.blocked(surface, SET8, texts=[QUEUE])
    assert w.blocked(surface, SET8, texts=["The queue is full, headroom is available."])
    assert not w.blocked(surface, "hermes config set display.skin default", texts=[TEMP_FULL, "Skip the paperwork."])
    assert not w.blocked(surface, "hermes config set display.skin default", texts=["Thanks."])


def test_neutral_grammar_is_closed(w):
    root_cap = w.rc
    for s in COURTESY + ["Keep that unchanged."]:
        assert root_cap._neutral_followup(s), s
    for s in ["Thanks, but only for an hour.", "Ok, skip the paperwork.", "Yes, until Friday.", "Good for this sprint.",
              "Do it without conditions.", "Please remove all conditions.", "Sure, whatever it takes."]:
        assert not root_cap._neutral_followup(s), s
