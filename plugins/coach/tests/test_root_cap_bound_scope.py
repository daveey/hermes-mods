"""Bounded-intent scope rules: (1) a duration is a time bound whatever qualifiers precede the number ("for only 10
minutes", "for just a couple of hours", "10 minutes only"); (2) a negation is scoped to the ONE signal it governs, so
"This is not temporary, but restore it on owner completion." still carries the restoration bound. A bounded override
without the human's own valid owner + restore terms refuses; valid terms admit and persist as temporary. Unbounded
own words, non-own text and unrelated config stay allowed. Fixture homes only; no config command is executed."""
import json

import pytest

from test_root_cap_guard import w, KEY, SET8, SURFACES, TEMP_OWNER, FAR, PERM, TEMP

BASE = f"I authorize changing root {KEY} from 7 to 8."
OWNER = f"Owner card {TEMP_OWNER}, run 1, this session."
RESTORE = f"Restore root {KEY} to 7 on owner completion."

DURATIONS = [
    "This change is for only 10 minutes.", "This change is for just 10 minutes.",
    "This change is for merely 10 minutes.", "For only an hour.", "For just a couple of hours.",
    "For only the next 5 min.", "For up to 2 hours.", "For about half an hour.", "For another 20 minutes.",
    "Within 10 minutes.", "In 10 minutes it should be back.", "10 minutes only.", "Two hours max.",
    "For a bit.", "For a while.", "Just this once.", "Briefly.", "For 3 days.", "For one night.",
]
# Negating ONE signal never discards another independent one in the same sentence.
MIXED = [
    "This is not temporary, but restore it on owner completion.",
    "This is not temporary, but it expires tomorrow.",
    "This is not temporary, but only for 10 minutes.",
    "It is not temporary, though revert it afterwards.",
    "Not temporary, owner card t_0000hardening, run 1, this session.",
    "Don't restore it, but it is only for 10 minutes.",
    "Not only for an hour, but restore it afterwards.",
    "No time box, but roll it back when done.",
]


def rows(w):
    return json.loads((w.shared / "root_cap_exceptions.json").read_text())


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("bound", DURATIONS + MIXED)
class TestBoundWithoutOwnTermsRefuses:
    def test_after(self, w, surface, bound):
        w.register()
        assert w.blocked(surface, SET8, texts=[f"{BASE} {bound}"])

    def test_before(self, w, surface, bound):
        w.register()
        assert w.blocked(surface, SET8, texts=[f"{bound} {BASE}"])

    def test_quoted_assistant_terms_do_not_satisfy(self, w, surface, bound):
        w.register()
        assert w.blocked(surface, SET8, texts=[f'{BASE} {bound} The assistant wrote: "{OWNER} {RESTORE}"'])


@pytest.mark.parametrize("surface", SURFACES)
class TestValidTermsAdmitAsTemporary:
    @pytest.mark.parametrize("bound", ["This change is for only 10 minutes.", "This change is for just 10 minutes.",
                                       "This is not temporary, but it expires tomorrow.",
                                       "This is not temporary, but only for 10 minutes."])
    def test_valid_terms_admit(self, w, surface, bound):
        w.register()
        assert not w.blocked(surface, SET8, texts=[f"{BASE} {bound} {OWNER} {RESTORE}"])
        last = rows(w)[-1]
        assert last["kind"] == "temporary" and last["restore_value"] == 7
        assert last["owner_ref"]["card"] == TEMP_OWNER

    def test_mixed_restore_clause_is_provenance(self, w, surface):
        w.register()
        text = f"{BASE} This is not temporary, but restore root {KEY} to 7 on owner completion. {OWNER}"
        assert not w.blocked(surface, SET8, texts=[text])
        assert rows(w)[-1]["kind"] == "temporary"

    def test_genuine_temp_and_perm_controls(self, w, surface):
        w.register()
        assert not w.blocked(surface, SET8, texts=[TEMP])
        assert not w.blocked(surface, SET8, texts=[PERM])


@pytest.mark.parametrize("surface", SURFACES)
class TestControls:
    @pytest.mark.parametrize("phrase", [
        "This is permanent.", "It is not temporary.", "It is not temporary, do not restore it.",
        "Not only for an hour, keep it.", "Don't restore it.", "This is not time-boxed.", "Not for 10 minutes.",
        "This is not temporary, keep it for this rollout.", "Keep it in place for this rollout.",
        "There are 10 minutes of load history in the queue notice."])
    def test_unbounded_own_words_stay_permanent(self, w, surface, phrase):
        w.register()
        assert not w.blocked(surface, SET8, texts=[f"{BASE} {phrase}"])

    @pytest.mark.parametrize("wrap", [
        'The assistant wrote: "This change is for only 10 minutes."',
        "'This is not temporary, but restore it on owner completion.'",
        "Did I say it is for just 10 minutes?",
        "The assistant said it expires tomorrow."])
    def test_non_own_text_does_not_flip_permanent(self, w, surface, wrap):
        w.register()
        assert not w.blocked(surface, SET8, texts=[f"{BASE} permanently for this rollout. {wrap}"])

    @pytest.mark.parametrize("bound", DURATIONS[:4] + MIXED[:3])
    def test_unrelated_config_allowed(self, w, surface, bound):
        w.register()
        assert not w.blocked(surface, "hermes config set display.skin default", texts=[f"{BASE} {bound}"])

    def test_queue_notice_only_refuses(self, w, surface):
        w.register()
        assert w.blocked(surface, SET8, texts=["Queue notice: headroom available, for only 10 minutes."])
