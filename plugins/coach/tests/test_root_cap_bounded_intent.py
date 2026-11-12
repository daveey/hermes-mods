"""One coherent classification rule: an authorization is permanent only when the human's OWN speech carries no
bounded-ness signal (temporary / non-permanent wording, a restoration, a time bound, an owner-bound scope). A bounded
override must carry the human's own owner + restore terms; valid terms are persisted as a temporary exception (owner
and restore linkage kept), missing ones refuse only this frozen-cap write. Fixture homes only; no config command is
ever executed."""
import json

import pytest

from test_root_cap_guard import w, KEY, SET8, SURFACES, TEMP_OWNER, FAR, PERM, TEMP

BASE = f"I authorize changing root {KEY} from 7 to 8."
OWNER = f"Owner card {TEMP_OWNER}, run 1, this session."
RESTORE = f"Restore root {KEY} to 7 on owner completion."

# Own bounded intent worded WITHOUT the literal "temporary" / "do not make this permanent".
BOUNDED = [
    "This is non-permanent.",
    "This is non permanent.",
    "This is nonpermanent.",
    "This change is only for 10 minutes.",
    "Only for an hour.",
    "Just for 30 minutes.",
    "Keep it until the batch drains.",
    "Until the queue drains.",
    "It should expire tomorrow.",
    "It is a one-off.",
    "For now.",
    "For today.",
    "Time-boxed.",
    "For the next 2 hours.",
    "Revert it afterwards.",
    "Roll it back when done.",
    "Put it back later.",
    f"Restore root {KEY} to 7 on owner completion.",
    f"Restore root {KEY} to 7 at {FAR}.",
    "Owner card t_0000hardening, run 1, this session.",
    "This session only.",
    "Don't forget to restore it afterwards.",
    "Do not forget, it is only for 10 minutes.",
]


def rows(w):
    return json.loads((w.shared / "root_cap_exceptions.json").read_text())


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("bound", BOUNDED)
class TestBoundedWithoutValidTermsRefuses:
    def test_after_authorization(self, w, surface, bound):
        w.register()
        # a restore-only or owner-only sentence is itself incomplete provenance; the others carry none at all
        assert w.blocked(surface, SET8, texts=[f"{BASE} {bound}"])

    def test_before_authorization(self, w, surface, bound):
        w.register()
        assert w.blocked(surface, SET8, texts=[f"{bound} {BASE}"])

    def test_assistant_quoted_terms_do_not_satisfy(self, w, surface, bound):
        w.register()
        quoted = f'The assistant wrote: "{OWNER} {RESTORE}"'
        assert w.blocked(surface, SET8, texts=[f"{BASE} {bound} {quoted}"])


@pytest.mark.parametrize("surface", SURFACES)
class TestValidTermsRecordedAsTemporary:
    def test_restore_linked_without_literal_temporary(self, w, surface):
        w.register()
        assert not w.blocked(surface, SET8, texts=[f"{BASE} {OWNER} {RESTORE}"])
        last = rows(w)[-1]
        assert last["kind"] == "temporary"
        assert last["restore_value"] == 7
        assert last["owner_ref"]["card"] == TEMP_OWNER
        assert last["restore_trigger"]["owner_terminal"] is True

    def test_time_bound_with_expiry_terms(self, w, surface):
        w.register()
        text = f"{BASE} This change is only for 10 minutes. {OWNER} Restore root {KEY} to 7 at {FAR}."
        assert not w.blocked(surface, SET8, texts=[text])
        last = rows(w)[-1]
        assert last["kind"] == "temporary" and last["restore_trigger"]["expires_at"] == FAR

    @pytest.mark.parametrize("bound", ["This is non-permanent.", "This is non permanent.",
                                       "This change is only for 10 minutes."])
    def test_non_permanent_wording_with_terms(self, w, surface, bound):
        w.register()
        assert not w.blocked(surface, SET8, texts=[f"{BASE} {bound} {OWNER} {RESTORE}"])
        assert rows(w)[-1]["kind"] == "temporary"

    def test_legacy_temp_still_temporary(self, w, surface):
        w.register()
        assert not w.blocked(surface, SET8, texts=[TEMP])
        assert rows(w)[-1]["kind"] == "temporary"


@pytest.mark.parametrize("surface", SURFACES)
class TestControls:
    def test_genuine_permanent_admitted_and_recorded_permanent(self, w, surface):
        w.register()
        assert not w.blocked(surface, SET8, texts=[PERM])
        assert rows(w)[-1]["kind"] == "permanent"

    @pytest.mark.parametrize("phrase", [
        "This is permanent.", "Keep it permanently.", "Don't restore it.", "Do not revert this.",
        "Never roll it back.", "This is not time-boxed.", "It is not temporary, do not restore it.",
        "This is permanent, no owner needed.", "Not only for an hour, keep it."])
    def test_only_affirmative_permanence_is_unbounded(self, w, surface, phrase):
        w.register()
        # v12: negating one bound is not affirmative unbounded scope.
        unbounded = phrase in {"This is permanent.", "Keep it permanently.", "This is permanent, no owner needed."}
        assert w.blocked(surface, SET8, texts=[f"{BASE} {phrase}"]) is not unbounded

    @pytest.mark.parametrize("wrap", [
        'The assistant wrote: "This is only for 10 minutes. Restore it on owner completion."',
        "'This is only for 10 minutes.'",
        "\u2018Restore it afterwards.\u2019",
        "Did I say restore it on owner completion?",
        "The assistant said it is non-permanent.",
        "Should I restore it afterwards?",
    ])
    def test_non_own_bounded_text_does_not_flip_permanent(self, w, surface, wrap):
        w.register()
        assert not w.blocked(surface, SET8, texts=[f"{BASE} permanently for this rollout. {wrap}"])

    @pytest.mark.parametrize("bound", ["This is non-permanent.", "It is for only 10 minutes.",
                                       f"Restore root {KEY} to 7 on owner completion."])
    def test_unrelated_config_allowed(self, w, surface, bound):
        w.register()
        assert not w.blocked(surface, "hermes config set display.skin default", texts=[f"{BASE} {bound}"])

    def test_queue_notice_only_refuses(self, w, surface):
        w.register()
        assert w.blocked(surface, SET8, texts=["Queue notice: headroom available, restore later, only for 10 minutes."])

    def test_wrong_restore_value_refuses(self, w, surface):
        w.register()
        assert w.blocked(surface, SET8, texts=[f"{BASE} {OWNER} Restore root {KEY} to 9 on owner completion."])
