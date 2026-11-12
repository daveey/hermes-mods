"""A human's own restriction ("it is temporary", "do not make this permanent") must never be discarded and
upgraded to an untracked permanent authorization. Without valid own owner/restore terms the write refuses; with them
it is admitted; non-own restriction text (quoted, reported, question) neither adds nor removes anything.
Fixture homes only; no real config write is ever executed."""
import pytest

from test_root_cap_guard import w, KEY, SET8, SURFACES, TEMP_OWNER, FAR, PERM

BASE = f"I authorize changing root {KEY} from 7 to 8."
OWNER = f"Owner card {TEMP_OWNER}, run 1, this session."
RESTORE = f"Restore root {KEY} to 7 on owner completion or at {FAR}, whichever is first."

RESTRICTIONS = [
    "Do not make this permanent, it is temporary.",
    "Never treat this as permanent, it is temporary.",
    "Do not make this permanent.",
    "Don't make this permanent.",
    "Don\u2019t make this permanent.",
    "This is not permanent.",
    "It must never be permanent.",
    "It is temporary.",
    "This is only temporary, do not keep it.",
    "No longer permanent.",
]


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("restriction", RESTRICTIONS)
class TestOwnRestrictionRequiresProvenance:
    def test_without_terms_refuses(self, w, surface, restriction):
        w.register()
        assert w.blocked(surface, SET8, texts=[f"{BASE} {restriction}"])

    def test_restriction_before_authorization_refuses(self, w, surface, restriction):
        w.register()
        assert w.blocked(surface, SET8, texts=[f"{restriction} {BASE}"])

    def test_owner_only_refuses(self, w, surface, restriction):
        w.register()
        assert w.blocked(surface, SET8, texts=[f"{BASE} {restriction} {OWNER}"])

    def test_restore_only_refuses(self, w, surface, restriction):
        w.register()
        assert w.blocked(surface, SET8, texts=[f"{BASE} {restriction} {RESTORE}"])

    def test_own_valid_terms_admit(self, w, surface, restriction):
        w.register()
        assert not w.blocked(surface, SET8, texts=[f"{BASE} {restriction} {OWNER} {RESTORE}"])

    def test_quoted_terms_do_not_satisfy(self, w, surface, restriction):
        w.register()
        quoted = f'The assistant wrote: "{OWNER} {RESTORE}"'
        assert w.blocked(surface, SET8, texts=[f"{BASE} {restriction} {quoted}"])


@pytest.mark.parametrize("surface", SURFACES)
class TestControls:
    def test_permanent_still_admitted(self, w, surface):
        w.register()
        assert not w.blocked(surface, SET8, texts=[PERM])

    @pytest.mark.parametrize("phrase", [
        "This is not temporary.", "Do not make this temporary.", "It isn't a temporary exception.",
        "This is permanent, no owner needed."])
    def test_negated_temporary_alone_is_not_permanence(self, w, surface, phrase):
        w.register()
        assert w.blocked(surface, SET8, texts=[f"{BASE} {phrase}"]) is (phrase != "This is permanent, no owner needed.")

    @pytest.mark.parametrize("wrap", [
        'The assistant wrote: "Do not make this permanent, it is temporary."',
        "'Do not make this permanent, it is temporary.'",
        "\u2018It is temporary.\u2019",
        "Did I say it is temporary?",
        "The assistant said it is temporary.",
    ])
    def test_non_own_restriction_does_not_flip_permanent(self, w, surface, wrap):
        w.register()
        assert not w.blocked(surface, SET8, texts=[f"{BASE} permanently for this rollout. {wrap}"])

    def test_unrelated_config_allowed(self, w, surface):
        w.register()
        assert not w.blocked(surface, "hermes config set display.skin default", texts=[f"{BASE} It is temporary."])

    def test_queue_notice_only_still_refuses(self, w, surface):
        w.register()
        assert w.blocked(surface, SET8, texts=["Queue notice: headroom available, it is temporary."])
