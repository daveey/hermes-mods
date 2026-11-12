"""Follow-up state rules (v11): a later native own message that WITHDRAWS the only restoration of a temporary
authorization leaves no valid restore term (refuse until a valid replacement or a genuine permanent supersession), and a
relaxation to 'permanent' is read only from a sentence that itself concerns the cap decision, never from an unrelated
sentence that happens to sit next to a relevant one. Fixture homes only; no config command is executed."""
import json

import pytest

from test_root_cap_guard import w, KEY, SET8, SURFACES, TEMP_OWNER, PERM, TEMP, FAR, PAD

OWNER = f"Owner card {TEMP_OWNER}, run 1, this session."
RESTORE = f"Restore root {KEY} to 7 on owner completion."
# Temporary authorization whose ONLY restore trigger is owner completion (no expiry clause).
TEMP_ONE = (f"I authorize a temporary root {KEY} increase from 7 to 8. {OWNER} {RESTORE}")
TEMP_TWO = TEMP  # owner completion and an expiry in the same restore sentence


def rows(w):
    return json.loads((w.shared / "root_cap_exceptions.json").read_text())


WITHDRAWALS = [
    "Do not restore it to 7 on owner completion.",
    "For that change, do not restore it to 7 on owner completion.",
    "Don't restore that on owner completion.",
    "Never restore it to 7.",
    f"Do not restore root {KEY} to 7 on owner completion.",
    "Do not revert that.",
    "Don't roll it back.",
    "Do not put that back.",
]


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("withdrawal", WITHDRAWALS)
def test_withdrawn_restore_refuses(w, surface, withdrawal):
    w.register()
    assert w.blocked(surface, SET8, texts=[TEMP_ONE, withdrawal])
    assert w.blocked(surface, SET8, texts=[TEMP_ONE, withdrawal, "Thanks.", "How is the build?"])


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("withdrawal", WITHDRAWALS[:3])
def test_withdrawn_restore_with_expiry_also_refuses(w, surface, withdrawal):
    # the restore sentence carried both triggers; the withdrawn restore removes the sentence, so nothing remains
    w.register()
    assert w.blocked(surface, SET8, texts=[TEMP_TWO, withdrawal])


@pytest.mark.parametrize("surface", SURFACES)
def test_withdrawn_then_valid_replacement_admits_and_persists_temporary(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[TEMP_ONE, "Do not restore it to 7 on owner completion.",
                                               f"Make that temporary. Restore root {KEY} to 7 at {FAR}."])
    row = rows(w)[-1]
    assert row["kind"] == "temporary"
    assert row["owner_ref"]["card"] == TEMP_OWNER
    assert row["restore_trigger"]["expires_at"] == FAR
    assert row["restore_trigger"]["owner_terminal"] is False


@pytest.mark.parametrize("surface", SURFACES)
def test_replacement_in_same_message_admits(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[
        TEMP_ONE, f"Do not restore it to 7 on owner completion. Restore root {KEY} to 7 at {FAR}."])
    assert rows(w)[-1]["kind"] == "temporary"


@pytest.mark.parametrize("surface", SURFACES)
def test_withdrawn_then_genuine_permanent_supersession_admits(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[TEMP_ONE, "Do not restore it to 7 on owner completion.",
                                               "Make that permanent."])
    assert rows(w)[-1]["kind"] == "permanent"
    assert not w.blocked(surface, SET8, texts=[TEMP_ONE, "Do not restore it. Make that permanent."])
    assert not w.blocked(surface, SET8, texts=[TEMP_ONE, "Do not restore it to 7 on owner completion.", PERM])


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("wrapper", [
    'The assistant wrote: "{}"', "'{}'", "\u2018{}\u2019", "Did I say {}", "The notice said {}", "> {}"])
def test_non_own_withdrawal_changes_nothing(w, surface, wrapper):
    w.register()
    assert not w.blocked(surface, SET8, texts=[TEMP_ONE, wrapper.format("Do not restore it to 7 on owner completion.")])
    assert rows(w)[-1]["kind"] == "temporary"


@pytest.mark.parametrize("surface", SURFACES)
def test_unrelated_withdrawal_and_negated_other_things_change_nothing(w, surface):
    w.register()
    for text in ("Do not restore the database backup.", "Don't forget to restore it afterwards.",
                 "Never roll back the migration.", "Do not revert my notes."):
        assert not w.blocked(surface, SET8, texts=[TEMP_ONE, *PAD, text]), text
        assert rows(w)[-1]["kind"] == "temporary"


UNRELATED_PERMANENT = [
    "Keep that unchanged. The database migration is permanent.",
    "Keep that unchanged. The new schema is permanent.",
    "Make that temporary. The database migration is permanent.",
    "Keep that unchanged. My vacation is permanently booked.",
    "Keep that unchanged. It is permanent.",
]


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("followup", UNRELATED_PERMANENT[:2] + UNRELATED_PERMANENT[3:])
def test_unrelated_permanent_sentence_does_not_relax_a_temporary(w, surface, followup):
    w.register()
    assert not w.blocked(surface, SET8, texts=[TEMP, *PAD, followup])
    row = rows(w)[-1]
    assert row["kind"] == "temporary" and row["owner_ref"]["card"] == TEMP_OWNER


@pytest.mark.parametrize("surface", SURFACES)
def test_unrelated_permanent_sentence_does_not_erase_a_pending_restriction(w, surface):
    w.register()
    base = f"I authorize changing root {KEY} from 7 to 8."
    assert w.blocked(surface, SET8, texts=[base, "Make that temporary. The database migration is permanent."])
    assert w.blocked(surface, SET8, texts=[base, "Make that temporary.", "Keep that unchanged. The database migration is permanent."])


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("relax", [
    "Make that permanent.", "Keep that permanent.",
    f"Root {KEY} is permanent.", "Treat that change as permanent.", "Make this raise permanent."])
def test_genuine_relaxation_still_works(w, surface, relax):
    w.register()
    assert not w.blocked(surface, SET8, texts=[TEMP, relax])
    assert rows(w)[-1]["kind"] == "permanent"


@pytest.mark.parametrize("surface", SURFACES)
def test_controls(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[PERM])
    assert not w.blocked(surface, SET8, texts=[TEMP])
    assert not w.blocked(surface, SET8, texts=[TEMP_ONE])
    assert w.blocked(surface, SET8, texts=["The queue is full, headroom is available."])
    assert not w.blocked(surface, "hermes config set display.skin default",
                         texts=[TEMP_ONE, "Do not restore it to 7 on owner completion."])
