"""Expiry/trigger provenance mutations (v13): the restore trigger of a temporary authorization is ONE field (owner
completion and/or an expiry). A later own message that withdraws, cancels, re-dates or otherwise mutates the expiry
retires the old trigger; the write refuses until a valid own restore sentence supplies a new one, or a genuine
permanent supersession. Same-message ordering uses the same boundary. Fixture homes only; no config command runs."""
import json

import pytest

from test_root_cap_guard import w, KEY, SET8, SURFACES, TEMP_OWNER, PERM, TEMP, FAR

OWNER = f"Owner card {TEMP_OWNER}, run 1, this session."
TEMP_EXPIRY = f"I authorize a temporary root {KEY} increase from 7 to 8. {OWNER} Restore root {KEY} to 7 at {FAR}."
TEMP_ONE = f"I authorize a temporary root {KEY} increase from 7 to 8. {OWNER} Restore root {KEY} to 7 on owner completion."
NEAR = "2098-06-01T00:00:00Z"


def rows(w):
    return json.loads((w.shared / "root_cap_exceptions.json").read_text())


MUTATIONS = [
    "The expiry is cancelled.", "The expiry no longer applies.", "Do not use the expiry anymore.",
    "The expiry is now 2000-01-01T00:00:00Z.", "Drop the expiry.", "Remove the expiry.", "There is no expiry.",
    "Ignore the expiry.", "The expiration is withdrawn.", "Forget the expiry.", "It expires tomorrow.",
    f"Make the expiry {NEAR}.", f"That change now lasts until {NEAR}.", f"Keep it until {NEAR}.",
    "That change is valid until Friday.", f"Set the expiry to {NEAR}.",
]


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("later", MUTATIONS)
def test_mutating_the_only_expiry_trigger_refuses(w, surface, later):
    w.register()
    assert w.blocked(surface, SET8, texts=[TEMP_EXPIRY, later]), later
    assert w.blocked(surface, SET8, texts=[TEMP_EXPIRY, later, "Thanks.", "How is the build?"]), later


@pytest.mark.parametrize("surface", SURFACES)
def test_mutating_the_expiry_in_the_same_message_refuses(w, surface):
    w.register()
    for tail in ("The expiry is cancelled.", "Do not use the expiry anymore.",
                 "The expiry is now 2000-01-01T00:00:00Z."):
        assert w.blocked(surface, SET8, texts=[f"{TEMP_EXPIRY} {tail}"]), tail


@pytest.mark.parametrize("surface", SURFACES)
def test_expiry_withdrawal_removes_the_whole_trigger_sentence(w, surface):
    # the restore sentence carried owner completion AND an expiry; conservative invalidation removes both parts
    w.register()
    assert w.blocked(surface, SET8, texts=[TEMP, "The expiry is cancelled."])


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("later", ["The expiry is cancelled.", "Do not use the expiry anymore.",
                                   f"The expiry is now {NEAR}."])
def test_valid_replacement_after_a_mutation_supersedes_and_persists(w, surface, later):
    w.register()
    assert not w.blocked(surface, SET8, texts=[TEMP_EXPIRY, later, f"Restore root {KEY} to 7 at {NEAR}."])
    row = rows(w)[-1]
    assert row["kind"] == "temporary" and row["owner_ref"]["card"] == TEMP_OWNER
    assert row["restore_trigger"]["expires_at"] == NEAR
    assert FAR not in json.dumps(row["restore_trigger"])


@pytest.mark.parametrize("surface", SURFACES)
def test_valid_replacement_with_owner_completion_persists(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[TEMP_EXPIRY, "The expiry is cancelled.",
                                               f"Restore root {KEY} to 7 on owner completion."])
    row = rows(w)[-1]
    assert row["restore_trigger"] == {"owner_terminal": True, "expires_at": None}


@pytest.mark.parametrize("surface", SURFACES)
def test_replacement_in_the_same_message_as_the_mutation(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[TEMP_EXPIRY, f"The expiry is cancelled. Restore root {KEY} to 7 at {NEAR}."])
    assert rows(w)[-1]["restore_trigger"]["expires_at"] == NEAR
    # and in the other order: the later withdrawal wins
    assert w.blocked(surface, SET8, texts=[TEMP_EXPIRY, f"Restore root {KEY} to 7 at {NEAR}. The expiry is cancelled."])


@pytest.mark.parametrize("surface", SURFACES)
def test_owner_completion_only_trigger_unaffected_by_unrelated_expiry_talk(w, surface):
    w.register()
    for text in ("My passport expires next year.", "I'm away until Monday.", "The report deadline is Friday.",
                 "Standup ends at 2099-03-01T10:00:00Z."):
        assert not w.blocked(surface, SET8, texts=[TEMP_ONE, text]), text
        assert rows(w)[-1]["restore_trigger"]["owner_terminal"] is True


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("wrapper", ['The assistant wrote: "{}"', "'{}'", "\u2018{}\u2019", "Did I say {}", "Is it true that {}",
                                     "The notice said {}", "> {}", "Should I say {}?"])
def test_quoted_reported_question_expiry_text_changes_nothing(w, surface, wrapper):
    w.register()
    assert not w.blocked(surface, SET8, texts=[TEMP_EXPIRY, wrapper.format("The expiry is cancelled.")])
    row = rows(w)[-1]
    assert row["kind"] == "temporary" and row["restore_trigger"]["expires_at"] == FAR


@pytest.mark.parametrize("surface", SURFACES)
def test_genuine_permanent_supersession_after_expiry_withdrawal_admits(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[TEMP_EXPIRY, "The expiry is cancelled.", "Make that permanent."])
    assert rows(w)[-1]["kind"] == "permanent"
    assert not w.blocked(surface, SET8, texts=[TEMP_EXPIRY, "The expiry is cancelled.", PERM])


@pytest.mark.parametrize("surface", SURFACES)
def test_controls(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[TEMP_EXPIRY])
    assert rows(w)[-1]["restore_trigger"]["expires_at"] == FAR
    assert not w.blocked(surface, SET8, texts=[TEMP])
    assert not w.blocked(surface, SET8, texts=[PERM])
    assert not w.blocked(surface, "hermes config set display.skin default", texts=[TEMP_EXPIRY, "The expiry is cancelled."])
    assert w.blocked(surface, SET8, texts=["The queue is full, headroom is available."])
