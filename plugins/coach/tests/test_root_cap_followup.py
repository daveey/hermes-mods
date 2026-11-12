"""Cross-message temporal rules: a later native message of the human that restricts (temporary / non-permanent /
restoration / time bound / owner scope) or supplies owner+restore terms for the surviving authorization is folded into
it. Missing own valid terms refuse the frozen-cap write; valid ones persist as kind=temporary with the follow-up linked.
Quoted / reported / interrogative follow-ups, unrelated messages and unrelated config change nothing; a later plain
'permanent', a later authorization and a cancellation still govern. Fixture homes only; no config command is executed."""
import json

import pytest

from test_root_cap_guard import w, KEY, SET8, SURFACES, TEMP_OWNER, PERM, TEMP, PAD

BASE = f"I authorize changing root {KEY} from 7 to 8."
OWNER = f"Owner card {TEMP_OWNER}, run 1, this session."
RESTORE = f"Restore root {KEY} to 7 on owner completion."
TERMS = f"{OWNER} {RESTORE}"

RESTRICTIONS = [
    f"The root {KEY} change from 7 to 8 is temporary, not permanent.",
    f"The root {KEY} change is temporary.",
    "Make that temporary, not permanent.",
    "Make that temporary.",
    "Make it temporary.",
    "Don't make that permanent.",
    "Do not make this permanent.",
    "Keep it non-permanent.",
    "That's only for 10 minutes.",
    "The change is for only an hour.",
    "This raise should be temporary.",
    "Treat that as a one-off.",
    "Restore it to 7 afterwards.",
    "Revert that when the rollout is done.",
    f"Root {KEY} must go back to 7 afterwards.",
]


def rows(w):
    return json.loads((w.shared / "root_cap_exceptions.json").read_text())


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("restriction", RESTRICTIONS)
def test_later_restriction_without_terms_refuses(w, surface, restriction):
    w.register()
    assert w.blocked(surface, SET8, texts=[BASE, restriction])


@pytest.mark.parametrize("surface", SURFACES)
def test_restriction_survives_unrelated_messages_between(w, surface):
    w.register()
    assert w.blocked(surface, SET8, texts=[BASE, "Make that temporary.", "Thanks.", "How is the build doing?"])
    assert w.blocked(surface, SET8, texts=[BASE, "Thanks.", "Make that temporary.", "Looks fine."])


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("partial", [OWNER, RESTORE, f"Restore root {KEY} to 9 on owner completion. {OWNER}",
                                     f"Owner card t_doesnotexist1, run 1, this session. {RESTORE}"])
def test_later_restriction_with_partial_or_wrong_terms_refuses(w, surface, partial):
    w.register()
    assert w.blocked(surface, SET8, texts=[BASE, f"The root {KEY} change is temporary. {partial}"])
    assert w.blocked(surface, SET8, texts=[BASE, f"The root {KEY} change is temporary.", partial])


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("wrapper", [
    'The assistant wrote: "{}"', "'{}'", "\u2018{}\u2019", "Did I say {}", "The notice said {}"])
def test_non_own_restriction_changes_nothing(w, surface, wrapper):
    w.register()
    for text in ("Make that temporary.", f"The root {KEY} change is temporary. {TERMS}"):
        assert not w.blocked(surface, SET8, texts=[BASE, wrapper.format(text)])
    assert not w.blocked(surface, SET8, texts=[BASE, 'The assistant wrote: "Make that temporary."', "Thanks."])


@pytest.mark.parametrize("surface", SURFACES)
def test_non_own_terms_do_not_satisfy_a_real_restriction(w, surface):
    w.register()
    quoted = f'The assistant wrote: "{TERMS}"'
    assert w.blocked(surface, SET8, texts=[BASE, "Make that temporary.", quoted])
    assert w.blocked(surface, SET8, texts=[BASE, f"Make that temporary. {quoted}"])
    assert w.blocked(surface, SET8, texts=[BASE, "Make that temporary.", f"Did I say {TERMS}"])


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("shape", ["same", "split"])
def test_later_own_terms_admit_and_persist_temporary(w, surface, shape):
    w.register()
    if shape == "same":
        texts = [BASE, f"The root {KEY} change from 7 to 8 is temporary. {TERMS}"]
    else:
        texts = [BASE, f"The root {KEY} change from 7 to 8 is temporary.", f"Regarding that change: {TERMS}"]
    assert not w.blocked(surface, SET8, texts=texts)
    row = rows(w)[-1]
    assert row["kind"] == "temporary"
    assert row["owner_ref"]["card"] == TEMP_OWNER
    assert row["restore_value"] == 7
    assert row["restore_trigger"]["owner_terminal"] is True
    assert row["decision_followups"], "follow-up linkage must be recorded"
    assert "decision_sha256" in row


@pytest.mark.parametrize("surface", SURFACES)
def test_keyless_followup_terms_admit(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[BASE, "Make that temporary.", f"Restore it to 7 on owner completion. {OWNER}"])
    assert rows(w)[-1]["kind"] == "temporary"


@pytest.mark.parametrize("surface", SURFACES)
def test_later_terms_override_earlier_field_by_field(w, surface):
    w.register()
    first = f"{BASE} Temporary. Owner card t_doesnotexist1, run 1, this session. {RESTORE}"
    assert w.blocked(surface, SET8, texts=[first])
    assert not w.blocked(surface, SET8, texts=[first, f"Make that temporary. {OWNER}"])
    assert rows(w)[-1]["owner_ref"]["card"] == TEMP_OWNER


@pytest.mark.parametrize("surface", SURFACES)
def test_later_permanent_and_new_authorization_govern(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[BASE, "Make that temporary.", PERM])
    assert not w.blocked(surface, SET8, texts=[BASE, "Make that temporary.", "Make that permanent."])
    assert rows(w)[-1]["kind"] == "permanent"
    assert not w.blocked(surface, SET8, texts=[BASE, "Make that temporary.", TEMP])
    assert rows(w)[-1]["kind"] == "temporary"
    assert w.blocked(surface, SET8, texts=[PERM, "Make that temporary.", "Do not make that permanent."])


@pytest.mark.parametrize("surface", SURFACES)
def test_cancellation_and_latest_value_still_govern(w, surface):
    w.register()
    assert w.blocked(surface, SET8, texts=[BASE, "Make that temporary.", "Cancel that."])
    assert w.blocked(surface, SET8, texts=[BASE, f"The root {KEY} change is temporary. {TERMS}", "Cancel that."])
    assert w.blocked(surface, SET8, texts=[BASE, f"I authorize changing root {KEY} from 7 to 9 permanently."])


@pytest.mark.parametrize("surface", SURFACES)
def test_controls_permanent_temp_unrelated(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[PERM])
    assert not w.blocked(surface, SET8, texts=[TEMP])
    assert not w.blocked(surface, SET8, texts=[BASE])
    assert not w.blocked(surface, SET8, texts=[BASE, "Thanks, go ahead.", *PAD, "Restore my database backup until tomorrow."])
    assert not w.blocked(surface, SET8, texts=[BASE, *PAD, "Please keep the notes short.", "I will check it later."])
    assert rows(w)[-1]["kind"] == "permanent"
    assert not w.blocked(surface, "hermes config set display.skin default", texts=[BASE, "Make that temporary."])
    assert not w.blocked(surface, "hermes config set display.skin default", texts=["Thanks."])
    assert w.blocked(surface, SET8, texts=[f"The queue is full, headroom is available. Make that temporary."])
