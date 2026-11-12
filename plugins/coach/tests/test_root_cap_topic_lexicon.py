"""Topic-lexicon retirement of the restore trigger (v14). v10-v13 each recognised a withdrawal of the restore trigger by
its wording and each lost to the next phrasing. The restore trigger (owner completion and/or an expiry) is now ONE
field recognised by its TOPIC: an own sentence that mentions the trigger's subject matter retires the stale trigger
unless it is clearly about another object, and the write refuses until a valid own `Restore ... to N <trigger>` sentence
(or a genuine permanent supersession) exists. Fixture homes only; no config command runs."""
import json
import itertools

import pytest

from test_root_cap_guard import w, KEY, SET8, SURFACES, TEMP_OWNER, PERM, TEMP, FAR, PAD

OWNER = f"Owner card {TEMP_OWNER}, run 1, this session."
TEMP_EXPIRY = f"I authorize a temporary root {KEY} increase from 7 to 8. {OWNER} Restore root {KEY} to 7 at {FAR}."
TEMP_ONE = f"I authorize a temporary root {KEY} increase from 7 to 8. {OWNER} Restore root {KEY} to 7 on owner completion."
NEAR = "2098-06-01T00:00:00Z"


def rows(w):
    return json.loads((w.shared / "root_cap_exceptions.json").read_text())


# every topic noun x every withdrawal template: the product, not a hand-picked list
NOUNS = ["expiry", "expiration", "deadline", "time limit", "time-limit", "timebox", "time-box", "timeout", "cutoff",
         "cut-off", "end date", "due date", "timer", "date", "countdown", "lifetime", "duration", "TTL"]
TEMPLATES = ["The {n} is cancelled.", "Cancel the {n}.", "No {n}.", "No more {n}.", "{N} cancelled.", "{N}: none.",
             "Scrap {n}.", "Scrap the {n}.", "Drop the {n}.", "Forget the {n}.", "Forget the 2099 {n}.", "Disregard the {n}.",
             "Ignore the {n}.", "Remove the {n}.", "Delete the {n}.", "Never mind the {n}.", "Without any {n}.",
             "The {n} is gone.", "The {n} is withdrawn.", "The {n} no longer applies.", "Get rid of the {n}.",
             "Lose the {n}.", "Kill the {n}.", "Nuke {n}.", "Skip the {n}.", "I don't want a {n}.",
             "I'm cancelling the {n}.", "Let's drop the {n}.", "Please remove the {n}.", "The {n} is void.",
             "There is no {n}.", "No {n} needed.", "The {n} is moved to Friday.", "Move the {n} to Friday.",
             "Extend the {n}.", "The {n} is now 2000-01-01T00:00:00Z.", "Change the {n}.", "The {n} is off.", "Turn off the {n}."]
CASES = sorted({t.format(n=n, N=n.capitalize()) for n in NOUNS for t in TEMPLATES})

OWNER_NOUNS = ["owner completion", "the owner finishing", "owner done", "owner terminal state"]
OWNER_TEMPLATES = ["Forget {n}.", "Ignore {n}.", "{n} no longer triggers it.", "{n} is not a trigger.", "Don't tie it to {n}.",
                   "Drop {n}.", "Scrap {n}.", "Never mind {n}.", "{n} is irrelevant.", "The completion trigger is cancelled.",
                   "Cancel the completion trigger.", "No more owner completion trigger."]
OWNER_CASES = sorted({t.format(n=n).replace("the the", "the") for n in OWNER_NOUNS for t in OWNER_TEMPLATES})


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("later", CASES)
def test_expiry_topic_mutation_refuses(w, surface, later):
    w.register()
    assert w.blocked(surface, SET8, texts=[TEMP_EXPIRY, later]), later


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("later", OWNER_CASES)
def test_owner_completion_topic_mutation_refuses(w, surface, later):
    w.register()
    assert w.blocked(surface, SET8, texts=[TEMP_ONE, later]), later


@pytest.mark.parametrize("surface", SURFACES)
def test_same_message_topic_mutation_refuses(w, surface):
    w.register()
    for tail in ("Expiry cancelled.", "Forget the 2099 deadline.", "No more expiry.", "Disregard the date."):
        assert w.blocked(surface, SET8, texts=[f"{TEMP_EXPIRY} {tail}"]), tail


UNRELATED = ["My passport expires next year.", "I'm away until Monday.", "The report deadline is Friday.",
             "Standup ends at 2099-03-01T10:00:00Z.", "The meeting date is Friday.", "Thanks.", "How is the build?",
             "What is the date today?", "The window is open.", "Our licence expires in March.",
             "Today's date is Thursday.", "The sprint timeline looks fine.", "The invoice is due Friday."]


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("text", UNRELATED)
def test_unrelated_topics_leave_a_valid_trigger_alone(w, surface, text):
    w.register()
    assert not w.blocked(surface, SET8, texts=[TEMP_ONE, *PAD, text]), text
    assert rows(w)[-1]["restore_trigger"]["owner_terminal"] is True
    assert not w.blocked(surface, SET8, texts=[TEMP_EXPIRY, *PAD, text]), text
    assert rows(w)[-1]["restore_trigger"]["expires_at"] == FAR


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("later", ["Expiry cancelled.", "Forget the 2099 deadline.", "The cutoff is cancelled.", "Nuke expiry."])
def test_valid_replacement_after_a_topic_mutation_persists(w, surface, later):
    w.register()
    assert not w.blocked(surface, SET8, texts=[TEMP_EXPIRY, later, f"Restore root {KEY} to 7 at {NEAR}."])
    row = rows(w)[-1]
    assert row["kind"] == "temporary" and row["owner_ref"]["card"] == TEMP_OWNER
    assert row["restore_trigger"]["expires_at"] == NEAR and FAR not in json.dumps(row["restore_trigger"])
    assert not w.blocked(surface, SET8, texts=[TEMP_EXPIRY, later, f"Restore root {KEY} to 7 on owner completion."])
    assert rows(w)[-1]["restore_trigger"] == {"owner_terminal": True, "expires_at": None}


@pytest.mark.parametrize("surface", SURFACES)
def test_genuine_permanent_supersession_after_a_topic_mutation_admits(w, surface):
    w.register()
    for later in ("Expiry cancelled.", "Forget owner completion."):
        assert not w.blocked(surface, SET8, texts=[TEMP_EXPIRY, later, "Make that permanent."])
        assert rows(w)[-1]["kind"] == "permanent"
        assert not w.blocked(surface, SET8, texts=[TEMP_EXPIRY, later, PERM])


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("wrapper", ['The assistant wrote: "{}"', "'{}'", "\u2018{}\u2019", "Did I say {}", "Is it true that {}",
                                     "The notice said {}", "> {}", "Should I say {}?"])
@pytest.mark.parametrize("text", ["Expiry cancelled.", "Forget owner completion.", "Cancel the time limit."])
def test_quoted_reported_question_topic_text_changes_nothing(w, surface, wrapper, text):
    w.register()
    assert not w.blocked(surface, SET8, texts=[TEMP_EXPIRY, wrapper.format(text)])
    assert rows(w)[-1]["restore_trigger"]["expires_at"] == FAR


@pytest.mark.parametrize("surface", SURFACES)
def test_controls(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[TEMP_EXPIRY])
    assert not w.blocked(surface, SET8, texts=[TEMP_ONE])
    assert not w.blocked(surface, SET8, texts=[TEMP])
    assert not w.blocked(surface, SET8, texts=[PERM])
    assert not w.blocked(surface, "hermes config set display.skin default", texts=[TEMP_EXPIRY, "Expiry cancelled."])
    assert w.blocked(surface, SET8, texts=["The queue is full, headroom is available."])
