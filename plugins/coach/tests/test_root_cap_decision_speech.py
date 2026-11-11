"""Override admission classifies speech exactly like registration: only the human's own, unquoted, non-interrogative,
non-reported sentence can authorize, supersede or cancel. Fixture homes only; no real config write is ever executed."""
import pytest

from test_root_cap_guard import w, KEY, PERM, SET8, SURFACES, TEMP

PERM9 = f"I authorize changing root {KEY} from 7 to 9 permanently for this rollout."
BENIGN = "hermes config set display.skin default"


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("text", [
    "'" + PERM + "'",
    "\u2018" + PERM + "\u2019",
    '"' + PERM + '"',
    "\u201c" + PERM + "\u201d",
    f"Did I authorize changing root {KEY} from 7 to 8 permanently for this rollout",
    f"Can I authorize changing root {KEY} from 7 to 8 permanently for this rollout",
    f"Will you authorize changing root {KEY} from 7 to 8 permanently for this rollout",
    f"The assistant wrote: '{PERM}'",
    f"> {PERM}",
    f"Someone said {PERM}",
])
def test_non_decision_never_authorizes(w, surface, text):
    w.register()
    assert w.blocked(surface, SET8, texts=[text]), repr(text)


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("text", [
    PERM,
    f"I authorize changing root {KEY} from 7 to 8 permanently, it's the owner's call and I'm sure.",
    f"I authorize changing root `{KEY}` from 7 to 8 permanently for this rollout.",
    f"I authorize changing root {KEY} from 7 to 8 permanently for this rollout. Thanks, it's 'fine'.",
])
def test_genuine_decisions_including_apostrophes_and_backticks_still_admit(w, surface, text):
    w.register()
    assert not w.blocked(surface, SET8, texts=[text]), repr(text)


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("text", [
    'The assistant wrote: "Cancel that."',
    "'Cancel that.'",
    "\u2018Cancel that.\u2019",
    "> Cancel that.",
    "The assistant said cancel that.",
    f'The assistant wrote: "do not raise {KEY}"',
    "Did I say cancel that?",
])
def test_reported_or_quoted_cancellation_does_not_withdraw(w, surface, text):
    w.register()
    assert not w.blocked(surface, SET8, texts=[PERM, text]), repr(text)


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("text", [
    "Cancel that.",
    "Never mind.",
    "I said cancel that, the owner is not ready.",
    f"Do not raise {KEY}.",
    f"Don't raise {KEY} after all.",
])
def test_own_cancellation_still_withdraws(w, surface, text):
    w.register()
    assert w.blocked(surface, SET8, texts=[PERM, text]), repr(text)


@pytest.mark.parametrize("surface", SURFACES)
def test_quoted_later_value_does_not_supersede_but_native_one_does(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[PERM, f"'{PERM9}'"])
    assert w.blocked(surface, SET8, texts=[PERM, PERM9])
    assert not w.blocked(surface, "hermes config set kanban.max_in_progress 9", texts=[PERM, PERM9])


@pytest.mark.parametrize("surface", SURFACES)
def test_quoted_authorization_after_cancel_does_not_revive(w, surface):
    w.register()
    assert w.blocked(surface, SET8, texts=[PERM, "Cancel that.", f"'{PERM}'"])


@pytest.mark.parametrize("surface", SURFACES)
def test_unrelated_config_and_temporary_controls(w, surface):
    w.register()
    for texts in (["hello"], [f"'{PERM}'"], [PERM, "'Cancel that.'"]):
        assert not w.blocked(surface, BENIGN, texts=texts)
    # a quoted temporary authorization is still not authority; the genuine one remains governed by owner provenance
    assert w.blocked(surface, SET8, texts=[f"'{TEMP}'"])
